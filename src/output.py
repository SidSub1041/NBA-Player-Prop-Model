"""
Output formatting for prop model results.
Writes a daily report to logs/ and prints to stdout.
Also produces a JSON file consumed by the Vercel web dashboard.
"""

import json
import logging
import os
from datetime import datetime, timezone
from tabulate import tabulate

from src.config import CONDITION_PASS_RATE, CONDITION_PASS_RATE_POINTS, HITRATE_THRESHOLD, season_for_date, slate_today
from src.season import stats_season, stats_season_reason

logger = logging.getLogger(__name__)

LOGS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")


def format_report(prop_scores: list, date: str | None = None) -> str:
    """Build the full daily report string."""
    if date is None:
        date = slate_today()

    valid = [p for p in prop_scores if p.is_valid]
    on_hold = [p for p in prop_scores if getattr(p, "on_hold", False)]
    watchlist = [
        p for p in prop_scores
        if not p.is_valid and not getattr(p, "on_hold", False) and p.final_grade in ("B+", "B")
    ]

    lines = [
        "",
        "=" * 72,
        f"  NBA PLAYER PROP MODEL — {date}",
        f"  Run at: {datetime.now().strftime('%I:%M %p')}",
        "=" * 72,
        "",
        f"  Candidates analyzed : {len(prop_scores)}",
        f"  Valid picks (A/A+)  : {len(valid)}",
        f"  On hold (no line)   : {len(on_hold)}",
        f"  Watchlist (B/B+)    : {len(watchlist)}",
        f"  Thresholds          : >={CONDITION_PASS_RATE:.0%} conditions (points {CONDITION_PASS_RATE_POINTS:.0%}) | >={HITRATE_THRESHOLD:.0%} hit rate",
        "",
    ]

    headers = [
        "Player", "Team", "Opp", "Pos", "Stat",
        "Edge", "Line", "Odds", "Score", "Pct", "HR", "Grade"
    ]

    # ── VALID PICKS ──────────────────────────────────────────────────
    if valid:
        lines.append("┌─────────────────────────────────────────────────────────────────────┐")
        lines.append("│  ✅  VALID PICKS                                                     │")
        lines.append("└─────────────────────────────────────────────────────────────────────┘")
        lines.append("")

        table_data = []
        for p in sorted(valid, key=lambda x: x.total_score_pct, reverse=True):
            hr = p.hitrate_data.get("hitrate", -1)
            hr_str = f"{hr:.0%}" if hr >= 0 else "N/A"
            line_str = str(p.line) if p.line is not None else "-"
            odds = f"{p.side_odds()} {p.side_book()}".strip() if p.line is not None else "-"
            table_data.append([
                p.player_name,
                p.team,
                p.opponent,
                p.position,
                p.stat.upper(),
                p.edge.upper(),
                line_str,
                odds,
                f"{p.total_points}/{p.total_conditions}",
                f"{p.total_score_pct:.0%}",
                hr_str,
                p.final_grade,
            ])

        lines.append(tabulate(table_data, headers=headers, tablefmt="rounded_outline"))
        lines.append("")
    else:
        lines.append("  No valid picks today.\n")

    # ── ON HOLD ──────────────────────────────────────────────────────
    if on_hold:
        lines.append("┌─────────────────────────────────────────────────────────────────────┐")
        lines.append("│  ⏸  ON HOLD  (conditions met — waiting on a line; re-checked next run) │")
        lines.append("└─────────────────────────────────────────────────────────────────────┘")
        lines.append("")
        lines.append(tabulate(
            [[p.player_name, p.team, p.opponent, p.stat.upper(), p.edge.upper(),
              f"{p.total_points}/{p.total_conditions}", p.hold_reason or "Awaiting line"]
             for p in sorted(on_hold, key=lambda x: x.total_score_pct, reverse=True)],
            headers=["Player", "Team", "Opp", "Stat", "Edge", "Score", "Why"],
            tablefmt="rounded_outline",
        ))
        lines.append("")

    # ── WATCHLIST ────────────────────────────────────────────────────
    if watchlist:
        lines.append("┌─────────────────────────────────────────────────────────────────────┐")
        lines.append("│  👀  WATCHLIST  (60-79% conditions met)                              │")
        lines.append("└─────────────────────────────────────────────────────────────────────┘")
        lines.append("")

        table_data = []
        for p in sorted(watchlist, key=lambda x: x.total_score_pct, reverse=True):
            hr = p.hitrate_data.get("hitrate", -1)
            hr_str = f"{hr:.0%}" if hr >= 0 else "N/A"
            line_str = str(p.line) if p.line is not None else "-"
            odds = f"{p.side_odds()} {p.side_book()}".strip() if p.line is not None else "-"
            table_data.append([
                p.player_name,
                p.team,
                p.opponent,
                p.position,
                p.stat.upper(),
                p.edge.upper(),
                line_str,
                odds,
                f"{p.total_points}/{p.total_conditions}",
                f"{p.total_score_pct:.0%}",
                hr_str,
                p.final_grade,
            ])

        lines.append(tabulate(table_data, headers=headers, tablefmt="rounded_outline"))
        lines.append("")

    # ── DETAILED BREAKDOWNS ──────────────────────────────────────────
    top_picks = (valid + watchlist)[:10]
    if top_picks:
        lines.append("=" * 72)
        lines.append("  DETAILED BREAKDOWNS")
        lines.append("=" * 72)
        for p in top_picks:
            lines.append(p.detailed_report())

    lines.append("=" * 72)
    lines.append("  END OF REPORT")
    lines.append("=" * 72)
    lines.append("")

    return "\n".join(lines)


