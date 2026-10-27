"""
Post-game grading engine.

After games finish, this module:
  1. Loads that day's picks from logs/props_{date}.json
  2. Fetches actual box-score stats from NBA API
  3. Compares each pick (over/under on a line) to reality
  4. Checks Underdog API for voided lines (injury / DNP)
  5. Writes graded results back and updates model_results history

A pick without a line is void ("no line"): there is nothing to settle it
against. Every decided pick also gets its closing-line value (CLV) from the
day's line history (src/line_history.py): the last price the books hung
before tip-off, found through the game's commence time in
logs/vegas_{date}.json. Beating the close is the one piece of evidence,
independent of the result, that a pick had an edge.

A close only counts when it is verified (see _verified_close): the game's
market was re-bought within CLOSE_WINDOW_MINUTES before tip and the books
still hung the prop at that re-price. Otherwise the pick's CLV fields stay
None, its "close_reason" says why, and the day's CLV counts it as
"unverified", never as a 0.0 close.
"""

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone

from nba_api.stats.endpoints import boxscoretraditionalv3, scoreboardv3
from nba_api.stats.static import players as nba_players

from src import line_history
from src.config import CLOSE_WINDOW_MINUTES, NBA_TEAM_IDS, season_for_date
from src.underdog import norm_name, norm_name_base
from src.vegas_odds import MARKETS

logger = logging.getLogger(__name__)

LOGS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
WEB_DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "web", "public", "data"
)
RESULTS_FILE = os.path.join(WEB_DATA_DIR, "results_history.json")

STAT_COL = {"points": "pts", "rebounds": "reb", "assists": "ast"}

# Combo stat → list of component stat keys
COMBO_STAT_COLS = {
    "pts+ast": ["pts", "ast"],
    "pts+reb": ["pts", "reb"],
    "reb+ast": ["reb", "ast"],
    "pra": ["pts", "reb", "ast"],
}


def _american_odds_to_profit(odds_str: str | None) -> float:
    """
    Convert American odds string to profit in units on a 1-unit stake.
    +126  → 1.26u profit
    -157  → 0.637u profit
    Returns 1.0 (even money) if odds are unavailable.
    """
    if not odds_str:
        return 1.0
    try:
        odds = int(odds_str.replace("+", ""))
    except (ValueError, AttributeError):
        return 1.0
    if odds > 0:
        return odds / 100.0
    elif odds < 0:
        return 100.0 / abs(odds)
    return 1.0


# ── Fetch actual stats ───────────────────────────────────────────────────────

def _fetch_game_ids(date: str) -> list[str]:
    """Return NBA game IDs for the given date (YYYY-MM-DD)."""
    time.sleep(0.6)
    sb = scoreboardv3.ScoreboardV3(game_date=date)
    data = sb.get_dict()
    games = data.get("scoreboard", {}).get("games", [])
    return [g["gameId"] for g in games]


_BOX_CACHE: dict[str, dict] = {}
_BOX_STATUS: dict[str, dict] = {}   # date -> {"games": n, "failed": k}


def clear_box_cache() -> None:
    """Start of a grading run: never reuse box scores fetched by an earlier run in this process."""
    _BOX_CACHE.clear()
    _BOX_STATUS.clear()


def box_scores_complete(date: str) -> bool:
    """Did every game on *date* come back? (False if any game's box score failed or nothing was fetched.)"""
    st = _BOX_STATUS.get(date)
    return bool(st) and st["games"] > 0 and st["failed"] == 0


def _fetch_box_scores(date: str) -> dict[str, dict]:
    """
    Fetch box-score stats for every player who played on *date* (once per date per run;
    grading and the learning loop share it). An empty result is never cached.

    Returns: {player_name_lower: {"pts": int, "reb": int, "ast": int, "min": float, "dnp": bool}}
    """
    if date in _BOX_CACHE:
        return _BOX_CACHE[date]
    box = _fetch_box_scores_uncached(date)
    if box:
        _BOX_CACHE[date] = box
    return box


