"""
Fetch today's NBA games and the teams playing.
"""

import time
import logging
from datetime import datetime
from nba_api.stats.endpoints import scoreboardv3
from nba_api.stats.static import teams as nba_teams

from src.config import NBA_TEAM_IDS

logger = logging.getLogger(__name__)

TEAM_ID_TO_ABBREV = {v: k for k, v in NBA_TEAM_IDS.items()}


def get_todays_games(date: str | None = None) -> list[dict]:
    """
    Returns list of dicts: [{"home": "BOS", "away": "LAL", "home_id": ..., "away_id": ...}, ...]
    """
    if date is None:
        date = datetime.today().strftime("%Y-%m-%d")

    logger.info(f"Fetching scoreboard for {date}")
    time.sleep(0.6)
    sb = scoreboardv3.ScoreboardV3(game_date=date)
    data = sb.get_dict()

    scoreboard = data.get("scoreboard", {})
    raw_games = scoreboard.get("games", [])

    games = []
    for game in raw_games:
        home = game.get("homeTeam", {})
        away = game.get("awayTeam", {})
        home_id = home.get("teamId")
        away_id = away.get("teamId")
        home_abbrev = home.get("teamTricode", "") or TEAM_ID_TO_ABBREV.get(home_id, "")
        away_abbrev = away.get("teamTricode", "") or TEAM_ID_TO_ABBREV.get(away_id, "")

        if home_abbrev and away_abbrev:
            games.append({
                "home": home_abbrev,
                "away": away_abbrev,
                "home_id": home_id,
                "away_id": away_id,
            })
            logger.info(f"  Game: {away_abbrev} @ {home_abbrev}")

    if not games:
        logger.warning("No games found for today.")

    return games


def get_teams_playing_today(date: str | None = None) -> set[str]:
    """Returns set of team abbreviations playing today."""
    games = get_todays_games(date)
    teams = set()
    for g in games:
        teams.add(g["home"])
        teams.add(g["away"])
    return teams


def get_matchups_today(date: str | None = None) -> dict[str, str]:
    """Returns dict mapping each team abbreviation to its opponent abbreviation."""
    games = get_todays_games(date)
    matchups = {}
    for g in games:
        matchups[g["home"]] = g["away"]
        matchups[g["away"]] = g["home"]
    return matchups
