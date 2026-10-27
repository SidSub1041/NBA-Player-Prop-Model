"""
Core scoring engine that implements the full methodology.

LEGACY (pre-round-18) path, kept so older boards and tests still evaluate.
Round 18's edge model lives in main.py, src/projection.py and src/matchup.py;
PropScore.compute_edge_grade() is its grade.

For each DvP-highlighted matchup:
1. Find the player at the relevant position on the opposing team
2. For POINTS:
   a. Get player's primary shooting zones (>= 20% FGA)
   b. Check if opponent is top/bottom 10 in FG% allowed per zone
   c. Get player's primary playtypes (>= 15% freq)
   d. Check if opponent is top/bottom 10 in PPP allowed per playtype
   e. If >= 80% of conditions met, check hit rate (>= 56%)
3. For REBOUNDS/ASSISTS: use DvP ranking + hit rate (simplified path)

Each condition met = 1 "point". Final score = points / total conditions.
"""

import logging

from src.config import CONDITION_PASS_RATE, CONDITION_PASS_RATE_POINTS, HITRATE_THRESHOLD, POINTS_STATS
from src.shooting_zones import (
    get_player_shooting_zones,
    get_team_zone_rankings,
    score_shooting_zones,
)
from src.playtypes import (
    get_player_playtypes,
    get_team_playtype_defense_rankings,
    score_playtypes,
)
from src.hitrate import SOURCE_LINE, get_player_hitrate
from src.adaptive import get_adaptive_score

logger = logging.getLogger(__name__)