def _fetch_box_scores_uncached(date: str) -> dict[str, dict]:
    try:
        game_ids = _fetch_game_ids(date)
    except Exception as e:
        logger.warning(f"Could not list games for {date}: {e}")
        _BOX_STATUS[date] = {"games": 0, "failed": 1}
        return {}
    player_stats: dict[str, dict] = {}
    failed = 0

    for gid in game_ids:
        time.sleep(0.6)
        try:
            box = boxscoretraditionalv3.BoxScoreTraditionalV3(game_id=gid)
            data = box.get_dict()

            # The v3 endpoint nests player stats under boxScoreTraditional → ...
            bs = data.get("boxScoreTraditional", data)
            for side in ("homeTeam", "awayTeam"):
                team_data = bs.get(side, {})
                for p in team_data.get("players", []):
                    stats = p.get("statistics", {})
                    name = f"{p.get('firstName', '')} {p.get('familyName', '')}".strip()
                    if not name:
                        name = p.get("name", p.get("nameI", ""))

                    minutes_str = stats.get("minutes", "PT00M00.00S")
                    minutes = _parse_minutes(minutes_str)

                    player_stats[name.lower()] = {
                        "pts": int(stats.get("points", 0)),
                        "reb": int(stats.get("reboundsTotal", 0)),
                        "ast": int(stats.get("assists", 0)),
                        "min": minutes,
                        "dnp": minutes == 0,
                    }
        except Exception as e:
            failed += 1
            logger.warning(f"Box-score fetch failed for game {gid}: {e}")

    _BOX_STATUS[date] = {"games": len(game_ids), "failed": failed}
    logger.info(f"Fetched box-score data for {len(player_stats)} players"
                + (f" ({failed}/{len(game_ids)} games FAILED — their picks will be voided)" if failed else ""))
    return player_stats


def _parse_minutes(minutes_val) -> float:
    """Parse minutes from various NBA API formats."""
    if isinstance(minutes_val, (int, float)):
        return float(minutes_val)
    s = str(minutes_val)
    # Format: "PT32M15.00S"
    if s.startswith("PT"):
        s = s[2:]
        mins = 0.0
        if "M" in s:
            parts = s.split("M")
            mins = float(parts[0])
            s = parts[1]
        if "S" in s:
            secs = float(s.replace("S", "") or 0)
            mins += secs / 60
        return mins
    # Format: "32:15" or "32"
    if ":" in s:
        parts = s.split(":")
        return float(parts[0]) + float(parts[1]) / 60
    try:
        return float(s)
    except ValueError:
        return 0.0


# ── Closing-line value ───────────────────────────────────────────────────────

CLV_KEYS = ("closing_line", "closing_odds", "clv_line", "clv_prob")

# Who hung a pick's line when it was not the sportsbooks (src/lines.py "source")
_SOURCE_LABEL = {"dfs": "a DFS app", "underdog": "Underdog"}


def _ts(value) -> datetime | None:
    """ISO 8601 ('...Z' or with an offset) -> aware UTC datetime; None when unparseable."""
    try:
        ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return ts.astimezone(timezone.utc) if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _priced_day(date: str) -> tuple[dict, dict]:
    """
    The day's sportsbook cache (logs/vegas_{date}.json), read once per
    grading run, as ({team abbreviation: event}, {prop key: last price
    bought}). An event carries "commence_time" and "markets": {market key:
    UTC time that market was last bought}; purchases stop at tip-off, so after
    the game each market's stamp is its last pre-tip re-price and each prop's
    line (with "fetched_at" and "tier") is the last price the run that bought
    it saw. Both empty without a readable file.
    """
    try:
        with open(os.path.join(LOGS_DIR, f"vegas_{date}.json"), encoding="utf-8") as f:
            cache = json.load(f)
    except (OSError, ValueError):
        return {}, {}
    if not isinstance(cache, dict):
        return {}, {}
    events = cache.get("events") if isinstance(cache.get("events"), dict) else {}
    by_team: dict[str, dict] = {}
    for ev in events.values():
        if isinstance(ev, dict):
            for team in ev.get("teams") or []:
                by_team[str(team)] = ev
    lines = cache.get("lines") if isinstance(cache.get("lines"), dict) else {}
    return by_team, lines


