"""
CSV Exporter — flattens the model's JSON output into tidy CSV fact tables
for BI tools (Tableau, Power BI, Google Sheets).

Reads:
    logs/props_YYYY-MM-DD.json          (daily model runs)
    logs/graded_YYYY-MM-DD.json         (post-game graded results)
    web/public/data/results_history.json (daily win/loss summary)

Writes:
    web/public/data/picks.csv           one row per prop per day
    web/public/data/graded_picks.csv    one row per graded valid pick
    web/public/data/daily_results.csv   one row per graded day

All paths are anchored to this file's location, so the script can be run
from any working directory:
    python export_csv.py
"""

import csv
import glob
import json
import os
import re
from datetime import date as _date

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOGS_DIR = os.path.join(BASE_DIR, "logs")
DATA_DIR = os.path.join(BASE_DIR, "web", "public", "data")

DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _season(date_str):
    """2026-10-20 -> '2026-27' (seasons start in October)."""
    try:
        d = _date.fromisoformat(date_str)
    except (TypeError, ValueError):
        return None
    start = d.year if d.month >= 10 else d.year - 1
    return f"{start}-{(start + 1) % 100:02d}"

PICK_FIELDS = [
    "date", "player_name", "team", "opponent", "position", "stat", "edge",
    "grade", "bucket", "is_valid", "is_combo", "total_points",
    "total_conditions", "pass_rate", "hitrate", "hitrate_source",
    "season_avg", "games_played", "ud_line", "ud_over_odds", "ud_under_odds",
    "adaptive_multiplier", "n_zone_conditions", "n_playtype_conditions",
    "details", "season",
]

GRADED_FIELDS = PICK_FIELDS + ["actual_value", "result", "units"]

RESULT_FIELDS = ["date", "total_picks", "hits", "misses", "voided", "units", "season"]


def _load_days(prefix):
    """
    Load every logs/<prefix>_*.json as (date, day_dict), sorted by date.

    The date comes from the file's own "date" key, falling back to the
    YYYY-MM-DD in the filename; files with neither are skipped rather
    than emitting garbage dates.
    """
    days = []
    for path in sorted(glob.glob(os.path.join(LOGS_DIR, f"{prefix}_*.json"))):
        try:
            with open(path, encoding="utf-8") as f:
                day = json.load(f)
        except (OSError, ValueError) as e:
            print(f"  Skipping {path} — unreadable JSON ({e})")
            continue
        if not isinstance(day, dict):
            print(f"  Skipping {path} — expected a JSON object")
            continue
        date = day.get("date")
        if not date:
            m = DATE_RE.search(os.path.basename(path))
            date = m.group(1) if m else None
        if not date:
            print(f"  Skipping {path} — no date in file or filename")
            continue
        days.append((date, day))
    return days


def _bucket(prop):
    """Classify a prop the same way the report sections do."""
    combo = "_combo" if prop.get("is_combo") else ""
    if prop.get("is_valid"):
        return "valid" + combo
    if prop.get("grade") in ("B+", "B"):
        return "watchlist" + combo
    return "other" + combo


def _flatten_prop(prop, date):
    """One prop dict -> one flat CSV row. Tolerates older-schema files."""
    zones = prop.get("zone_details") or []
    plays = prop.get("playtype_details") or []
    details = " | ".join(s.strip() for s in (zones + plays) if s and s.strip())
    # Combo props carry a single "Combo: ..." summary line in zone_details;
    # it is not a scored condition, so keep it out of the condition counts.
    n_zones = sum(1 for s in zones if s and "Combo:" not in s)
    n_plays = sum(1 for s in plays if s and "Combo:" not in s)
    return {
        "date": date,
        "player_name": prop.get("player_name"),
        "team": prop.get("team"),
        "opponent": prop.get("opponent"),
        "position": prop.get("position"),
        "stat": prop.get("stat"),
        "edge": prop.get("edge"),
        "grade": prop.get("grade"),
        "bucket": _bucket(prop),
        "is_valid": prop.get("is_valid", False),
        "is_combo": prop.get("is_combo", False),
        "total_points": prop.get("total_points"),
        "total_conditions": prop.get("total_conditions"),
        "pass_rate": prop.get("pass_rate"),
        "hitrate": prop.get("hitrate"),
        "hitrate_source": prop.get("hitrate_source"),
        "season_avg": prop.get("season_avg"),
        "games_played": prop.get("games_played"),
        "ud_line": prop.get("ud_line"),
        "ud_over_odds": prop.get("ud_over_odds"),
        "ud_under_odds": prop.get("ud_under_odds"),
        "adaptive_multiplier": prop.get("adaptive_multiplier"),
        "n_zone_conditions": n_zones,
        "n_playtype_conditions": n_plays,
        "details": details,
        "season": _season(date),
    }


def _write_csv(path, fieldnames, rows):
    # utf-8-sig: the BOM keeps accented player names (Jokić, Dončić)
    # intact when the CSV is opened in Excel or legacy BI importers.
    # Write to a temp file and swap it in, so a crash mid-write never leaves a
    # truncated CSV behind for the workflow to commit.
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, path)
    print(f"  Wrote {len(rows):4d} rows -> {path}")


def export_picks():
    rows = []
    for date, day in _load_days("props"):
        for prop in day.get("all_props", []):
            rows.append(_flatten_prop(prop, date))
    _write_csv(os.path.join(DATA_DIR, "picks.csv"), PICK_FIELDS, rows)


def export_graded(graded_days):
    rows = []
    for date, day in graded_days:
        for pick in day.get("graded_picks", []):
            row = _flatten_prop(pick, date)
            row["actual_value"] = pick.get("actual_value")
            row["result"] = pick.get("result")
            row["units"] = pick.get("units")
            rows.append(row)
    _write_csv(os.path.join(DATA_DIR, "graded_picks.csv"), GRADED_FIELDS, rows)


def export_daily_results(graded_days):
    # Build from graded files (complete), then backfill any day that only
    # exists in results_history.json.
    def _daily_row(date, src):
        row = {k: src.get(k) for k in RESULT_FIELDS}
        row["date"] = date
        for k in ("total_picks", "hits", "misses", "voided"):
            if row[k] is None:
                row[k] = 0
        if row["units"] is None:
            row["units"] = 0.0
        row["season"] = row.get("season") or _season(date)
        return row

    by_date = {}
    for date, day in graded_days:
        by_date[date] = _daily_row(date, day)

    history_path = os.path.join(DATA_DIR, "results_history.json")
    history = []
    if os.path.exists(history_path):
        try:
            with open(history_path, encoding="utf-8") as f:
                history = json.load(f)
        except (OSError, ValueError) as e:
            print(f"  Skipping {history_path} — unreadable JSON ({e})")
    if isinstance(history, list):
        for entry in history:
            date = entry.get("date")
            if not date:
                continue
            by_date.setdefault(date, _daily_row(date, entry))

    rows = [by_date[d] for d in sorted(by_date)]
    _write_csv(os.path.join(DATA_DIR, "daily_results.csv"), RESULT_FIELDS, rows)


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    print("Exporting CSV fact tables...")
    export_picks()
    graded_days = _load_days("graded")
    export_graded(graded_days)
    export_daily_results(graded_days)
    print("Done.")


if __name__ == "__main__":
    main()
