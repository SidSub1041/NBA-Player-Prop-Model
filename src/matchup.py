"""
Continuous matchup adjustments for the edge model (round 18).

The old model turned every matchup input into a yes/no condition (a DvP
colour, a top-10 rank). Measured on last season's labeled props those
conditions were inert, so round 18 keeps the same inputs but reads them as
NUMBERS: how far the opponent sits from the league (a z-score), shrunk into
a small multiplier on the player's projection. Everything here is pure
arithmetic over dicts the integrator already has:

  * `scrape_dvp_table()`   -> dvp_z / dvp_multiplier   (by-position DvP)
  * zone / playtype tables -> zone_score / playtype_score  (points only)
  * pace factor, OUT starters -> matchup_multiplier    (the product, clipped)

Sign convention everywhere: positive z = the opponent allows MORE than the
league = good for the over.
"""

import logging
from statistics import fmean, pstdev

from src.config import (
    DVP_SHRINK, MATCHUP_WEIGHT, PACE_WEIGHT, POINTS_STATS,
    TEAMMATE_OUT_BOOST, TEAMMATE_OUT_MAX,
)
from src.injuries import injury_for
from src.underdog import norm_name, norm_name_base

logger = logging.getLogger(__name__)

POSITIONS = ["PG", "SG", "SF", "PF", "C"]

# Bounds on the individual DvP multiplier and on the combined multiplier:
# a matchup should nudge a projection, never rewrite it.
DVP_MULT_RANGE = (0.85, 1.15)
MULTIPLIER_RANGE = (0.80, 1.25)


# ── generic league-table arithmetic ──────────────────────────────────────

def _numeric(entry, field=None) -> float | None:
    """A team's number from a table cell that is either the number itself or
    a dict holding it under `field` (the rankings dicts keep fg_pct / ppp
    next to a rank)."""
    if isinstance(entry, dict):
        entry = entry.get(field) if field else None
    try:
        v = float(entry)
    except (TypeError, ValueError):
        return None
    return None if v != v else v  # NaN


def league_stats(column: dict, field: str | None = None) -> dict | None:
    """Mean / population sd / count of one table column ({team: value}).

    The column IS the league (all 30 teams), so the population sd is the
    right denominator for a team's z-score. None when nothing is numeric.
    """
    vals = [v for v in (_numeric(e, field) for e in (column or {}).values()) if v is not None]
    if not vals:
        return None
    return {"mean": fmean(vals), "sd": pstdev(vals), "n": len(vals)}


def table_z(table: dict, key, team: str, field: str | None = None) -> float | None:
    """z-score of `team` within `table[key]` ({team: value-or-dict}).

    None when the team or key is missing; 0.0 when every team has the same
    value (sd 0).
    """
    column = (table or {}).get(key) or {}
    if team not in column:
        return None
    v = _numeric(column[team], field)
    stats = league_stats(column, field)
    if v is None or stats is None:
        return None
    if stats["sd"] == 0:
        return 0.0
    return (v - stats["mean"]) / stats["sd"]


# ── defense vs position ──────────────────────────────────────────────────

def dvp_z(dvp: dict, opponent: str, position: str, stat: str) -> float | None:
    """How far the opponent's allowed `stat` to `position` sits from the
    league, in league sds, from `scrape_dvp_table()` output.

    None when the team/position/stat is missing from the table; 0.0 when
    the league sd is 0 (every team identical, nothing to say).
    """
    values = (dvp or {}).get("values") or {}
    league = (dvp or {}).get("league") or {}
    value = values.get((opponent, position, stat))
    stats = league.get((position, stat))
    if value is None or not stats or stats.get("mean") is None:
        return None
    sd = stats.get("sd") or 0.0
    if sd == 0:
        return 0.0
    return (float(value) - float(stats["mean"])) / float(sd)


def dvp_multiplier(z: float | None, mean: float | None, sd: float | None, shrink: float = DVP_SHRINK) -> float:
    """Projection multiplier from a DvP z-score: `1 + shrink × (value/mean − 1)`
    with value = mean + z·sd, i.e. the opponent's relative deviation from the
    league, shrunk (DVP_SHRINK, default half) because a by-position DvP table
    is noisy. 1.0 when any input is unknown; clipped to DVP_MULT_RANGE.
    """
    if z is None or mean is None or sd is None:
        return 1.0
    try:
        z, mean, sd = float(z), float(mean), float(sd)
    except (TypeError, ValueError):
        return 1.0
    if mean <= 0 or z != z or sd != sd:
        return 1.0
    value = mean + z * sd
    m = 1.0 + shrink * (value / mean - 1.0)
    lo, hi = DVP_MULT_RANGE
    return min(hi, max(lo, m))


# ── zones and playtypes (points) ─────────────────────────────────────────