def _prop_key(player: str, stat: str, lines: dict, history: dict) -> str:
    """
    The prop's key in the day's cache and line history: the exact normalised
    name (line_history.key_for), else the one key for the same stat whose
    suffix-free name matches ('Jimmy Butler' at the books, 'Jimmy Butler III'
    on the board), as VegasLines.lookup found the pick's line. Ambiguous or
    absent: the exact key.
    """
    key = line_history.key_for(player, stat)
    snaps = history.get("snapshots") if isinstance(history.get("snapshots"), dict) else {}
    base = norm_name_base(player)

    def suffix_free(pool) -> set:
        found = set()
        for k in pool:
            name, _, s = str(k).rpartition("|")
            if s == stat and name and norm_name_base(name) == base:
                found.add(k)
        return found

    # The live sportsbook cache first, exactly as VegasLines.lookup found the pick's
    # line (exact, then the one suffix-free match); the history only when the cache
    # has no such key (it never drops a key, so a stale spelling must not win).
    if key in lines:
        return key
    in_cache = suffix_free(lines)
    if len(in_cache) == 1:
        return in_cache.pop()
    if key in snaps:
        return key
    in_history = suffix_free(snaps)
    return in_history.pop() if len(in_history) == 1 else key


def _price(snap: dict) -> tuple:
    try:
        line = float(snap.get("line"))
    except (TypeError, ValueError):
        line = None
    return line, snap.get("over_odds"), snap.get("under_odds")


def _verified_close(pick: dict, events: dict, lines: dict, history: dict, date: str) -> tuple[dict | None, str | None]:
    """
    (close, None) when the pick has a verified close, else (None, why not).

    line_history.record only appends a snapshot when a price CHANGES, so the
    snapshot times cannot show that anyone checked the price near tip: a game
    no snapshot run re-priced would close at its morning price and read as a
    0.0 CLV. The day's sportsbook cache can show it. The close counts only when
      * the pick's line came from the sportsbooks (a DFS-app or Underdog line
        has no sportsbook close to be measured against);
      * the game's market for the stat (vegas_odds.MARKETS) was last bought
        at or before tip-off and no more than CLOSE_WINDOW_MINUTES before it
        (the event is found through the pick's team, then its opponent);
      * the books still hung the prop at that re-price: its cached line is a
        sportsbook line fetched in the same window (a prop pulled at the last
        re-price, or left only on the DFS apps, has no close).
    The price is the last line-history snapshot at or before tip-off; if that
    disagrees with the verified cached price (the run that bought the close
    saved its history after tip), the cached price is the close.
    """
    stat = pick.get("stat", "")
    source = (pick.get("line_source") or "vegas") if "line" in pick else "underdog"   # pre-Oct-22 boards: Underdog only
    if source != "vegas":
        return None, f"line from {_SOURCE_LABEL.get(source, source)}, not a sportsbook"
    ev = events.get(str(pick.get("team"))) or events.get(str(pick.get("opponent"))) or {}
    tip = _ts(ev.get("commence_time"))
    window = timedelta(minutes=CLOSE_WINDOW_MINUTES)

    def near_tip(t: datetime | None) -> bool:
        return t is not None and tip is not None and t <= tip and tip - t <= window

    markets = ev.get("markets") if isinstance(ev.get("markets"), dict) else {}
    if not near_tip(_ts(markets.get(MARKETS.get(stat)))):
        return None, f"no re-price within {CLOSE_WINDOW_MINUTES} min of tip"
    key = _prop_key(pick.get("player_name", ""), stat, lines, history)
    entry = lines.get(key)
    if not isinstance(entry, dict) or (entry.get("tier") or "books") != "books" \
            or _price(entry)[0] is None or not near_tip(_ts(entry.get("fetched_at"))):
        return None, "books pulled the prop before tip"
    close = line_history.closing(date, key, ev.get("commence_time"), LOGS_DIR, history=history)
    if not close or _price(close) != _price(entry):
        close = {"t": entry.get("fetched_at"), "line": _price(entry)[0], "over_odds": entry.get("over_odds"),
                 "under_odds": entry.get("under_odds"), "books": entry.get("books"), "tier": "books",
                 "event_id": entry.get("event_id")}
    return close, None


