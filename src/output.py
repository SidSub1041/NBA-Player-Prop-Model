"""
Output formatting for prop model results.
Writes a daily report to logs/ and prints to stdout.
"""

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
            table_data.append([
                p.player_name,
                p.team,
                p.opponent,
                p.position,
                p.stat.upper(),
                p.edge.upper(),
                f"{p.total_points}/{p.total_conditions}",
                f"{p.total_score_pct:.0%}",
                hr_str,
                p.final_grade,
            ])

        headers = [
            "Player", "Team", "Opp", "Pos", "Stat",
            "Edge", "Score", "Pct", "HR", "Grade"
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
            table_data.append([
                p.player_name,
                p.team,
                p.opponent,
                p.position,
                p.stat.upper(),
                p.edge.upper(),
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
