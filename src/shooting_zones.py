"""
Fetch player and team shooting zone data from NBA.com Stats API.

Player shooting zones: identifies zones where a player takes >= 20% of their FGA.
Team shooting zones (defense): ranks teams by FG% allowed in each zone.
"""

import logging
import time
import pandas as pd
from nba_api.stats.endpoints import (
    leaguedashplayershotlocations,
    leaguedashteamshotlocations,
)

from src.config import (
    SEASON_NBA_API, SEASON_TYPE, SHOOTING_ZONES,
    FGA_ZONE_THRESHOLD, TOP_BOTTOM_RANK, POSITION_MAP, NBA_TEAM_IDS,
)

logger = logging.getLogger(__name__)


def get_player_shooting_zones(player_name: str, team_abbrev: str) -> list[dict]:
    """
    Get player's primary shooting zones (>= 20% of total FGA).

    Returns list of dicts:
    [{"zone": "Restricted Area", "fga_pct": 0.35, "fga": 150, "fgm": 90, "fg_pct": 0.60}, ...]
    """
    logger.info(f"Fetching shooting zones for {player_name} ({team_abbrev})")
    time.sleep(0.6)

    try:
        team_id = NBA_TEAM_IDS.get(team_abbrev, 0)
        shot_data = leaguedashplayershotlocations.LeagueDashPlayerShotLocations(
            season=SEASON_NBA_API,
            season_type_all_star=SEASON_TYPE,
            distance_range="By Zone",
            team_id_nullable=team_id,
        )

        # This endpoint returns a complex multi-header structure
        result_sets = shot_data.get_normalized_dict()

        # Parse the shot locations data
        player_zones = _parse_player_shot_locations(result_sets, player_name)
        return player_zones

    except Exception as e:
        logger.error(f"Failed to fetch player shooting zones: {e}")
        return []


def _parse_player_shot_locations(result_sets: dict, player_name: str) -> list[dict]:
    """
    Parse the complex shot locations response to extract zone data for a player.
    The endpoint returns multi-level headers with zone-specific FGA/FGM/FG% columns.
    """
    zones = []

    # The shot locations endpoint has a specific structure
    # Headers contain zone names, and each row is a player
    try:
        # Get the data frames directly
        headers_key = list(result_sets.keys())[0] if result_sets else None
        if not headers_key:
            return zones

        rows = result_sets[headers_key]
        if not rows:
            return zones

        # Find the player row (case-insensitive partial match)
        player_row = None
        for row in rows:
            name = row.get("PLAYER_NAME", row.get("Player", ""))
            if _names_match(name, player_name):
                player_row = row
                break

        if not player_row:
            logger.warning(f"Player {player_name} not found in shot locations data")
            return zones

        # Extract zone data from the row
        # Column naming pattern: {ZONE}_FGA, {ZONE}_FGM, {ZONE}_FG_PCT
        total_fga = 0
        zone_data = {}

        zone_key_patterns = {
            "Restricted Area": ["RESTRICTED_AREA", "RA", "Restricted Area"],
            "In The Paint (Non-RA)": ["IN_THE_PAINT", "ITP", "In The Paint"],
            "Mid-Range": ["MID_RANGE", "MR", "Mid-Range", "Mid Range"],
            "Left Corner 3": ["LEFT_CORNER_3", "LC3", "Left Corner 3"],
            "Right Corner 3": ["RIGHT_CORNER_3", "RC3", "Right Corner 3"],
            "Above the Break 3": ["ABOVE_THE_BREAK_3", "AB3", "Above the Break 3"],
        }

        # First pass: get total FGA
        for zone_name, patterns in zone_key_patterns.items():
            for pattern in patterns:
                fga_key = f"{pattern}_FGA"
                if fga_key in player_row:
                    fga = player_row[fga_key]
                    total_fga += fga
                    fgm_key = f"{pattern}_FGM"
                    pct_key = f"{pattern}_FG_PCT"
                    zone_data[zone_name] = {
                        "fga": fga,
                        "fgm": player_row.get(fgm_key, 0),
                        "fg_pct": player_row.get(pct_key, 0.0),
                    }
                    break

        if total_fga == 0:
            # Try alternative column structure (numbered columns)
            return _parse_player_zones_alternative(player_row)

        # Second pass: calculate percentages and filter
        for zone_name, data in zone_data.items():
            fga_pct = data["fga"] / total_fga if total_fga > 0 else 0
            if fga_pct >= FGA_ZONE_THRESHOLD:
                zones.append({
                    "zone": zone_name,
                    "fga_pct": round(fga_pct, 3),
                    "fga": data["fga"],
                    "fgm": data["fgm"],
                    "fg_pct": round(data["fg_pct"], 3),
                })
                logger.info(
                    f"  Primary zone: {zone_name} - "
                    f"{fga_pct:.1%} FGA, {data['fg_pct']:.1%} FG%"
                )

    except Exception as e:
        logger.error(f"Error parsing shot locations: {e}")

    return zones


