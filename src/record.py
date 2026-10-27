"""
The season's record, cut the ways the site shows it.

Built from logs/graded_{date}.json for the current season on every run (model
and grading): the overall record, the record by grade, by side (over/under),
by stat and single vs combo, the best and latest nights, the current streak,
and "highlights" — the cuts that are ahead of break-even (52.4%, the hit rate
a −110 price needs) or in profit, on at least MIN_SAMPLE decided picks.

Every number is what the grader recorded; nothing is reweighted. Sample sizes
travel with every cut so a 6–2 segment reads as 6–2, not as "75%".

Since round 18 the record also carries the season's closing-line value (CLV):
over the decided picks the grader found a close for, the mean line CLV (points
in the pick's favour between the line taken and the line at tip-off), the
share that beat the close and the mean probability CLV — the only evidence of
an edge that doesn't need the result to land.
"""

import glob
import json
import os
from datetime import datetime

from src.config import season_for_date

LOGS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
BREAKEVEN = 0.524       # hit rate a −110 price needs to profit
MIN_SAMPLE = 8          # decided picks before a cut can be a highlight
MAX_HIGHLIGHTS = 4

STAT_LABELS = {"points": "Points", "rebounds": "Rebounds", "assists": "Assists", "pra": "PRA",
               "pts+reb": "PTS+REB", "pts+ast": "PTS+AST", "reb+ast": "REB+AST", "3-pointers made": "3PM"}


def _empty(label: str) -> dict:
    return {"label": label, "hits": 0, "misses": 0, "units": 0.0}


def _finish(seg: dict) -> dict:
    n = seg["hits"] + seg["misses"]
    seg["n"] = n
    seg["rate"] = round(seg["hits"] / n, 3) if n else None
    seg["units"] = round(seg["units"], 2)
    seg["record"] = f"{seg['hits']}–{seg['misses']}"
    return seg


def _load_nights(season: str, logs_dir: str) -> list[dict]:
    """One night per date, in date order; a later file for the same date replaces an earlier one (as the grader's history does)."""
    by_date: dict[str, dict] = {}
    for path in sorted(glob.glob(os.path.join(logs_dir, "graded_*.json"))):
        try:
            with open(path, encoding="utf-8") as f:
                g = json.load(f)
        except (OSError, ValueError):
            continue
        date = str(g.get("date") or os.path.basename(path)[7:17])
        try:
            night_season = g.get("season") or season_for_date(datetime.strptime(date, "%Y-%m-%d").date())
        except ValueError:
            continue
        if night_season != season:
            continue
        decided = [p for p in (g.get("graded_picks") or []) if isinstance(p, dict) and p.get("result") in ("hit", "miss")]
        if not decided and not g.get("voided"):
            continue
        by_date[date] = ({
            "date": date,
            "hits": sum(1 for p in decided if p["result"] == "hit"),
            "misses": sum(1 for p in decided if p["result"] == "miss"),
            "voided": int(g.get("voided") or 0),
            "units": sum(float(p.get("units") or 0) for p in decided),   # rounded only where shown
            "picks": decided,
        })
    return [by_date[d] for d in sorted(by_date)]


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def clv_record(picks: list[dict]) -> dict:
    """
    Closing-line value over the graded picks that have one (clv_line set):
    {"n", "avg_line" (mean points in the pick's favour vs the close),
     "beat_close" (share with clv_line > 0), "avg_prob" (mean probability
     CLV, None when no pick carried one)}; n is 0 when no pick had a close.
    """
    rows = [(float(_num(p.get("clv_line"))), _num(p.get("clv_prob"))) for p in picks
            if isinstance(p, dict) and _num(p.get("clv_line")) is not None]
    if not rows:
        return {"n": 0, "avg_line": None, "beat_close": None, "avg_prob": None}
    n = len(rows)
    probs = [pr for _, pr in rows if pr is not None]
    return {
        "n": n,
        "avg_line": round(sum(cl for cl, _ in rows) / n, 3),
        "beat_close": round(sum(1 for cl, _ in rows if cl > 0) / n, 3),
        "avg_prob": round(sum(probs) / len(probs), 4) if probs else None,
    }


def season_record(season: str, logs_dir: str | None = None) -> dict:
    """The season's graded record and its cuts (see module docstring)."""
    nights = _load_nights(season, logs_dir or LOGS_DIR)
    segs: dict[str, dict] = {}

    def add(key: str, label: str, p: dict) -> None:
        seg = segs.setdefault(key, _empty(label))
        seg["hits" if p["result"] == "hit" else "misses"] += 1
        seg["units"] += float(p.get("units") or 0)

    for night in nights:
        for p in night["picks"]:
            grade = str(p.get("grade") or "?")
            add(f"grade:{grade}", f"Grade {grade}", p)
            side = str(p.get("edge") or "").lower()
            if side in ("over", "under"):
                add(f"side:{side}", "Overs" if side == "over" else "Unders", p)
            combo = bool(p.get("is_combo")) or "+" in str(p.get("stat") or "") or p.get("stat") == "pra"
            add("kind:combo" if combo else "kind:single", "Combos" if combo else "Single stats", p)
            stat = str(p.get("stat") or "")
            if stat:
                add(f"stat:{stat}", STAT_LABELS.get(stat, stat.upper()), p)

    segments = {k: _finish(dict(v)) for k, v in segs.items()}
    hits = sum(n["hits"] for n in nights)
    misses = sum(n["misses"] for n in nights)
    units = round(sum(n["units"] for n in nights), 2)
    decided_nights = [n for n in nights if n["hits"] + n["misses"]]

    def night_view(n: dict | None) -> dict | None:
        if not n:
            return None
        d = n["hits"] + n["misses"]
        return {"date": n["date"], "hits": n["hits"], "misses": n["misses"], "units": round(n["units"], 2),
                "rate": round(n["hits"] / d, 3) if d else None, "record": f"{n['hits']}–{n['misses']}"}

    best = max(decided_nights, key=lambda n: (n["units"], n["hits"] - n["misses"]), default=None)
    last = decided_nights[-1] if decided_nights else None
    streak = 0   # +k: k winning nights in a row (units > 0); -k: losing nights; 0: none / flat
    for n in reversed(decided_nights):
        sign = 1 if n["units"] > 0 else -1 if n["units"] < 0 else 0
        if sign == 0 or (streak and sign != (1 if streak > 0 else -1)):
            break
        streak += sign

    highlights = sorted(
        (s for s in segments.values() if s["n"] >= MIN_SAMPLE and ((s["rate"] or 0) >= BREAKEVEN or s["units"] > 0)),
        key=lambda s: (-s["units"], -(s["rate"] or 0), -s["n"]),
    )[:MAX_HIGHLIGHTS]

    return {
        "season": season,
        "nights": len(decided_nights),
        "hits": hits, "misses": misses, "units": units,
        "rate": round(hits / (hits + misses), 3) if hits + misses else None,
        "record": f"{hits}–{misses}",
        "voided": sum(n["voided"] for n in nights),
        "breakeven": BREAKEVEN,
        "min_sample": MIN_SAMPLE,
        "segments": segments,
        "highlights": highlights,
        "best_night": night_view(best),
        "last_night": night_view(last),
        "streak": streak,
        "clv": clv_record([p for n in nights for p in n["picks"]]),
    }
