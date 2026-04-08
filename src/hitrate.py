"""
Fetch player prop hit rates from linemate.com.
Checks if a player's season hit rate for a given stat exceeds 56%.
"""

import logging
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

LINEMATE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

STAT_URL_SLUGS = {
    "points": "pts",
    "rebounds": "reb",
    "assists": "ast",
}


def get_player_hitrate(player_name: str, stat: str, line: float | None = None) -> dict:
    """
    Fetch player's season hit rate from linemate.com for a given stat.

    Returns: {"hitrate": float (0-1), "games_played": int, "source": str}
    Returns hitrate of -1 if data unavailable.
    """
    logger.info(f"Fetching hit rate for {player_name} - {stat}")

    slug = _player_name_to_slug(player_name)
    stat_slug = STAT_URL_SLUGS.get(stat, stat)

    # Try linemate.com
    try:
        url = f"https://www.linemate.com/nba/players/{slug}/props/{stat_slug}"
        resp = requests.get(url, headers=LINEMATE_HEADERS, timeout=15)

        if resp.status_code == 200:
            return _parse_linemate_page(resp.text, player_name, stat)
    except requests.RequestException as e:
        logger.debug(f"Linemate fetch failed: {e}")

    # Fallback: estimate hit rate from NBA API game logs
    return _estimate_hitrate_from_gamelogs(player_name, stat)


def _parse_linemate_page(html: str, player_name: str, stat: str) -> dict:
    """Parse linemate.com page for hit rate data."""
    soup = BeautifulSoup(html, "lxml")

    # Look for hit rate percentage on the page
    # Linemate typically shows "X% hit rate" or similar
    text = soup.get_text()

    import re
    # Look for patterns like "65%" or "65% hit rate" or "Over 65%"
    patterns = [
        r"(\d{1,3}(?:\.\d+)?)\s*%\s*(?:hit\s*rate|over|o)",
        r"hit\s*rate[:\s]*(\d{1,3}(?:\.\d+)?)\s*%",
        r"season[:\s]*(\d{1,3}(?:\.\d+)?)\s*%",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            hitrate = float(match.group(1)) / 100
            if 0 < hitrate <= 1:
                logger.info(f"  Linemate hit rate: {hitrate:.1%}")
                return {"hitrate": hitrate, "games_played": -1, "source": "linemate.com"}

    logger.warning(f"Could not parse hit rate from linemate.com for {player_name}")
    return _estimate_hitrate_from_gamelogs(player_name, stat)


def _estimate_hitrate_from_gamelogs(player_name: str, stat: str) -> dict:
    """
    Estimate hit rate from NBA API player game logs.
    Uses the player's season average as proxy for their typical line,
    then calculates what % of games they went over that average.
    """
    import time
    from nba_api.stats.endpoints import playergamelog
    from nba_api.stats.static import players

    logger.info(f"  Estimating hit rate from game logs for {player_name}")

    try:
        # Find player ID
        player_list = players.find_players_by_full_name(player_name)
        if not player_list:
            # Try last name only
            last_name = player_name.split()[-1]
            player_list = players.find_players_by_last_name(last_name)
            player_list = [p for p in player_list if p.get("is_active", False)]

        if not player_list:
            logger.warning(f"  Player not found: {player_name}")
            return {"hitrate": -1, "games_played": 0, "source": "unavailable"}

        player_id = player_list[0]["id"]

        time.sleep(0.6)
        gamelog = playergamelog.PlayerGameLog(
            player_id=player_id,
            season="2025",
            season_type_all_star="Regular Season",
        )
        df = gamelog.get_data_frames()[0]

        if df.empty:
            return {"hitrate": -1, "games_played": 0, "source": "unavailable"}

        # Map stat to column name
        stat_col_map = {
            "points": "PTS",
            "rebounds": "REB",
            "assists": "AST",
        }
        col = stat_col_map.get(stat)
        if not col or col not in df.columns:
            return {"hitrate": -1, "games_played": 0, "source": "unavailable"}

        values = df[col].astype(float)
        season_avg = values.mean()
        games_over = (values > season_avg).sum()
        total_games = len(values)
        hitrate = games_over / total_games if total_games > 0 else 0

        logger.info(
            f"  Game log hit rate (vs avg {season_avg:.1f}): "
            f"{hitrate:.1%} ({games_over}/{total_games} games)"
        )

        return {
            "hitrate": round(hitrate, 3),
            "games_played": total_games,
            "source": "nba_api_gamelog",
            "season_avg": round(season_avg, 1),
        }

    except Exception as e:
        logger.error(f"  Game log estimation failed: {e}")
        return {"hitrate": -1, "games_played": 0, "source": "unavailable"}


def _player_name_to_slug(name: str) -> str:
    """Convert player name to URL slug."""
    import re
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"\s+", "-", slug)
    return slug