def save_report(report: str, date: str | None = None):
    """Save report to logs directory."""
    if date is None:
        date = slate_today()

    os.makedirs(LOGS_DIR, exist_ok=True)
    path = os.path.join(LOGS_DIR, f"props_{date}.txt")

    with open(path, "w") as f:
        f.write(report)

    logger.info(f"Report saved to {path}")
    return path


def print_report(report: str):
    """Print the report to stdout."""
    print(report)



def _eastern_label(now_utc: datetime) -> str:
    """Human label for the run time in US Eastern, e.g. '09:15 AM ET'."""
    try:
        from zoneinfo import ZoneInfo
        return now_utc.astimezone(ZoneInfo("America/New_York")).strftime("%I:%M %p ET")
    except Exception:
        return now_utc.strftime("%I:%M %p UTC")


COMBO_STATS = {"pts+ast", "pts+reb", "reb+ast", "pra"}


def _prop_to_dict(p) -> dict:
    """Convert a PropScore object to a JSON-serializable dict."""
    hr = p.hitrate_data.get("hitrate", -1)
    return {
        "player_name": p.player_name,
        "team": p.team,
        "opponent": p.opponent,
        "position": p.position,
        "stat": p.stat,
        "edge": p.edge,
        "total_points": p.total_points,
        "total_conditions": p.total_conditions,
        "pass_rate": round(p.total_score_pct, 3),
        "hitrate": round(hr, 3) if hr >= 0 else None,
        "hitrate_source": p.hitrate_data.get("source", "N/A"),
        "season_avg": p.hitrate_data.get("season_avg"),
        "games_played": p.hitrate_data.get("games_played"),
        "games_hit": p.hitrate_data.get("games_hit"),
        # the designated line: sportsbooks first, Underdog backup (src/lines.py)
        "line": p.line,
        "line_source": (p.line_info or {}).get("source"),
        "line_book": p.side_book() or None,
        "line_books": (p.line_info or {}).get("books"),
        "over_odds": (p.line_info or {}).get("over_odds"),
        "over_book": (p.line_info or {}).get("over_book"),
        "under_odds": (p.line_info or {}).get("under_odds"),
        "under_book": (p.line_info or {}).get("under_book"),
        # Underdog's own line when it has one (backup source; shown for comparison)
        "ud_line": p.ud_line,
        "ud_over_odds": p.ud_over_odds or None,
        "ud_under_odds": p.ud_under_odds or None,
        "vegas_line": (getattr(p, "vegas", None) or {}).get("line"),
        "vegas_over_odds": (getattr(p, "vegas", None) or {}).get("over_odds"),
        "vegas_under_odds": (getattr(p, "vegas", None) or {}).get("under_odds"),
        "vegas_book": ((getattr(p, "vegas", None) or {}).get("under_book") if p.edge == "under"
                       else (getattr(p, "vegas", None) or {}).get("over_book")),
        "vegas_books": (getattr(p, "vegas", None) or {}).get("books"),
        "p_hit": (getattr(p, "learned", None) or {}).get("p_hit"),
        "ev": (getattr(p, "learned", None) or {}).get("ev"),
        "breakeven": (getattr(p, "learned", None) or {}).get("breakeven"),
        "learn_note": getattr(p, "learn_note", "") or None,
        "on_hold": bool(getattr(p, "on_hold", False)),
        "hold_reason": getattr(p, "hold_reason", "") or None,
        "injury": getattr(p, "injury", None),
        "grade": p.final_grade,
        "is_valid": p.is_valid,
        "is_combo": p.stat in COMBO_STATS,
        "adaptive_multiplier": round(p.adaptive_multiplier, 4),
        "zone_details": p.zone_score.get("details", []),
        "playtype_details": p.playtype_score.get("details", []),
    }


