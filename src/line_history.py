"""
Line history: every price the books hang for a prop through the day, so the
model can see where a line opened, how it moved and where it closed.

Each save of the day's sportsbook cache (src/vegas_odds.VegasLines.save)
appends a snapshot for every prop whose line or prices changed since the last
one, to logs/lines_{date}.json:

    {"date": "2026-10-27",
     "snapshots": {"jaylen brown|points": [
         {"t": "2026-10-27T13:16:02Z", "line": 24.5, "over_odds": "-110", "under_odds": "-110",
          "books": 5, "tier": "books", "event_id": "..."},
         ...]}}

Keys are `src.underdog.norm_name(player) + "|" + stat`, exactly as the
sportsbook cache keys its lines. Identical consecutive snapshots are never
duplicated, so a run that re-buys an unchanged market leaves the file alone.

Why it matters: a line that moves DOWN during the day favours the UNDER at
the ORIGINAL number (the books now think the player does less), and vice
versa — `movement()` signs that toward the pick. The last snapshot before
tip-off is the close; `clv()` measures a pick against it (closing-line value):
beating the close is the only pre-result evidence that a pick had an edge.
"""

import json
import logging
import math
import os
from datetime import datetime, timezone

from src.underdog import norm_name

logger = logging.getLogger(__name__)

LOGS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")

# What a snapshot keeps from a cached line (plus "t", when it was seen)
SNAPSHOT_FIELDS = ("line", "over_odds", "under_odds", "books", "tier", "event_id")

# Rough per-stat spread of a player's game-to-game output, sd ≈ k·√line, used
# only when a closing probability must be shifted to a different line and no
# projection sd travelled with the pick. (25 ppg ≈ sd 7, 8 rpg ≈ sd 3, 6 apg ≈ sd 2.5.)
_SD_PER_SQRT_LINE = {"points": 1.4, "rebounds": 1.1, "assists": 1.0, "pra": 1.5, "pts+reb": 1.45,
                     "pts+ast": 1.45, "reb+ast": 1.2, "3-pointers made": 0.9}


def key_for(player: str, stat: str) -> str:
    """The history key for a prop, the same key the sportsbook cache uses."""
    return f"{norm_name(player)}|{stat}"


def _path(date: str, logs_dir: str | None = None) -> str:
    return os.path.join(logs_dir or LOGS_DIR, f"lines_{date}.json")


