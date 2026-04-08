"""
Output formatting for prop model results.
Writes a daily report to logs/ and prints to stdout.
Also produces a JSON file consumed by the Vercel web dashboard.
"""

import json
import logging
import os
from datetime import datetime
from tabulate import tabulate

from src.config import CONDITION_PASS_RATE, HITRATE_THRESHOLD

logger = logging.getLogger(__name__)

LOGS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")


def format_report(prop_scores: list, date: str | None = None) -> str:
    """Build the full daily report string."""
    if date is None:
        date = datetime.today().strftime("%Y-%m-%d")

    valid = [p for p in prop_scores if p.is_valid]
    watchlist = [
        p for p in prop_scores
        if not p.is_valid and p.total_score_pct >= 0.60
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
        f"  Watchlist (B/B+)    : {len(watchlist)}",
        f"  Thresholds          : >={CONDITION_PASS_RATE:.0%} conditions | >={HITRATE_THRESHOLD:.0%} hit rate",
        "",
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
            line_str = str(p.ud_line) if p.ud_line is not None else "-"
            odds = (p.ud_over_odds if p.edge == "over" else p.ud_under_odds) if p.ud_line is not None else "-"
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

        headers = [
            "Player", "Team", "Opp", "Pos", "Stat",
            "Edge", "Line", "Odds", "Score", "Pct", "HR", "Grade"
        ]
        lines.append(tabulate(table_data, headers=headers, tablefmt="rounded_outline"))
        lines.append("")
    else:
        lines.append("  No valid picks today.\n")

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
            line_str = str(p.ud_line) if p.ud_line is not None else "-"
            odds = (p.ud_over_odds if p.edge == "over" else p.ud_under_odds) if p.ud_line is not None else "-"
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
        date = datetime.today().strftime("%Y-%m-%d")

    os.makedirs(LOGS_DIR, exist_ok=True)
    path = os.path.join(LOGS_DIR, f"props_{date}.txt")

    with open(path, "w") as f:
        f.write(report)

    logger.info(f"Report saved to {path}")
    return path


def print_report(report: str):
    """Print the report to stdout."""
    print(report)


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
        "ud_line": p.ud_line,
        "ud_over_odds": p.ud_over_odds or None,
        "ud_under_odds": p.ud_under_odds or None,
        "grade": p.final_grade,
        "is_valid": p.is_valid,
        "zone_details": p.zone_score.get("details", []),
        "playtype_details": p.playtype_score.get("details", []),
    }


def save_json_report(prop_scores: list, date: str | None = None) -> str:
    """Save model results as JSON for the web dashboard."""
    if date is None:
        date = datetime.today().strftime("%Y-%m-%d")

    valid = [p for p in prop_scores if p.is_valid]
    watchlist = [p for p in prop_scores if not p.is_valid and p.total_score_pct >= 0.60]

    # Hitrate summary across all evaluated props
    all_hr = [p.hitrate_data.get("hitrate", -1) for p in prop_scores]
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

    payload = {
        "date": date,
        "run_at": datetime.now().strftime("%I:%M %p"),
        "run_at_iso": datetime.now().isoformat(),
        "candidates_analyzed": len(prop_scores),
        "valid_count": len(valid),
        "watchlist_count": len(watchlist),
        "thresholds": {
            "condition_pass_rate": CONDITION_PASS_RATE,
            "hitrate": HITRATE_THRESHOLD,
        },
        "valid_picks": [_prop_to_dict(p) for p in valid],
        "watchlist": [_prop_to_dict(p) for p in watchlist],
        "all_props": [_prop_to_dict(p) for p in prop_scores],
        "hitrate_summary": hitrate_summary,
    }

    # Load existing model results history if available
    web_data_dir = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "web", "public", "data"
    )
    results_history_path = os.path.join(web_data_dir, "results_history.json")
    if os.path.exists(results_history_path):
        with open(results_history_path) as f:
            payload["model_results"] = json.load(f)
    else:
        payload["model_results"] = []

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