def _clv_for_pick(pick: dict, line, edge: str, pick_odds, events: dict, lines: dict, history: dict, date: str) -> dict:
    """
    The pick's CLV fields (closing_line, closing_odds, clv_line, clv_prob)
    plus "close_reason": against a verified close (_verified_close) the four
    fields are filled and the reason is None; without one all four are None
    and the reason says why ("no re-price within 90 min of tip", ...).
    """
    close, reason = _verified_close(pick, events, lines, history, date)
    if not close:
        return {**{k: None for k in CLV_KEYS}, "close_reason": reason}
    other = pick.get("under_odds") if edge == "over" else pick.get("over_odds")
    out = line_history.clv(line, edge, pick_odds, close, pick.get("stat", ""),
                           pick_other_odds=other, sd=pick.get("proj_sd"))
    out["close_reason"] = None if out.get("clv_line") is not None else "no closing line on file"
    return out


def clv_summary(graded: list[dict]) -> dict:
    """
    A night's CLV in one line: over the picks with a verified close (CLV
    fields set; see _verified_close), the mean line CLV, the share that beat
    the close (clv_line > 0), the share that pushed (== 0) and the mean
    probability CLV (None when no pick has one). "unverified" counts the
    decided picks (hit / miss) without a verified close; they are never in n.
    """
    with_close = [p for p in graded if isinstance(p, dict) and p.get("clv_line") is not None]
    unverified = sum(1 for p in graded if isinstance(p, dict) and p.get("result") in ("hit", "miss")
                     and p.get("clv_line") is None)
    if not with_close:
        return {"n": 0, "unverified": unverified, "avg_line": None, "beat_close": None, "pushed_close": None, "avg_prob": None}
    n = len(with_close)
    probs = [float(p["clv_prob"]) for p in with_close if p.get("clv_prob") is not None]
    return {
        "n": n,
        "unverified": unverified,
        "avg_line": round(sum(float(p["clv_line"]) for p in with_close) / n, 3),
        "beat_close": round(sum(1 for p in with_close if float(p["clv_line"]) > 0) / n, 3),
        "pushed_close": round(sum(1 for p in with_close if float(p["clv_line"]) == 0) / n, 3),
        "avg_prob": round(sum(probs) / len(probs), 4) if probs else None,
    }


# ── Grade the day ────────────────────────────────────────────────────────────

