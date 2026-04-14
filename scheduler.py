"""
Daily scheduler for the NBA Player Prop Model.
Runs the model every morning at a configurable time (default 9:00 AM).

Usage:
    # Run with default 9:00 AM schedule:
    python scheduler.py

    # Run at a specific time (e.g. 8:30 AM):
    python scheduler.py --time 08:30

    # Run immediately once then continue on schedule:
    python scheduler.py --run-now

Keep this process running (e.g. via nohup, tmux, or a system service).
The setup_cron.sh script installs a system cron job instead if preferred.
"""

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timedelta

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def run_model(fast: bool = False):
    """Import and run the main model pipeline."""
    logger.info("Triggering NBA Prop Model run...")
    try:
        from main import run
        run(fast=fast)
    except Exception as e:
        logger.error(f"Model run failed: {e}", exc_info=True)


def run_grader():
    """Import and run the grading pipeline."""
    logger.info("Triggering grading run...")
    try:
        from grade import main as grade_main
        grade_main()
    except Exception as e:
        logger.error(f"Grading run failed: {e}", exc_info=True)


def next_run_at(target_time_str: str) -> datetime:
    """
    Compute the next datetime when we should run, given a HH:MM target time.
    If that time has already passed today, schedules for tomorrow.
    """
    now = datetime.now()
    h, m = map(int, target_time_str.split(":"))
    candidate = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def loop(target_time: str = "09:00", grade_time: str = "23:00", fast: bool = False, run_now: bool = False):
    """Main scheduler loop — runs model at target_time and grader at grade_time."""
    logger.info(f"Scheduler started. Model at {target_time}, Grading at {grade_time}.")

    if run_now:
        logger.info("--run-now flag set: running model immediately.")
        run_model(fast=fast)

    while True:
        next_model = next_run_at(target_time)
        next_grade = next_run_at(grade_time)

        if next_model <= next_grade:
            next_run = next_model
            task = "model"
        else:
            next_run = next_grade
            task = "grader"

        wait_secs = (next_run - datetime.now()).total_seconds()
        logger.info(
            f"Next task: {task} at {next_run.strftime('%Y-%m-%d %H:%M')} "
            f"({wait_secs/3600:.1f} hours from now)"
        )

        # Sleep in chunks so we can react to signals / keyboard interrupts
        while (remaining := (next_run - datetime.now()).total_seconds()) > 0:
            time.sleep(min(remaining, 60))

        if task == "model":
            run_model(fast=fast)
        else:
            run_grader()


def main():
    parser = argparse.ArgumentParser(
        description="Keeps the NBA Prop Model running on a daily schedule."
    )
    parser.add_argument(
        "--time",
        default="09:00",
        help="Daily run time in HH:MM (24-hour). Default: 09:00",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Pass --fast to the model (skip playtype rankings).",
    )
    parser.add_argument(
        "--run-now",
        action="store_true",
        help="Run the model immediately on startup, then continue on schedule.",
    )
    parser.add_argument(
        "--grade-time",
        default="23:00",
        help="Daily grading time in HH:MM (24-hour). Default: 23:00",
    )
    args = parser.parse_args()

    try:
        loop(target_time=args.time, grade_time=args.grade_time, fast=args.fast, run_now=args.run_now)
    except KeyboardInterrupt:
        logger.info("Scheduler stopped by user.")


if __name__ == "__main__":
    main()
