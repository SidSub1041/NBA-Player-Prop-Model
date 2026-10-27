"""
League-wide game logs with an incremental disk cache.

The edge model projects ~150 players a run from two seasons of game logs.
Fetched per player (PlayerGameLog) that is ~300 requests a run through a
metered proxy; fetched league-wide (PlayerGameLogs: every player's games in
one table) it is at most two, and with this cache usually two small ones:

  * last season never changes: once it is on disk it is read from disk and
    never requested again;
  * this season grows by one slate a day: only games since the last fetch
    are requested (DateFrom = the newest game on disk) and merged in.

Files: CACHE_DIR/{season}.json.gz (CACHE_DIR = $GAMELOG_CACHE_DIR or
<repo>/cache/gamelogs, persisted between GitHub Actions runs by
actions/cache) holding a compact table

    {"season": "2026-27", "fetched_through": "2026-10-26" | null, "complete": bool,
     "columns": [PLAYER_ID, PLAYER_NAME, TEAM_ABBREVIATION, GAME_ID, GAME_DATE,
                 MIN, PTS, REB, AST, FG3M, STL, BLK, TOV],
     "rows": [[...], ...]}

GAME_DATE is YYYY-MM-DD, MIN is float minutes, newest game first.
"complete" marks a table fetched after its season ended: only then is it
final (a table saved during the season is topped up once after the season
rolls over, so the last few days of the regular season are never lost).

Everything degrades: a failed request falls back to the table on disk (as
fresh as the last successful fetch, usually a day) or, without one, returns
None and src/hitrate.py falls back to per-player PlayerGameLog calls. "playergamelogs" is an optional endpoint
in src/nba_http.py, so its breaker never aborts a board.
"""

import gzip
import json
import logging
import os
import time

import pandas as pd

import src.nba_http  # noqa: F401  — timeouts, retries and the per-endpoint breaker
from src import config
from src.underdog import norm_name, norm_name_base

logger = logging.getLogger(__name__)

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.environ.get("GAMELOG_CACHE_DIR") or os.path.join(_REPO, "cache", "gamelogs")

COLUMNS = ["PLAYER_ID", "PLAYER_NAME", "TEAM_ABBREVIATION", "GAME_ID", "GAME_DATE", "MIN",
           "PTS", "REB", "AST", "FG3M", "STL", "BLK", "TOV"]
STAT_COLUMNS = ["PTS", "REB", "AST", "FG3M", "STL", "BLK", "TOV"]

_frames: dict[str, pd.DataFrame | None] = {}   # season -> this run's table (None: unavailable this run)
_indexes: dict[str, dict] = {}                  # season -> name/id -> row positions in _frames[season]
requests_made: list[tuple[str, str]] = []       # (season, DateFrom) of every request this run, for logs/tests


def reset_cache() -> None:
    """Forget this run's tables (the disk cache stays). main.run() calls it via hitrate.reset_cache()."""
    _frames.clear()
    _indexes.clear()
    requests_made.clear()


def cache_path(season: str) -> str:
    return os.path.join(CACHE_DIR, f"{season}.json.gz")


# ── table shape ──────────────────────────────────────────────────────────

def _compact(raw: pd.DataFrame) -> pd.DataFrame:
    """
    The endpoint's (or the disk's) frame reduced to COLUMNS with stable types:
    PLAYER_ID int, GAME_ID str, GAME_DATE "YYYY-MM-DD" (the endpoint sends
    "2026-10-21T00:00:00"), MIN float ("32:15" -> 32.25), stats float.
    Rows without a player id or game id are dropped; newest game first.
    """
    from src.hitrate import parse_minutes

    missing = [c for c in ("PLAYER_ID", "PLAYER_NAME", "GAME_ID", "GAME_DATE") if c not in raw.columns]
    if missing:
        raise ValueError(f"PlayerGameLogs frame lacks {missing}")
    out = pd.DataFrame(index=raw.index)
    out["PLAYER_ID"] = pd.to_numeric(raw["PLAYER_ID"], errors="coerce")
    out["PLAYER_NAME"] = [str(v) if v is not None and v == v else "" for v in raw["PLAYER_NAME"]]
    team = raw["TEAM_ABBREVIATION"] if "TEAM_ABBREVIATION" in raw.columns else [None] * len(raw)
    out["TEAM_ABBREVIATION"] = [str(v) if v is not None and v == v else None for v in team]
    out["GAME_ID"] = [str(v) if v is not None and v == v and str(v) != "" else None for v in raw["GAME_ID"]]
    dates = pd.to_datetime(pd.Series(raw["GAME_DATE"], index=raw.index, dtype=object), format="mixed", errors="coerce")
    out["GAME_DATE"] = [ts.strftime("%Y-%m-%d") if not pd.isna(ts) else None for ts in dates]
    minutes = raw["MIN"] if "MIN" in raw.columns else [None] * len(raw)
    out["MIN"] = [round(parse_minutes(m), 2) for m in minutes]
    for col in STAT_COLUMNS:
        out[col] = pd.to_numeric(raw[col], errors="coerce") if col in raw.columns else float("nan")
    out = out[out["PLAYER_ID"].notna() & out["GAME_ID"].notna()]
    out = out.astype({"PLAYER_ID": "int64", "MIN": "float64", **{c: "float64" for c in STAT_COLUMNS}})
    for col in ("PLAYER_NAME", "TEAM_ABBREVIATION", "GAME_ID", "GAME_DATE"):
        out[col] = out[col].astype(object).where(out[col].notna(), None)   # None, never NaN, for a missing text
    return _sorted(out)