class PropScore:
    """Represents the score evaluation for a single player prop."""

    def __init__(self, player_name: str, team: str, opponent: str,
                 position: str, stat: str, edge: str):
        self.player_name = player_name
        self.team = team
        self.opponent = opponent
        self.position = position
        self.stat = stat
        self.edge = edge  # "over" or "under"

        # Scoring results
        self.zone_score = {"points": 0, "total": 0, "details": []}
        self.playtype_score = {"points": 0, "total": 0, "details": []}
        self.total_points = 0
        self.total_conditions = 0
        self.pass_rate = 0.0
        self.hitrate_data = {}
        self.is_valid = False
        self.final_grade = "F"
        self.adaptive_multiplier = 1.0  # set by adaptive module

        # Underdog Fantasy line/odds
        self.ud_line = None       # e.g. 20.5
        self.ud_over_odds = ""    # e.g. "-112"
        self.ud_under_odds = ""   # e.g. "-110"

        # On hold: passes the conditions but has no line to measure against yet
        self.on_hold = False
        self.hold_reason = ""
        self.injury = None        # injury-report label, e.g. "GTD · Ankle"

        # The designated line (books first, DFS apps as backup) — see src/lines.py
        self.line_info = {}
        self.resolution = None
        # The books' consensus for this prop (display + CSV)
        self.vegas = {}

        # The learning loop's view of this prop (src/learner.py)
        self.learned = {}
        self.learn_note = ""

        # ── Round 18: the edge model (src/projection.py, src/matchup.py) ──
        self.projection = {}      # {"mu", "sd", "minutes", "dist", "n_this", "n_last", "weight_this", "p_over", "p_under", "adjustments": {...}}
        self.p_model = None       # modelled probability of the pick side at the pick line
        self.p_market = None      # de-vigged market probability of the pick side at the pick line (current consensus, shifted to the pick line)
        self.edge_value = None    # p_model − p_market
        self.open_line = None     # first line seen today (line history)
        self.line_move = None     # latest line − opening line, signed toward the pick (+ = market moved our way)
        self.dvp_z = None         # opponent's by-position stat allowed, z-score (+ = allows more)
        self.matchup_z = None     # points: zone + playtype continuous score (+ = favourable for over)
        self.teammate_out = 0     # OUT starters on the player's own team
        self.paused = False       # stat paused by PAUSED_STATS: evaluated and shown, never published
        self.cap_note = ""        # dropped by the board caps (MAX_PICKS / MAX_PER_GAME / MAX_PER_PLAYER)
        self.guards = []          # guard names that failed: price / books / line_size / minutes

    # ── designated line helpers ──────────────────────────────────────
    @property
    def line(self):
        return (self.line_info or {}).get("line")

    def side_odds(self) -> str:
        info = self.line_info or {}
        return (info.get("under_odds") if self.edge == "under" else info.get("over_odds")) or ""

    def side_book(self) -> str:
        info = self.line_info or {}
        return (info.get("under_book") if self.edge == "under" else info.get("over_book")) or ""

    @property
    def total_score_pct(self) -> float:
        if self.total_conditions == 0:
            return 0.0
        raw = self.total_points / self.total_conditions
        return min(1.0, raw * self.adaptive_multiplier)

    @property
    def valid_bar(self) -> float:
        """Share of conditions a prop of this stat must meet to be valid."""
        return CONDITION_PASS_RATE_POINTS if self.stat in POINTS_STATS else CONDITION_PASS_RATE

    def compute_edge_grade(self):
        """
        Round 18 grade: from the edge over the market at the best price.
        A+ edge ≥ STRONG_EDGE, A ≥ MIN_EDGE, B+ ≥ WATCH_EDGE, B > 0, C ≤ 0; HOLD
        without a line. A pick is valid only when it is A+/A, every guard
        passes (a STRONG_EDGE edge may override the price and line-size
        guards), the stat is not paused and no cap dropped it.
        """
        from src.config import MIN_EDGE, STRONG_EDGE, WATCH_EDGE
        self.is_valid = False
        self.on_hold = False
        if self.line is None:
            self.on_hold = True
            self.final_grade = "HOLD"
            return
        e = self.edge_value
        if e is None:
            self.final_grade = "D"
            return
        if e >= STRONG_EDGE:
            self.final_grade = "A+"
        elif e >= MIN_EDGE:
            self.final_grade = "A"
        elif e >= WATCH_EDGE:
            self.final_grade = "B+"
        elif e > 0:
            self.final_grade = "B"
        else:
            self.final_grade = "C"
        hard = [g for g in self.guards if g in ("books", "minutes")]
        soft = [g for g in self.guards if g in ("price", "line_size")]
        self.is_valid = (
            self.final_grade in ("A+", "A") and not hard and (not soft or e >= STRONG_EDGE)
            and not self.paused and not self.cap_note
        )

    def compute_grade(self):
        """Legacy grade (pre-round-18): share of conditions met + hit rate vs the line."""
        pct = self.total_score_pct
        hr = self.hitrate_data.get("hitrate", -1)
        valid_bar = self.valid_bar
        watch_bar = 0.45 if self.stat in POINTS_STATS else 0.60   # B+/B
        c_bar = 0.30 if self.stat in POINTS_STATS else 0.40        # C
        # The hit-rate gate is measured against the line. A prop that passes the
        # conditions but has no line yet is put ON HOLD: later runs re-check it
        # once Underdog posts (or restores) the line.
        vs_line = self.hitrate_data.get("source") == SOURCE_LINE
        self.is_valid = False
        self.on_hold = False

        if pct >= valid_bar and self.hitrate_data.get("line") is None:
            self.on_hold = True
            self.final_grade = "HOLD"
        elif pct >= valid_bar and vs_line and hr >= HITRATE_THRESHOLD:
            self.is_valid = True
            self.final_grade = "A+"
        elif pct >= valid_bar and vs_line and hr >= 0.50:
            self.is_valid = True
            self.final_grade = "A"
        elif pct >= watch_bar and hr >= HITRATE_THRESHOLD:
            self.final_grade = "B+"
        elif pct >= watch_bar:
            self.final_grade = "B"
        elif pct >= c_bar:
            self.final_grade = "C"
        else:
            self.final_grade = "D"

    def summary(self) -> str:
        """One-line summary."""
        ud = ""
        if self.line is not None:
            ud = f" | Line: {self.line} ({self.side_odds()} {self.side_book()})"
        return (
            f"{self.player_name} ({self.team} vs {self.opponent}) | "
            f"{self.stat.upper()} {self.edge.upper()}{ud} | "
            f"Score: {self.total_points}/{self.total_conditions} "
            f"({self.total_score_pct:.0%}) | "
            f"Hit Rate: {self.hitrate_data.get('hitrate', -1):.0%} | "
            f"Grade: {self.final_grade} | "
            f"{'✅ VALID' if self.is_valid else ('⏸ HOLD — ' + (self.hold_reason or 'awaiting line')) if self.on_hold else '❌ SKIP'}"
        )

    def detailed_report(self) -> str:
        """Multi-line detailed report."""
        lines = [
            f"{'='*70}",
            f"PLAYER: {self.player_name} ({self.team})",
            f"OPPONENT: {self.opponent}",
            f"POSITION: {self.position}",
            f"STAT: {self.stat.upper()} | EDGE: {self.edge.upper()}",
            f"{'='*70}",
        ]

        if self.stat == "points":
            lines.append(f"\n--- Shooting Zone Analysis ---")
            lines.append(
                f"  Zones met: {self.zone_score['points']}/{self.zone_score['total']}"
            )
            for d in self.zone_score["details"]:
                lines.append(d)

            lines.append(f"\n--- Playtype Analysis ---")
            lines.append(
                f"  Playtypes met: {self.playtype_score['points']}/{self.playtype_score['total']}"
            )
            for d in self.playtype_score["details"]:
                lines.append(d)

        lines.append(f"\n--- Overall Score ---")
        lines.append(
            f"  Total: {self.total_points}/{self.total_conditions} "
            f"({self.total_score_pct:.0%})"
        )
        lines.append(
            f"  Threshold: {self.valid_bar:.0%} needed for validity"
        )

        hr = self.hitrate_data.get("hitrate", -1)
        hd = self.hitrate_data
        lines.append(f"\n--- Hit Rate ---")
        if hd.get("source") == SOURCE_LINE:
            lines.append(
                f"  {self.edge.capitalize()} {hd.get('line')}: {hr:.1%} "
                f"({hd.get('games_hit')}/{hd.get('games_played')} games)"
            )
        elif hr >= 0:
            lines.append(f"  Estimate vs season average (no line — can't validate): {hr:.1%}")
        else:
            lines.append("  Unavailable")
        lines.append(f"  Source: {hd.get('source', 'N/A')}")
        if "season_avg" in hd:
            lines.append(f"  Season average: {hd['season_avg']}")
        lines.append(f"  Threshold: {HITRATE_THRESHOLD:.0%} needed against the line")

        if self.line is not None:
            info = self.line_info
            src_label = {"vegas": "sportsbooks", "dfs": "DFS apps (backup)", "underdog": "Underdog backup"}.get(info.get("source"), "line")
            lines.append(f"\n--- Line ({src_label}) ---")
            lines.append(f"  Line: {self.line}  ({info.get('books') or 1} book(s))")
            lines.append(f"  Over:  {info.get('over_odds')} @ {info.get('over_book')}")
            lines.append(f"  Under: {info.get('under_odds')} @ {info.get('under_book')}")
        if self.ud_line is not None and (self.line_info or {}).get("source") != "underdog":
            lines.append(f"  Underdog: {self.ud_line} (O {self.ud_over_odds} / U {self.ud_under_odds})")

        lines.append(f"\n--- FINAL GRADE: {self.final_grade} ---")
        lines.append(f"  {'✅ VALID PICK' if self.is_valid else '❌ DOES NOT QUALIFY'}")
        lines.append("")

        return "\n".join(lines)


