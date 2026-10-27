"""
Scrape FantasyPros "NBA Defense vs. Position" page for FanDuel.

Two readings of the same table:

* `scrape_dvp_data` (the original): the green (easy/over) and gold
  (hard/under) cells per position for points, rebounds and assists — the
  learner still gets that colour as a feature.
* `scrape_dvp_table` (round 18): the NUMBERS in those cells for every team,
  with the league mean/sd per position+stat, so the edge model can read how
  far an opponent sits from the league instead of whether it is coloured.

The page is fetched and parsed once per run (module cache, `reset_cache()`).
"""

import logging
import re
from collections import defaultdict
from statistics import fmean, pstdev

import requests
from bs4 import BeautifulSoup

from src.config import FANTASYPROS_HEADERS

from src.season import stats_season

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

_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")

# The parsed <table> for this run (None once a fetch/parse has failed, so a
# second caller does not repeat a slow or blocked request).
_page_cache: dict[str, object] = {}


def reset_cache() -> None:
    """Forget the fetched page (main calls this at the start of every run)."""
    _page_cache.clear()


def _fetch_dvp_table():
    """
    The FantasyPros DvP <table> element, fetched and parsed once per run.

    Returns None when the page could not be fetched or holds no table; the
    outcome (table or None) is cached until reset_cache().
    """
    if "table" in _page_cache:
        return _page_cache["table"]
    table = None
    try:
        resp = requests.get(DVP_URL, headers=FANTASYPROS_HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"Failed to fetch DvP page: {e}")
    else:
        soup = BeautifulSoup(resp.text, "lxml")
        table = soup.select_one("table")
        if table is None:
            logger.warning("Could not find DvP table")
    _page_cache["table"] = table
    return table


def _header_stats(table) -> dict[int, str]:
    """{column index: stat} for every <thead> cell whose text contains PTS/REB/AST."""
    headers = [th.get_text(strip=True).upper() for th in table.select("thead th")]
    col_to_stat = {}
    for idx, hdr in enumerate(headers):
        for key, stat_name in STAT_COLUMNS.items():
            if key in hdr:
                col_to_stat[idx] = stat_name
    return col_to_stat


def _stat_columns(table) -> dict[str, int]:
    """
    {stat: column index}, one column per stat: the header that IS the key
    ("PTS") wins over one that merely contains it ("FPTS"), so fantasy
    points never overwrite points.
    """
    headers = [th.get_text(strip=True).upper() for th in table.select("thead th")]
    cols: dict[str, int] = {}
    for key, stat_name in STAT_COLUMNS.items():
        exact = [i for i, h in enumerate(headers) if h == key or h.startswith(key + " ")]
        loose = [i for i, h in enumerate(headers) if key in h]
        for candidates in (exact, loose):
            if candidates:
                cols[stat_name] = candidates[0]
                break
    return cols


def _season_rows(table, position: str):
    """The full-season (GC-0) <tr> rows for one position tab."""
    for row in table.select("tbody tr"):
        row_classes = row.get("class", [])
        if position in row_classes and "GC-0" in row_classes:
            yield row


def _first_number(text: str) -> float | None:
    """24.3 from "24.3", "24.3 (3)" or " 24.3 "; None for a blank cell."""
    m = _NUMBER_RE.search(text or "")
    return float(m.group()) if m else None


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

    table = _fetch_dvp_table()
    if table is None:
        logger.warning("DvP page unavailable, using NBA API fallback")
        return _fallback_dvp_data(teams_playing)

    # FantasyPros uses a single table with all positions/time-windows.
    # Rows have CSS classes: position (PG/SG/SF/PF/C/ALL) and time window
    # (GC-0=season, GC-7=last7, etc.).  Cells use class "easy" (over) or
    # "hard" (under) to flag matchup edges.
    col_to_stat = _header_stats(table)
    if not col_to_stat:
        logger.warning("Could not map DvP column headers, using fallback")
        return _fallback_dvp_data(teams_playing)

    matchups = []
    for position in POSITION_TABS:
        for row in _season_rows(table, position):
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