def _sorted(df: pd.DataFrame) -> pd.DataFrame:
    """Newest game first (undated rows last), then game id, then player id: a stable order on and off disk."""
    return df.sort_values(
        ["GAME_DATE", "GAME_ID", "PLAYER_ID"], ascending=[False, False, True], na_position="last", kind="stable"
    ).reset_index(drop=True)


def _merge(old: pd.DataFrame | None, new: pd.DataFrame) -> pd.DataFrame:
    """
    old + new, one row per (PLAYER_ID, GAME_ID): the newly fetched row wins
    (stat corrections). Every row of a player then carries his newest
    PLAYER_NAME, so a mid-season display-name change ('Carlton' -> 'Bub
    Carrington') never splits one player's season across two names.
    """
    if old is None or old.empty:
        merged = new
    elif new.empty:
        merged = old
    else:
        merged = pd.concat([old, new], ignore_index=True)
    merged = _sorted(merged.drop_duplicates(subset=["PLAYER_ID", "GAME_ID"], keep="last"))
    if not merged.empty:
        latest = merged.drop_duplicates("PLAYER_ID", keep="first")     # newest first: each player's latest row
        newest = dict(zip(latest["PLAYER_ID"], latest["PLAYER_NAME"]))
        names = [newest.get(pid, n) for pid, n in zip(merged["PLAYER_ID"], merged["PLAYER_NAME"])]
        merged = merged.assign(PLAYER_NAME=pd.Series(names, index=merged.index, dtype=object))
    return merged


def _fetched_through(df: pd.DataFrame) -> str | None:
    dates = df["GAME_DATE"].dropna()
    return str(dates.max()) if len(dates) else None


def _cell(v):
    """JSON-friendly scalar: NaN -> null, 31.0 -> 31, numpy -> python."""
    if v is None:
        return None
    if hasattr(v, "item"):
        v = v.item()
    if isinstance(v, float):
        if v != v:
            return None
        return int(v) if v.is_integer() else v
    return v


# ── disk ─────────────────────────────────────────────────────────────────

def _load(season: str) -> dict | None:
    """{"frame", "fetched_through", "complete"} from disk, or None (missing or unreadable — then it is refetched)."""
    path = cache_path(season)
    if not os.path.exists(path):
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            blob = json.load(f)
        if blob.get("season") != season:
            raise ValueError(f"file holds season {blob.get('season')!r}")
        frame = _compact(pd.DataFrame(blob["rows"], columns=blob["columns"]))
    except Exception as e:
        logger.warning(f"  Game-log cache {path} unreadable ({type(e).__name__}: {e}) — ignoring it")
        return None
    return {"frame": frame, "fetched_through": blob.get("fetched_through") or _fetched_through(frame),
            "complete": bool(blob.get("complete"))}


def _save(season: str, df: pd.DataFrame, complete: bool) -> None:
    """Write atomically (tmp file + os.replace): a killed run never leaves half a table behind."""
    path = cache_path(season)
    tmp = f"{path}.{os.getpid()}.tmp"
    blob = {
        "season": season,
        "fetched_through": _fetched_through(df),
        "complete": bool(complete),
        "columns": COLUMNS,
        "rows": [[_cell(v) for v in row] for row in df[COLUMNS].itertuples(index=False, name=None)],
    }
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            json.dump(blob, f, separators=(",", ":"))
        os.replace(tmp, path)
    except Exception as e:   # a read-only or full disk costs the cache, never the board
        logger.warning(f"  Could not write game-log cache {path}: {type(e).__name__}: {e}")
        try:
            os.remove(tmp)
        except OSError:
            pass


