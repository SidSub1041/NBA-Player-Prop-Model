"""
Post-game grading engine.

After games finish, this module:
  1. Loads that day's picks from logs/props_{date}.json
  2. Fetches actual box-score stats from NBA API
  3. Compares each pick (over/under on a line) to reality
  4. Checks Underdog API for voided lines (injury / DNP)
  5. Writes graded results back and updates model_results history
"""

import json
import logging
import os
import time
from datetime import datetime

from nba_api.stats.endpoints import boxscoretraditionalv3, scoreboardv3
from nba_api.stats.static import players as nba_players

from src.config import NBA_TEAM_IDS

logger = logging.getLogger(__name__)

LOGS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
WEB_DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "web", "public", "data"
)
RESULTS_FILE = os.path.join(WEB_DATA_DIR, "results_history.json")

STAT_COL = {"points": "pts", "rebounds": "reb", "assists": "ast"}


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


def _fetch_box_scores(date: str) -> dict[str, dict]:
    """
    Fetch box-score stats for every player who played on *date*.

    Returns: {player_name_lower: {"pts": int, "reb": int, "ast": int, "min": float, "dnp": bool}}
    """
    game_ids = _fetch_game_ids(date)
    player_stats: dict[str, dict] = {}

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
            logger.warning(f"Box-score fetch failed for game {gid}: {e}")

    logger.info(f"Fetched box-score data for {len(player_stats)} players")
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


# ── Check for voided Underdog lines ──────────────────────────────────────────

def _fetch_voided_players() -> set[str]:
    """
    Check Underdog Fantasy API for voided/suspended lines.
    Returns set of lowercased player names whose lines are voided.
    """
    import requests

    try:
        resp = requests.get(
            "https://api.underdogfantasy.com/beta/v5/over_under_lines",
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning(f"Could not check Underdog for voided lines: {e}")
        return set()

    players_map = {p["id"]: p for p in data.get("players", [])}
    appearances = {a["id"]: a for a in data.get("appearances", [])}
    voided = set()

    for line in data.get("over_under_lines", []):
        status = line.get("status", "")
        if status in ("voided", "suspended", "cancelled"):
            ou = line.get("over_under", {})
            app_stat = ou.get("appearance_stat", {})
            app_id = app_stat.get("appearance_id")
            appearance = appearances.get(app_id, {})
            player_id = appearance.get("player_id")
            player = players_map.get(player_id, {})
            first = player.get("first_name", "")
            last = player.get("last_name", "")
            name = f"{first} {last}".strip()
            if name:
                voided.add(name.lower())

    logger.info(f"Found {len(voided)} voided Underdog lines")
    return voided


# ── Grade picks ──────────────────────────────────────────────────────────────

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
        "graded_picks": [...]
      }
    """
    # Load picks
    json_path = os.path.join(LOGS_DIR, f"props_{date}.json")
    if not os.path.exists(json_path):
        logger.error(f"No picks file found at {json_path}")
        return {}

    with open(json_path) as f:
        props_data = json.load(f)

    valid_picks = props_data.get("valid_picks", [])
    if not valid_picks:
        logger.info(f"No valid picks to grade for {date}")
        return {"date": date, "total_picks": 0, "hits": 0, "misses": 0, "voided": 0, "graded_picks": []}

    # Fetch actual stats
    logger.info(f"Fetching box-score data for {date}...")
    actual_stats = _fetch_box_scores(date)

    # Fetch voided lines
    voided_players = _fetch_voided_players()

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
        line = pick.get("ud_line")

        name_lower = name.lower()
        actual = actual_stats.get(name_lower)

        # Determine which odds apply to this pick's edge
        pick_odds = pick.get("ud_over_odds") if edge == "over" else pick.get("ud_under_odds")

        result_entry = {
            **pick,
            "actual_value": None,
            "result": "pending",
            "units": 0.0,
        }

        # Check if voided (Underdog suspended or player DNP)
        if name_lower in voided_players:
            result_entry["result"] = "void"
            voided_count += 1
            graded.append(result_entry)
            logger.info(f"  {name} | {stat} {edge} → VOID (Underdog voided)")
            continue

        if actual is None:
            # Player not in box scores at all (DNP / inactive)
            result_entry["result"] = "void"
            voided_count += 1
            graded.append(result_entry)
            logger.info(f"  {name} | {stat} {edge} → VOID (not in box scores)")
            continue

        if actual.get("dnp"):
            result_entry["result"] = "void"
            voided_count += 1
            graded.append(result_entry)
            logger.info(f"  {name} | {stat} {edge} → VOID (0 minutes played)")
            continue

        # Get actual stat value
        stat_key = STAT_COL.get(stat)
        if not stat_key or stat_key not in actual:
            result_entry["result"] = "void"
            voided_count += 1
            graded.append(result_entry)
            continue

        actual_value = actual[stat_key]
        result_entry["actual_value"] = actual_value

        # Determine if pick hit
        if line is not None:
            if edge == "over":
                hit = actual_value > line
            else:
                hit = actual_value < line
        else:
            # No line available — use season average as proxy
            season_avg = pick.get("season_avg")
            if season_avg is not None:
                if edge == "over":
                    hit = actual_value > season_avg
                else:
                    hit = actual_value < season_avg
            else:
                result_entry["result"] = "void"
                voided_count += 1
                graded.append(result_entry)
                continue

        if hit:
            profit = round(_american_odds_to_profit(pick_odds), 3)
            result_entry["result"] = "hit"
            result_entry["units"] = profit
            units += profit
            hits += 1
            logger.info(
                f"  ✅ {name} | {stat} {edge} {line or 'avg'} → "
                f"Actual: {actual_value} — HIT (+{profit}u)"
            )
        else:
            result_entry["result"] = "miss"
            result_entry["units"] = -1.0
            units -= 1.0
            misses += 1
            logger.info(
                f"  ❌ {name} | {stat} {edge} {line or 'avg'} → "
                f"Actual: {actual_value} — MISS (-1u)"
            )

        graded.append(result_entry)

    day_result = {
        "date": date,
        "total_picks": hits + misses,  # excludes voided
        "hits": hits,
        "misses": misses,
        "voided": voided_count,
        "units": round(units, 3),
        "graded_picks": graded,
    }

    logger.info(
        f"Grading complete: {hits} hits, {misses} misses, "
        f"{voided_count} voided, {units:+.2f}u out of {len(valid_picks)} picks"
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
        "total_picks": day_result["total_picks"],
        "hits": day_result["hits"],
        "misses": day_result["misses"],
        "voided": day_result.get("voided", 0),
        "units": day_result.get("units", 0.0),
    }

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
