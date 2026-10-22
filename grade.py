#!/usr/bin/env python3
"""
CLI to grade a day's picks against actual NBA box-score results.

Usage:
    python grade.py                  # grade yesterday's picks
    python grade.py --date 2026-04-08  # grade a specific date
"""

import argparse
import logging
from datetime import datetime, timedelta

import src.nba_http  # noqa: F401  — timeouts, retries, proxy, circuit breaker
from src.config import slate_today
from src.grader import grade_picks, save_graded_results

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)


def main():
    parser = argparse.ArgumentParser(description="Grade model picks against results")
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="Date to grade (YYYY-MM-DD). Defaults to yesterday.",
    )
    args = parser.parse_args()

    if args.date:
        date = args.date
    else:
        date = (datetime.strptime(slate_today(), "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")

    logging.info(f"Grading picks for {date}")
    from src.grader import clear_box_cache
    clear_box_cache()
    result = grade_picks(date)

    if result:
        save_graded_results(result)
    else:
        logging.error(f"No results to save for {date}")

    # The learning loop: label every prop on last night's board (and any older
    # board not yet labeled), retrain, and write the post-mortem. A failure here
    # never loses the grading above.
    try:
        from src.grader import _fetch_box_scores, box_scores_complete
        from src.learner import nightly_update
        nightly_update(date, result or None, _fetch_box_scores, fetch_ok=box_scores_complete)
    except Exception as e:
        logging.warning(f"Learning loop skipped ({type(e).__name__}: {e})")

    if not result:
        return

    total = result["total_picks"]
    hits = result["hits"]
    voided = result.get("voided", 0)
    units = result.get("units", 0.0)
    rate = f"{hits/total*100:.1f}%" if total > 0 else "N/A"
    print(f"\n{'='*50}")
    print(f"  Results for {date}")
    print(f"  Hits: {hits}  |  Misses: {result['misses']}  |  Voided: {voided}")
    print(f"  Success Rate: {rate} ({hits}/{total})")
    print(f"  Units: {units:+.2f}u")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    main()