def _mmddyyyy(iso: str) -> str:
    y, m, d = iso.split("-")
    return f"{m}/{d}/{y}"


# ── public ───────────────────────────────────────────────────────────────

def league_logs(season: str) -> pd.DataFrame | None:
    """
    Every player's regular-season games in `season` (COLUMNS, newest first),
    cached for the run and on disk.

    A completed season (season < config.SEASON) whose table on disk is
    complete is served from disk without a request. Otherwise one
    PlayerGameLogs request: everything when nothing is on disk, else only
    games since the newest one on disk (that day included, so games that
    were unfinished at the last fetch come in); the result is merged into
    the disk table (newest row wins per player and game) and saved.

    A failed request returns the disk table when there is one (it may be a
    day stale) and None otherwise: callers then use per-player logs. The
    outcome, None included, is remembered until reset_cache().
    """
    if season in _frames:
        return _frames[season]
    cached = _load(season)
    completed = season < config.SEASON
    if cached is not None and completed and cached["complete"]:
        logger.info(f"  Game logs {season}: {len(cached['frame'])} rows from disk (season complete, no request)")
        _frames[season] = cached["frame"]
        return cached["frame"]

    since = cached["fetched_through"] if cached is not None else None
    date_from = _mmddyyyy(since) if since else ""
    try:
        from nba_api.stats.endpoints import playergamelogs

        requests_made.append((season, date_from))
        time.sleep(0.6)
        raw = playergamelogs.PlayerGameLogs(
            season_nullable=season,
            season_type_nullable=config.SEASON_TYPE,
            date_from_nullable=date_from,
        ).get_data_frames()[0]
        fresh = _compact(raw)
    except Exception as e:
        if cached is not None:
            logger.warning(
                f"  Game logs {season}: league request failed ({type(e).__name__}: {e}) — using the disk cache "
                f"through {since} ({len(cached['frame'])} rows; may be a day stale)"
            )
            frame = cached["frame"]
        else:
            logger.warning(
                f"  Game logs {season}: league request failed ({type(e).__name__}: {e}) and nothing on disk — "
                f"falling back to per-player game logs"
            )
            frame = None
        _frames[season] = frame
        return frame

    merged = _merge(cached["frame"] if cached is not None else None, fresh)
    if not merged.empty:   # once final, always final (a backfill with NBA_SEASON set to that season tops it up harmlessly)
        _save(season, merged, complete=completed or (cached is not None and cached["complete"]))
    logger.info(
        f"  Game logs {season}: {len(fresh)} rows fetched"
        f"{f' since {date_from}' if date_from else ' (full season)'}, {len(merged)} on file through {_fetched_through(merged)}"
    )
    _frames[season] = merged
    return merged


def _index(season: str, frame: pd.DataFrame) -> dict:
    """Row positions by normalised name (and which names two players share), by unambiguous suffix-free name, by player id."""
    idx = _indexes.get(season)
    if idx is not None and idx["frame"] is frame:
        return idx
    names = frame["PLAYER_NAME"].astype(str)
    full_of = {n: norm_name(n) for n in names.unique()}
    base_of = {n: norm_name_base(n) for n in names.unique()}
    by_full = {k: list(v) for k, v in frame.groupby(names.map(full_of), sort=False).indices.items()}
    by_base = {k: list(v) for k, v in frame.groupby(names.map(base_of), sort=False).indices.items()}
    by_id = {int(k): list(v) for k, v in frame.groupby("PLAYER_ID", sort=False).indices.items()}
    ids = frame["PLAYER_ID"].to_numpy()
    idx = {
        "frame": frame,
        "full": by_full,
        "shared": {k for k, v in by_full.items() if len({int(ids[i]) for i in v}) > 1},     # one name, two players
        "base": {k: v for k, v in by_base.items() if len({int(ids[i]) for i in v}) == 1},   # ambiguous bases dropped
        "id": by_id,
    }
    _indexes[season] = idx
    return idx