def save_json_report(prop_scores: list, date: str | None = None) -> str:
    """Save model results as JSON for the web dashboard."""
    if date is None:
        date = slate_today()

    # Separate individual and combo props
    individual = [p for p in prop_scores if p.stat not in COMBO_STATS]
    combos = [p for p in prop_scores if p.stat in COMBO_STATS]

    def _held(p):
        return bool(getattr(p, "on_hold", False))

    def _by_ev(ps):   # best expected value first — only once the learner is ACTIVE (shadow mode changes nothing)
        def key(p):
            l = getattr(p, "learned", None) or {}
            ev = l.get("ev")
            return -ev if (l.get("active") and ev is not None) else 9.0
        return sorted(ps, key=key)

    valid = _by_ev([p for p in individual if p.is_valid])
    watchlist = [p for p in individual if not p.is_valid and not _held(p) and p.final_grade in ("B+", "B")]
    valid_combos = _by_ev([p for p in combos if p.is_valid])
    watchlist_combos = [p for p in combos if not p.is_valid and not _held(p) and p.final_grade in ("B+", "B")]
    on_hold = [p for p in prop_scores if _held(p)]

    # Hitrate summary across all evaluated individual props
    all_hr = [p.hitrate_data.get("hitrate", -1) for p in individual]
    valid_hr = [h for h in all_hr if h >= 0]
    hitrate_summary = {
        "avg": round(sum(valid_hr) / len(valid_hr), 3) if valid_hr else None,
        "above_threshold": sum(1 for h in valid_hr if h >= HITRATE_THRESHOLD),
        "below_threshold": sum(1 for h in valid_hr if h < HITRATE_THRESHOLD),
        "unavailable": sum(1 for h in all_hr if h < 0),
        "threshold": HITRATE_THRESHOLD,
        "distribution": {
            "60_plus": sum(1 for h in valid_hr if h >= 0.60),
            "56_to_60": sum(1 for h in valid_hr if 0.56 <= h < 0.60),
            "50_to_56": sum(1 for h in valid_hr if 0.50 <= h < 0.56),
            "below_50": sum(1 for h in valid_hr if h < 0.50),
        },
    }

    now_utc = datetime.now(timezone.utc)
    payload = {
        "date": date,
        "season": season_for_date(datetime.strptime(date, "%Y-%m-%d").date()),
        "stats_season": stats_season(),
        "stats_season_reason": stats_season_reason(),
        "run_at": _eastern_label(now_utc),
        "run_at_iso": now_utc.isoformat(timespec="seconds"),
        "candidates_analyzed": len(individual),
        "valid_count": len(valid),
        "watchlist_count": len(watchlist),
        "combo_valid_count": len(valid_combos),
        "combo_watchlist_count": len(watchlist_combos),
        "on_hold_count": len(on_hold),
        "demoted_count": sum(1 for p in prop_scores if getattr(p, "learn_note", "")),
        "thresholds": {
            "condition_pass_rate": CONDITION_PASS_RATE,
            "condition_pass_rate_points": CONDITION_PASS_RATE_POINTS,
            "hitrate": HITRATE_THRESHOLD,
        },
        "valid_picks": [_prop_to_dict(p) for p in valid],
        "watchlist": [_prop_to_dict(p) for p in watchlist],
        "valid_combos": [_prop_to_dict(p) for p in valid_combos],
        "watchlist_combos": [_prop_to_dict(p) for p in watchlist_combos],
        "on_hold": [_prop_to_dict(p) for p in on_hold],
        "demoted": [_prop_to_dict(p) for p in prop_scores if getattr(p, "learn_note", "")],
        "all_props": [_prop_to_dict(p) for p in prop_scores],
        "hitrate_summary": hitrate_summary,
    }
    try:
        from src.learner import summary as learner_summary
        payload["learning"] = learner_summary()
    except Exception as e:
        logger.warning(f"Learning summary unavailable ({e})")

    # Load existing model results history if available
    web_data_dir = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "web", "public", "data"
    )
    results_history_path = os.path.join(web_data_dir, "results_history.json")
    payload["model_results"] = []
    if os.path.exists(results_history_path):
        try:
            with open(results_history_path) as f:
                payload["model_results"] = json.load(f)
        except (OSError, ValueError) as e:
            logger.warning(f"Could not read results history ({e}); embedding none")

    # Save to logs/
    os.makedirs(LOGS_DIR, exist_ok=True)
    path = os.path.join(LOGS_DIR, f"props_{date}.json")
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)
    logger.info(f"JSON report saved to {path}")

    # Also save as latest.json for the web dashboard
    os.makedirs(web_data_dir, exist_ok=True)
    latest_path = os.path.join(web_data_dir, "latest.json")
    with open(latest_path, "w") as f:
        json.dump(payload, f, indent=2)
    logger.info(f"Web data saved to {latest_path}")

    return path
