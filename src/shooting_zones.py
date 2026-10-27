"""
Fetch player and team shooting zone data from NBA.com Stats API.

Player shooting zones: identifies zones where a player takes >= 20% of their FGA.
Team shooting zones (defense): ranks teams by FG% allowed in each zone.

The ShotLocations endpoints return a two-level header (a zone row over a
FGM/FGA/FG_PCT row). nba_api exposes that as a pandas MultiIndex, which we
flatten to "<Zone>_FGA"-style column names before reading.
"""

import logging
import time
from datetime import date

import pandas as pd
from nba_api.stats.endpoints import (
    leaguedashplayershotlocations,
    leaguedashteamshotlocations,
)

from src.config import (
    SEASON_TYPE, SHOOTING_ZONES, FGA_ZONE_THRESHOLD, TOP_BOTTOM_RANK, NBA_TEAM_IDS,
)
from src.season import stats_season

logger = logging.getLogger(__name__)

# Backcourt heaves are noise for this analysis
SCORING_ZONES = [z for z in SHOOTING_ZONES if z != "Backcourt"]


def _flatten_shot_frame(endpoint) -> pd.DataFrame:
    """DataFrame with single-level columns like 'Restricted Area_FGA'."""
    df = endpoint.get_data_frames()[0]
    if isinstance(df.columns, pd.MultiIndex):
        flat = []
        for col in df.columns:
            parts = [str(p) for p in col if p not in (None, "") and not str(p).startswith("Unnamed")]
            flat.append("_".join(parts))
        df = df.copy()
        df.columns = flat
    return df


def _zone_cols(df: pd.DataFrame, zone: str) -> tuple[str, str, str] | None:
    fga, fgm, pct = f"{zone}_FGA", f"{zone}_FGM", f"{zone}_FG_PCT"
    if fga in df.columns and pct in df.columns:
        return fga, fgm, pct
    return None


_player_frame_cache: dict[tuple, pd.DataFrame] = {}


def _num(x) -> float:
    """float() that treats None/NaN/blank as 0."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if v != v else v  # NaN check


def _league_player_shot_frame() -> pd.DataFrame:
    """
    League-wide player shot-location frame for the stats season, fetched once
    per run (no team filter: during the early-season window the stats come
    from last season, so a filter on this season's team would drop every
    player who changed teams).
    """
    key = (stats_season(), date.today().isoformat())
    if key in _player_frame_cache:
        return _player_frame_cache[key]
    time.sleep(0.6)
    shot_data = leaguedashplayershotlocations.LeagueDashPlayerShotLocations(
        season=stats_season(),
        season_type_all_star=SEASON_TYPE,
        distance_range="By Zone",
    )
    df = _flatten_shot_frame(shot_data)
    _player_frame_cache.clear()
    _player_frame_cache[key] = df
    logger.info(f"Loaded shot locations for {len(df)} players ({key[0]})")
    return df


def get_player_shooting_zones(player_name: str, team_abbrev: str) -> list[dict]:
    """
    Get player's primary shooting zones (>= 20% of total FGA).

    Returns list of dicts:
    [{"zone": "Restricted Area", "fga_pct": 0.35, "fga": 150, "fgm": 90, "fg_pct": 0.60}, ...]
    """
    logger.info(f"Fetching shooting zones for {player_name} ({team_abbrev})")
    try:
        df = _league_player_shot_frame()
        return _parse_player_shot_locations(df, player_name)
    except Exception as e:
        logger.error(f"Failed to fetch player shooting zones: {e}")
        return []


def _player_zone_shares(df: pd.DataFrame, player_name: str) -> list[dict]:
    """
    Every scoring zone for a player from the flattened shot-location frame,
    with the zone's (unrounded) share of his FGA. [] when the player is not
    in the frame or has no attempts. Shared by the primary-zone filter and
    the round-18 continuous zone score.
    """
    zones: list[dict] = []
    if df.empty or "PLAYER_NAME" not in df.columns:
        return zones

    names = df["PLAYER_NAME"].astype(str)
    exact = df[names.str.strip().str.lower() == player_name.strip().lower()]
    match = exact if not exact.empty else df[names.apply(lambda n: _names_match(n, player_name))]
    if match.empty:
        logger.warning(f"Player {player_name} not found in shot locations data")
        return zones
    row = match.iloc[0]

    zone_data = {}
    total_fga = 0.0
    for zone in SCORING_ZONES:
        cols = _zone_cols(df, zone)
        if not cols:
            continue
        fga_col, fgm_col, pct_col = cols
        fga = _num(row[fga_col])
        total_fga += fga
        zone_data[zone] = {
            "fga": fga,
            "fgm": _num(row[fgm_col]) if fgm_col in df.columns else 0.0,
            "fg_pct": _num(row[pct_col]),
        }

    if total_fga <= 0:
        logger.warning(f"No shot attempts on record for {player_name}")
        return zones

    for zone, data in zone_data.items():
        zones.append({
            "zone": zone,
            "fga_pct": data["fga"] / total_fga,
            "fga": data["fga"],
            "fgm": data["fgm"],
            "fg_pct": data["fg_pct"],
        })
    return zones


def _round_zone(z: dict) -> dict:
    return {
        "zone": z["zone"],
        "fga_pct": round(z["fga_pct"], 3),
        "fga": z["fga"],
        "fgm": z["fgm"],
        "fg_pct": round(z["fg_pct"], 3),
    }


def _parse_player_shot_locations(df: pd.DataFrame, player_name: str) -> list[dict]:
    """Extract a player's primary zones from the flattened shot-location frame."""
    zones: list[dict] = []
    for z in _player_zone_shares(df, player_name):
        if z["fga_pct"] >= FGA_ZONE_THRESHOLD:
            zones.append(_round_zone(z))
            logger.info(f"  Primary zone: {z['zone']} - {z['fga_pct']:.1%} FGA, {z['fg_pct']:.1%} FG%")
    return zones


