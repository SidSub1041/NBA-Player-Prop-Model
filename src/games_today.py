"""
Fetch today's NBA games and the teams playing.
"""

import time
import logging
from datetime import datetime
import requests
from nba_api.stats.endpoints import scoreboardv3
from nba_api.stats.static import teams as nba_teams

from src.config import NBA_STATS_HEADERS, NBA_TEAM_IDS, slate_today

CDN_SCOREBOARD_URL = "https://cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_00.json"


def _fetch_cdn_scoreboard() -> dict:
    """Today's games from the NBA CDN (public, not geo/cloud blocked)."""
    resp = requests.get(
        CDN_SCOREBOARD_URL,
        headers={"User-Agent": NBA_STATS_HEADERS["User-Agent"], "Accept": "application/json"},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()

logger = logging.getLogger(__name__)

TEAM_ID_TO_ABBREV = {v: k for k, v in NBA_TEAM_IDS.items()}


def get_todays_games(date: str | None = None) -> list[dict]:
    """
    Returns list of dicts: [{"home": "BOS", "away": "LAL", "home_id": ..., "away_id": ...,
                             "status": 1|2|3|None, "start_utc": "2026-10-21T23:30:00Z"|None}, ...]
    """
    if date is None:
        date = slate_today()

    logger.info(f"Fetching scoreboard for {date}")
    time.sleep(0.6)
    try:
        sb = scoreboardv3.ScoreboardV3(game_date=date)
        data = sb.get_dict()
    except Exception as e:
        # stats.nba.com is often unreachable from cloud networks. The NBA's
        # public CDN serves today's scoreboard in the same shape, so fall back
        # to it when we're asking about today.
        if date != slate_today():
            raise
        logger.warning(f"stats.nba.com scoreboard failed ({type(e).__name__}); trying the NBA CDN")
        data = _fetch_cdn_scoreboard()
        cdn_date = data.get("scoreboard", {}).get("gameDate")
        if cdn_date and cdn_date != date:
            logger.warning(f"CDN scoreboard is for {cdn_date}, not {date}; treating as no games")
            data = {"scoreboard": {"games": []}}

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
                # 1 = not started, 2 = in progress, 3 = final (None if unknown)
                "status": game.get("gameStatus"),
                "start_utc": game.get("gameTimeUTC"),
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


def pre_tip_teams(games: list[dict]) -> set[str]:
    """Teams whose game is known not to have started yet (status 1 and start time still ahead)."""
    from datetime import timezone

    now = datetime.now(timezone.utc)
    teams = set()
    for g in games:
        if g.get("status") != 1:
            continue
        start = g.get("start_utc")
        if start:
            try:
                if datetime.fromisoformat(str(start).replace("Z", "+00:00")) <= now:
                    continue
            except ValueError:
                pass
        teams.update((g["home"], g["away"]))
    return teams