def _stamp(now: datetime) -> str:
    if now.tzinfo is not None:
        now = now.astimezone(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(value) -> datetime | None:
    try:
        ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _num(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _snapshot(entry: dict, t: str) -> dict | None:
    """One cached line -> the snapshot kept for it (None when it has no line)."""
    line = _num(entry.get("line"))
    if line is None:
        return None
    snap = {"t": t, "line": line}
    for k in SNAPSHOT_FIELDS[1:]:
        snap[k] = entry.get(k)
    if snap.get("tier") is None:
        snap["tier"] = "books"
    return snap


def _same_price(a: dict, b: dict) -> bool:
    return (_num(a.get("line")), a.get("over_odds"), a.get("under_odds")) == \
           (_num(b.get("line")), b.get("over_odds"), b.get("under_odds"))


# ── writing ───────────────────────────────────────────────────────────────
def load(date: str, logs_dir: str | None = None) -> dict:
    """The day's history ({"date", "snapshots"}); empty when there is no file yet or it is unreadable."""
    try:
        with open(_path(date, logs_dir), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {"date": date, "snapshots": {}}
    if not isinstance(data, dict) or not isinstance(data.get("snapshots"), dict):
        return {"date": date, "snapshots": {}}
    if data.get("date") not in (None, date):
        return {"date": date, "snapshots": {}}
    data["date"] = date
    return data


def record(date: str, lines: dict, now: datetime, logs_dir: str | None = None) -> int:
    """
    Append a snapshot (stamped `now`) for every key in `lines` whose line or
    prices differ from its last snapshot; a key's first sight is always
    recorded. Returns how many were appended. Nothing is written when nothing
    changed, so identical consecutive saves are idempotent; the write is
    atomic (tmp + os.replace).
    """
    history = load(date, logs_dir)
    snaps = history["snapshots"]
    t = _stamp(now)
    appended = 0
    for key, entry in (lines or {}).items():
        if not isinstance(entry, dict):
            continue
        snap = _snapshot(entry, t)
        if snap is None:
            continue
        series = snaps.get(key)
        if series and _same_price(series[-1], snap):
            continue
        snaps.setdefault(key, []).append(snap)
        appended += 1
    if not appended:
        return 0
    path = _path(date, logs_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=1, sort_keys=True)
    os.replace(tmp, path)
    return appended


# ── reading ───────────────────────────────────────────────────────────────
def _series(date: str, key: str, logs_dir: str | None, history: dict | None) -> list:
    data = history if isinstance(history, dict) else load(date, logs_dir)
    series = (data.get("snapshots") or {}).get(key) or []
    return [s for s in series if isinstance(s, dict) and _num(s.get("line")) is not None]


def opening(date: str, key: str, logs_dir: str | None = None, history: dict | None = None) -> dict | None:
    """The first snapshot of the day for `key`, or None. Pass `history` (from load) to avoid re-reading the file."""
    series = _series(date, key, logs_dir, history)
    return series[0] if series else None


def latest(date: str, key: str, logs_dir: str | None = None, history: dict | None = None) -> dict | None:
    """The most recent snapshot for `key`, or None."""
    series = _series(date, key, logs_dir, history)
    return series[-1] if series else None


def at_or_before(date: str, key: str, t_iso, logs_dir: str | None = None, history: dict | None = None) -> dict | None:
    """The last snapshot taken at or before `t_iso` (ISO 8601, UTC), or None if none was."""
    cutoff = _parse_ts(t_iso)
    if cutoff is None:
        return None
    hit = None
    for snap in _series(date, key, logs_dir, history):
        ts = _parse_ts(snap.get("t"))
        if ts is not None and ts <= cutoff:
            hit = snap
    return hit


def movement(date: str, key: str, edge: str, logs_dir: str | None = None, history: dict | None = None) -> dict | None:
    """
    How the line moved from its first sight to its latest (within the latest
    snapshot's source tier: sportsbooks or DFS apps), signed toward the pick: `delta_line` is latest − open; `move` is +delta for an over and
    −delta for an under, so a positive `move` means the market moved the
    pick's way (a line that fell favours the under at the original number).
    None when the key has no history.
    """
    series = _series(date, key, logs_dir, history)
    if not series:
        return None
    # Movement is measured within one source: when the books post after the DFS
    # apps (the night before), the opening is the books' first line, not a DFS number.
    tier = series[-1].get("tier")
    same = [snap for snap in series if snap.get("tier") == tier] or series
    o, l = same[0], same[-1]
    series = same
    delta = round(float(l["line"]) - float(o["line"]), 2)
    under = str(edge).lower() == "under"
    return {
        "open_line": float(o["line"]), "open_over_odds": o.get("over_odds"), "open_under_odds": o.get("under_odds"),
        "latest_line": float(l["line"]), "latest_over_odds": l.get("over_odds"), "latest_under_odds": l.get("under_odds"),
        "delta_line": delta,
        "move": round(-delta if under else delta, 2),
        "n_snapshots": len(series),
        "opened_at": o.get("t"),
        "updated_at": l.get("t"),
    }


def closing(date: str, key: str, tip_iso, logs_dir: str | None = None, history: dict | None = None) -> dict | None:
    """The close: the last snapshot at or before tip-off (None without a tip time or a pre-tip snapshot)."""
    if not tip_iso:
        return None
    return at_or_before(date, key, tip_iso, logs_dir, history)


# ── closing-line value ────────────────────────────────────────────────────
def default_sd(stat: str, line) -> float:
    """A stand-in spread for shifting a market probability between lines when no projection sd is at hand."""
    value = _num(line) or 0.0
    return max(_SD_PER_SQRT_LINE.get(stat, 1.3) * math.sqrt(max(value, 1.0)), 1.0)


def clv(pick_line, edge: str, pick_odds, close: dict | None, stat: str,
        pick_other_odds=None, sd=None) -> dict:
    """
    Closing-line value of a pick against `close` (a snapshot from `closing`).

    `clv_line` is in the pick's favour when positive: closing − pick line for
    an over, pick − closing for an under. `clv_prob` is the market's
    de-vigged probability of the pick side at the PICK line, read from the
    closing prices (shifted from the closing line when it differs, using the
    projection's distribution with `sd`, or a per-stat default), minus the
    de-vigged probability the pick was taken at. That needs
    src.projection.market_prob / market_prob_at; without them (or without
    usable prices) `clv_prob` is None. `pick_other_odds` is the opposite
    side's price at pick time; with only the pick side priced the other side
    is assumed at the same price.
    """
    out = {"closing_line": None, "closing_odds": None, "clv_line": None, "clv_prob": None}
    line = _num(pick_line)
    close_line = _num((close or {}).get("line")) if isinstance(close, dict) else None
    if line is None or close_line is None:
        return out
    under = str(edge).lower() == "under"
    out["closing_line"] = close_line
    out["closing_odds"] = close.get("under_odds") if under else close.get("over_odds")
    out["clv_line"] = round(line - close_line if under else close_line - line, 2)
    try:
        from src.projection import market_prob, market_prob_at
    except Exception as e:   # implementer 2's module not there yet, or broken: the line CLV still stands
        logger.debug(f"clv_prob unavailable ({type(e).__name__}: {e})")
        return out
    try:
        m_close = market_prob(close.get("over_odds"), close.get("under_odds"))
        if under:
            m_pick = market_prob(pick_other_odds, pick_odds)
        else:
            m_pick = market_prob(pick_odds, pick_other_odds)
        if not m_close or not m_pick:
            return out
        p_over_close_at_pick = m_close[0]
        if close_line != line:
            spread = _num(sd)
            if not spread or spread <= 0:
                spread = default_sd(stat, line)
            # stay on the books' no-push basis, like the pick-time price it is compared with
            p_over_close_at_pick = market_prob_at(stat, close_line, m_close[0], spread, line, p_push=0.0)
        p_close = (1.0 - p_over_close_at_pick) if under else p_over_close_at_pick
        p_pick = m_pick[1] if under else m_pick[0]
        out["clv_prob"] = round(float(p_close) - float(p_pick), 4)
    except Exception as e:
        logger.debug(f"clv_prob not computed ({type(e).__name__}: {e})")
    return out
