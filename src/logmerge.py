"""
Merge two versions of a day's price cache (logs/vegas_{date}.json) or line
history (logs/lines_{date}.json).

Snapshot runs and board runs are separate GitHub Actions jobs that can finish
close together; both rewrite these two files. When the second one rebases onto
the first, git cannot merge JSON, so the commit script (scripts/commit_data.sh)
calls this module on each conflicted file instead of failing the run:

  price cache   for every (game, market), the version that bought it last is
                authoritative for that market's lines (so a line the books
                pulled is not resurrected by the older copy); events and market
                stamps are unioned, newest stamp kept; anything else, newest
                fetched_at wins.
  line history  per prop, the union of both snapshot lists in time order, with
                consecutive identical prices collapsed (as record() does).

Usage during a conflicted rebase:  python -m src.logmerge logs/vegas_2026-10-27.json
(reads git's stage 2 and stage 3 copies and writes the merged file in place).
"""

import json
import os
import subprocess
import sys

# stat -> The Odds API market key (same as src.vegas_odds.MARKETS; kept here so
# the merge runs with nothing but the standard library during a rebase)
MARKETS = {
    "points": "player_points", "rebounds": "player_rebounds", "assists": "player_assists",
    "pra": "player_points_rebounds_assists", "pts+reb": "player_points_rebounds",
    "pts+ast": "player_points_assists", "reb+ast": "player_rebounds_assists",
    "3-pointers made": "player_threes",
}


def _newer(a, b):
    """The later of two ISO stamps (None-safe; same format, so text order is time order)."""
    if not a:
        return b
    if not b:
        return a
    return a if str(a) >= str(b) else b


def merge_cache(a: dict, b: dict) -> dict:
    a, b = a or {}, b or {}
    events: dict = {}
    for src in (a.get("events") or {}, b.get("events") or {}):
        for ev_id, ev in src.items():
            cur = events.setdefault(ev_id, {**{k: v for k, v in ev.items() if k != "markets"}, "markets": {}})
            for k, v in ev.items():
                if k != "markets" and v is not None:
                    cur.setdefault(k, v)
            for market, stamp in (ev.get("markets") or {}).items():
                cur["markets"][market] = _newer(cur["markets"].get(market), stamp)

    def owner(ev_id, stat):
        """Which version bought this (game, market) last: 'a', 'b' or None."""
        market = MARKETS.get(stat)
        sa = ((a.get("events") or {}).get(ev_id) or {}).get("markets", {}).get(market)
        sb = ((b.get("events") or {}).get(ev_id) or {}).get("markets", {}).get(market)
        if not sa and not sb:
            return None
        return "a" if _newer(sa, sb) == sa and sa else "b"

    lines: dict = {}
    la, lb = a.get("lines") or {}, b.get("lines") or {}
    for key in set(la) | set(lb):
        ea, eb = la.get(key), lb.get(key)
        entry = ea or eb
        who = owner(entry.get("event_id"), entry.get("stat")) if isinstance(entry, dict) else None
        if who == "a":
            if ea is not None:
                lines[key] = ea
        elif who == "b":
            if eb is not None:
                lines[key] = eb
        else:
            pick = ea if (eb is None or (ea is not None and _newer(ea.get("fetched_at"), eb.get("fetched_at")) == ea.get("fetched_at"))) else eb
            lines[key] = pick
    return {"date": a.get("date") or b.get("date"), "events": events, "lines": lines}


def _price(s: dict) -> tuple:
    return (s.get("line"), s.get("over_odds"), s.get("under_odds"), s.get("tier"))


def merge_history(a: dict, b: dict) -> dict:
    a, b = a or {}, b or {}
    snaps: dict = {}
    sa, sb = a.get("snapshots") or {}, b.get("snapshots") or {}
    for key in set(sa) | set(sb):
        seen, merged = set(), []
        for s in sorted(list(sa.get(key) or []) + list(sb.get(key) or []), key=lambda s: str(s.get("t") or "")):
            ident = (s.get("t"),) + _price(s)
            if ident in seen:
                continue
            seen.add(ident)
            if merged and _price(merged[-1]) == _price(s):
                continue      # unchanged price: record() never stores it twice in a row
            merged.append(s)
        snaps[key] = merged
    return {"date": a.get("date") or b.get("date"), "snapshots": snaps}


def merge_texts(path: str, ours: str, theirs: str) -> dict:
    a = json.loads(ours) if ours.strip() else {}
    b = json.loads(theirs) if theirs.strip() else {}
    name = os.path.basename(path)
    if name.startswith("vegas_"):
        return merge_cache(a, b)
    if name.startswith("lines_"):
        return merge_history(a, b)
    raise ValueError(f"don't know how to merge {path}")


def _stage(n: int, path: str) -> str:
    r = subprocess.run(["git", "show", f":{n}:{path}"], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def main(argv: list[str]) -> int:
    for path in argv:
        merged = merge_texts(path, _stage(2, path), _stage(3, path))
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(merged, f, indent=1, sort_keys=True)
        os.replace(tmp, path)
        print(f"merged {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