def get_player_zone_shares(player_name: str) -> list[dict]:
    """
    ALL of a player's scoring zones with their share of his FGA (no 20% cut):
    [{"zone", "fga_pct", "fga", "fgm", "fg_pct"}, ...] in SCORING_ZONES order.
    The round-18 zone score weights every zone by this share. [] on failure.
    """
    logger.info(f"Fetching zone shares for {player_name}")
    try:
        df = _league_player_shot_frame()
        return [_round_zone(z) for z in _player_zone_shares(df, player_name)]
    except Exception as e:
        logger.error(f"Failed to fetch player zone shares: {e}")
        return []


def get_team_zone_rankings() -> dict[str, dict[str, dict]]:
    """
    Get all teams' defensive FG% allowed rankings by zone.

    Returns: {zone_name: {team_abbrev: {"fg_pct": float, "rank": int}}}
    Rank 1 = allows highest FG% (worst defense / best for over).
    Rank 30 = allows lowest FG% (best defense / best for under).

    NOTE: The methodology specifies filtering by position group
    (Guard / Forward / Center) when evaluating team zone defense.
    The nba_api LeagueDashTeamShotLocations endpoint does not expose
    a position filter, so overall team zone defense is used as a proxy.
    """
    logger.info("Fetching team shooting zone defense rankings...")
    time.sleep(0.6)

    try:
        team_data = leaguedashteamshotlocations.LeagueDashTeamShotLocations(
            season=stats_season(),
            season_type_all_star=SEASON_TYPE,
            distance_range="By Zone",
        )
        df = _flatten_shot_frame(team_data)
        return _parse_team_zone_rankings(df)
    except Exception as e:
        logger.error(f"Failed to fetch team zone rankings: {e}")
        return {}


def _parse_team_zone_rankings(df: pd.DataFrame) -> dict[str, dict[str, dict]]:
    """Per-zone rankings of FG% allowed from the flattened team frame."""
    rankings: dict[str, dict[str, dict]] = {}
    if df.empty or "TEAM_ID" not in df.columns:
        return rankings
    id_to_abbrev = {v: k for k, v in NBA_TEAM_IDS.items()}

    for zone in SCORING_ZONES:
        cols = _zone_cols(df, zone)
        if not cols:
            continue
        pct_col = cols[2]
        team_pcts = []
        for _, row in df.iterrows():
            abbrev = id_to_abbrev.get(int(row["TEAM_ID"]), "")
            pct = row[pct_col]
            if abbrev and pd.notna(pct):
                team_pcts.append((abbrev, float(pct)))
        team_pcts.sort(key=lambda x: x[1], reverse=True)
        rankings[zone] = {
            abbrev: {"fg_pct": round(pct, 3), "rank": rank}
            for rank, (abbrev, pct) in enumerate(team_pcts, start=1)
        }
    return rankings