def player_rows(name: str, season: str) -> pd.DataFrame | None:
    """
    `name`'s rows in the season's league table, newest game first.

    Matched by normalised name, then suffix-free ("Kelly Oubre" finds
    "Kelly Oubre Jr." and vice versa; a suffix-free name two players share
    is not guessed), then by player id (src.hitrate._find_player_id: live ids
    main registers, then nba_api's list). An empty frame when the player has
    no games that season; None only when the league table is unavailable
    (the caller then falls back to the per-player log).
    """
    frame = league_logs(season)
    if frame is None:
        return None
    idx = _index(season, frame)
    # The player is an id, not a name: the live id main registered (this run's
    # NBA.com stats) when that id has games in the table, else the id the name
    # resolves to. All of that id's rows are returned, whatever name they carry.
    from src import hitrate
    exact = hitrate._live_ids.get(norm_name(name))
    if exact is not None:
        # An exact live id is authoritative: a rookie who shares a veteran's
        # name has no games last season, so he gets none (never the veteran's).
        pos = idx["id"].get(int(exact))
        return frame.iloc[sorted(pos)].reset_index(drop=True) if pos else frame.iloc[0:0].copy()
    alias = hitrate._live_ids.get(norm_name_base(name))
    pid = int(alias) if alias is not None and int(alias) in idx["id"] else None
    if pid is None:
        key = norm_name(name)
        pos = idx["full"].get(key)
        if pos is not None and key in idx["shared"]:
            pos = _pick_one(name, frame, pos)    # two players share the name: the id main knows, else the latest
        if pos is None:
            pos = idx["base"].get(norm_name_base(name))
        if pos:
            pid = int(frame.at[pos[0], "PLAYER_ID"])
    if pid is None:
        try:
            found = hitrate._find_player_id(name)
        except Exception:
            found = None
        pid = int(found) if found is not None and int(found) in idx["id"] else None
    pos = idx["id"].get(pid) if pid is not None else None
    if not pos:
        return frame.iloc[0:0].copy()
    return frame.iloc[sorted(pos)].reset_index(drop=True)   # the table is newest first, so his rows are too


def _pick_one(name: str, frame: pd.DataFrame, pos: list) -> list:
    from src import hitrate
    try:
        pid = hitrate._find_player_id(name)
    except Exception:
        pid = None
    ids = [int(frame.at[i, "PLAYER_ID"]) for i in pos]
    if pid is None or int(pid) not in ids:
        pid = ids[min(range(len(pos)), key=lambda k: pos[k])]   # the frame is newest first
    return [i for i, p in zip(pos, ids) if p == int(pid)]


def name_index(seasons: tuple[str, ...]) -> dict:
    """
    {norm_name: {"name", "team", "last_game", "gp_this", "id"}} for every
    player in the given seasons' league tables (this season first):

      name       the NBA's spelling (latest game)
      team       the team of his most recent game — this season's when he
                 has played this season, so a traded player is on his new team
      last_game  that game's date, YYYY-MM-DD
      gp_this    games in seasons[0]
      id         PLAYER_ID

    Each entry is also filed under its suffix-free name (norm_name_base)
    when no other player's name or suffix-free name claims that key; a key
    two players' suffix-free names share maps to None (ambiguous: not
    guessed). Two players with the same full name: the one who played most
    recently keeps the key. Look a book's name up as
    `idx.get(norm_name(n)) or idx.get(norm_name_base(n))`.
    Empty when no table is available.
    """
    players: dict[int, dict] = {}
    for i, season in enumerate(seasons):
        frame = league_logs(season)
        if frame is None or frame.empty:
            continue
        counts = frame.groupby("PLAYER_ID").size().to_dict() if i == 0 else {}
        latest = frame.drop_duplicates("PLAYER_ID", keep="first")   # newest first: each player's latest game
        for pid, pname, team, day in zip(latest["PLAYER_ID"], latest["PLAYER_NAME"],
                                         latest["TEAM_ABBREVIATION"], latest["GAME_DATE"]):
            pid = int(pid)
            if pid in players:
                continue                                             # an earlier (newer) season already placed him
            players[pid] = {"name": str(pname), "team": team, "last_game": day,
                            "gp_this": int(counts.get(pid, 0)), "id": pid}

    out: dict[str, dict | None] = {}
    for entry in sorted(players.values(), key=lambda e: (e["last_game"] or "", e["gp_this"]), reverse=True):
        out.setdefault(norm_name(entry["name"]), entry)
    bases: dict[str, set] = {}
    for entry in players.values():
        base = norm_name_base(entry["name"])
        if base not in out:
            bases.setdefault(base, set()).add(entry["id"])
    for base, ids in bases.items():
        out[base] = players[next(iter(ids))] if len(ids) == 1 else None
    return out