def _share_score(shares, table, opponent, share_key, zone_key, field, unit) -> dict:
    """Σ share × z over the entries where the opponent is ranked.

    `shares` is the player's list of {zone_key, share_key, ...}; `table` is
    {entry: {team: {field: value, "rank": n}}} (or {entry: {team: value}}).
    Entries with no share are not used; entries where the opponent has no
    number are noted but not counted.
    """
    z_total, n, details = 0.0, 0, []
    for s in shares or []:
        key = s.get(zone_key)
        try:
            share = float(s.get(share_key) or 0.0)
        except (TypeError, ValueError):
            share = 0.0
        if not key or share <= 0:
            continue
        z = table_z(table, key, opponent, field)
        if z is None:
            details.append(f"{key}: no data for {opponent} (not counted)")
            continue
        z_total += share * z
        n += 1
        details.append(f"{key}: {share:.0%} of {unit} × z {z:+.1f}")
    return {"z": z_total, "n": n, "details": details}


def zone_score(shares: list[dict], zone_rankings: dict, opponent: str) -> dict:
    """Shooting-zone fit: Σ fga_pct × z(opponent FG% allowed in that zone).

    `shares` from `shooting_zones.get_player_zone_shares` (all zones, no
    20% cut); `zone_rankings` from `get_team_zone_rankings` ({zone: {team:
    {"fg_pct","rank"}}}) or a plain {zone: {team: fg_pct}} table. Positive =
    the opponent allows a higher FG% where this player shoots = good for the
    over. Returns {"z", "n" (zones used), "details"}.
    """
    return _share_score(shares, zone_rankings, opponent, "fga_pct", "zone", "fg_pct", "FGA")


def playtype_score(shares: list[dict], pt_rankings: dict, opponent: str) -> dict:
    """Playtype fit: Σ freq_pct × z(opponent PPP allowed in that playtype).

    `shares` from `playtypes.get_player_playtype_shares` (all playtypes);
    `pt_rankings` from `get_team_playtype_defense_rankings` ({playtype:
    {team: {"ppp","rank"}}}). Same sign convention as zone_score.
    """
    return _share_score(shares, pt_rankings, opponent, "freq_pct", "playtype", "ppp", "poss")


# ── the combined multiplier ──────────────────────────────────────────────

def matchup_multiplier(stat: str, dvp_z_value, dvp_league, zone, playtype, pace_f, teammate_boost) -> dict:
    """Combine the matchup inputs into one multiplier on the projection mean.

    points (and combos that include points):
        pace_f^PACE_WEIGHT × dvp_mult × (1 + MATCHUP_WEIGHT × (zone.z + playtype.z)) × teammate_boost
    rebounds / assists:
        pace_f^PACE_WEIGHT × dvp_mult × teammate_boost

    `dvp_league` is the {"mean","sd","n"} entry for (position, stat) from
    `scrape_dvp_table()["league"]`; `zone`/`playtype` are the score dicts
    (None when not computed). Any missing input is neutral (1.0 / z 0).
    Clipped to MULTIPLIER_RANGE. Returns the applied components too:
    {"multiplier", "pace", "dvp", "matchup_z", "teammate"}.
    """
    try:
        pace_f = float(pace_f) if pace_f is not None else 1.0
    except (TypeError, ValueError):
        pace_f = 1.0
    if pace_f <= 0 or pace_f != pace_f:
        pace_f = 1.0
    pace_part = pace_f ** PACE_WEIGHT

    mean = sd = None
    if isinstance(dvp_league, dict):
        mean, sd = dvp_league.get("mean"), dvp_league.get("sd")
    dvp_part = dvp_multiplier(dvp_z_value, mean, sd)

    try:
        tb = float(teammate_boost) if teammate_boost is not None else 1.0
    except (TypeError, ValueError):
        tb = 1.0
    if tb <= 0 or tb != tb:
        tb = 1.0

    matchup_z = None
    if stat in POINTS_STATS:
        parts = []
        for score in (zone, playtype):
            if isinstance(score, dict) and _numeric(score.get("z")) is not None:
                parts.append(float(score["z"]))
        if parts:
            matchup_z = sum(parts)
        m = pace_part * dvp_part * (1.0 + MATCHUP_WEIGHT * (matchup_z or 0.0)) * tb
    else:
        m = pace_part * dvp_part * tb

    lo, hi = MULTIPLIER_RANGE
    m = min(hi, max(lo, m))
    return {"multiplier": m, "pace": pace_part, "dvp": dvp_part, "matchup_z": matchup_z, "teammate": tb}


# ── teammates and positions from the depth chart ─────────────────────────

def _starter_names(depth_charts: dict, team: str) -> list[tuple[str, str]]:
    """[(position, name)] for a team's depth chart, PG..C first then anything else."""
    chart = (depth_charts or {}).get(team) or {}
    order = [p for p in POSITIONS if p in chart] + [p for p in chart if p not in POSITIONS]
    out = []
    for pos in order:
        entry = chart.get(pos)
        name = entry.get("name") if isinstance(entry, dict) else entry
        if name:
            out.append((pos, str(name)))
    return out


