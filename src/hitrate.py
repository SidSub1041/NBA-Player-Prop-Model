"""
Hit rate against the line.

The methodology's hit-rate gate asks: in the stats season, how often did this
player clear tonight's line in the pick's direction? For an over at 24.5, a
"hit" is a game with 25+; for an under, a game with 24 or fewer. Combos
(PTS+REB+AST etc.) use the summed stats against the combo line.

Game logs come from the league-wide table in src/gamelogs.py (one
PlayerGameLogs request per season, cached on disk and topped up
incrementally) for the stats season — last season's logs until every team has
played enough games this season, see src/season.py. When that table is
unavailable (request failed, nothing on disk) the player's own PlayerGameLog
is fetched instead, once per player per season per run.

Without a line there is nothing to measure the gate against, so the result is
an estimate (games beyond the player's own average, in the pick's direction)
that is shown for context but can't make a pick valid.
"""

import logging
import time

import pandas as pd

from src import gamelogs
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
    """Start a run afresh: per-player logs, live ids and the league tables' run cache (src/gamelogs.py)."""
    _gamelog_cache.clear()
    _live_ids.clear()
    gamelogs.reset_cache()


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


def _league_rows(player_name: str, season: str) -> pd.DataFrame | None:
    """The player's rows in the season's league table; None when that table is unavailable."""
    try:
        return gamelogs.player_rows(player_name, season)
    except Exception as e:   # never let the cache layer cost a sample: the per-player path still works
        logger.warning(f"  League game logs failed for {player_name} ({season}): {type(e).__name__}: {e}")
        return None


def _game_log(player_name: str) -> pd.DataFrame | None:
    """
    The player's game log for the stats season: his rows in the league table
    (src/gamelogs.py), or his own PlayerGameLog (cached per run) when the
    table is unavailable.
    """
    rows = _league_rows(player_name, stats_season())
    if rows is not None:
        return rows
    return _player_game_log(player_name)


def _player_game_log(player_name: str) -> pd.DataFrame | None:
    """The player's own PlayerGameLog for the stats season (cached per run)."""
    return game_log_for(player_name, stats_season())


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


# ── Round 18: two-season samples for the projection (src/projection.py) ──

def _fetch_game_log(player_id: int, season: str) -> pd.DataFrame:
    """One PlayerGameLog call per (player, season) per run, cached in _gamelog_cache."""
    from nba_api.stats.endpoints import playergamelog

    key = (player_id, season)
    if key not in _gamelog_cache:
        time.sleep(0.6)
        _gamelog_cache[key] = playergamelog.PlayerGameLog(
            player_id=player_id,
            season=season,
            season_type_all_star=SEASON_TYPE,
        ).get_data_frames()[0]
    return _gamelog_cache[key]


def game_log_for(player_name: str, season: str) -> pd.DataFrame | None:
    """
    The player's own PlayerGameLog for an explicit season (one request per
    player and season, cached per run). None when the player is unknown.

    The fallback for when the league-wide table (src/gamelogs.py) is
    unavailable: player_sample needs both seasons and get_player_hitrate the
    stats season, so both come here then.
    """
    player_id = _find_player_id(player_name)
    if player_id is None:
        logger.warning(f"  Player not found: {player_name}")
        return None
    return _fetch_game_log(player_id, season)


def parse_minutes(value) -> float:
    """
    Minutes from a box-score MIN cell: 32 -> 32.0, "32:15" -> 32.25,
    "32.5" -> 32.5, None/NaN/blank -> 0.0.
    """
    if value is None:
        return 0.0
    try:
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return 0.0
            if ":" in text:
                mins, secs = text.split(":", 1)
                return float(mins or 0) + float(secs or 0) / 60.0
            return float(text)
        v = float(value)
        return v if v == v else 0.0   # NaN -> 0
    except (TypeError, ValueError):
        return 0.0


def _empty_sample() -> dict:
    return {"values": [], "minutes": [], "dates": []}


def _sample_from_log(df, cols: list[str], before: str | None) -> dict:
    """values / minutes / dates (newest first, the frame's own order) from one season's log."""
    if df is None or getattr(df, "empty", True) or not all(c in df.columns for c in cols):
        return _empty_sample()
    played = None
    if "GAME_DATE" in df.columns:
        played = pd.to_datetime(df["GAME_DATE"], format="mixed", errors="coerce")
        if before:
            keep = played.isna() | (played < pd.Timestamp(before))   # undated rows are kept, never silently dropped
            df, played = df[keep], played[keep]
    if df.empty:
        return _empty_sample()
    values = df[cols].astype(float).sum(axis=1).tolist()
    minutes = [parse_minutes(m) for m in df["MIN"]] if "MIN" in df.columns else [0.0] * len(values)
    if played is not None:
        dates = [
            ts.strftime("%Y-%m-%d") if not pd.isna(ts) else (str(raw).strip() or None) if isinstance(raw, str) else None
            for ts, raw in zip(played, df["GAME_DATE"])
        ]
    else:
        dates = [None] * len(values)
    return {"values": values, "minutes": minutes, "dates": dates}


def player_sample(
    player_name: str, stat: str, before: str | None = None, seasons: tuple[str, str] | None = None
) -> dict:
    """
    The player's per-game `stat` values for this season and last season,
    the raw material of the round-18 projection (src/blend.py).

    `seasons` is (this_season, last_season); it defaults to the configured
    season and the one before it. Only games before `before` (YYYY-MM-DD)
    count, exactly as in get_player_hitrate. Each season is
    {"values": [...], "minutes": [...], "dates": [...]} with the newest game
    first (PlayerGameLog order); minutes are floats ("32:15" -> 32.25) and
    dates are ISO strings (None for an undated row). Unknown player, unknown stat or a failed fetch
    gives empty lists for that season, never an exception.

    Each season comes from the league-wide table (src/gamelogs.py) when it is
    available — a player with no games in it simply has none that season —
    and from the player's own PlayerGameLog (game_log_for) otherwise.
    """
    cols = STAT_COLUMNS.get(stat)
    out = {"this": _empty_sample(), "last": _empty_sample()}
    if not cols:
        return out
    if seasons is None:
        from src.config import SEASON, previous_season
        seasons = (SEASON, previous_season(SEASON))
    for label, season in zip(("this", "last"), seasons):
        df = _league_rows(player_name, season)    # one league table per season, disk-cached (src/gamelogs.py)
        try:
            if df is None:
                df = game_log_for(player_name, season)   # table unavailable: the player's own log
        except Exception as e:
            logger.error(f"  Game log fetch failed for {player_name} ({season}): {type(e).__name__}: {e}")
            continue
        out[label] = _sample_from_log(df, cols, before)
    logger.info(
        f"  Sample {stat} for {player_name}: {len(out['this']['values'])} games this season, "
        f"{len(out['last']['values'])} last season"
    )
    return out