def evaluate_points_prop(
    player_name: str,
    team: str,
    opponent: str,
    position: str,
    edge: str,
    team_zone_rankings: dict,
    team_playtype_rankings: dict,
    line: float | None = None,
    before: str | None = None,
) -> PropScore:
    """
    Full methodology evaluation for a POINTS prop.

    1. Get player's primary shooting zones
    2. Score each zone against opponent's defensive ranking
    3. Get player's primary playtypes
    4. Score each playtype against opponent's defensive ranking
    5. Calculate total score
    6. If >= 80% met, check hit rate
    """
    prop = PropScore(player_name, team, opponent, position, "points", edge)

    # Step 1-2: Shooting zones
    player_zones = get_player_shooting_zones(player_name, team)
    if player_zones:
        prop.zone_score = score_shooting_zones(
            player_zones, team_zone_rankings, opponent, edge
        )
    else:
        logger.warning(f"No shooting zone data for {player_name}")

    # Step 3-4: Playtypes (skipped in --fast mode, when no team rankings were
    # fetched: a playtype with nothing to rank against can never be met)
    if not team_playtype_rankings:
        logger.info(f"  Playtype analysis skipped for {player_name} (no team rankings / fast mode)")
    else:
        player_playtypes = get_player_playtypes(player_name)
        if player_playtypes:
            prop.playtype_score = score_playtypes(
                player_playtypes, team_playtype_rankings, opponent, edge
            )
        else:
            logger.warning(f"No playtype data for {player_name}")

    # Step 5: Total score
    prop.total_points = prop.zone_score["points"] + prop.playtype_score["points"]
    prop.total_conditions = prop.zone_score["total"] + prop.playtype_score["total"]

    # Step 6: Hit rate check (only if >= 80% conditions met OR for reporting)
    prop.hitrate_data = get_player_hitrate(player_name, "points", line, edge, before)
    if prop.total_conditions > 0:
        prop.pass_rate = prop.total_score_pct

    prop.compute_grade()
    return prop


