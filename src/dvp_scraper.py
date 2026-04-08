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
        resp = requests.get(DVP_URL, headers=FANTASYPROS_HEADERS, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"Failed to fetch DvP page: {e}")
        return _fallback_dvp_data(teams_playing)

    soup = BeautifulSoup(resp.text, "lxml")
    matchups = []

    # FantasyPros renders the DvP data in tables, one per position tab.
    # Each table has rows per team, columns per stat, with color coding.
    tables = soup.select("table.table-dvp, table.dvp-table, div.dvp-table table")

    if not tables:
        # Try alternative selectors
        tables = soup.select("table")
        tables = [t for t in tables if _is_dvp_table(t)]

    if not tables:
        logger.warning("Could not find DvP tables, using NBA API fallback")
        return _fallback_dvp_data(teams_playing)

    for pos_idx, position in enumerate(POSITION_TABS):
        if pos_idx >= len(tables):
            break

        table = tables[pos_idx]
        rows = table.select("tbody tr")

        for row in rows:
            cells = row.select("td")
            # First cell is typically the team name
            if not cells:
                continue

            team_cell = cells[0]
            team_name = team_cell.get_text(strip=True)
            team_abbrev = _normalize_team_name(team_name)

            if not team_abbrev or team_abbrev not in teams_playing:
                continue

            # Check stat columns for color highlighting
            for cell_idx, cell in enumerate(cells[1:], start=1):
                stat_name = _get_stat_for_column(cell_idx, table)
                if not stat_name:
                    continue

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
    Detect if a cell is highlighted green (over) or gold (under).
    FantasyPros uses various methods: CSS classes, inline styles, or data attributes.
    """
    # Check for CSS classes
    classes = " ".join(cell.get("class", []))
    style = cell.get("style", "")
    data_color = cell.get("data-color", "")

    # Green indicators (easy matchup -> over)
    green_indicators = [
        "green" in classes.lower(),
        "success" in classes.lower(),
        "positive" in classes.lower(),
        "#00" in style and "ff" in style.lower(),
        "green" in style.lower(),
        "green" in data_color.lower(),
        "background-color: #d4edda" in style.lower(),
        "background: #c3e6cb" in style.lower(),
        "dvp-high" in classes.lower(),
    ]

    # Gold/yellow indicators (tough matchup -> under)
    gold_indicators = [
        "gold" in classes.lower(),
        "warning" in classes.lower(),
        "negative" in classes.lower(),
        "yellow" in style.lower(),
        "gold" in style.lower(),
        "gold" in data_color.lower(),
        "background-color: #fff3cd" in style.lower(),
        "background: #ffeeba" in style.lower(),
        "dvp-low" in classes.lower(),
        "red" in classes.lower(),
        "danger" in classes.lower(),
    ]

    if any(green_indicators):
        return "over"
    if any(gold_indicators):
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
            measure_type_detailed_response="Opponent",
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