def _parse_player_zones_alternative(player_row: dict) -> list[dict]:
    """
    Alternative parser for when column names don't match expected patterns.
    Maps numbered/positional columns to zones.
    """
    zones = []
    # Try to extract any FGA/FG% columns
    fga_cols = [k for k in player_row.keys() if "FGA" in str(k).upper()]
    fgm_cols = [k for k in player_row.keys() if "FGM" in str(k).upper()]
    pct_cols = [k for k in player_row.keys() if "PCT" in str(k).upper() or "FG%" in str(k).upper()]

    if fga_cols:
        total_fga = sum(player_row.get(c, 0) for c in fga_cols if isinstance(player_row.get(c, 0), (int, float)))

        zone_names = list(SHOOTING_ZONES[:len(fga_cols)])
        for i, (fga_col, zone_name) in enumerate(zip(fga_cols, zone_names)):
            fga = player_row.get(fga_col, 0)
            if not isinstance(fga, (int, float)):
                continue
            fga_pct = fga / total_fga if total_fga > 0 else 0

            fg_pct = 0
            if i < len(pct_cols):
                fg_pct = player_row.get(pct_cols[i], 0)

            if fga_pct >= FGA_ZONE_THRESHOLD:
                zones.append({
                    "zone": zone_name,
                    "fga_pct": round(fga_pct, 3),
                    "fga": fga,
                    "fgm": player_row.get(fgm_cols[i], 0) if i < len(fgm_cols) else 0,
                    "fg_pct": round(fg_pct, 3) if isinstance(fg_pct, (int, float)) else 0,
                })

    return zones


def get_team_zone_rankings() -> dict[str, dict[str, dict]]:
    """
    Get all teams' defensive FG% allowed rankings by zone.

    Returns: {zone_name: {team_abbrev: {"fg_pct": float, "rank": int}}}
    Rank 1 = allows highest FG% (worst defense / best for over).
    Rank 30 = allows lowest FG% (best defense / best for under).
    """
    logger.info("Fetching team shooting zone defense rankings...")
    time.sleep(0.6)

    try:
        team_data = leaguedashteamshotlocations.LeagueDashTeamShotLocations(
            season=SEASON_NBA_API,
            season_type_all_star=SEASON_TYPE,
            distance_range="By Zone",
        )

        result_sets = team_data.get_normalized_dict()
        return _parse_team_zone_rankings(result_sets)

    except Exception as e:
        logger.error(f"Failed to fetch team zone rankings: {e}")
        return {}


def _parse_team_zone_rankings(result_sets: dict) -> dict[str, dict[str, dict]]:
    """Parse team shot location data into per-zone rankings."""
    rankings = {}
    id_to_abbrev = {v: k for k, v in NBA_TEAM_IDS.items()}

    try:
        headers_key = list(result_sets.keys())[0] if result_sets else None
        if not headers_key:
            return rankings

        rows = result_sets[headers_key]
        if not rows:
            return rankings

        zone_key_patterns = {
            "Restricted Area": ["RESTRICTED_AREA", "RA"],
            "In The Paint (Non-RA)": ["IN_THE_PAINT", "ITP"],
            "Mid-Range": ["MID_RANGE", "MR"],
            "Left Corner 3": ["LEFT_CORNER_3", "LC3"],
            "Right Corner 3": ["RIGHT_CORNER_3", "RC3"],
            "Above the Break 3": ["ABOVE_THE_BREAK_3", "AB3"],
        }

        for zone_name, patterns in zone_key_patterns.items():
            pct_key = None
            for pattern in patterns:
                candidate = f"{pattern}_FG_PCT"
                if rows and candidate in rows[0]:
                    pct_key = candidate
                    break

            if not pct_key:
                continue

            # Build list of (team_abbrev, fg_pct) and sort descending
            team_pcts = []
            for row in rows:
                team_id = row.get("TEAM_ID", 0)
                team_abbrev = id_to_abbrev.get(team_id, "")
                fg_pct = row.get(pct_key, 0)
                if team_abbrev and isinstance(fg_pct, (int, float)):
                    team_pcts.append((team_abbrev, fg_pct))

            # Sort descending (rank 1 = highest FG% allowed = worst defense)
            team_pcts.sort(key=lambda x: x[1], reverse=True)

            zone_rankings = {}
            for rank, (abbrev, pct) in enumerate(team_pcts, start=1):
                zone_rankings[abbrev] = {"fg_pct": round(pct, 3), "rank": rank}

            rankings[zone_name] = zone_rankings

    except Exception as e:
        logger.error(f"Error parsing team zone rankings: {e}")

    return rankings


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
    total = len(player_zones)
    details = []

    for zone_info in player_zones:
        zone_name = zone_info["zone"]
        zone_data = team_zone_rankings.get(zone_name, {})
        opp_data = zone_data.get(opponent_abbrev)

        if not opp_data:
            details.append(f"  {zone_name}: No data for {opponent_abbrev}")
            continue

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
