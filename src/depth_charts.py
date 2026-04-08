"""
Fetch NBA depth charts from ESPN to map position -> starter for each team.
Falls back to NBA API common player info if ESPN scraping fails.
"""

import logging
import time
import requests
from bs4 import BeautifulSoup

from src.config import ESPN_HEADERS, NBA_TEAM_IDS, TEAM_ABBREVS

logger = logging.getLogger(__name__)

ESPN_DEPTH_URL = "https://www.espn.com/nba/depth"

# ESPN sometimes uses slightly different team abbreviations
ESPN_TEAM_MAP = {
    "GS": "GSW", "SA": "SAS", "NY": "NYK", "NO": "NOP",
    "WSH": "WAS", "PHO": "PHX", "UTAH": "UTA", "BKN": "BKN",
    "BK": "BKN", "CHA": "CHA", "CHO": "CHA",
}


def get_depth_charts() -> dict[str, dict[str, dict]]:
    """
    Returns nested dict: {team_abbrev: {position: {"name": str, "player_id": int|None}}}
    Positions: PG, SG, SF, PF, C

    Example:
        {"BOS": {"PG": {"name": "Jrue Holiday"}, "SG": {"name": "Derrick White"}, ...}}
    """
    charts = _scrape_espn_depth_charts()
    if not charts:
        logger.warning("ESPN scraping failed, using NBA API fallback for depth charts")
        charts = _fallback_nba_api_depth_charts()
    return charts


def _scrape_espn_depth_charts() -> dict[str, dict[str, dict]]:
    """Scrape ESPN depth chart page."""
    logger.info("Fetching ESPN depth charts...")
    try:
        resp = requests.get(ESPN_DEPTH_URL, headers=ESPN_HEADERS, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"Failed to fetch ESPN depth charts: {e}")
        return {}

    soup = BeautifulSoup(resp.text, "lxml")
    charts = {}

    # ESPN depth chart page has sections per team
    # Each team section has a table with positions and player names
    team_sections = soup.select("div.Table__Title, div.headline, h2")

    if not team_sections:
        # Try parsing tables directly
        return _parse_espn_tables(soup)

    return charts


def _parse_espn_tables(soup) -> dict[str, dict[str, dict]]:
    """Parse ESPN depth chart tables when team sections aren't found."""
    charts = {}
    tables = soup.select("table")
    current_team = None

    for element in soup.find_all(["h2", "h3", "table", "div"]):
        if element.name in ["h2", "h3"] or (
            element.name == "div" and "headline" in " ".join(element.get("class", []))
        ):
            team_text = element.get_text(strip=True)
            current_team = _normalize_espn_team(team_text)

        if element.name == "table" and current_team:
            rows = element.select("tr")
            positions_found = {}

            for row in rows:
                cells = row.select("td, th")
                if len(cells) >= 2:
                    pos = cells[0].get_text(strip=True).upper()
                    if pos in ["PG", "SG", "SF", "PF", "C"]:
                        # First player listed is the starter
                        player_link = cells[1].select_one("a")
                        if player_link:
                            name = player_link.get_text(strip=True)
                        else:
                            name = cells[1].get_text(strip=True)

                        if name:
                            positions_found[pos] = {"name": _clean_player_name(name)}

            if positions_found:
                charts[current_team] = positions_found

    return charts


def _normalize_espn_team(text: str) -> str | None:
    """Normalize ESPN team name to standard abbreviation."""
    text = text.strip()

    # Check direct abbreviation
    upper = text.upper()
    if upper in NBA_TEAM_IDS:
        return upper
    if upper in ESPN_TEAM_MAP:
        return ESPN_TEAM_MAP[upper]

    # Check full names
    for full_name, abbrev in TEAM_ABBREVS.items():
        if text.lower() in full_name.lower() or full_name.lower() in text.lower():
            return abbrev
        # Check city or team name
        parts = full_name.split()
        for part in parts:
            if part.lower() == text.lower():
                return abbrev

    return None


def _clean_player_name(name: str) -> str:
    """Clean up player name string."""
    # Remove suffixes like (IL), (OUT), injury designations
    import re
    name = re.sub(r"\s*\([^)]*\)\s*", "", name)
    name = re.sub(r"\s*[A-Z]{2,3}$", "", name)  # Remove trailing status codes
    return name.strip()


def _fallback_nba_api_depth_charts() -> dict[str, dict[str, dict]]:
    """
    Use NBA API to build approximate depth charts from common team rosters
    and player stats (starters by minutes played per position).
    """
    from nba_api.stats.endpoints import commonteamroster, leaguedashplayerstats
    from src.config import SEASON_NBA_API, SEASON_TYPE

    logger.info("Building depth charts from NBA API (minutes-based)...")
    charts = {}

    try:
        time.sleep(0.6)
        player_stats = leaguedashplayerstats.LeagueDashPlayerStats(
            season=SEASON_NBA_API,
            season_type_all_star=SEASON_TYPE,
            per_mode_detailed="PerGame",
        )
        df = player_stats.get_data_frames()[0]

        if df.empty:
            return charts

        id_to_abbrev = {v: k for k, v in NBA_TEAM_IDS.items()}

        # Group by team
        df["TEAM_ABBREV"] = df["TEAM_ID"].map(id_to_abbrev)

        for team_abbrev in NBA_TEAM_IDS:
            team_df = df[df["TEAM_ABBREV"] == team_abbrev].copy()
            if team_df.empty:
                continue

            team_df = team_df.sort_values("MIN", ascending=False)
            positions = {}

            for _, row in team_df.iterrows():
                player_name = row["PLAYER_NAME"]
                # NBA API doesn't always have clean position data,
                # but we can use what's available
                pos_raw = str(row.get("PLAYER_POSITION", ""))

                assigned_positions = _map_nba_position(pos_raw)
                for pos in assigned_positions:
                    if pos not in positions:
                        positions[pos] = {"name": player_name}

                if len(positions) >= 5:
                    break

            if positions:
                charts[team_abbrev] = positions

    except Exception as e:
        logger.error(f"NBA API depth chart fallback failed: {e}")

    return charts


def _map_nba_position(pos_raw: str) -> list[str]:
    """Map NBA API position strings to standard positions."""
    pos_raw = pos_raw.upper().strip()
    mapping = {
        "G": ["PG", "SG"],
        "F": ["SF", "PF"],
        "C": ["C"],
        "G-F": ["SG", "SF"],
        "F-G": ["SF", "SG"],
        "F-C": ["PF", "C"],
        "C-F": ["C", "PF"],
        "PG": ["PG"],
        "SG": ["SG"],
        "SF": ["SF"],
        "PF": ["PF"],
        "GUARD": ["PG", "SG"],
        "FORWARD": ["SF", "PF"],
        "CENTER": ["C"],
    }
    return mapping.get(pos_raw, ["SF"])  # Default to SF if unknown


def find_player_for_matchup(
    depth_charts: dict, team_abbrev: str, position: str
) -> dict | None:
    """
    Find the starter at a given position for a team.
    Returns {"name": str} or None.
    """
    team = depth_charts.get(team_abbrev, {})
    player = team.get(position)
    if player:
        return player

    # Try adjacent positions if exact match not found
    adjacent = {
        "PG": ["SG"], "SG": ["PG", "SF"], "SF": ["SG", "PF"],
        "PF": ["SF", "C"], "C": ["PF"],
    }
    for alt_pos in adjacent.get(position, []):
        player = team.get(alt_pos)
        if player:
            return player

    return None
