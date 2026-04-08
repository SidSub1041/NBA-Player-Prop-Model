"""
Scrape FantasyPros "NBA Defense vs. Position" page for FanDuel.

Identifies green (easy/over) and gold (hard/under) matchups per position
for points, rebounds, and assists.
"""

import logging
import requests
from bs4 import BeautifulSoup

from src.config import FANTASYPROS_HEADERS

logger = logging.getLogger(__name__)

DVP_URL = "https://www.fantasypros.com/daily-fantasy/nba/fanduel-defense-vs-position.php"

# FantasyPros uses CSS classes to indicate matchup quality
# Green cells = easy matchup (over), gold/yellow = tough matchup (under)
# The page uses data-color attributes or inline styles with specific colors.

POSITION_TABS = ["PG", "SG", "SF", "PF", "C"]

# Stat columns we care about on the DvP page
STAT_COLUMNS = {
    "PTS": "points",
    "REB": "rebounds",
    "AST": "assists",
}


def scrape_dvp_data(teams_playing: set[str]) -> list[dict]:
    """
    Scrape the FantasyPros DvP page and return highlighted matchups.

    Returns list of dicts:
    [
        {
            "position": "PG",
            "team": "BOS",          # The DEFENSIVE team (highlighted)
            "stat": "points",
            "edge": "over",         # green = over, gold = under
        },
        ...
    ]
    """
    logger.info("Scraping FantasyPros Defense vs Position data...")

    try:
        resp = requests.get(DVP_URL, headers=FANTASYPROS_HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"Failed to fetch DvP page: {e}")
        return _fallback_dvp_data(teams_playing)

    soup = BeautifulSoup(resp.text, "lxml")
    matchups = []

    # FantasyPros uses a single table with all positions/time-windows.
    # Rows have CSS classes: position (PG/SG/SF/PF/C/ALL) and time window
    # (GC-0=season, GC-7=last7, etc.).  Cells use class "easy" (over) or
    # "hard" (under) to flag matchup edges.
    table = soup.select_one("table")
    if table is None:
        logger.warning("Could not find DvP table, using NBA API fallback")
        return _fallback_dvp_data(teams_playing)

    # Build header-index → stat mapping from <thead>
    headers = [th.get_text(strip=True).upper() for th in table.select("thead th")]
    col_to_stat = {}
    for idx, hdr in enumerate(headers):
        for key, stat_name in STAT_COLUMNS.items():
            if key in hdr:
                col_to_stat[idx] = stat_name

    if not col_to_stat:
        logger.warning("Could not map DvP column headers, using fallback")
        return _fallback_dvp_data(teams_playing)

    rows = table.select("tbody tr")

    for position in POSITION_TABS:
        for row in rows:
            row_classes = row.get("class", [])
            # Only full-season rows (GC-0) for the specific position
            if position not in row_classes or "GC-0" not in row_classes:
                continue

            cells = row.select("td")
            if not cells:
                continue

            team_name = cells[0].get_text(strip=True)
            team_abbrev = _normalize_team_name(team_name)

            if not team_abbrev or team_abbrev not in teams_playing:
                continue

            for col_idx, stat_name in col_to_stat.items():
                if col_idx >= len(cells):
                    continue
                cell = cells[col_idx]
                edge = _detect_edge(cell)
                if edge:
                    matchups.append({
                        "position": position,
                        "team": team_abbrev,
                        "stat": stat_name,
                        "edge": edge,
                    })
                    logger.info(
                        f"  DvP edge: {position} vs {team_abbrev} - "
                        f"{stat_name} {edge}"
                    )

    if not matchups:
        logger.warning("No highlighted matchups found via scraping, using fallback")
        return _fallback_dvp_data(teams_playing)

    return matchups


def _is_dvp_table(table) -> bool:
    """Heuristic check if a table is a DvP table."""
    text = table.get_text()
    return any(kw in text for kw in ["PTS", "REB", "AST", "Points", "Rebounds"])


def _detect_edge(cell) -> str | None:
    """
    Detect if a cell is highlighted as an easy or hard matchup.
    FantasyPros uses CSS class 'easy' (over) or 'hard' (under) on <td> elements.
    """
    classes = cell.get("class", [])

    if "easy" in classes:
        return "over"
    if "hard" in classes:
        return "under"
    return None


def _get_stat_for_column(col_idx: int, table) -> str | None:
    """Map column index to stat name from table header."""
    headers = table.select("thead th, thead td")
    if col_idx < len(headers):
        header_text = headers[col_idx].get_text(strip=True).upper()
        for key, stat_name in STAT_COLUMNS.items():
            if key in header_text:
                return stat_name
    return None


def _normalize_team_name(name: str) -> str | None:
    """Convert various team name formats to standard abbreviation."""
    from src.config import TEAM_ABBREVS

    name = name.strip().upper()

    # Direct abbreviation check
    if name in TEAM_ABBREVS.values():
        return name

    # Full name mapping
    for full, abbrev in TEAM_ABBREVS.items():
        if name.upper() in full.upper() or full.upper() in name.upper():
            return abbrev
        # Check last word (team name)
        if full.split()[-1].upper() in name.upper():
            return abbrev

    return None


def _fallback_dvp_data(teams_playing: set[str]) -> list[dict]:
    """
    Fallback: use NBA API league dash stats to estimate DvP rankings.
    Ranks teams by points/rebounds/assists allowed per position and
    identifies top 5 and bottom 5 as green/gold matchups.
    """
    import time
    from nba_api.stats.endpoints import leaguedashteamstats
    from src.config import SEASON_NBA_API, SEASON_TYPE

    logger.info("Using NBA API fallback for DvP data...")
    matchups = []

    try:
        time.sleep(0.6)
        stats = leaguedashteamstats.LeagueDashTeamStats(
            season=SEASON_NBA_API,
            season_type_all_star=SEASON_TYPE,
            measure_type_detailed_defense="Opponent",
        )
        df = stats.get_data_frames()[0]

        if df.empty:
            return matchups

        # Map team IDs to abbreviations
        from src.config import NBA_TEAM_IDS
        id_to_abbrev = {v: k for k, v in NBA_TEAM_IDS.items()}
        df["TEAM_ABBREV"] = df["TEAM_ID"].map(id_to_abbrev)

        stat_columns = {
            "OPP_PTS": "points",
            "OPP_REB": "rebounds",
            "OPP_AST": "assists",
        }

        for col, stat_name in stat_columns.items():
            if col not in df.columns:
                continue

            sorted_df = df.sort_values(col, ascending=False)
            abbrevs = sorted_df["TEAM_ABBREV"].tolist()

            # Top 5 = give up most (green/over matchup)
            for abbrev in abbrevs[:5]:
                if abbrev in teams_playing:
                    for pos in POSITION_TABS:
                        matchups.append({
                            "position": pos,
                            "team": abbrev,
                            "stat": stat_name,
                            "edge": "over",
                        })

            # Bottom 5 = give up least (gold/under matchup)
            for abbrev in abbrevs[-5:]:
                if abbrev in teams_playing:
                    for pos in POSITION_TABS:
                        matchups.append({
                            "position": pos,
                            "team": abbrev,
                            "stat": stat_name,
                            "edge": "under",
                        })

    except Exception as e:
        logger.error(f"DvP fallback also failed: {e}")

    return matchups