def grade_picks(date: str) -> dict:
    """
    Grade a day's picks against actual results.

    Returns dict with:
      {
        "date": str,
        "total_picks": int,
        "hits": int,
        "misses": int,
        "voided": int,
        "clv": {"n", "unverified", "avg_line", "beat_close", "pushed_close", "avg_prob"},
        "graded_picks": [...]   # each pick carries closing_line / closing_odds / clv_line / clv_prob
                                # (set only against a verified close) and close_reason (why not, else None)
      }
    """
    # Load picks
    json_path = os.path.join(LOGS_DIR, f"props_{date}.json")
    if not os.path.exists(json_path):
        logger.error(f"No picks file found at {json_path}")
        return {}

    with open(json_path) as f:
        props_data = json.load(f)

    # Combo plays appear on the board, so they are graded with everything
    # else (COMBO_STAT_COLS below sums their component stats).
    valid_picks = props_data.get("valid_picks", []) + props_data.get("valid_combos", [])
    if not valid_picks:
        logger.info(f"No valid picks to grade for {date}")
        return {
            "date": date,
            "season": season_for_date(datetime.strptime(date, "%Y-%m-%d").date()),
            "total_picks": 0, "hits": 0, "misses": 0, "voided": 0, "units": 0.0,
            "clv": clv_summary([]),
            "graded_picks": [],
        }

    # Fetch actual stats
    logger.info(f"Fetching box-score data for {date}...")
    actual_stats = _fetch_box_scores(date)

    # The close for CLV: the day's priced events (tip-offs, when each market
    # was last bought) and lines from the sportsbook cache, and the day's
    # line history, each read once.
    events, cached_lines = _priced_day(date)
    history = line_history.load(date, LOGS_DIR)

    # Normalized index so 'Nikola Jokic' / 'P.J. Washington' / 'Jimmy Butler'
    # still find their box-score rows ('Nikola Jokić', 'PJ Washington', 'Jimmy Butler III').
    stats_by_norm: dict[str, dict] = {}
    for box_name, row in actual_stats.items():
        stats_by_norm.setdefault(norm_name(box_name), row)
        stats_by_norm.setdefault(norm_name_base(box_name), row)

    # Grade each pick
    hits = 0
    misses = 0
    voided_count = 0
    units = 0.0
    graded = []

    for pick in valid_picks:
        name = pick["player_name"]
        stat = pick["stat"]
        edge = pick["edge"]
        # The line the pick was made against (sportsbooks; DFS apps or Underdog's feed on older boards).
        # Boards from before Oct 22, 2026 only carry Underdog's line.
        line = pick.get("line") if "line" in pick else pick.get("ud_line")

        name_lower = name.lower()
        actual = (
            actual_stats.get(name_lower)
            or stats_by_norm.get(norm_name(name))
            or stats_by_norm.get(norm_name_base(name))
        )

        # Determine which odds apply to this pick's edge
        if "line" in pick:
            pick_odds = pick.get("over_odds") if edge == "over" else pick.get("under_odds")
        else:
            pick_odds = pick.get("ud_over_odds") if edge == "over" else pick.get("ud_under_odds")

        result_entry = {
            **pick,
            "actual_value": None,
            "result": "pending",
            "units": 0.0,
            "void_reason": None,
            **{k: None for k in CLV_KEYS},
            "close_reason": None,
        }

        def void(reason: str) -> None:
            result_entry["result"] = "void"
            result_entry["void_reason"] = reason
            graded.append(result_entry)
            logger.info(f"  {name} | {stat} {edge} → VOID ({reason})")

        # No line: nothing to settle the pick against.
        if line is None:
            void("no line")
            voided_count += 1
            continue

        # Voids follow Underdog's settlement rule: a player who didn't play.
        if actual is None:
            # Player not in box scores at all (DNP / inactive)
            void("not in box scores")
            voided_count += 1
            continue

        if actual.get("dnp"):
            void("0 minutes played")
            voided_count += 1
            continue

        # Get actual stat value
        if stat in COMBO_STAT_COLS:
            # Combo prop — sum component stats
            combo_cols = COMBO_STAT_COLS[stat]
            if not all(c in actual for c in combo_cols):
                void("stat missing from box score")
                voided_count += 1
                continue
            actual_value = sum(actual[c] for c in combo_cols)
        else:
            stat_key = STAT_COL.get(stat)
            if not stat_key or stat_key not in actual:
                void("stat missing from box score")
                voided_count += 1
                continue
            actual_value = actual[stat_key]
        result_entry["actual_value"] = actual_value

        # Determine if pick hit
        if edge == "over":
            hit = actual_value > line
        else:
            hit = actual_value < line

        if hit:
            profit = round(_american_odds_to_profit(pick_odds), 3)
            result_entry["result"] = "hit"
            result_entry["units"] = profit
            units += profit
            hits += 1
            logger.info(
                f"  ✅ {name} | {stat} {edge} {line} → "
                f"Actual: {actual_value} — HIT (+{profit}u)"
            )
        else:
            result_entry["result"] = "miss"
            result_entry["units"] = -1.0
            units -= 1.0
            misses += 1
            logger.info(
                f"  ❌ {name} | {stat} {edge} {line} → "
                f"Actual: {actual_value} — MISS (-1u)"
            )

        # Closing-line value: where the books closed this prop versus where the pick took it
        try:
            result_entry.update(_clv_for_pick(pick, line, edge, pick_odds, events, cached_lines, history, date))
        except Exception as e:   # never lets the history break grading
            result_entry.update({**{k: None for k in CLV_KEYS}, "close_reason": f"CLV error ({type(e).__name__})"})
            logger.warning(f"  CLV not computed for {name} | {stat} ({type(e).__name__}: {e})")
        if result_entry.get("clv_line") is not None:
            logger.info(f"     close {result_entry['closing_line']} ({result_entry['closing_odds']}) → CLV {result_entry['clv_line']:+.1f}"
                        + (f", {result_entry['clv_prob']:+.3f} prob" if result_entry.get("clv_prob") is not None else ""))
        else:
            logger.info(f"     close unverified ({result_entry.get('close_reason')})")

        graded.append(result_entry)

    clv = clv_summary(graded)
    day_result = {
        "date": date,
        "season": season_for_date(datetime.strptime(date, "%Y-%m-%d").date()),
        "total_picks": hits + misses,  # excludes voided
        "hits": hits,
        "misses": misses,
        "voided": voided_count,
        "units": round(units, 3),
        "clv": clv,
        "graded_picks": graded,
    }

    logger.info(
        f"Grading complete: {hits} hits, {misses} misses, "
        f"{voided_count} voided, {units:+.2f}u out of {len(valid_picks)} picks"
        + (f"; CLV {clv['avg_line']:+.2f} pts, beat the close {clv['beat_close']:.0%} of {clv['n']} verified" if clv["n"] else "; no verified closes")
        + (f" ({clv['unverified']} unverified)" if clv.get("unverified") else "")
    )

    return day_result


