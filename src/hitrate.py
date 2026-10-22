"""
Hit rate against the line.

The methodology's hit-rate gate asks: in the stats season, how often did this
player clear tonight's line in the pick's direction? For an over at 24.5, a
"hit" is a game with 25+; for an under, a game with 24 or fewer. Combos
(PTS+REB+AST etc.) use the summed stats against the combo line.

Game logs come from the NBA API (PlayerGameLog for the stats season — last
season's logs until every team has played enough games this season, see
src/season.py) and are fetched once per player per run.

Without a line there is nothing to measure the gate against, so the result is
an estimate (games beyond the player's own average, in the pick's direction)
that is shown for context but can't make a pick valid.
"""

import logging
import time

import pandas as pd

from src.config import SEASON_TYPE
from src.season import stats_season
from src.underdog import norm_name, norm_name_base

logger = logging.getLogger(__name__)

# Internal stat -> box-score columns summed per game
STAT_COLUMNS = {
    "points": ["PTS"],
    "rebounds": ["REB"],
    "assists": ["AST"],
    "pts+ast": ["PTS", "AST"],
    "pts+reb": ["PTS", "REB"],
    "reb+ast": ["REB", "AST"],
    "pra": ["PTS", "REB", "AST"],
    "3-pointers made": ["FG3M"],
    "steals": ["STL"],
    "blocks": ["BLK"],
    "turnovers": ["TOV"],
}

SOURCE_LINE = "gamelog_vs_line"
SOURCE_ESTIMATE = "season_avg_estimate"   # no line: context only, never valid
SOURCE_UNAVAILABLE = "unavailable"

_gamelog_cache: dict[tuple[int, str], pd.DataFrame] = {}
_live_ids: dict[str, int] = {}   # normalized name -> PLAYER_ID from this run's live stats


def reset_cache() -> None:
    _gamelog_cache.clear()
    _live_ids.clear()


def register_live_ids(df) -> None:
    """
    Learn player ids from a live NBA stats frame (PLAYER_ID, PLAYER_NAME).

    nba_api's bundled player list is frozen at its release, so rookies and
    recent signings are missing from it; the live LeagueDashPlayerStats frame
    main.py already loads has them.
    """
    if df is None or getattr(df, "empty", True) or not {"PLAYER_ID", "PLAYER_NAME"} <= set(df.columns):
        return
    for pid, name in zip(df["PLAYER_ID"], df["PLAYER_NAME"]):
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            continue
        _live_ids[norm_name(name)] = pid
        _live_ids.setdefault(norm_name_base(name), pid)


def _unavailable(line: float | None = None) -> dict:
    return {"hitrate": -1, "games_played": 0, "source": SOURCE_UNAVAILABLE, "line": line}


def _find_player_id(player_name: str) -> int | None:
    """NBA player id; tolerant of accents, initials and Jr./III differences."""
    import re

    from nba_api.stats.static import players

    live = _live_ids.get(norm_name(player_name)) or _live_ids.get(norm_name_base(player_name))
    if live:
        return live
    found = players.find_players_by_full_name(f"^{re.escape(player_name)}$")
    if not found:
        target, target_base = norm_name(player_name), norm_name_base(player_name)
        everyone = players.get_players()
        found = [p for p in everyone if norm_name(p["full_name"]) == target] or [
            p for p in everyone if norm_name_base(p["full_name"]) == target_base
        ]
    if not found:
        return None
    active = [p for p in found if p.get("is_active")]
    return (active or found)[0]["id"]


def _game_log(player_name: str) -> pd.DataFrame | None:
    """The player's game log for the stats season (cached per run)."""
    from nba_api.stats.endpoints import playergamelog

    player_id = _find_player_id(player_name)
    if player_id is None:
        logger.warning(f"  Player not found: {player_name}")
        return None
    season = stats_season()
    key = (player_id, season)
    if key not in _gamelog_cache:
        time.sleep(0.6)
        _gamelog_cache[key] = playergamelog.PlayerGameLog(
            player_id=player_id,
            season=season,
            season_type_all_star=SEASON_TYPE,
        ).get_data_frames()[0]
    return _gamelog_cache[key]


def get_player_hitrate(
    player_name: str, stat: str, line: float | None = None, edge: str = "over", before: str | None = None
) -> dict:
    """
    Season hit rate for `stat` against `line` in the `edge` direction.

    `before` (the slate date, YYYY-MM-DD) keeps only games played before it,
    so a re-run after an early game has finished never counts the game it is
    predicting, and back-runs only see what was known that day.

    Returns {"hitrate": 0-1 or -1, "games_played", "games_hit", "line",
             "season_avg", "source"}.
    """
    cols = STAT_COLUMNS.get(stat)
    if not cols:
        return _unavailable(line)
    try:
        df = _game_log(player_name)
    except Exception as e:
        logger.error(f"  Game log fetch failed for {player_name}: {type(e).__name__}: {e}")
        return _unavailable(line)
    if df is None or df.empty or not all(c in df.columns for c in cols):
        return _unavailable(line)
    if before and "GAME_DATE" in df.columns:
        played = pd.to_datetime(df["GAME_DATE"], format="mixed", errors="coerce")
        df = df[played.isna() | (played < pd.Timestamp(before))]   # undated rows are kept, never silently dropped
        if df.empty:
            return _unavailable(line)

    values = df[cols].astype(float).sum(axis=1)
    games = len(values)
    season_avg = float(values.mean())
    under = str(edge).lower() == "under"

    if line is None:
        cleared = (values < season_avg) if under else (values > season_avg)
        source, ref = SOURCE_ESTIMATE, season_avg
    else:
        line = float(line)
        cleared = (values < line) if under else (values > line)   # a push is not a hit
        source, ref = SOURCE_LINE, line

    hits = int(cleared.sum())
    hitrate = hits / games if games else 0.0
    logger.info(
        f"  Hit rate {stat} {'under' if under else 'over'} {ref:.1f}"
        f"{'' if line is not None else ' (season avg — no line)'}: {hitrate:.1%} ({hits}/{games} games, {stats_season()})"
    )
    return {
        "hitrate": round(hitrate, 3),
        "games_played": games,
        "games_hit": hits,
        "line": line,
        "season_avg": round(season_avg, 1),
        "source": source,
    }
