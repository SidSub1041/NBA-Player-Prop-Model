"""
Fetch NBA injury reports from CBS Sports to filter out unavailable players
and find their depth-chart replacements.
"""

import logging
import re
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from src.config import ESPN_HEADERS, TEAM_ABBREVS

logger = logging.getLogger(__name__)

CBS_INJURY_URL = "https://www.cbssports.com/nba/injuries/"

# Map CBS city names → standard team abbreviations
_CBS_CITY_TO_ABBREV: dict[str, str] = {}
for full_name, abbrev in TEAM_ABBREVS.items():
    city = full_name.rsplit(" ", 1)[0]          # "Los Angeles Lakers" → "Los Angeles"
    _CBS_CITY_TO_ABBREV[city.lower()] = abbrev
    # Also handle single-word cities
    for word in city.split():
        _CBS_CITY_TO_ABBREV[word.lower()] = abbrev
# Fix collisions (LA has two teams, New York/Brooklyn share city)
_CBS_CITY_TO_ABBREV.update({
    "la clippers": "LAC", "clippers": "LAC",
    "la lakers": "LAL", "lakers": "LAL",
    "brooklyn": "BKN", "nets": "BKN",
    "new york": "NYK", "knicks": "NYK",
    "golden state": "GSW", "warriors": "GSW",
    "oklahoma city": "OKC", "thunder": "OKC",
    "san antonio": "SAS", "spurs": "SAS",
    "portland": "POR", "trail blazers": "POR",
    "new orleans": "NOP", "pelicans": "NOP",
})


def fetch_injury_report() -> dict[str, dict]:
    """
    Scrape CBS Sports NBA injury page.

    Returns dict keyed by full player name::

        {
            "Jaylen Brown": {
                "status": "out" | "gtd" | "day-to-day",
                "injury": "Achilles",
                "team": "BOS",
                "position": "SF",
                "detail": "Game Time Decision",
            },
            ...
        }

    Players marked OUT or OUT FOR THE SEASON are definitively unavailable.
    GTD players are flagged but not auto-excluded.
    """
    logger.info("Fetching injury report from CBS Sports...")
    try:
        resp = requests.get(CBS_INJURY_URL, headers=ESPN_HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"Failed to fetch injury report: {e}")
        return {}

    soup = BeautifulSoup(resp.text, "lxml")
    injuries: dict[str, dict] = {}

    h4s = soup.select("h4.TableBase-title")
    tables = soup.select("table")

    for h4, table in zip(h4s, tables):
        team_city = h4.get_text(strip=True).lower()
        team_abbrev = _CBS_CITY_TO_ABBREV.get(team_city, "")
        if not team_abbrev:
            logger.debug(f"Could not map CBS team city: {team_city!r}")
            continue

        rows = table.select("tr")[1:]  # skip header
        for row in rows:
            cells = row.select("td")
            if len(cells) < 5:
                continue

            # Full name from long-form span
            long_span = cells[0].select_one("span.CellPlayerName--long")
            if long_span:
                name = long_span.get_text(strip=True)
            else:
                name = cells[0].get_text(strip=True)

            position = cells[1].get_text(strip=True).upper()
            injury_type = cells[3].get_text(strip=True)
            status_text = cells[4].get_text(strip=True).lower()

            status = _classify_status(status_text)

            injuries[name] = {
                "status": status,
                "injury": injury_type,
                "team": team_abbrev,
                "position": position,
                "detail": cells[4].get_text(strip=True),
            }

    logger.info(
        f"  Injury report: {len(injuries)} players listed "
        f"({sum(1 for v in injuries.values() if v['status'] == 'out')} out, "
        f"{sum(1 for v in injuries.values() if v['status'] == 'gtd')} GTD)"
    )
    return injuries


def _classify_status(status_text: str) -> str:
    """Classify CBS status string into out / gtd / day-to-day."""
    s = status_text.lower()
    if "out for the season" in s:
        return "out"
    if "expected to be out" in s:
        return "out"
    if "game time decision" in s:
        return "gtd"
    if "day-to-day" in s:
        return "day-to-day"
    # Default anything else to day-to-day (conservative)
    return "day-to-day"


def is_player_out(injuries: dict[str, dict], player_name: str) -> bool:
    """Check if a player is definitively OUT (not GTD)."""
    info = injuries.get(player_name)
    if not info:
        return False
    return info["status"] == "out"


def find_backup_player(
    player_stats_df,
    team_abbrev: str,
    position: str,
    injuries: dict[str, dict],
    team_id_map: dict[str, int] | None = None,
) -> str | None:
    """
    Find the best available backup at a position on a team.

    Uses the NBA API leaguedashplayerstats DataFrame (already loaded in main.py)
    to find the next-highest-minutes player at the position who is not injured.

    Args:
        player_stats_df: pandas DataFrame from leaguedashplayerstats
        team_abbrev: e.g. "BOS"
        position: e.g. "SF"
        injuries: injury dict from fetch_injury_report()
        team_id_map: {abbrev: team_id} mapping

    Returns:
        Player name string of the backup, or None.
    """
    if player_stats_df is None or player_stats_df.empty:
        return None

    from src.config import NBA_TEAM_IDS

    team_id = (team_id_map or NBA_TEAM_IDS).get(team_abbrev)
    if team_id is None:
        return None

    # Filter to players on this team
    team_df = player_stats_df[player_stats_df["TEAM_ID"] == team_id].copy()
    if team_df.empty:
        return None

    # Map positions to filter candidates
    pos_groups = {
        "PG": {"Guard", "G"},
        "SG": {"Guard", "G"},
        "SF": {"Forward", "F"},
        "PF": {"Forward", "F"},
        "C":  {"Center", "C", "Forward-Center", "Center-Forward"},
    }
    valid_pos_keywords = pos_groups.get(position, set())

    # Filter by position (NBA API uses strings like "Guard", "Forward", "Center",
    # "Guard-Forward", etc.)
    def matches_position(pos_raw: str) -> bool:
        if not pos_raw:
            return False
        parts = pos_raw.replace("-", " ").split()
        return bool(valid_pos_keywords & {p.title() for p in parts})

    if "PLAYER_POSITION" in team_df.columns:
        team_df = team_df[
            team_df["PLAYER_POSITION"].astype(str).apply(matches_position)
        ]

    # Sort by minutes descending — starter is #1, backup is #2+
    team_df = team_df.sort_values("MIN", ascending=False)

    # Skip the starter (index 0) and any injured players
    for _, row in team_df.iterrows():
        candidate = row["PLAYER_NAME"]
        mpg = float(row["MIN"])

        # Skip if this player is also injured-out
        if is_player_out(injuries, candidate):
            continue

        # Skip very low-minutes players (<10 MPG)
        if mpg < 10:
            continue

        return candidate

    return None