# ── Persist results ──────────────────────────────────────────────────────────

def save_graded_results(day_result: dict):
    """
    Save graded results to:
      1. logs/graded_{date}.json   — detailed per-pick results
      2. results_history.json      — running model_results array
      3. latest.json               — update model_results field
    """
    if not day_result or not day_result.get("date"):
        return

    date = day_result["date"]

    # 1. Save detailed graded file
    os.makedirs(LOGS_DIR, exist_ok=True)
    graded_path = os.path.join(LOGS_DIR, f"graded_{date}.json")
    with open(graded_path, "w") as f:
        json.dump(day_result, f, indent=2)
    logger.info(f"Graded results saved to {graded_path}")

    # 2. Update results history
    os.makedirs(WEB_DATA_DIR, exist_ok=True)
    history = []
    if os.path.exists(RESULTS_FILE):
        with open(RESULTS_FILE) as f:
            history = json.load(f)

    # Summary entry (no per-pick details)
    summary = {
        "date": date,
        "season": season_for_date(datetime.strptime(date, "%Y-%m-%d").date()),
        "total_picks": day_result["total_picks"],
        "hits": day_result["hits"],
        "misses": day_result["misses"],
        "voided": day_result.get("voided", 0),
        "units": day_result.get("units", 0.0),
    }
    clv = day_result.get("clv") or {}
    if clv.get("n"):   # closing-line value, when the night had closes on file
        summary["clv_avg_line"] = clv.get("avg_line")
        summary["clv_beat"] = clv.get("beat_close")

    # Replace existing entry for this date if re-grading
    history = [h for h in history if h["date"] != date]
    history.append(summary)
    history.sort(key=lambda x: x["date"])

    with open(RESULTS_FILE, "w") as f:
        json.dump(history, f, indent=2)
    logger.info(f"Results history updated ({len(history)} days)")

    # 3. Update latest.json with model_results
    latest_path = os.path.join(WEB_DATA_DIR, "latest.json")
    if os.path.exists(latest_path):
        with open(latest_path) as f:
            latest = json.load(f)
        latest["model_results"] = history
        with open(latest_path, "w") as f:
            json.dump(latest, f, indent=2)
        logger.info("Updated latest.json with model_results")