def evaluate_rebound_prop(
    player_name: str,
    team: str,
    opponent: str,
    position: str,
    edge: str,
    line: float | None = None,
    before: str | None = None,
) -> PropScore:
    """
    Evaluate REBOUNDS prop.
    Uses DvP signal + hit rate analysis.
    For rebounds, the DvP signal itself counts as the primary condition.
    Additional check: opponent's rebounding stats allowed by position.
    """
    prop = PropScore(player_name, team, opponent, position, "rebounds", edge)

    # DvP already identified this as a favorable matchup (1 point)
    prop.total_points = 1
    prop.total_conditions = 1

    # Hit rate check
    prop.hitrate_data = get_player_hitrate(player_name, "rebounds", line, edge, before)
    prop.compute_grade()
    return prop


def evaluate_assist_prop(
    player_name: str,
    team: str,
    opponent: str,
    position: str,
    edge: str,
    line: float | None = None,
    before: str | None = None,
) -> PropScore:
    """
    Evaluate ASSISTS prop.
    Uses DvP signal + hit rate analysis.
    """
    prop = PropScore(player_name, team, opponent, position, "assists", edge)

    # DvP already identified this as a favorable matchup (1 point)
    prop.total_points = 1
    prop.total_conditions = 1

    # Hit rate check
    prop.hitrate_data = get_player_hitrate(player_name, "assists", line, edge, before)
    prop.compute_grade()
    return prop


def evaluate_prop(
    player_name: str,
    team: str,
    opponent: str,
    position: str,
    stat: str,
    edge: str,
    team_zone_rankings: dict | None = None,
    team_playtype_rankings: dict | None = None,
    adaptive_weights: dict | None = None,
    line: float | None = None,
    before: str | None = None,
) -> PropScore:
    """Route to the correct evaluation function based on stat category."""
    if stat == "points":
        prop = evaluate_points_prop(
            player_name, team, opponent, position, edge,
            team_zone_rankings or {},
            team_playtype_rankings or {},
            line,
            before,
        )
    elif stat == "rebounds":
        prop = evaluate_rebound_prop(player_name, team, opponent, position, edge, line, before)
    elif stat == "assists":
        prop = evaluate_assist_prop(player_name, team, opponent, position, edge, line, before)
    else:
        return PropScore(player_name, team, opponent, position, stat, edge)

    # Apply adaptive learning multiplier
    if adaptive_weights:
        hr = prop.hitrate_data.get("hitrate", -1)
        hr_val = hr if hr >= 0 else None
        prop.adaptive_multiplier = get_adaptive_score(
            stat, edge, hr_val, adaptive_weights
        )
        if prop.adaptive_multiplier != 1.0:
            logger.info(
                f"    Adaptive adjustment: {prop.adaptive_multiplier:.3f}x "
                f"(raw {prop.total_points}/{prop.total_conditions})"
            )
            # Recompute grade after adjustment
            prop.compute_grade()

    return prop


# ── Combo prop definitions ───────────────────────────────────────────────────

