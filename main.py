"""
NBA Player Prop Model — Main Orchestrator (round 18: the edge model)
====================================================================

For every player with a points, rebounds or assists line on tonight's board:
  1. Project tonight's number from a minutes-weighted blend of this season's
     and last season's game logs (recent games weigh more), scaled by the
     matchup: the opponent's stat allowed to the position (FantasyPros DvP,
     numeric), pace, shooting-zone and playtype fit for points, and OUT
     teammates. Assists are projected from passing instead (src/passing.py):
     passes made x potential assists per pass x conversion, with the
     opponent's shooting defence and ball movement allowed.
  2. Turn the projection into a probability of clearing the line (normal
     for points and combos, negative binomial for rebounds and assists).
  3. Compare it with the de-vigged market probability at the best price —
     shifted to the pick's number when the market has moved — and publish
     the side whose edge clears MIN_EDGE, subject to the guards (price,
     books at the line, line size, projected minutes, passing data for
     assists), the paused stats and the board caps.
Every line seen during the day is recorded (logs/lines_{date}.json); the
--snapshot runs re-price games about to tip so each pick gets a closing
line and a closing-line value when it is graded.

Run manually:
    python main.py                 # today's board
    python main.py --tomorrow      # tomorrow's board (the night-before run)
    python main.py --fill-holds    # keep published picks as they are; re-price the rest, fill the caps' room
    python main.py --snapshot      # re-price games tipping within the next 45 minutes (closing lines)
    python main.py --fast          # skip the playtype tables
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
from src.dvp_scraper import scrape_dvp_table, reset_cache as reset_dvp_cache
from src.depth_charts import get_depth_charts
from src.scoring_engine import PropScore, COMBO_DEFS
from src.output import format_report, save_report, print_report, save_json_report
from src.config import (
    SEASON, previous_season, EDGE_STATS, PAUSED_STATS, MIN_CANDIDATE_MPG, MIN_BOOKS, MIN_LINE,
    MIN_PROJ_MINUTES, MAX_PRICE, SEASON_TYPE, MODEL_TRUST,
)
from src.season import stats_season
from src.underdog import fetch_underdog_lines, load_cached_lines, merge_lines, norm_name, norm_name_base
from src.lines import apply as apply_line, hold_reason, load_board_lines, lock_key, resolve
from src.vegas_odds import VegasLines, dfs_backup_enabled
from src.injuries import fetch_injury_report, is_player_out, injury_label
from src.hitrate import get_player_hitrate, player_sample, register_live_ids
from src.projection import project, edge_for, combo_projection, market_prob, market_prob_at, p_over, p_sides, implied_prob
from src.matchup import (
    dvp_z, zone_score, playtype_score, matchup_multiplier, teammate_outs, teammate_boost, position_of,
    roster_index, dvp_positions, dvp_blend, reset_roster_cache,
)
from src.pace import blended_pace, pace_factor
from src import passing as passing_model
from src.line_history import movement, load as load_history, key_for
from src.caps import apply_caps


def _assert_stats_reachable(stage: str):
    """
    Abort before anything is written if stats.nba.com has stopped answering.
    A board assembled from fallbacks alone (no minutes, projections or
    matchups) would overwrite real data with junk and feed the grader empty picks.
    """
    from src.nba_http import breaker_open
    if breaker_open():
        raise RuntimeError(
            f"stats.nba.com is unreachable from this machine (failed during {stage}); "
            "not writing a board. Run the model from a non-cloud network or set NBA_PROXY."
        )


def _price_num(odds) -> int | None:
    try:
        return int(str(odds).replace("+", ""))
    except (TypeError, ValueError):
        return None


def _float(x) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _uses_assists(stat: str) -> bool:
    return stat == "assists" or "assists" in COMBO_DEFS.get(stat, {}).get("components", [])


def _guards(prop: PropScore) -> list[str]:
    """
    Which guards a priced prop fails: price / books / line_size / minutes /
    passing. The books guard is about the market's depth: a shopped number
    (one book lagging the consensus) is judged by the consensus it lags. The
    passing guard holds back an assists prop (or a combo with assists) whose
    assists could not be projected from passing (src/passing.py): the
    box-score fallback is what the assists pause was about.
    """
    out = []
    info = prop.line_info or {}
    price = _price_num(prop.side_odds())
    if price is not None and price < MAX_PRICE:
        out.append("price")
    depth = info.get("consensus_books") if info.get("shopped") else info.get("books")
    if info.get("source") != "vegas" or (depth or 0) < MIN_BOOKS:
        out.append("books")
    if prop.line is not None and float(prop.line) < MIN_LINE:
        out.append("line_size")
    minutes = (prop.projection or {}).get("minutes")
    if minutes is not None and minutes < MIN_PROJ_MINUTES:
        out.append("minutes")
    if _uses_assists(prop.stat) and (prop.projection or {}).get("source") != "passing":
        out.append("passing")
    return out


def _decide(stat: str, proj: dict, line, over_odds, under_odds, current: dict | None) -> dict:
    """
    The pick decision for one (line, prices).

    p_model: the projection's probability of each side at this line.
    p_market: the market's probability of each side at this line — the current
      consensus de-vigged and, when the consensus has moved away from this
      line, shifted to it (a line that moved down makes the under at the old
      number likelier in the market's eyes).
    p_hat = MODEL_TRUST × p_model + (1 − MODEL_TRUST) × p_market.
    edge = p_hat − break-even of the side's price (what you actually pay), so
      both a confident model and a stale number at a lagging book count, and
      the vig is always charged.
    """
    cur = current or {}
    cur_line = cur.get("line")
    ef = edge_for(
        stat, proj, line, over_odds, under_odds,
        current_line=cur_line if (cur_line is not None and cur_line != line) else None,
        current_over_odds=cur.get("over_odds"), current_under_odds=cur.get("under_odds"),
    )
    out = {"p_model_over": ef.get("p_model_over"), "p_model_under": ef.get("p_model_under"),
           "p_market_over": ef.get("p_market_over"), "p_market_under": ef.get("p_market_under"),
           "p_push": ef.get("p_push"),
           "market_basis": ef.get("market_basis"), "side": None, "edge": None, "p_hat": None, "breakeven": None,
           "p_model": None, "p_market": None}
    best = None
    for side, odds in (("over", over_odds), ("under", under_odds)):
        pm, pk = out[f"p_model_{side}"], out[f"p_market_{side}"]
        be = implied_prob(odds)
        if pm is None or pk is None or be is None:
            continue
        p_hat = MODEL_TRUST * pm + (1 - MODEL_TRUST) * pk
        net = p_hat - be
        if best is None or net > best[0]:
            best = (net, side, p_hat, be, pm, pk)
    if best:
        net, side, p_hat, be, pm, pk = best
        out.update({"side": side, "edge": net, "p_hat": p_hat, "breakeven": be, "p_model": pm, "p_market": pk})
    return out


def _shop_line(prop: PropScore, stat: str, proj: dict, current: dict, res) -> None:
    """
    Line shopping. The consensus is the number most books hang; a book that
    lags a move (or shades the other way) may offer a better number for the
    pick's side. Evaluate every book's (line, price) against the market
    probability shifted from the consensus, and take the one with the most
    edge when it beats the consensus by at least half a point of probability.
    Locked picks (fill mode) and non-sportsbook lines are left alone.
    """
    if getattr(res, "locked", False) or (prop.line_info or {}).get("source") != "vegas":
        return
    offers = (current or {}).get("offers") or {}
    cons_line = (current or {}).get("line")
    mkt = market_prob(current.get("over_odds"), current.get("under_odds")) if current else None
    if not offers or cons_line is None or mkt is None or prop.p_model is None or prop.edge_value is None:
        return
    side = prop.edge
    best = None
    for book, o in offers.items():
        try:
            bl = float(o.get("line"))
        except (TypeError, ValueError):
            continue
        price = o.get("over") if side == "over" else o.get("under")
        if price in (None, ""):
            continue
        d = _decide(stat, proj, bl, o.get("over"), o.get("under"), current)
        # the side is fixed by the consensus decision; read that side's numbers at this book's line
        pm, pk = d[f"p_model_{side}"], d[f"p_market_{side}"]
        be = implied_prob(price)
        if pm is None or pk is None or be is None:
            continue
        e = MODEL_TRUST * pm + (1 - MODEL_TRUST) * pk - be
        if best is None or e > best[0]:
            best = (e, book, bl, price, pm, pk, be)
    if best is None or best[0] < prop.edge_value + 0.005 or best[2] == prop.line:
        return
    e, book, bl, price, pm, pk, be = best
    other = "under" if side == "over" else "over"
    at_line = [(bk, o) for bk, o in offers.items() if _float(o.get("line")) == bl]
    other_prices = [(float(str(o.get(other)).replace("+", "")), bk) for bk, o in at_line if _price_num(o.get(other)) is not None]
    other_price, other_book = (max(other_prices) if other_prices else (None, None))
    info = dict(prop.line_info)
    info.update({"line": bl, "consensus_line": cons_line, "consensus_books": info.get("books"), "shopped": True,
                 "books": len(at_line),
                 f"{side}_odds": price, f"{side}_book": book,
                 f"{other}_odds": (f"+{int(other_price)}" if other_price and other_price > 0 else (str(int(other_price)) if other_price is not None else None)),
                 f"{other}_book": other_book})
    prop.line_info = info
    prop.p_model, prop.p_market, prop.edge_value = pm, pk, e
    po, pu, pp = p_sides(stat, proj["mu"], proj["sd"], bl)   # a push loses either side on this board
    prop.projection = {**prop.projection, "p_over": po, "p_under": pu, "p_push": pp,
                       "p_hat": MODEL_TRUST * pm + (1 - MODEL_TRUST) * pk, "breakeven": be, "shopped_from": prop.line}


def _player_frames(season_now: str, season_prev: str) -> dict[str, "object"]:
    """LeagueDashPlayerStats (per game) for both seasons: team, minutes, games."""
    from nba_api.stats.endpoints import leaguedashplayerstats
    frames = {}
    for season in (season_now, season_prev):
        try:
            time.sleep(0.6)
            frames[season] = leaguedashplayerstats.LeagueDashPlayerStats(
                season=season, season_type_all_star=SEASON_TYPE, per_mode_detailed="PerGame"
            ).get_data_frames()[0]
            logger.info(f"  Player stats {season}: {len(frames[season])} players")
        except Exception as e:
            logger.warning(f"  Player stats {season} unavailable: {type(e).__name__}: {e}")
            frames[season] = None
    return frames


def _roster_index(frames: dict, season_now: str, season_prev: str) -> dict[str, dict]:
    """norm name -> {"name", "team", "mpg", "gp_this"} from the two per-game frames (this season first)."""
    idx: dict[str, dict] = {}
    for season in (season_prev, season_now):      # this season overwrites last season's team
        df = frames.get(season)
        if df is None or getattr(df, "empty", True):
            continue
        for _, row in df.iterrows():
            key = norm_name(str(row["PLAYER_NAME"]))
            entry = idx.setdefault(key, {"name": str(row["PLAYER_NAME"]), "team": None, "mpg": 0.0, "gp_this": 0, "mpg_last": 0.0})
            try:
                minutes = float(row["MIN"])
                gp = int(row["GP"])
            except (TypeError, ValueError):
                minutes, gp = 0.0, 0
            if season == season_now:
                entry["team"] = str(row["TEAM_ABBREVIATION"])
                entry["gp_this"] = gp
                entry["mpg"] = minutes if gp >= 3 else max(minutes, entry["mpg_last"])
                entry["name"] = str(row["PLAYER_NAME"])
            else:
                entry["team"] = entry["team"] or str(row["TEAM_ABBREVIATION"])
                entry["mpg_last"] = minutes
                entry["mpg"] = max(entry["mpg"], minutes)
    base: dict[str, dict | None] = {}
    for key, entry in list(idx.items()):
        b = norm_name_base(entry["name"])
        if b == key:
            continue
        base[b] = None if (b in base and base[b] is not entry) else entry
    for b, entry in base.items():
        if b not in idx and entry is not None:
            idx[b] = entry
    return idx


def _who(roster: dict, index: dict, book_name: str, pname: str) -> dict | None:
    """
    The player behind a book's name: exact normalised name, then suffix-free
    (books write 'Jimmy Butler' where NBA.com has 'Jimmy Butler III'), from
    the league game-log index first (its team is the team of the player's most
    recent game, so trades show up), then the per-game stats frames.
    """
    keys = [pname, norm_name(book_name or ""), norm_name_base(book_name or pname)]
    for source in (index, roster):
        for k in keys:
            hit = source.get(k) if source else None
            if hit:
                return hit
    return None


def _team_for(who: dict | None, roster_hit: dict | None, ev_teams: set, depth_charts: dict, name: str,
              injuries: dict, teams_playing: set, rosters: dict | None = None) -> str | None:
    """
    Which side of tonight's game a priced player is on. The line belongs to a
    specific event, so the answer must be one of that event's two teams:
    the team of his latest game, then this season's stats frame, then the
    depth charts and the injury report. A known team that is not in the event
    (a trade the tables have not caught up with) is never used.
    """
    candidates = []
    current = (rosters or {}).get(norm_name(name)) or (rosters or {}).get(norm_name_base(name))
    if current and current.get("team"):
        candidates.append(current["team"])       # NBA.com's current roster: a trade shows up the same day
    for hit in (who, roster_hit):
        if hit and hit.get("team"):
            candidates.append(hit["team"])
    for team, chart in (depth_charts or {}).items():
        for v in (chart or {}).values():
            nm = v.get("name") if isinstance(v, dict) else v
            if nm and norm_name_base(str(nm)) == norm_name_base(name):
                candidates.append(team)
    from src.injuries import injury_for
    inj = injury_for(injuries, name) or {}
    if inj.get("team"):
        candidates.append(inj["team"])
    if ev_teams:
        for t in candidates:
            if t in ev_teams:
                return t
        return None
    for t in candidates:
        if t in teams_playing:
            return t
    return None


def _hist_key(name: str, stat: str, history: dict) -> str:
    """The prop's key in the day's line history: the NBA name, else the one suffix-free match (books drop Jr./III)."""
    key = key_for(name, stat)
    snaps = (history or {}).get("snapshots") or {}
    if key in snaps:
        return key
    base = norm_name_base(name)
    matches = [k for k in snaps if k.rsplit("|", 1)[-1] == stat and norm_name_base(k.rsplit("|", 1)[0]) == base]
    return matches[0] if len(matches) == 1 else key


def _prop_from_board(d: dict) -> PropScore:
    """Rebuild a locked pick from the board JSON (fill mode keeps its line, edge and grade)."""
    p = PropScore(d["player_name"], d["team"], d["opponent"], d.get("position", "?"), d["stat"], d["edge"])
    p.line_info = {k: d.get(k) for k in ("line", "over_odds", "over_book", "under_odds", "under_book")}
    p.line_info["source"] = d.get("line_source") or "vegas"
    p.line_info["books"] = d.get("line_books")
    p.projection = {"mu": d.get("proj_mu"), "sd": d.get("proj_sd"), "minutes": d.get("proj_minutes"), "dist": d.get("proj_dist"),
                    "n_this": d.get("n_this"), "n_last": d.get("n_last"), "weight_this": d.get("weight_this"),
                    "p_hat": d.get("p_hat"), "breakeven": d.get("breakeven_price"), "market_basis": d.get("market_basis"),
                    "source": d.get("proj_source"), "mu_box": d.get("proj_mu_box"), "passing": d.get("passing")}
    if d.get("line_shopped"):
        p.line_info["shopped"] = True
        p.line_info["consensus_line"] = d.get("consensus_line")
        p.line_info["consensus_books"] = d.get("consensus_books")
    p.p_model, p.p_market, p.edge_value = d.get("p_model"), d.get("p_market"), d.get("edge_value")
    p.open_line, p.line_move = d.get("open_line"), d.get("line_move")
    p.dvp_z, p.matchup_z, p.teammate_out = d.get("dvp_z"), d.get("matchup_z"), d.get("teammate_out") or 0
    p.paused, p.guards = bool(d.get("paused")), list(d.get("guards") or [])
    p.hitrate_data = {"hitrate": d.get("hitrate") if d.get("hitrate") is not None else -1, "source": d.get("hitrate_source"),
                      "line": d.get("line"), "season_avg": d.get("season_avg"), "games_played": d.get("games_played"), "games_hit": d.get("games_hit")}
    p.total_points, p.total_conditions = d.get("total_points") or 0, d.get("total_conditions") or 0
    p.injury = d.get("injury")
    p.zone_score = {"points": 0, "total": 0, "details": d.get("zone_details") or []}
    p.playtype_score = {"points": 0, "total": 0, "details": d.get("playtype_details") or []}
    p.final_grade = d.get("grade") or "D"
    p.learn_note = d.get("learn_note") or ""
    # the comparison fields the board carried (Underdog's line, the books' consensus)
    p.ud_line, p.ud_over_odds, p.ud_under_odds = d.get("ud_line"), d.get("ud_over_odds") or "", d.get("ud_under_odds") or ""
    if d.get("vegas_line") is not None:
        side_book = "under_book" if d.get("edge") == "under" else "over_book"
        p.vegas = {"line": d.get("vegas_line"), "over_odds": d.get("vegas_over_odds"), "under_odds": d.get("vegas_under_odds"),
                   "books": d.get("vegas_books"), side_book: d.get("vegas_book")}
    p.on_hold = bool(d.get("on_hold"))
    p.hold_reason = d.get("hold_reason") or ""
    p.is_valid = bool(d.get("is_valid")) and not d.get("cap_note")
    p.pinned = p.is_valid          # a pick already published stays on the board (fill mode)
    if not p.pinned and not p.learn_note:
        # capped or guarded earlier: re-grade from the frozen edge so it can fill room the caps leave
        p.cap_note = ""
        if p.edge_value is not None and p.line is not None:
            p.compute_edge_grade()
    p.learned = {k: d.get(k) for k in ("p_hit", "ev", "breakeven") if d.get(k) is not None}
    from src.lines import Resolution
    p.resolution = Resolution(line=dict(p.line_info), locked=True)
    return p


def run(date: str | None = None, fast: bool = False, fill_holds: bool = False, snapshot: bool = False):
    """
    Main pipeline for a single day.

    Args:
        date: Date string YYYY-MM-DD. Defaults to today.
        fast: Skip the playtype tables (fewer API calls).
        fill_holds: Keep every pick already on the day's board (line, edge and
                    grade frozen; withdrawn only if the player is ruled OUT), re-price
                    and re-grade everything else, and fill the room the caps leave.
        snapshot: Only re-price the games about to tip (closing lines); no board.
    """
    if date is None:
        date = slate_today()

    from src.season import reset as reset_stats_season
    reset_stats_season()
    from src.nba_http import reset_breakers
    reset_breakers()            # a new run (a new day in the long-lived scheduler) starts with every endpoint closed
    from src.hitrate import reset_cache as reset_gamelog_cache
    reset_gamelog_cache()
    from src.playtypes import reset_cache as reset_playtype_cache
    reset_playtype_cache()
    reset_dvp_cache()
    reset_roster_cache()
    passing_model.reset_cache()
    from src.shooting_zones import reset_cache as reset_zone_cache
    reset_zone_cache()
    try:
        from src.pace import reset_cache as reset_pace_cache
        reset_pace_cache()
    except ImportError:
        pass

    mode = "snapshot" if snapshot else "fill holds" if fill_holds else "full"
    logger.info(f"{'='*60}")
    logger.info(f"NBA Player Prop Model starting for {date} ({mode})")
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

    # ── Snapshot mode: closing lines only ────────────────────────────
    if snapshot:
        vegas = VegasLines(date)
        n = vegas.ensure_snapshot(games)
        vegas.save()
        logger.info(f"  Snapshot: {n} game(s) re-priced | {vegas.report()}")
        return

    season_now, season_prev = SEASON, previous_season(SEASON)

    # ── Step 2: Defense vs Position — numeric, by position ───────────
    logger.info("Step 2: Fetching Defense vs Position (numeric)...")
    dvp = scrape_dvp_table(teams_playing)
    logger.info(f"  DvP: {len(dvp.get('values', {}))} cells, {len(dvp.get('flags', {}))} flagged, "
                f"league stats for {len(dvp.get('league', {}))} position×stat pairs")

    # ── Step 3: Depth charts, rosters, minutes (both seasons) ────────
    logger.info("Step 3: Fetching depth charts and player stats...")
    depth_charts = get_depth_charts(teams_playing)
    frames = _player_frames(season_now, season_prev)
    for season in (season_prev, season_now):      # ids for rookies/new signings missing from nba_api's static list
        if frames.get(season) is not None:
            register_live_ids(frames[season])
    roster = _roster_index(frames, season_now, season_prev)
    rosters = roster_index(season_now)          # current team + listed position for everyone (one request)
    _assert_stats_reachable("player stats")

    # ── Step 3c: Underdog's own feed (last-resort lines) ─────────────
    logger.info("Step 3c: Underdog lines...")
    ud_lines = load_cached_lines(date)
    fresh_ok = False
    if dfs_backup_enabled():
        logger.info("  Underdog/PrizePicks lines come through The Odds API (DFS region); Underdog's own feed not used")
    elif date >= slate_today():
        fresh = fetch_underdog_lines(date)
        fresh_ok = any(not v.get("suspended") for v in fresh.values())
        ud_lines = merge_lines(ud_lines, fresh)
    else:
        logger.info("  Past date — using lines recorded on that day only")
    not_started = pre_tip_teams(games) if fresh_ok else set()

    # ── Step 4: League tables: zones, playtypes, pace ────────────────
    logger.info("Step 4: Fetching league tables (zones, playtypes, pace)...")
    from src.shooting_zones import get_team_zone_rankings
    team_zone_rankings = get_team_zone_rankings()
    logger.info(f"  Zone rankings for {len(team_zone_rankings)} zones")
    team_playtype_rankings = {}
    if not fast:
        from src.playtypes import get_team_playtype_defense_rankings
        team_playtype_rankings = get_team_playtype_defense_rankings()
        logger.info(f"  Playtype rankings for {len(team_playtype_rankings)} playtypes")
    else:
        logger.info("  [Fast mode] Skipping playtype rankings")
    pace = blended_pace(season_now, season_prev)
    logger.info(f"  Pace for {len(pace)} teams")
    from src.nba_http import optional_calls
    with optional_calls():                      # the passing tables never abort a board (assists are held back instead)
        pctx = passing_model.context(date, season_now, season_prev)
    logger.info(f"  Passing: {passing_model.describe(pctx)}")
    _assert_stats_reachable("league tables")
    if not team_zone_rankings:
        raise RuntimeError("No shooting-zone rankings came back from stats.nba.com — refusing to publish a board built without them")

    # ── Step 3e: Injury report ───────────────────────────────────────
    logger.info("Step 3e: Fetching injury report...")
    injuries = fetch_injury_report()

    # ── Step 4b: Lines for every player on the slate ─────────────────
    # The line board IS the candidate universe: every points, rebounds and
    # assists market for every game. Fill mode locks the picks already made.
    logger.info("Step 4b: Fetching lines (The Odds API)...")
    locked: dict = {}
    board_meta: dict = {}
    if fill_holds:
        locked = load_board_lines(date)
        try:
            import json
            with open(os.path.join("logs", f"props_{date}.json"), encoding="utf-8") as f:
                board = json.load(f)
            board_meta = {lock_key(p["player_name"], p["stat"]): p for p in board.get("all_props", [])
                          if p.get("line") is not None and p.get("p_model") is not None}
        except (OSError, ValueError):
            board_meta = {}
        logger.info(f"  Fill mode: {len(locked)} line(s) locked from the {date} board" if locked else f"  Fill mode: no board for {date} yet — building it from scratch")
    vegas = VegasLines(date, locked={f"{n}|{s}" for (n, s) in locked}, fill=fill_holds)
    future = date > slate_today()
    if future:
        vegas.dfs_regions = ""     # a DFS-only line can never be a pick: don't buy them for tomorrow
    wants = {t: {s: None for s in EDGE_STATS} for t in teams_playing}   # everyone, every single-stat market
    vegas.ensure(wants)
    vegas.save()
    logger.info(f"  {vegas.report()}")
    book_lines = sum(1 for v in vegas.cache["lines"].values() if v.get("tier", "books") == "books")
    if future and book_lines == 0:
        logger.warning(f"  The books have not posted {date}'s props yet — no board written; the morning run builds it")
        return
    board_path = os.path.join("logs", f"props_{date}.json")
    if not vegas.cache["lines"] and not ud_lines and os.path.exists(board_path):
        logger.warning(f"  No lines from any source (Odds API down or out of credits?) — keeping the existing {date} board")
        return

    history = load_history(date)

    # ── Step 5a: Candidates — every priced player on a team playing ──
    # Sportsbook/DFS lines (The Odds API) first; Underdog's own feed adds the
    # players it prices that the API does not (its lines are shown, never
    # published: the books guard needs 3 sportsbooks).
    logger.info("Step 5a: Selecting candidates from the line board...")
    try:
        from src.gamelogs import name_index
        index = name_index((season_now, season_prev)) or {}
    except Exception as e:
        logger.warning(f"  Game-log name index unavailable ({type(e).__name__}: {e}); using the stats frames only")
        index = {}
    candidates: dict[tuple, dict] = {}
    skipped = {"unknown_team": 0, "not_playing": 0, "minutes": 0, "out": 0}
    withdrawn = []
    priced = [(key.rsplit("|", 1)[0], key.rsplit("|", 1)[1], entry.get("player", ""),
               (vegas.cache["events"].get(entry.get("event_id")) or {}).get("teams") or [])
              for key, entry in vegas.cache["lines"].items()]
    priced += [(pn, st, entry.get("player", ""), []) for (pn, st), entry in (ud_lines or {}).items()
               if f"{pn}|{st}" not in vegas.cache["lines"] and not entry.get("suspended")]
    # One candidate per (NBA name, stat), keyed like the board's locks so fill mode finds its picks
    # whatever spelling the book used; the book's own key is kept for the line history.
    for pname, stat, book_name, ev_teams in priced:
        if stat not in EDGE_STATS:
            continue
        who = _who({}, index, book_name, pname)
        roster_hit = _who(roster, {}, book_name, pname)
        name = (who or roster_hit or {}).get("name") or book_name or pname
        lk = lock_key(name, stat)
        if lk in candidates:
            continue
        team = _team_for(who, roster_hit, set(ev_teams), depth_charts, name, injuries, teams_playing, rosters)
        if team is None:
            skipped["unknown_team" if (who or roster_hit) is None or ev_teams else "not_playing"] += 1
            continue
        if team not in teams_playing:
            skipped["not_playing"] += 1
            continue
        mpg = (roster_hit or {}).get("mpg", 0.0)
        if mpg < MIN_CANDIDATE_MPG and lk not in locked:
            skipped["minutes"] += 1
            continue
        if is_player_out(injuries, name):
            skipped["out"] += 1
            if lk in board_meta and board_meta[lk].get("is_valid"):
                withdrawn.append(f"{name} {stat}")
            continue
        candidates[lk] = {"player": name, "team": team, "opponent": matchups.get(team), "stat": stat,
                          "hist_key": f"{pname}|{stat}"}
    # Locked picks are candidates too (so fill mode keeps them on the board) — unless ruled OUT since
    for lk in locked:
        stat = lk[1]
        if stat in EDGE_STATS and lk not in candidates and lk in board_meta:
            d = board_meta[lk]
            if is_player_out(injuries, d["player_name"]):
                if d.get("is_valid"):
                    withdrawn.append(f"{d['player_name']} {stat}")
                continue
            candidates[lk] = {"player": d["player_name"], "team": d["team"], "opponent": d["opponent"], "stat": stat,
                              "hist_key": _hist_key(d["player_name"], stat, history)}
    if withdrawn:
        logger.warning(f"  Withdrawn — ruled OUT since the board was published: {', '.join(sorted(set(withdrawn)))}")
    logger.info(f"  {len(candidates)} candidate props ({sum(skipped.values())} skipped: {skipped})")

    # ── Step 5: Project, price, grade ────────────────────────────────
    logger.info("Step 5: Projecting and pricing every candidate...")
    prop_scores: list[PropScore] = []
    projections: dict[tuple, dict] = {}
    for lk, c in candidates.items():
        name, team, opponent, stat = c["player"], c["team"], c["opponent"], c["stat"]
        if not opponent:
            continue
        key = c["hist_key"]
        locked_pick = board_meta.get(lk) if fill_holds and lk in locked else None
        if locked_pick:
            prop = _prop_from_board(locked_pick)
            prop.injury = injury_label(injuries, name) or prop.injury
            mv = movement(date, key, prop.edge, history=history)
            if mv:
                prop.open_line, prop.line_move = mv.get("open_line"), mv.get("move")
            prop_scores.append(prop)
            projections[lk] = prop.projection
            continue

        sample = player_sample(name, stat, before=date, seasons=(season_now, season_prev))
        dvp_rows, position = dvp_positions(name, depth_charts, team, rosters)
        position = position or "?"
        z, dvp_rel = dvp_blend(dvp, opponent, dvp_rows, stat)
        league = {"mean": 1.0, "sd": 1.0} if dvp_rel is not None else None   # dvp_rel is already z·sd/mean
        zs = ps = None
        if stat == "points":
            from src.shooting_zones import get_player_zone_shares
            zs = zone_score(get_player_zone_shares(name), team_zone_rankings, opponent)
            if team_playtype_rankings:
                from src.playtypes import get_player_playtype_shares
                ps = playtype_score(get_player_playtype_shares(name), team_playtype_rankings, opponent)
        n_out = teammate_outs(injuries, depth_charts, team, name)
        mult = matchup_multiplier(stat, dvp_rel, league, zs, ps, pace_factor(pace, team, opponent), teammate_boost(n_out))
        proj = project(stat, sample, multiplier=mult["multiplier"])
        if proj is None:
            logger.info(f"  {name} {stat}: no game-log sample — skipped")
            continue
        if stat == "assists":
            # passes made x potential assists per pass x conversion; the box-score projection gives minutes and spread
            pproj = passing_model.project_assists(pctx, name, team, opponent, proj, mult)
            if pproj is not None:
                proj, mult = pproj, pproj["adjustments"]
        projections[lk] = proj

        res = resolve(name, team, stat, vegas, ud_lines, not_started, locked)
        line = res.line["line"] if res.line else None
        current = vegas.lookup(name, stat) or {}
        ef = _decide(stat, proj, line, (res.line or {}).get("over_odds"), (res.line or {}).get("under_odds"), current) if line is not None else {}
        side = ef.get("side") or ("over" if proj["mu"] >= (line or proj["mu"]) else "under")

        prop = PropScore(name, team, opponent, position, stat, side)
        apply_line(prop, res)
        prop.projection = {**proj, "p_over": ef.get("p_model_over"), "p_under": ef.get("p_model_under"), "p_push": ef.get("p_push"), "adjustments": mult,
                           "p_hat": ef.get("p_hat"), "breakeven": ef.get("breakeven"), "market_basis": ef.get("market_basis")}
        prop.p_model, prop.p_market, prop.edge_value = ef.get("p_model"), ef.get("p_market"), ef.get("edge")
        prop.dvp_z, prop.matchup_z, prop.teammate_out = z, mult.get("matchup_z"), n_out
        if zs:
            prop.zone_score = {"points": 0, "total": zs.get("n", 0), "details": zs.get("details", [])}
        if ps:
            prop.playtype_score = {"points": 0, "total": ps.get("n", 0), "details": ps.get("details", [])}
        _shop_line(prop, stat, proj, current, res)   # a lagging book's number beats the consensus? take it
        line = prop.line
        mv = movement(date, key, side, history=history)
        if mv:
            prop.open_line, prop.line_move = mv.get("open_line"), mv.get("move")
        prop.hitrate_data = get_player_hitrate(name, stat, line, side, before=date)   # display + learner feature
        prop.paused = stat in PAUSED_STATS
        prop.guards = _guards(prop) if line is not None else []
        prop.compute_edge_grade()
        prop.injury = injury_label(injuries, name)
        if prop.on_hold:
            prop.hold_reason = hold_reason(stat, res, prop.injury)
        prop_scores.append(prop)
        logger.info(f"  → {prop.summary()}")
        time.sleep(0.3)

    _assert_stats_reachable("projections")
    _log_passing_vs_box(prop_scores)

    # ── Step 6: Combos — players with two A-grade singles on one side ─
    logger.info("Step 6: Building combo props...")
    by_player: dict[tuple, dict] = {}
    for p in prop_scores:
        if p.stat in EDGE_STATS and p.final_grade in ("A+", "A") and not p.paused:
            by_player.setdefault((p.player_name, p.team, p.edge), {})[p.stat] = p
    carried: set = set()
    if fill_holds:
        # Every combo on the earlier board keeps its line, edge and grade, whatever its singles do now
        for lk, d in board_meta.items():
            if lk[1] not in COMBO_DEFS or lk not in locked:
                continue
            if is_player_out(injuries, d["player_name"]):
                if d.get("is_valid"):
                    withdrawn.append(f"{d['player_name']} {lk[1]}")
                    logger.warning(f"  Withdrawn — ruled OUT since the board was published: {d['player_name']} {lk[1]}")
                continue
            combo = _prop_from_board(d)
            combo.injury = injury_label(injuries, d["player_name"]) or combo.injury
            mv = movement(date, _hist_key(d["player_name"], lk[1], history), combo.edge, history=history)
            if mv:
                combo.open_line, combo.line_move = mv.get("open_line"), mv.get("move")
            prop_scores.append(combo)
            carried.add(lk)
    combo_wants: dict = {}
    combo_specs = []
    for (name, team, side), stats in by_player.items():
        for combo_stat, cdef in COMBO_DEFS.items():
            comps = [stats.get(s) for s in cdef["components"]]
            if all(comps) and lock_key(name, combo_stat) not in carried:
                combo_specs.append((name, team, side, combo_stat, comps))
                combo_wants.setdefault(team, {}).setdefault(combo_stat, set()).add(name)
    if combo_wants:
        vegas.ensure(combo_wants)
        vegas.save()
        history = load_history(date)     # the combo markets just bought are in the history now
    for name, team, side, combo_stat, comps in combo_specs:
        if is_player_out(injuries, name):
            continue
        proj = combo_projection([c.projection for c in comps])
        if proj is None:
            continue
        if any(c.stat == "assists" for c in comps):
            ast = next(c for c in comps if c.stat == "assists")
            if (ast.projection or {}).get("source") == "passing":
                proj["source"] = "passing"      # its assists part came from passing (the passing guard reads this)
        res = resolve(name, team, combo_stat, vegas, ud_lines, not_started, locked)
        line = res.line["line"] if res.line else None
        current = vegas.lookup(name, combo_stat) or {}
        ef = _decide(combo_stat, proj, line, (res.line or {}).get("over_odds"), (res.line or {}).get("under_odds"), current) if (line is not None and proj) else {}
        combo = PropScore(name, team, comps[0].opponent, comps[0].position, combo_stat, side)
        apply_line(combo, res)
        # the combo's side is the components' side; read that side's numbers
        pm = ef.get(f"p_model_{side}"); pk = ef.get(f"p_market_{side}")
        be = implied_prob((res.line or {}).get("over_odds") if side == "over" else (res.line or {}).get("under_odds"))
        combo.projection = {**(proj or {}), "p_over": ef.get("p_model_over"), "p_under": ef.get("p_model_under"), "p_push": ef.get("p_push"), "breakeven": be,
                            "p_hat": (MODEL_TRUST * pm + (1 - MODEL_TRUST) * pk) if (pm is not None and pk is not None) else None,
                            "market_basis": ef.get("market_basis")}
        combo.p_model, combo.p_market = pm, pk
        combo.edge_value = (combo.projection["p_hat"] - be) if (combo.projection["p_hat"] is not None and be is not None) else None
        mv = movement(date, _hist_key(name, combo_stat, history), side, history=history)
        if mv:
            combo.open_line, combo.line_move = mv.get("open_line"), mv.get("move")
        combo.dvp_z = comps[0].dvp_z
        combo.teammate_out = comps[0].teammate_out
        combo.hitrate_data = get_player_hitrate(name, combo_stat, line, side, before=date)
        combo.paused = any(c.paused for c in comps)
        combo.guards = _guards(combo) if line is not None else []
        combo.zone_score = {"points": 0, "total": 0, "details": [f"  Combo: {cdef_label(combo_stat)} = " + " + ".join(f"{c.stat.upper()} (edge {c.edge_value:+.3f})" for c in comps)]}
        combo.compute_edge_grade()
        combo.injury = injury_label(injuries, name)
        if combo.on_hold:
            combo.hold_reason = hold_reason(combo_stat, res, combo.injury)
        prop_scores.append(combo)

    # ── Step 6b: The learning loop (shadow) ──────────────────────────
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
                if pred and model.get("trusted") and p.is_valid and not getattr(p, "pinned", False) and pred["ev"] < 0:
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

    # ── Step 6c: Board caps ──────────────────────────────────────────
    # In fill mode the picks already published keep their place; new picks
    # only fill the room the caps leave.
    pinned = [p for p in prop_scores if getattr(p, "pinned", False)]
    caps = apply_caps(prop_scores, pinned=pinned)
    held = [p for p in prop_scores if p.on_hold]
    by_source: dict = {}
    for p in prop_scores:
        src = (p.line_info or {}).get("source", "none")
        by_source[src] = by_source.get(src, 0) + 1
    n_locked = sum(1 for p in prop_scores if getattr(p.resolution, "locked", False))
    logger.info(f"  {len([p for p in prop_scores if p.is_valid])} picks after caps ({caps}); {len(held)} on hold; "
                f"lines by source {by_source}" + (f"; {n_locked} kept from the earlier board" if n_locked else "") + f" | {vegas.report()}")

    _assert_stats_reachable("player evaluation")

    # ── Step 7: Output ───────────────────────────────────────────────
    logger.info(f"\nStep 7: Generating report ({len(prop_scores)} props evaluated)...")
    report = format_report(prop_scores, date)
    save_report(report, date)
    save_json_report(prop_scores, date, run_mode="fill-holds" if fill_holds else "full", lines_locked=n_locked, caps=caps)
    print_report(report)

    valid_count = sum(1 for p in prop_scores if p.is_valid)
    logger.info(f"Done. {valid_count} valid picks out of {len(prop_scores)} evaluated.")


def _log_passing_vs_box(prop_scores: list) -> None:
    """How far the passing projection moved assists from the box-score one: the median and the five largest moves."""
    moves, missing = [], []
    for p in prop_scores:
        proj = p.projection or {}
        if p.stat != "assists" or getattr(p, "pinned", False) or getattr(getattr(p, "resolution", None), "locked", False):
            continue
        if proj.get("source") == "passing" and proj.get("mu_box") is not None:
            moves.append((float(proj["mu"]) - float(proj["mu_box"]), p.player_name, float(proj["mu"]), float(proj["mu_box"])))
        elif proj.get("mu") is not None:
            missing.append(p.player_name)
    if missing:
        logger.warning(f"  {len(missing)} assists prop(s) without a passing projection (held back by the passing guard): "
                       + ", ".join(sorted(missing)[:12]) + (" ..." if len(missing) > 12 else ""))
    if not moves:
        return
    diffs = sorted(abs(m[0]) for m in moves)
    median = diffs[len(diffs) // 2]
    top = sorted(moves, key=lambda m: -abs(m[0]))[:5]
    logger.info(f"  Assists from passing vs box score: {len(moves)} props, median move {median:.2f}; largest "
                + ", ".join(f"{n} {mu:.1f} (box {box:.1f})" for _d, n, mu, box in top))


def cdef_label(combo_stat: str) -> str:
    return COMBO_DEFS.get(combo_stat, {}).get("label", combo_stat.upper())


def main():
    parser = argparse.ArgumentParser(description="NBA Player Prop Model — scores today's player props")
    parser.add_argument("--date", type=str, default=None, help="Date to run model for (YYYY-MM-DD). Defaults to today.")
    parser.add_argument("--tomorrow", action="store_true", help="Build tomorrow's board (the night-before run).")
    parser.add_argument("--fill-holds", action="store_true",
                        help="Afternoon re-check: keep every published pick as it was (withdrawn if the player is ruled OUT), re-price "
                             "and re-grade everything else, and add new picks into the room the caps leave.")
    parser.add_argument("--snapshot", action="store_true",
                        help="Re-price the games tipping within the next SNAPSHOT_WINDOW_MINUTES (45) for closing lines (CLV); writes no board.")
    parser.add_argument("--fast", action="store_true", help="Skip the playtype tables (fewer API calls, less detail).")
    args = parser.parse_args()
    date = args.date
    if args.tomorrow and not date:
        from datetime import timedelta
        date = (datetime.strptime(slate_today(), "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    try:
        run(date=date, fast=args.fast, fill_holds=args.fill_holds, snapshot=args.snapshot)
    except Exception as e:
        logger.error(f"Model run failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
