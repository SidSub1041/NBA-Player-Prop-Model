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
  5. Check season hit rate against the line (sportsbooks first, DFS apps
     as backup; >= 56% required)
  6. Output graded picks: A+/A = valid, B+/B = watchlist

Run manually:
    python main.py

Run for a specific date:
    python main.py --date 2026-04-08

Run in --fast mode (skips playtypes to reduce API calls):
    python main.py --fast

Afternoon re-check — keep the board's lines, fill the holds only:
    python main.py --fill-holds
"""

import argparse
import logging
import os
import sys
import time
from datetime import datetime

from src.config import slate_today  # stdlib-only module, safe before logging setup

# ── ensure logs dir exists before logging setup ──────────────────────────────
os.makedirs("logs", exist_ok=True)

# ── logging setup ────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            f"logs/model_{slate_today()}.log",
            mode="a",
            encoding="utf-8",
        ),
    ],
)
logger = logging.getLogger(__name__)

# ── imports (after logging) ───────────────────────────────────────────────────

import src.nba_http  # noqa: F401  — timeouts, retries, proxy, circuit breaker
from src.games_today import get_todays_games, pre_tip_teams
from src.dvp_scraper import scrape_dvp_data
from src.depth_charts import get_depth_charts, find_player_for_matchup
from src.scoring_engine import evaluate_prop, build_combo_props, combo_candidates
from src.output import format_report, save_report, print_report, save_json_report
from src.config import POSITION_MAP
from src.season import stats_season
from src.underdog import fetch_underdog_lines, load_cached_lines, merge_lines
from src.lines import apply as apply_line, hold_reason, load_board_lines, lock_key, resolve
from src.vegas_odds import VegasLines, dfs_backup_enabled
from src.injuries import fetch_injury_report, is_player_out, find_backup_player, injury_for, injury_label


def _assert_stats_reachable(stage: str):
    """
    Abort before anything is written if stats.nba.com has stopped answering.
    A board assembled from fallbacks alone (no minutes, zones or hit rates)
    would overwrite real data with junk and feed the grader empty picks.
    """
    from src.nba_http import breaker_open
    if breaker_open():
        raise RuntimeError(
            f"stats.nba.com is unreachable from this machine (failed during {stage}); "
            "not writing a board. Run the model from a non-cloud network or set NBA_PROXY."
        )


def run(date: str | None = None, fast: bool = False, fill_holds: bool = False):
    """
    Main pipeline for a single day.

    Args:
        date: Date string YYYY-MM-DD. Defaults to today.
        fast: Skip per-player playtype fetches (fewer API calls).
        fill_holds: Keep every line already on the day's board and only look
                    for lines for the props still waiting on one (afternoon runs).
    """
    if date is None:
        date = slate_today()

    from src.season import reset as reset_stats_season
    reset_stats_season()
    from src.hitrate import reset_cache as reset_gamelog_cache
    reset_gamelog_cache()
    from src.playtypes import reset_cache as reset_playtype_cache
    reset_playtype_cache()

    logger.info(f"{'='*60}")
    logger.info(f"NBA Player Prop Model starting for {date}" + (" (fill holds)" if fill_holds else ""))
    logger.info(f"{'='*60}")

    # ── Step 1: Today's games ────────────────────────────────────────
    logger.info("Step 1: Fetching today's games...")
    games = get_todays_games(date)
    teams_playing = {t for g in games for t in (g["home"], g["away"])}
    matchups = {**{g["home"]: g["away"] for g in games}, **{g["away"]: g["home"] for g in games}}

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
    depth_charts = get_depth_charts(teams_playing)

    # ── Step 3b: Fetch player minutes for filtering ──────────────────
    logger.info("Step 3b: Fetching player minutes (MPG filter)...")
    player_mpg = {}
    player_stats_df = None
    try:
        from nba_api.stats.endpoints import leaguedashplayerstats
        from src.config import SEASON_TYPE
        import time as _t; _t.sleep(0.6)
        _stats = leaguedashplayerstats.LeagueDashPlayerStats(
            season=stats_season(), season_type_all_star=SEASON_TYPE,
            per_mode_detailed="PerGame"
        )
        player_stats_df = _stats.get_data_frames()[0]
        for _, row in player_stats_df.iterrows():
            player_mpg[row["PLAYER_NAME"]] = float(row["MIN"])
        from src.hitrate import register_live_ids
        register_live_ids(player_stats_df)   # ids for rookies/new signings missing from nba_api's static list
        logger.info(f"  Loaded MPG data for {len(player_mpg)} players")
    except Exception as e:
        logger.warning(f"Could not fetch MPG data: {e}")

    # ── Step 3c: Underdog's own feed (last-resort lines) ─────────────
    _assert_stats_reachable("player minutes")

    logger.info("Step 3c: Underdog lines...")
    ud_lines = load_cached_lines(date)
    fresh_ok = False   # did Underdog answer with tonight's lines on this run?
    if dfs_backup_enabled():
        logger.info("  Underdog/PrizePicks lines come through The Odds API (DFS region); Underdog's own feed not used")
    elif date >= slate_today():   # today, or tomorrow's board built the night before
        fresh = fetch_underdog_lines(date)
        fresh_ok = any(not v.get("suspended") for v in fresh.values())
        ud_lines = merge_lines(ud_lines, fresh)  # fresh lines win
    else:
        logger.info("  Past date — using lines recorded on that day only")
    # A line captured earlier that is gone from a live feed before its game has
    # tipped was pulled (usually injury news): it can't be bet, so it doesn't count.
    not_started = pre_tip_teams(games) if fresh_ok else set()

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

    _assert_stats_reachable("team defensive rankings")
    if has_points and not team_zone_rankings:
        raise RuntimeError(
            "No shooting-zone rankings came back from stats.nba.com — "
            "refusing to publish a board built without them"
        )

    # ── Step 3e: Fetch injury report ─────────────────────────────────
    logger.info("Step 3e: Fetching injury report...")
    injuries = fetch_injury_report()

    # ── Step 3d: (retired) adaptive multipliers ──────────────────────
    # Superseded by the learning loop (src/learner.py), applied after Step 6.
    adaptive_weights = None

    # ── Step 5a: Candidate props — who, and which stat ───────────────
    # Decided before any line is bought, so only the markets these players
    # need are paid for.
    logger.info("Step 5a: Selecting candidate props...")
    candidates = []
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

        # ── Injury check: swap in backup if starter is OUT ───────────
        if is_player_out(injuries, player_name):
            inj = injury_for(injuries, player_name) or {}
            logger.info(
                f"  {player_name} is OUT ({inj.get('injury', '?')}) — "
                f"looking for backup at {position} on {offensive_team}"
            )
            backup = find_backup_player(
                player_stats_df, offensive_team, position, injuries
            )
            if backup:
                logger.info(f"  → Replacement: {backup}")
                player_name = backup
            else:
                logger.info(f"  → No valid backup found, skipping")
                continue

        key = (player_name, stat)
        if key in seen:
            continue
        seen.add(key)

        # ── MPG filter: skip players averaging <= 10 minutes ─────────
        mpg = player_mpg.get(player_name)
        if mpg is not None and mpg <= 10:
            logger.info(f"  Skipping {player_name} — only {mpg:.1f} MPG")
            continue

        candidates.append({"player": player_name, "team": offensive_team, "opponent": defensive_team,
                           "position": position, "stat": stat, "edge": edge})
    logger.info(f"  {len(candidates)} candidate props")

    # ── Step 4b: Lines — sportsbooks first, DFS apps as backup ───────
    # Priced before evaluation (each prop's hit rate is measured against its
    # line) but after the stats checks above, so an aborted run buys nothing.
    # Only the markets the candidates need are bought. In fill mode every line
    # already on the board is locked (kept exactly as the board showed it) and
    # only markets with a candidate still waiting on a line are bought.
    logger.info("Step 4b: Fetching lines (The Odds API)...")
    locked: dict = {}
    if fill_holds:
        locked = load_board_lines(date)
        if locked:
            logger.info(f"  Fill mode: {len(locked)} line(s) already on the {date} board are locked; filling holds only")
        else:
            logger.info(f"  Fill mode: no board for {date} yet — building it from scratch")
    vegas = VegasLines(date, locked={f"{n}|{s}" for (n, s) in locked})

    def _wants(items):
        """{team: {stat: {player, ...}}} for the (player, team, stat) items without a locked line."""
        out: dict[str, dict] = {}
        for player, team, stat in items:
            if lock_key(player, stat) not in locked:
                out.setdefault(team, {}).setdefault(stat, set()).add(player)
        return out

    vegas.ensure(_wants((c["player"], c["team"], c["stat"]) for c in candidates))
    vegas.save()
    logger.info(f"  {vegas.report()}")

    # ── Step 5: Evaluate each candidate ──────────────────────────────
    logger.info("Step 5: Evaluating player props...")
    prop_scores = []

    for c in candidates:
        player_name, offensive_team, defensive_team = c["player"], c["team"], c["opponent"]
        position, stat, edge = c["position"], c["stat"], c["edge"]

        logger.info(
            f"\n  Evaluating: {player_name} ({offensive_team}) "
            f"| {stat.upper()} {edge.upper()} vs {defensive_team}"
        )

        # The line comes first: the hit-rate gate is measured against it.
        # Locked (fill mode), else books, else DFS apps, else Underdog's feed.
        res = resolve(player_name, offensive_team, stat, vegas, ud_lines, not_started, locked)

        prop = evaluate_prop(
            player_name=player_name,
            team=offensive_team,
            opponent=defensive_team,
            position=position,
            stat=stat,
            edge=edge,
            team_zone_rankings=team_zone_rankings,
            team_playtype_rankings=team_playtype_rankings,
            adaptive_weights=adaptive_weights,
            line=res.line["line"] if res.line else None,
            before=date,
        )

        prop_scores.append(prop)

        apply_line(prop, res)
        prop.injury = injury_label(injuries, player_name)
        if prop.on_hold:
            prop.hold_reason = hold_reason(stat, res, prop.injury)

        logger.info(f"  → {prop.summary()}")

        # Throttle API calls
        time.sleep(0.3)

    # ── Step 6: Combo props ────────────────────────────────────────
    logger.info(f"\nStep 6: Building combo props...")
    _assert_stats_reachable("player evaluation")   # before buying combo markets

    # Price the combo markets only for combos that will actually be built
    combo_wants = _wants((player, team, combo_stat) for player, team, combo_stat, _edge in combo_candidates(prop_scores))
    if combo_wants:
        vegas.ensure(combo_wants)
        vegas.save()

    def _line_for(player: str, team: str, stat: str):
        return resolve(player, team, stat, vegas, ud_lines, not_started, locked)

    combo_scores = build_combo_props(prop_scores, _line_for, before=date)
    for c in combo_scores:
        c.injury = injury_label(injuries, c.player_name)
        if c.on_hold:
            c.hold_reason = hold_reason(c.stat, c.resolution, c.injury)
    prop_scores.extend(combo_scores)
    held = [p for p in prop_scores if p.on_hold]
    if held:
        logger.info(f"  {len(held)} prop(s) ON HOLD awaiting a line — re-checked on the next run")
    n_locked = sum(1 for p in prop_scores if getattr(p.resolution, "locked", False))
    by_source = {}
    for p in prop_scores:
        src = (p.line_info or {}).get("source", "none")
        by_source[src] = by_source.get(src, 0) + 1
    logger.info(f"  Lines by source: {by_source}" + (f" | {n_locked} kept from the earlier board" if n_locked else "")
                + f" | {vegas.report()}")

    _assert_stats_reachable("player evaluation")

    # ── Step 6b: The learning loop ───────────────────────────────────
    # The model trained on every graded prop so far predicts P(hit) for each
    # prop with a line. In ACTIVE mode a valid pick must also be +EV at its
    # price. A learner problem never costs a board: it is logged and skipped.
    logger.info("Step 6b: Applying the learned model...")
    try:
        from src.learner import load_model, predict_prop
        from src.output import _prop_to_dict
        model = load_model()
        if model:
            demoted = 0
            for p in prop_scores:
                pred = predict_prop(model, _prop_to_dict(p))
                p.learned = pred or {}
                if pred and model.get("trusted") and p.is_valid and pred["ev"] < 0:
                    p.is_valid = False
                    p.final_grade = "B+"
                    p.learn_note = (f"Model says -EV: {pred['p_hit']:.0%} to hit vs "
                                    f"{pred['breakeven']:.0%} needed at this price")
                    demoted += 1
            logger.info(f"  Learner {model.get('mode', 'shadow').upper()} on {model.get('n_rows')} labeled props"
                        + (f"; {demoted} pick(s) demoted as -EV" if model.get("trusted") else " (predictions shown, nothing gated)"))
        else:
            logger.info("  No trained model yet (it trains after the first graded night)")
    except Exception as e:
        logger.warning(f"  Learner skipped ({type(e).__name__}: {e})")

    # ── Step 7: Output ───────────────────────────────────────────────
    logger.info(f"\nStep 7: Generating report ({len(prop_scores)} props evaluated)...")
    report = format_report(prop_scores, date)
    save_report(report, date)
    save_json_report(prop_scores, date, run_mode="fill-holds" if fill_holds else "full", lines_locked=n_locked)
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
        "--tomorrow",
        action="store_true",
        help="Build tomorrow's board (the night-before run: lines as the books post them).",
    )
    parser.add_argument(
        "--fill-holds",
        action="store_true",
        help="Afternoon re-check: keep every line already on the day's board and only look for "
             "lines for the props still On Hold (no re-pricing of picks already made).",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Skip per-playtype team rankings (fewer API calls, less detail).",
    )
    args = parser.parse_args()
    date = args.date
    if args.tomorrow and not date:
        from datetime import timedelta
        date = (datetime.strptime(slate_today(), "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    try:
        run(date=date, fast=args.fast, fill_holds=args.fill_holds)
    except Exception as e:
        logger.error(f"Model run failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