COMBO_DEFS = {
    "pts+ast": {"components": ["points", "assists"], "label": "PTS+AST"},
    "pts+reb": {"components": ["points", "rebounds"], "label": "PTS+REB"},
    "reb+ast": {"components": ["rebounds", "assists"], "label": "REB+AST"},
    "pra":     {"components": ["points", "rebounds", "assists"], "label": "PRA"},
}


def combo_candidates(prop_scores: list["PropScore"]) -> list[tuple]:
    """(player, team, combo_stat, edge) for every combo build_combo_props would build."""
    by_player: dict[tuple[str, str], dict[str, "PropScore"]] = {}
    for p in prop_scores:
        by_player.setdefault((p.player_name, p.edge), {})[p.stat] = p
    out = []
    for (player_name, edge), stat_props in by_player.items():
        for combo_stat, combo_def in COMBO_DEFS.items():
            comps = [stat_props.get(s) for s in combo_def["components"]]
            if all(comps) and all(c.total_score_pct >= 0.60 for c in comps):
                out.append((player_name, comps[0].team, combo_stat, edge))
    return out


def build_combo_props(prop_scores: list["PropScore"], line_for, before: str | None = None) -> list["PropScore"]:
    """
    Build combination prop picks from individually-evaluated props.

    A combo qualifies when a player has edges on multiple stats that combine
    into a known combo prop. The combo score aggregates the component scores.

    Only generates "over" combos from individual "over" edges, and "under"
    combos from individual "under" edges.

    Args:
        prop_scores: All individually evaluated PropScore objects.
        line_for: callable(player, team, stat) -> src.lines.Resolution (books
                  first, Underdog backup) for the combo's line.

    Returns:
        List of new PropScore objects for combo props.
    """
    from src.lines import apply as apply_line

    # Group by (player_name, edge) → {stat: PropScore}
    player_edges: dict[tuple[str, str], dict[str, "PropScore"]] = {}
    for p in prop_scores:
        key = (p.player_name, p.edge)
        if key not in player_edges:
            player_edges[key] = {}
        player_edges[key][p.stat] = p

    combos = []

    for (player_name, edge), stat_props in player_edges.items():
        for combo_stat, combo_def in COMBO_DEFS.items():
            components = combo_def["components"]

            # Check if player has ALL component stats evaluated for this edge
            component_props = [stat_props.get(s) for s in components]
            if not all(component_props):
                continue

            # All components must be at least B-grade (60%+ conditions)
            if not all(p.total_score_pct >= 0.60 for p in component_props):
                continue

            # Build combo PropScore
            first = component_props[0]
            combo = PropScore(
                player_name=first.player_name,
                team=first.team,
                opponent=first.opponent,
                position=first.position,
                stat=combo_stat,
                edge=edge,
            )

            # Aggregate scores across components
            combo.total_points = sum(p.total_points for p in component_props)
            combo.total_conditions = sum(p.total_conditions for p in component_props)

            # Average adaptive multiplier from components
            combo.adaptive_multiplier = sum(
                p.adaptive_multiplier for p in component_props
            ) / len(component_props)

            # The combo's own line (books first, Underdog backup), then the hit
            # rate of the summed stats against it — the same gate as single stats
            apply_line(combo, line_for(player_name, first.team, combo_stat))
            combo.hitrate_data = get_player_hitrate(player_name, combo_stat, combo.line, edge, before)

            combo.compute_grade()

            # Store which component stats contributed
            combo.zone_score = {
                "points": combo.total_points,
                "total": combo.total_conditions,
                "details": [
                    f"  Combo: {combo_def['label']} = "
                    + " + ".join(
                        f"{p.stat.upper()} ({p.total_points}/{p.total_conditions})"
                        for p in component_props
                    )
                ],
            }

            logger.info(
                f"  Combo: {player_name} {combo_def['label']} {edge.upper()} "
                f"| {combo.total_points}/{combo.total_conditions} "
                f"({combo.total_score_pct:.0%}) "
                f"| HR {combo.hitrate_data.get('hitrate', -1):.0%} | {combo.final_grade}"
                f"{' ✅' if combo.is_valid else ''}"
            )

            combos.append(combo)

    logger.info(f"Generated {len(combos)} combo props ({sum(1 for c in combos if c.is_valid)} valid)")
    return combos