def scrape_dvp_table(teams_playing: set[str] | None = None) -> dict:
    """
    The numbers behind the DvP colours: what each team allows to each
    position per game, season to date.

    Returns::

        {
            "values": {(team, position, stat): float},       # e.g. ("BOS", "PG", "points"): 22.4
            "flags":  {(team, position, stat): "over"|"under"},   # the page's colour, where present
            "league": {(position, stat): {"mean", "sd", "n"}},    # over ALL teams in the table
        }

    `values`/`flags` are limited to `teams_playing` when given; `league` is
    always computed over every team so a z-score is against the whole league.
    The value is the first number in the cell ("24.3", "24.3 (3)"); blank
    cells are skipped. On any failure returns empty dicts (and logs) — the
    model then applies no DvP adjustment.
    """
    empty = {"values": {}, "flags": {}, "league": {}}
    try:
        table = _fetch_dvp_table()
        if table is None:
            logger.warning("DvP table unavailable: no numeric DvP this run")
            return empty
        stat_cols = _stat_columns(table)
        if not stat_cols:
            logger.warning("Could not map DvP column headers: no numeric DvP this run")
            return empty

        values: dict[tuple, float] = {}
        flags: dict[tuple, str] = {}
        per_league: dict[tuple, list[float]] = defaultdict(list)
        for position in POSITION_TABS:
            seen: set[str] = set()
            for row in _season_rows(table, position):
                cells = row.select("td")
                if not cells:
                    continue
                team = _normalize_team_name(cells[0].get_text(strip=True))
                if not team or team in seen:
                    continue
                seen.add(team)
                for stat, idx in stat_cols.items():
                    if idx >= len(cells):
                        continue
                    cell = cells[idx]
                    value = _first_number(cell.get_text(" ", strip=True))
                    if value is None:
                        continue
                    per_league[(position, stat)].append(value)
                    if teams_playing is not None and team not in teams_playing:
                        continue
                    values[(team, position, stat)] = value
                    edge = _detect_edge(cell)
                    if edge:
                        flags[(team, position, stat)] = edge

        league = {
            key: {"mean": fmean(vals), "sd": pstdev(vals), "n": len(vals)}
            for key, vals in per_league.items()
        }
        if not league:
            logger.warning("DvP table had no numeric cells: no numeric DvP this run")
            return empty
        logger.info(
            f"DvP table: {len(values)} team/position/stat values, "
            f"{len(flags)} coloured, league stats for {len(league)} position/stat pairs"
        )
        return {"values": values, "flags": flags, "league": league}
    except Exception as e:
        logger.error(f"DvP table parse failed: {type(e).__name__}: {e}")
        return empty

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
    """
    Convert various team name formats to standard abbreviation.

    Exact matches (abbreviation, full name) are tried before the fuzzy
    containment and last-word checks, and the last word must match as a
    whole word: "Charlotte Hornets" used to come back as BKN because
    "NETS" is a substring of "HORNETS".
    """
    from src.config import TEAM_ABBREVS

    name = name.strip().upper()
    if not name:
        return None

    # Direct abbreviation check
    if name in TEAM_ABBREVS.values():
        return name

    # Exact full name
    for full, abbrev in TEAM_ABBREVS.items():
        if name == full.upper():
            return abbrev

    # Full name mapping
    for full, abbrev in TEAM_ABBREVS.items():
        if name in full.upper() or full.upper() in name:
            return abbrev
    # Check last word (team name), as a whole word
    for full, abbrev in TEAM_ABBREVS.items():
        if re.search(rf"\b{re.escape(full.split()[-1].upper())}\b", name):
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
    from src.config import SEASON_TYPE

    logger.info("Using NBA API fallback for DvP data...")
    matchups = []

    try:
        time.sleep(0.6)
        stats = leaguedashteamstats.LeagueDashTeamStats(
            season=stats_season(),
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
