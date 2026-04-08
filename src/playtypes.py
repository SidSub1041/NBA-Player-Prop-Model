"""
Fetch player and team playtype data from NBA.com Stats API (Synergy).

Player playtypes: identifies playtypes where a player has >= 15% frequency.
Team playtypes (defense): ranks teams by PPP allowed in each playtype.
"""

import logging
import time
import pandas as pd
from nba_api.stats.endpoints import synergyplaytypes

from src.config import (
    SEASON_NBA_API, PLAYTYPE_CATEGORIES,
    PLAYTYPE_FREQ_THRESHOLD, TOP_BOTTOM_RANK, NBA_TEAM_IDS,
)

logger = logging.getLogger(__name__)


def get_player_playtypes(player_name: str) -> list[dict]:
    """
    Get player's primary playtypes (>= 15% frequency).

    Returns list of dicts:
    [{"playtype": "Isolation", "freq_pct": 0.25, "ppp": 1.05, "poss": 120}, ...]
    """
    logger.info(f"Fetching playtypes for {player_name}")
    all_playtypes = []

    for playtype in PLAYTYPE_CATEGORIES:
        time.sleep(0.6)
        try:
            data = synergyplaytypes.SynergyPlaytypes(
                season=SEASON_NBA_API,
                play_type_nullable=playtype,
                player_or_team_abbreviation="P",
                type_grouping_nullable="offensive",
                per_mode_simple="PerGame",
            )
            df = data.get_data_frames()[0]

            if df.empty:
                continue

            # Find the player (case-insensitive match)
            player_rows = df[df["PLAYER_NAME"].str.lower().str.contains(
                player_name.lower().split()[-1], na=False
            )]

            if player_rows.empty:
                continue

            # Refine match
            for _, row in player_rows.iterrows():
                if _names_match(row["PLAYER_NAME"], player_name):
                    freq = row.get("POSS_PCT", row.get("FREQ", 0))
                    if isinstance(freq, str):
                        freq = float(freq.replace("%", "")) / 100
                    ppp = row.get("PPP", row.get("POINTS_PER_POSSESSION", 0))
                    poss = row.get("POSS", row.get("GP", 0))

                    all_playtypes.append({
                        "playtype": playtype,
                        "freq_pct": round(float(freq), 3),
                        "ppp": round(float(ppp), 3),
                        "poss": int(poss),
                    })
                    break

        except Exception as e:
            logger.debug(f"  Playtype {playtype} fetch failed: {e}")
            continue

    # Filter to primary playtypes (>= 15% frequency)
    primary = [pt for pt in all_playtypes if pt["freq_pct"] >= PLAYTYPE_FREQ_THRESHOLD]

    for pt in primary:
        logger.info(
            f"  Primary playtype: {pt['playtype']} - "
            f"{pt['freq_pct']:.1%} freq, {pt['ppp']:.3f} PPP"
        )

    return primary


def get_team_playtype_defense_rankings() -> dict[str, dict[str, dict]]:
    """
    Get all teams' defensive PPP allowed rankings per playtype.

    Returns: {playtype: {team_abbrev: {"ppp": float, "rank": int}}}
    Rank 1 = allows highest PPP (worst defense / best for over).
    Rank 30 = allows lowest PPP (best defense / best for under).
    """
    logger.info("Fetching team playtype defense rankings...")
    rankings = {}
    id_to_abbrev = {v: k for k, v in NBA_TEAM_IDS.items()}

    for playtype in PLAYTYPE_CATEGORIES:
        time.sleep(0.6)
        try:
            data = synergyplaytypes.SynergyPlaytypes(
                season=SEASON_NBA_API,
                play_type_nullable=playtype,
                player_or_team_abbreviation="T",
                type_grouping_nullable="defensive",
                per_mode_simple="PerGame",
            )
            df = data.get_data_frames()[0]

            if df.empty:
                continue

            # Build rankings sorted by PPP descending (worst defense first)
            team_ppp = []
            for _, row in df.iterrows():
                team_id = row.get("TEAM_ID", 0)
                team_name = row.get("TEAM_NAME", "")
                team_abbrev = id_to_abbrev.get(team_id, "")

                if not team_abbrev:
                    team_abbrev = _team_name_to_abbrev(team_name)

                ppp = row.get("PPP", row.get("POINTS_PER_POSSESSION", 0))

                if team_abbrev:
                    team_ppp.append((team_abbrev, float(ppp)))

            # Sort descending (rank 1 = highest PPP allowed = worst defense)
            team_ppp.sort(key=lambda x: x[1], reverse=True)

            playtype_rankings = {}
            for rank, (abbrev, ppp) in enumerate(team_ppp, start=1):
                playtype_rankings[abbrev] = {"ppp": round(ppp, 3), "rank": rank}

            rankings[playtype] = playtype_rankings

        except Exception as e:
            logger.debug(f"  Playtype {playtype} defense fetch failed: {e}")
            continue

    return rankings


def score_playtypes(
    player_playtypes: list[dict],
    team_playtype_rankings: dict,
    opponent_abbrev: str,
    edge: str,
) -> dict:
    """
    Score the playtype conditions.

    For 'over': opponent should be in TOP 10 (rank 1-10) for PPP allowed (worst defense).
    For 'under': opponent should be in BOTTOM 10 (rank 21-30) for PPP allowed (best defense).

    Returns: {"points": int, "total": int, "details": list[str]}
    """
    points = 0
    total = len(player_playtypes)
    details = []

    for pt_info in player_playtypes:
        playtype = pt_info["playtype"]
        pt_rankings = team_playtype_rankings.get(playtype, {})
        opp_data = pt_rankings.get(opponent_abbrev)

        if not opp_data:
            details.append(f"  {playtype}: No data for {opponent_abbrev}")
            continue

        rank = opp_data["rank"]
        ppp = opp_data["ppp"]
        num_teams = len(pt_rankings)

        if edge == "over" and rank <= TOP_BOTTOM_RANK:
            points += 1
            details.append(
                f"  ✓ {playtype}: {opponent_abbrev} rank {rank}/{num_teams} "
                f"(PPP allowed: {ppp:.3f}) - FAVORABLE for over"
            )
        elif edge == "under" and rank > (num_teams - TOP_BOTTOM_RANK):
            points += 1
            details.append(
                f"  ✓ {playtype}: {opponent_abbrev} rank {rank}/{num_teams} "
                f"(PPP allowed: {ppp:.3f}) - FAVORABLE for under"
            )
        else:
            details.append(
                f"  ✗ {playtype}: {opponent_abbrev} rank {rank}/{num_teams} "
                f"(PPP allowed: {ppp:.3f}) - Not favorable"
            )

    return {"points": points, "total": total, "details": details}


def _names_match(name1: str, name2: str) -> bool:
    """Fuzzy match two player names."""
    n1 = name1.lower().strip()
    n2 = name2.lower().strip()
    if n1 == n2:
        return True
    parts1 = n1.split()
    parts2 = n2.split()
    if len(parts1) >= 2 and len(parts2) >= 2:
        if parts1[-1] == parts2[-1] and parts1[0][0] == parts2[0][0]:
            return True
    return False


def _team_name_to_abbrev(team_name: str) -> str:
    """Convert team name to abbreviation."""
    from src.config import TEAM_ABBREVS
    for full_name, abbrev in TEAM_ABBREVS.items():
        if team_name.lower() in full_name.lower() or full_name.lower() in team_name.lower():
            return abbrev
    return ""
