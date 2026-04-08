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
        date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    logging.info(f"Grading picks for {date}")
    result = grade_picks(date)

    if not result:
        logging.error(f"No results to save for {date}")
        return

    save_graded_results(result)

    total = result["total_picks"]
    hits = result["hits"]
    voided = result.get("voided", 0)
    rate = f"{hits/total*100:.1f}%" if total > 0 else "N/A"
    print(f"\n{'='*50}")
    print(f"  Results for {date}")
    print(f"  Hits: {hits}  |  Misses: {result['misses']}  |  Voided: {voided}")
    print(f"  Success Rate: {rate} ({hits}/{total})")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    main()
