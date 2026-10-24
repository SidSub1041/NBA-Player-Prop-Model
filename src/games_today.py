"""
Fetch today's NBA games and the teams playing.
"""

import json
import logging
import os
import time
from datetime import datetime
import requests
from nba_api.stats.endpoints import scoreboardv3
from nba_api.stats.static import teams as nba_teams

from src.config import NBA_STATS_HEADERS, NBA_TEAM_IDS, slate_today

CDN_SCOREBOARD_URL = "https://cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_00.json"


def _proxies() -> dict | None:
    """The same residential proxy the stats API uses (hosted runners are blocked without it)."""
    proxy = os.environ.get("NBA_PROXY") or None
    return {"http": proxy, "https": proxy} if proxy else None


def _fetch_cdn_scoreboard() -> dict:
    """Today's games from the NBA CDN — through NBA_PROXY if set (the CDN 403s datacenter IPs)."""
    headers = {"User-Agent": NBA_STATS_HEADERS["User-Agent"], "Accept": "application/json"}
    try:
        resp = requests.get(CDN_SCOREBOARD_URL, headers=headers, timeout=20, proxies=_proxies())
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        if not _proxies():
            raise
        logger.warning(f"NBA CDN via proxy failed ({type(e).__name__}); trying it directly")
        resp = requests.get(CDN_SCOREBOARD_URL, headers=headers, timeout=20)
        resp.raise_for_status()
        return resp.json()


def _games_from_odds_api(date: str) -> list[dict]:
    """The slate from The Odds API's free events list (needs ODDS_API_KEY)."""
    api_key = os.environ.get("ODDS_API_KEY", "").strip()
    if not api_key:
        return []
    from src.vegas_odds import _started, _team_abbrev, fetch_events

    events, _ = fetch_events(date, api_key)
    games = []
    for ev in events:
        home, away = _team_abbrev(ev.get("home_team")), _team_abbrev(ev.get("away_team"))
        if home and away:
            games.append({"home": home, "away": away, "home_id": NBA_TEAM_IDS.get(home), "away_id": NBA_TEAM_IDS.get(away),
                          "status": 2 if _started(ev) else 1, "start_utc": ev.get("commence_time")})
    return games


def _games_from_board(date: str) -> list[dict]:
    """The slate as recorded on an earlier board for the same date (e.g. the night-before run)."""
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs", f"props_{date}.json")
    try:
        with open(path, encoding="utf-8") as f:
            props = json.load(f).get("all_props", [])
    except (OSError, ValueError):
        return []
    pairs = {}
    for p in props:
        t, o = p.get("team"), p.get("opponent")
        if t and o and (o, t) not in pairs:
            pairs[(t, o)] = True
    return [{"home": a, "away": b, "home_id": NBA_TEAM_IDS.get(a), "away_id": NBA_TEAM_IDS.get(b),
             "status": None, "start_utc": None} for (a, b) in pairs]

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
        # stats.nba.com is often blocked or flaky from cloud networks. Fall back,
        # in order: the NBA CDN (today only), The Odds API's free events list,
        # and finally the slate recorded on an earlier board for this date.
        logger.warning(f"stats.nba.com scoreboard failed ({type(e).__name__}); trying fallbacks")
        data = None
        if date == slate_today():
            try:
                data = _fetch_cdn_scoreboard()
                cdn_date = data.get("scoreboard", {}).get("gameDate")
                if cdn_date and cdn_date != date:
                    logger.warning(f"CDN scoreboard is for {cdn_date}, not {date}; ignoring it")
                    data = None
            except Exception as e2:
                logger.warning(f"NBA CDN unavailable ({type(e2).__name__})")
        if data is None:
            for name, fn in (("The Odds API events", _games_from_odds_api), ("earlier board for this date", _games_from_board)):
                try:
                    games = fn(date)
                except Exception as e3:
                    logger.warning(f"{name} unavailable ({type(e3).__name__})")
                    continue
                if games:
                    logger.warning(f"Slate for {date} taken from {name}: {len(games)} game(s)")
                    for g in games:
                        logger.info(f"  Game: {g['away']} @ {g['home']}")
                    return games
            raise RuntimeError(f"No schedule source answered for {date} (stats.nba.com, NBA CDN, The Odds API, earlier board)")

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
