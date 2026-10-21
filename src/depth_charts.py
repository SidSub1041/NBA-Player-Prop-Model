"""
Fetch NBA depth charts from ESPN to map position -> starter for each team.
Falls back to NBA API common player info if ESPN scraping fails.
"""

import logging
import time
import requests
from bs4 import BeautifulSoup

from src.config import ESPN_HEADERS, NBA_TEAM_IDS, TEAM_ABBREVS

from src.season import stats_season

logger = logging.getLogger(__name__)

ESPN_DEPTH_URL = "https://www.espn.com/nba/depth"

# ESPN sometimes uses slightly different team abbreviations
ESPN_TEAM_MAP = {
    "GS": "GSW", "SA": "SAS", "NY": "NYK", "NO": "NOP",
    "WSH": "WAS", "PHO": "PHX", "UTAH": "UTA", "BKN": "BKN",
    "BK": "BKN", "CHA": "CHA", "CHO": "CHA",
}


def get_depth_charts(teams: set[str] | None = None) -> dict[str, dict[str, dict]]:
    """
    Returns nested dict: {team_abbrev: {position: {"name": str, "player_id": int|None}}}
    Positions: PG, SG, SF, PF, C

    Example:
        {"BOS": {"PG": {"name": "Jrue Holiday"}, "SG": {"name": "Derrick White"}, ...}}
    """
    charts = _scrape_espn_depth_charts()
    missing = [t for t in (teams or NBA_TEAM_IDS) if t not in charts]
    if missing:
        logger.warning(
            f"ESPN depth charts missing for {len(missing)} team(s); "
            "building them from NBA rosters + minutes"
        )
        charts.update(_fallback_nba_api_depth_charts(set(missing)))
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
    charts = _parse_espn_tables(soup)
    logger.info(f"ESPN depth charts parsed for {len(charts)} team(s)")
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

    if len(text) < 2:
        return None

    # Check direct abbreviation
    upper = text.upper()
    if upper in NBA_TEAM_IDS:
        return upper
    if upper in ESPN_TEAM_MAP:
        return ESPN_TEAM_MAP[upper]

    # Check full names (whole team names only — short fragments match nothing)
    for full_name, abbrev in TEAM_ABBREVS.items():
        if len(text) >= 4 and (text.lower() in full_name.lower() or full_name.lower() in text.lower()):
            return abbrev
        # Check city or team name
        parts = full_name.split()
        for part in parts:
            if part.lower() == text.lower():
                return abbrev

    return None


_STATUS_CODES = r"(?:OUT|GTD|DTD|IL|IR|INJ|SUSP|TBD|Q|O|P|D)"


def _clean_player_name(name: str) -> str:
    """
    Strip ESPN injury/status decorations ("(IL)", trailing "OUT"/"GTD") while
    leaving real name parts alone — generational suffixes like "III", "II",
    "IV" or "Jr." must survive, since every other data source keys on them.
    """
    import re
    name = re.sub(r"\s*\([^)]*\)\s*", " ", name)
    name = re.sub(rf"\s+{_STATUS_CODES}$", "", name)
    return name.strip()


def _fallback_nba_api_depth_charts(teams: set[str]) -> dict[str, dict[str, dict]]:
    """
    Build approximate depth charts from NBA data: the CURRENT season's roster
    (so offseason trades and rookies are reflected) with each roster slot's
    listed position, ranked by last available minutes per game. The best
    available player at each of PG/SG/SF/PF/C is treated as the starter.
    """
    from nba_api.stats.endpoints import commonteamroster, leaguedashplayerstats
    from src.config import SEASON, SEASON_TYPE

    logger.info(f"Building depth charts from NBA rosters + minutes for {sorted(teams)}...")
    charts: dict[str, dict[str, dict]] = {}

    mpg: dict[str, float] = {}
    try:
        time.sleep(0.6)
        df = leaguedashplayerstats.LeagueDashPlayerStats(
            season=stats_season(),
            season_type_all_star=SEASON_TYPE,
            per_mode_detailed="PerGame",
        ).get_data_frames()[0]
        for _, row in df.iterrows():
            mpg[str(row["PLAYER_NAME"]).lower()] = float(row["MIN"] or 0)
    except Exception as e:
        logger.warning(f"Could not load minutes per game ({e}); ranking rosters by listing order")

    for team in sorted(teams):
        team_id = NBA_TEAM_IDS.get(team)
        if not team_id:
            continue
        try:
            time.sleep(0.6)
            roster = commonteamroster.CommonTeamRoster(
                team_id=team_id, season=SEASON
            ).common_team_roster.get_data_frame()
        except Exception as e:
            logger.warning(f"Roster fetch failed for {team}: {e}")
            continue
        if roster.empty:
            continue

        players = []
        for order, (_, row) in enumerate(roster.iterrows()):
            name = str(row.get("PLAYER", "")).strip()
            if not name:
                continue
            slots = _map_nba_position(str(row.get("POSITION", "")))
            players.append({
                "name": name,
                "slots": slots,
                "mpg": mpg.get(name.lower(), 0.0),
                "order": order,
            })
        # Most minutes first; unknown minutes (rookies, new arrivals) last.
        # Each player takes the first FREE slot in his own position preference
        # (a "C-F" big goes to C before PF), so stars land where they play.
        players.sort(key=lambda p: (-p["mpg"], p["order"]))

        positions: dict[str, dict] = {}
        for p in players:
            if len(positions) == 5:
                break
            for slot in p["slots"]:
                if slot not in positions:
                    positions[slot] = {"name": p["name"]}  # raw NBA name: other sources key on it
                    break
        if positions:
            charts[team] = positions
            logger.info(f"  {team}: " + ", ".join(f"{k}={v['name']}" for k, v in positions.items()))

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
        "G-C": ["SG", "C"],
        "C-G": ["C", "SG"],
    }
    # Unknown/blank position: eligible for any slot (ranked by minutes)
    return mapping.get(pos_raw, ["PG", "SG", "SF", "PF", "C"])


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