def zone_table_from_rankings(rankings: dict) -> dict[str, dict[str, float]]:
    """{zone: {team: fg_pct}} from a get_team_zone_rankings() dict (no extra request)."""
    table: dict[str, dict[str, float]] = {}
    for zone, teams in (rankings or {}).items():
        column = {}
        for team, entry in (teams or {}).items():
            pct = entry.get("fg_pct") if isinstance(entry, dict) else entry
            if pct is not None and pct == pct:
                column[team] = float(pct)
        table[zone] = column
    return table


def get_team_zone_table() -> dict[str, dict[str, float]]:
    """
    {zone: {team: FG% allowed}} — the numbers behind get_team_zone_rankings
    without the ranks. Its own request; when the rankings dict is already in
    hand, zone_table_from_rankings() gives the same table for free.
    """
    logger.info("Fetching team shooting zone table...")
    time.sleep(0.6)
    try:
        team_data = leaguedashteamshotlocations.LeagueDashTeamShotLocations(
            season=stats_season(),
            season_type_all_star=SEASON_TYPE,
            distance_range="By Zone",
        )
        df = _flatten_shot_frame(team_data)
        return zone_table_from_rankings(_parse_team_zone_rankings(df))
    except Exception as e:
        logger.error(f"Failed to fetch team zone table: {e}")
        return {}


def league_zone_stats(table_or_rankings: dict) -> dict[str, dict]:
    """{zone: {"mean", "sd", "n"}} of FG% allowed across the league, from either
    a {zone: {team: fg_pct}} table or a get_team_zone_rankings() dict."""
    from src.matchup import league_stats
    out = {}
    for zone, column in (table_or_rankings or {}).items():
        stats = league_stats(column, "fg_pct")
        if stats:
            out[zone] = stats
    return out


def zone_z(table_or_rankings: dict, zone: str, opponent: str) -> float | None:
    """
    How far the opponent's FG% allowed in `zone` sits from the league, in
    league sds (+ = allows more = good for the over). None when the zone or
    team is missing. Accepts the table or the rankings dict.
    """
    from src.matchup import table_z
    return table_z(table_or_rankings, zone, opponent, "fg_pct")


def score_shooting_zones(
    player_zones: list[dict],
    team_zone_rankings: dict,
    opponent_abbrev: str,
    edge: str,
) -> dict:
    """
    Score the shooting zone conditions.

    For 'over': opponent should be in TOP 10 (rank 1-10) for FG% allowed (worst defense).
    For 'under': opponent should be in BOTTOM 10 (rank 21-30) for FG% allowed (best defense).

    Returns: {"points": int, "total": int, "details": list[str]}
    """
    points = 0
    total = 0  # only zones the opponent can actually be ranked in count
    details = []

    for zone_info in player_zones:
        zone_name = zone_info["zone"]
        zone_data = team_zone_rankings.get(zone_name, {})
        opp_data = zone_data.get(opponent_abbrev)

        if not opp_data:
            details.append(f"  {zone_name}: No data for {opponent_abbrev} (not counted)")
            continue
        total += 1

        rank = opp_data["rank"]
        fg_pct = opp_data["fg_pct"]
        num_teams = len(zone_data)

        if edge == "over" and rank <= TOP_BOTTOM_RANK:
            points += 1
            details.append(
                f"  ✓ {zone_name}: {opponent_abbrev} rank {rank}/{num_teams} "
                f"(FG% allowed: {fg_pct:.1%}) - FAVORABLE for over"
            )
        elif edge == "under" and rank > (num_teams - TOP_BOTTOM_RANK):
            points += 1
            details.append(
                f"  ✓ {zone_name}: {opponent_abbrev} rank {rank}/{num_teams} "
                f"(FG% allowed: {fg_pct:.1%}) - FAVORABLE for under"
            )
        else:
            details.append(
                f"  ✗ {zone_name}: {opponent_abbrev} rank {rank}/{num_teams} "
                f"(FG% allowed: {fg_pct:.1%}) - Not favorable"
            )

    return {"points": points, "total": total, "details": details}


def _names_match(name1: str, name2: str) -> bool:
    """Fuzzy match two player names."""
    n1 = name1.lower().strip()
    n2 = name2.lower().strip()
    if n1 == n2:
        return True
    # Check if last names match and first initial matches
    parts1 = n1.split()
    parts2 = n2.split()
    if len(parts1) >= 2 and len(parts2) >= 2:
        if parts1[-1] == parts2[-1] and parts1[0][0] == parts2[0][0]:
            return True
    return False