def teammate_outs(injuries: dict, depth_charts: dict, team: str, player: str) -> int:
    """How many of the player's team-mates who START (listed on the team's
    depth chart, any position) are OUT on the injury report.

    Name matching tolerates the usual depth-chart vs CBS variants (accents,
    initials, Jr.). An injury entry that names a different team is ignored
    (stale depth chart after a trade). The player is never counted.
    """
    me = norm_name_base(player)
    seen, n = set(), 0
    for _pos, name in _starter_names(depth_charts, team):
        key = norm_name_base(name)
        if not key or key == me or key in seen:
            continue
        seen.add(key)
        info = injury_for(injuries, name)
        if not info or info.get("status") != "out":
            continue
        entry_team = (info.get("team") or "").upper()
        if entry_team and team and entry_team != team.upper():
            continue
        n += 1
    return n


def teammate_boost(n: int | None) -> float:
    """1 + TEAMMATE_OUT_BOOST per OUT starter, at most TEAMMATE_OUT_MAX of them."""
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        n = 0
    return 1.0 + TEAMMATE_OUT_BOOST * min(max(n, 0), TEAMMATE_OUT_MAX)


def position_of(player: str, depth_charts: dict, team: str, fallback: str = "SF") -> str:
    """The position the team's depth chart lists the player at (first match,
    exact normalised name before a suffix-insensitive one), else `fallback`."""
    starters = _starter_names(depth_charts, team)
    target = norm_name(player)
    for pos, name in starters:
        if norm_name(name) == target:
            return pos
    base = norm_name_base(player)
    for pos, name in starters:
        if norm_name_base(name) == base:
            return pos
    return fallback


# ── positions for players the depth chart does not list ─────────────────

# NBA.com roster position -> the DvP rows it can face (FantasyPros tables are by PG/SG/SF/PF/C)
ROSTER_TO_DVP = {
    "G": ["PG", "SG"], "F": ["SF", "PF"], "C": ["C"],
    "G-F": ["SG", "SF"], "F-G": ["SF", "SG"], "F-C": ["PF", "C"], "C-F": ["C", "PF"],
    "PG": ["PG"], "SG": ["SG"], "SF": ["SF"], "PF": ["PF"],
}

_roster_cache: dict[str, dict] = {}


def reset_roster_cache() -> None:
    _roster_cache.clear()


def roster_index(season: str) -> dict:
    """
    {norm_name (and suffix-free alias when unique): {"name", "team", "position"}}
    for every player on a current roster, from one PlayerIndex request per run
    (NBA.com's league-wide roster list: current team and listed position, e.g.
    "G-F"). Empty on failure; the endpoint is optional for the run.
    """
    if season in _roster_cache:
        return _roster_cache[season]
    out: dict = {}
    try:
        import time
        from nba_api.stats.endpoints import playerindex
        time.sleep(0.6)
        df = playerindex.PlayerIndex(season=season).get_data_frames()[0]
        base_seen: dict = {}
        for _, r in df.iterrows():
            name = f"{r.get('PLAYER_FIRST_NAME', '')} {r.get('PLAYER_LAST_NAME', '')}".strip()
            if not name:
                continue
            entry = {"name": name, "team": (str(r.get("TEAM_ABBREVIATION") or "") or None),
                     "position": (str(r.get("POSITION") or "").strip() or None)}
            out[norm_name(name)] = entry
            b = norm_name_base(name)
            base_seen[b] = None if b in base_seen else entry
        for b, entry in base_seen.items():
            if entry is not None and b not in out:
                out[b] = entry
    except Exception as e:
        logger.warning(f"  Roster positions unavailable ({type(e).__name__}: {e}); bench players get a neutral DvP")
    _roster_cache[season] = out
    return out


def dvp_positions(player: str, depth_charts: dict, team: str, rosters: dict) -> tuple[list, str | None]:
    """
    The DvP rows that apply to a player, and the label to show: the depth
    chart's position for a listed starter; else every row his NBA.com roster
    position covers ("G" -> PG and SG); else none (neutral DvP — never a guess).
    """
    pos = position_of(player, depth_charts, team, fallback="")
    if pos:
        return [pos], pos
    hit = (rosters or {}).get(norm_name(player)) or (rosters or {}).get(norm_name_base(player))
    raw = (hit or {}).get("position")
    if raw and raw.upper() in ROSTER_TO_DVP:
        return ROSTER_TO_DVP[raw.upper()], raw.upper()
    return [], None


def dvp_blend(dvp: dict, opponent: str, positions: list, stat: str) -> tuple[float | None, float | None]:
    """
    (average z, average relative deviation z·sd/mean) of the opponent's stat
    allowed over the given DvP rows; (None, None) when none is available. The
    relative deviation feeds dvp_multiplier with mean 1 and sd 1.
    """
    zs, rels = [], []
    for pos in positions or []:
        z = dvp_z(dvp, opponent, pos, stat)
        league = (dvp.get("league") or {}).get((pos, stat)) if isinstance(dvp, dict) else None
        if z is None or not league or not league.get("mean"):
            continue
        zs.append(z)
        rels.append(z * (league.get("sd") or 0.0) / league["mean"])
    if not zs:
        return None, None
    return sum(zs) / len(zs), sum(rels) / len(rels)
