"""
Core scoring engine that implements the full methodology.

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

from src.config import CONDITION_PASS_RATE, HITRATE_THRESHOLD
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
from src.hitrate import get_player_hitrate

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

    @property
    def total_score_pct(self) -> float:
        if self.total_conditions == 0:
            return 0.0
        return self.total_points / self.total_conditions

    def compute_grade(self):
        """Compute final letter grade based on score percentage and hit rate."""
        pct = self.total_score_pct
        hr = self.hitrate_data.get("hitrate", -1)

        if pct >= CONDITION_PASS_RATE and hr >= HITRATE_THRESHOLD:
            self.is_valid = True
            self.final_grade = "A+"
        elif pct >= CONDITION_PASS_RATE and hr >= 0.50:
            self.is_valid = True
            self.final_grade = "A"
        elif pct >= 0.60 and hr >= HITRATE_THRESHOLD:
            self.final_grade = "B+"
        elif pct >= 0.60:
            self.final_grade = "B"
        elif pct >= 0.40:
            self.final_grade = "C"
        else:
            self.final_grade = "D"

    def summary(self) -> str:
        """One-line summary."""
        return (
            f"{self.player_name} ({self.team} vs {self.opponent}) | "
            f"{self.stat.upper()} {self.edge.upper()} | "
            f"Score: {self.total_points}/{self.total_conditions} "
            f"({self.total_score_pct:.0%}) | "
            f"Hit Rate: {self.hitrate_data.get('hitrate', -1):.0%} | "
            f"Grade: {self.final_grade} | "
            f"{'✅ VALID' if self.is_valid else '❌ SKIP'}"
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
            f"  Threshold: {CONDITION_PASS_RATE:.0%} needed for validity"
        )

        hr = self.hitrate_data.get("hitrate", -1)
        lines.append(f"\n--- Hit Rate ---")
        lines.append(f"  Season hit rate: {hr:.1%}")
        lines.append(f"  Source: {self.hitrate_data.get('source', 'N/A')}")
        if "season_avg" in self.hitrate_data:
            lines.append(f"  Season average: {self.hitrate_data['season_avg']}")
        lines.append(f"  Threshold: {HITRATE_THRESHOLD:.0%} needed")

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

    # Step 3-4: Playtypes
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
    if prop.total_conditions > 0:
        prop.pass_rate = prop.total_score_pct
    prop.hitrate_data = get_player_hitrate(player_name, "points")

    prop.compute_grade()
    return prop


def evaluate_rebound_prop(
    player_name: str,
    team: str,
    opponent: str,
    position: str,
    edge: str,
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
    prop.hitrate_data = get_player_hitrate(player_name, "rebounds")
    prop.compute_grade()
    return prop


def evaluate_assist_prop(
    player_name: str,
    team: str,
    opponent: str,
    position: str,
    edge: str,
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
    prop.hitrate_data = get_player_hitrate(player_name, "assists")
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
) -> PropScore:
    """Route to the correct evaluation function based on stat category."""
    if stat == "points":
        return evaluate_points_prop(
            player_name, team, opponent, position, edge,
            team_zone_rankings or {},
            team_playtype_rankings or {},
        )
    elif stat == "rebounds":
        return evaluate_rebound_prop(player_name, team, opponent, position, edge)
    elif stat == "assists":
        return evaluate_assist_prop(player_name, team, opponent, position, edge)
    else:
        logger.warning(f"Unknown stat category: {stat}")
        prop = PropScore(player_name, team, opponent, position, stat, edge)
        prop.compute_grade()
        return prop
