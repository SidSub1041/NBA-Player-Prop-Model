"""
Fetch player prop lines from the Underdog Fantasy API.

Endpoint: https://api.underdogfantasy.com/beta/v5/over_under_lines
Returns line values and American odds keyed by (player_name, stat).
"""

import logging
import requests

logger = logging.getLogger(__name__)

API_URL = "https://api.underdogfantasy.com/beta/v5/over_under_lines"

# Map our internal stat names → Underdog stat titles
STAT_MAP = {
    "points": "Points",
    "rebounds": "Rebounds",
    "assists": "Assists",
    "3-pointers made": "3-Pointers Made",
    "steals": "Steals",
    "blocks": "Blocks",
    "turnovers": "Turnovers",
}


def fetch_underdog_lines() -> dict:
    """
    Fetch Underdog Fantasy NBA lines.

    Returns:
        dict keyed by (player_name_lower, stat_lower) → {
            "line": float,
            "over_odds": str,   # e.g. "-112"
            "under_odds": str,  # e.g. "-110"
            "over_multiplier": str,
            "under_multiplier": str,
        }
    """
    try:
        resp = requests.get(API_URL, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning(f"Failed to fetch Underdog lines: {e}")
        return {}

    # Build lookup indexes
    players = {p["id"]: p for p in data.get("players", [])}
    appearances = {a["id"]: a for a in data.get("appearances", [])}

    # Filter to NBA games only
    nba_game_ids = {
        g["id"] for g in data.get("games", [])
        if g.get("sport_id") == "NBA"
    }
    nba_appearance_ids = {
        a_id for a_id, a in appearances.items()
        if a.get("match_id") in nba_game_ids
    }

    lines = {}
    for line in data.get("over_under_lines", []):
        ou = line.get("over_under", {})
        app_stat = ou.get("appearance_stat", {})
        app_id = app_stat.get("appearance_id")

        if app_id not in nba_appearance_ids:
            continue

        stat_title = app_stat.get("display_stat", "")
        stat_value = line.get("stat_value")

        # Resolve player name
        appearance = appearances.get(app_id, {})
        player_id = appearance.get("player_id")
        player = players.get(player_id, {})
        first = player.get("first_name", "")
        last = player.get("last_name", "")
        player_name = f"{first} {last}".strip()

        if not player_name or stat_value is None:
            continue

        # Extract odds from options
        options = line.get("options", [])
        over_odds = ""
        under_odds = ""
        over_mult = ""
        under_mult = ""
        for opt in options:
            choice = opt.get("choice", "")
            if choice == "higher":
                over_odds = opt.get("american_price", "")
                over_mult = opt.get("payout_multiplier", "")
            elif choice == "lower":
                under_odds = opt.get("american_price", "")
                under_mult = opt.get("payout_multiplier", "")

        key = (player_name.lower(), stat_title.lower())
        lines[key] = {
            "line": float(stat_value),
            "over_odds": str(over_odds),
            "under_odds": str(under_odds),
            "over_multiplier": str(over_mult),
            "under_multiplier": str(under_mult),
        }

    logger.info(f"Fetched {len(lines)} Underdog NBA lines")
    return lines


def get_line_for_prop(lines: dict, player_name: str, stat: str) -> dict | None:
    """
    Look up the Underdog line for a player+stat combo.

    Returns the line dict or None if not found.
    """
    key = (player_name.lower(), STAT_MAP.get(stat, stat).lower())
    return lines.get(key)
