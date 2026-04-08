"""
NBA Player Prop Model — Main Orchestrator
==========================================

Methodology:
  1. Fetch today's games (NBA API)
  2. Scrape FantasyPros DvP (Defense vs Position) for highlighted matchups
  3. For each highlighted matchup, find the player at that position
     on the OPPOSING team (ESPN depth charts)
  4. Evaluate each player/prop via the scoring engine:
       - POINTS: shooting zone analysis + playtype analysis
       - REBOUNDS / ASSISTS: DvP signal + hit rate
  5. Check hit rate on linemate.com (>= 56% required)
  6. Output graded picks: A+/A = valid, B+/B = watchlist

Run manually:
    python main.py

Run for a specific date:
    python main.py --date 2026-04-08

Run in --fast mode (skips playtypes to reduce API calls):
    python main.py --fast
"""

import argparse
import logging
import os
import sys
import time
from datetime import datetime

# ── ensure logs directory exists before logging setup ────────────────────────
os.makedirs("logs", exist_ok=True)

# ── logging setup ────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            f"logs/model_{datetime.today().strftime('%Y-%m-%d')}.log",
            mode="a",
            encoding="utf-8",
        ),
    ],
)
logger = logging.getLogger(__name__)

from src.games_today import get_teams_playing_today, get_matchups_today
from src.dvp_scraper import scrape_dvp_data
from src.depth_charts import get_depth_charts, find_player_for_matchup
from src.scoring_engine import evaluate_prop
from src.output import format_report, save_report, print_report
from src.config import POSITION_MAP


def run(date: str | None = None, fast: bool = False):
    """
    Main pipeline for a single day.

    Args:
        date: Date string YYYY-MM-DD. Defaults to today.
        fast: Skip per-player playtype fetches (fewer API calls).
    """
    if date is None:
        date = datetime.today().strftime("%Y-%m-%d")

    logger.info(f"{'='*60}")
    logger.info(f"NBA Player Prop Model starting for {date}")
    logger.info(f"{'='*60}")

    # ── Step 1: Today's games ────────────────────────────────────────
    logger.info("Step 1: Fetching today's games...")
    teams_playing = get_teams_playing_today(date)
    matchups = get_matchups_today(date)

    if not teams_playing:
        logger.warning("No NBA games today. Exiting.")
        print("\nNo NBA games scheduled today.\n")
        return

    logger.info(f"Teams playing: {sorted(teams_playing)}")

    # ── Step 2: DvP highlighted matchups ────────────────────────────
    logger.info("Step 2: Fetching Defense vs Position data...")
    dvp_matchups = scrape_dvp_data(teams_playing)

    if not dvp_matchups:
        logger.error("Could not retrieve DvP data. Exiting.")
        return

    logger.info(f"Found {len(dvp_matchups)} highlighted DvP matchups")

    # ── Step 3: Depth charts ─────────────────────────────────────────
    logger.info("Step 3: Fetching depth charts...")
    depth_charts = get_depth_charts()

    # ── Step 4: Pre-fetch shared data (zone/playtype rankings) ───────
    logger.info("Step 4: Pre-fetching team-level defensive rankings...")

    team_zone_rankings = {}
    team_playtype_rankings = {}

    # Check if any matchup involves points (to decide what to pre-fetch)
    has_points = any(m["stat"] == "points" for m in dvp_matchups)

    if has_points:
        from src.shooting_zones import get_team_zone_rankings
        from src.playtypes import get_team_playtype_defense_rankings

        logger.info("  Fetching team shooting zone defense rankings...")
        team_zone_rankings = get_team_zone_rankings()
        logger.info(f"  Got zone rankings for {len(team_zone_rankings)} zones")

        if not fast:
            logger.info("  Fetching team playtype defense rankings...")
            team_playtype_rankings = get_team_playtype_defense_rankings()
            logger.info(
                f"  Got playtype rankings for {len(team_playtype_rankings)} playtypes"
            )
        else:
            logger.info("  [Fast mode] Skipping playtype rankings")

    # ── Step 5: Evaluate each matchup ───────────────────────────────
    logger.info("Step 5: Evaluating player props...")
    prop_scores = []
    seen = set()  # Avoid duplicate player+stat combos

    for matchup in dvp_matchups:
        defensive_team = matchup["team"]      # Team with the highlighted defense
        position = matchup["position"]
        stat = matchup["stat"]
        edge = matchup["edge"]

        # The OFFENSIVE player we care about is on the OPPOSING team
        offensive_team = matchups.get(defensive_team)
        if not offensive_team:
            logger.debug(f"No opponent found for {defensive_team}")
            continue

        # Find the player at this position on the offensive team
        player_info = find_player_for_matchup(depth_charts, offensive_team, position)
        if not player_info:
            logger.warning(
                f"No player found at {position} for {offensive_team} "
                f"(vs {defensive_team})"
            )
            continue

        player_name = player_info["name"]
        key = (player_name, stat)
        if key in seen:
            continue
        seen.add(key)

        logger.info(
            f"\n  Evaluating: {player_name} ({offensive_team}) "
            f"| {stat.upper()} {edge.upper()} vs {defensive_team}"
        )

        prop = evaluate_prop(
            player_name=player_name,
            team=offensive_team,
            opponent=defensive_team,
            position=position,
            stat=stat,
            edge=edge,
            team_zone_rankings=team_zone_rankings,
            team_playtype_rankings=team_playtype_rankings,
        )

        prop_scores.append(prop)
        logger.info(f"  → {prop.summary()}")

        # Throttle API calls
        time.sleep(0.3)

    # ── Step 6: Output ───────────────────────────────────────────────
    logger.info(f"\nStep 6: Generating report ({len(prop_scores)} props evaluated)...")
    report = format_report(prop_scores, date)
    save_report(report, date)
    print_report(report)

    valid_count = sum(1 for p in prop_scores if p.is_valid)
    logger.info(
        f"Done. {valid_count} valid picks out of {len(prop_scores)} evaluated."
    )


def main():
    parser = argparse.ArgumentParser(
        description="NBA Player Prop Model — scores today's player props"
    )
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="Date to run model for (YYYY-MM-DD). Defaults to today.",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Skip per-playtype team rankings (fewer API calls, less detail).",
    )
    args = parser.parse_args()
    run(date=args.date, fast=args.fast)


if __name__ == "__main__":
    main()
