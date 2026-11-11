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

from src.config import (
    CONDITION_PASS_RATE, CONDITION_PASS_RATE_POINTS, HITRATE_THRESHOLD, MAX_PER_GAME, MAX_PICKS, MAX_PRICE,
    BLOWOUT_SPREAD, MIN_BOOKS, MIN_EDGE, MIN_FAIR_BOOKS, MIN_LINE, MIN_PRICE_EDGE, MIN_PROJ_MINUTES, OVER_MIN_SUPPORT, PAUSED_STATS,
    STRONG_EDGE, STRONG_PRICE_EDGE, UNDER_MIN_SUPPORT, season_for_date, slate_today,
)
from src.season import stats_season, stats_season_reason

logger = logging.getLogger(__name__)

LOGS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")

GRADE_RANK = {"A+": 6, "A": 5, "B+": 4, "B": 3, "C": 2, "D": 1, "F": 0}

# The JSON key for the modelled edge is edge_value (p_model − p_market, pick
# side): "edge" has always been the side (over/under) in every board, CSV and
# graded file, so the number travels under the PropScore attribute's name.
MODEL_VERSION_MARKET = "market-2"   # round 23: the books' fair price, the best price on offer, the checklist
MODEL_VERSION_EDGE = "edge-1"       # round 18: projection vs the de-vigged market price
MODEL_VERSION_FILTER = "filter-0"   # the legacy DvP-colour + hit-rate-gate filter


def _num_or_none(v, digits: int | None = None):
    """A finite number rounded to `digits` (or as is), else None."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if v != v or v in (float("inf"), float("-inf")):
        return None
    return round(float(v), digits) if digits is not None else v


def rank_key(p, active: bool = False) -> tuple:
    """
    Best pick first: grade (A+ before A), then the modelled edge over the
    market when the prop has one, then — once the learner is ACTIVE —
    expected value at the price, else the hit rate against the line, then the
    share of conditions met, then how many books hang the line.
    """
    hr = p.hitrate_data.get("hitrate", -1) if isinstance(getattr(p, "hitrate_data", None), dict) else -1
    hr = hr if isinstance(hr, (int, float)) and hr >= 0 else -1
    edge = _num_or_none(getattr(p, "edge_value", None))
    learned = getattr(p, "learned", None) or {}
    ev = learned.get("ev") if active and learned.get("active") and learned.get("ev") is not None else None
    books = (p.line_info or {}).get("books") or 0
    return (-GRADE_RANK.get(p.final_grade, 0), -(edge if edge is not None else -9.0),
            -(ev if ev is not None else -9.0), -hr, -p.total_score_pct, -books)


def rank_picks(prop_scores: list, active: bool = False) -> list:
    return sorted(prop_scores, key=lambda p: rank_key(p, active))


def _has_edge(props: list) -> bool:
    """Did the edge model price any of these props (p_model set)?"""
    return any(_num_or_none(getattr(p, "p_model", None)) is not None for p in props)


def _is_market(p) -> bool:
    """Was this prop graded by the market-anchored model (round 23)?"""
    return getattr(p, "support", None) is not None


def _pct(v) -> str:
    v = _num_or_none(v)
    return f"{v:.0%}" if v is not None else "-"


def _edge_pts(v) -> str:
    v = _num_or_none(v)
    return f"{v * 100:+.1f}" if v is not None else "-"


def _table_rows(props: list, with_edge: bool) -> list:
    """One report row per prop; the edge columns (Model %, Market %, Edge) when the board has them."""
    rows = []
    for p in props:
        hr = p.hitrate_data.get("hitrate", -1)
        hr_str = f"{hr:.0%}" if hr >= 0 else "N/A"
        line_str = str(p.line) if p.line is not None else "-"
        odds = f"{p.side_odds()} {p.side_book()}".strip() if p.line is not None else "-"
        row = [p.player_name, p.team, p.opponent, p.position, p.stat.upper(), p.edge.upper(), line_str, odds]
        if with_edge:
            if _is_market(p):       # round 23: the fair price, the checklist's net, the price edge
                sup = p.support or {}
                row += [_pct((getattr(p, "fair", None) or {}).get("p")), f"{sup.get('net', 0):+d} (needs {sup.get('need', 0)})",
                        _edge_pts(getattr(p, "edge_value", None))]
            else:
                row += [_pct(getattr(p, "p_model", None)), _pct(getattr(p, "p_market", None)), _edge_pts(getattr(p, "edge_value", None))]
        row += [f"{p.total_points}/{p.total_conditions}", f"{p.total_score_pct:.0%}", hr_str, p.final_grade]
        rows.append(row)
    return rows


def format_report(prop_scores: list, date: str | None = None) -> str:
    """Build the full daily report string."""
    if date is None:
        date = slate_today()

    valid = [p for p in prop_scores if p.is_valid]
    on_hold = [p for p in prop_scores if getattr(p, "on_hold", False)]
    capped = [p for p in prop_scores if getattr(p, "cap_note", "")]
    paused = [p for p in prop_scores if getattr(p, "paused", False) and p.final_grade in ("A+", "A")]
    watchlist = [
        p for p in prop_scores
        if not p.is_valid and not getattr(p, "on_hold", False) and p.final_grade in ("B+", "B")
    ]
    with_edge = _has_edge(prop_scores)

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
    ]
    if with_edge:
        lines += [
            f"  Capped (board)      : {len(capped)}",
            f"  Paused              : {len(paused)}" + (f" ({', '.join(sorted(PAUSED_STATS))})" if PAUSED_STATS else ""),
            f"  Thresholds          : edge >= {MIN_EDGE * 100:.1f} pts (A), >= {STRONG_EDGE * 100:.1f} pts (A+) | "
            f"price >= {MAX_PRICE} | >= {MIN_BOOKS} books | line >= {MIN_LINE} | >= {MIN_PROJ_MINUTES:.0f} min | "
            f"{MAX_PICKS} on the board, {MAX_PER_GAME} per game",
            "",
        ]
    else:
        lines += [
            f"  Thresholds          : >={CONDITION_PASS_RATE:.0%} conditions (points {CONDITION_PASS_RATE_POINTS:.0%}) | >={HITRATE_THRESHOLD:.0%} hit rate",
            "",
        ]

    headers = ["Player", "Team", "Opp", "Pos", "Stat", "Edge", "Line", "Odds"]
    if with_edge:
        headers += (["Fair %", "Checks", "Edge pts"] if any(_is_market(p) for p in prop_scores) else ["Model %", "Market %", "Edge pts"])
    headers += ["Score", "Pct", "HR", "Grade"]

    # ── VALID PICKS ──────────────────────────────────────────────────
    if valid:
        lines.append("┌─────────────────────────────────────────────────────────────────────┐")
        lines.append("│  ✅  VALID PICKS                                                     │")
        lines.append("└─────────────────────────────────────────────────────────────────────┘")
        lines.append("")
        lines.append(tabulate(_table_rows(rank_picks(valid), with_edge), headers=headers, tablefmt="rounded_outline"))
        lines.append("")
    else:
        lines.append("  No valid picks today.\n")

    # ── CAPPED / PAUSED (edge model) ─────────────────────────────────
    if capped:
        lines.append("┌─────────────────────────────────────────────────────────────────────┐")
        lines.append("│  ✂  CAPPED  (A-grade edges dropped by the board caps)                 │")
        lines.append("└─────────────────────────────────────────────────────────────────────┘")
        lines.append("")
        lines.append(tabulate(
            [[p.player_name, p.team, p.opponent, p.stat.upper(), p.edge.upper(), str(p.line) if p.line is not None else "-",
              _edge_pts(getattr(p, "edge_value", None)), p.final_grade, p.cap_note]
             for p in rank_picks(capped)],
            headers=["Player", "Team", "Opp", "Stat", "Edge", "Line", "Edge pts", "Grade", "Why"],
            tablefmt="rounded_outline",
        ))
        lines.append("")
    if paused:
        lines.append("┌─────────────────────────────────────────────────────────────────────┐")
        lines.append("│  ⏯  PAUSED  (A-grade edges in a paused stat — shown, never published) │")
        lines.append("└─────────────────────────────────────────────────────────────────────┘")
        lines.append("")
        lines.append(tabulate(
            [[p.player_name, p.team, p.opponent, p.stat.upper(), p.edge.upper(), str(p.line) if p.line is not None else "-",
              _edge_pts(getattr(p, "edge_value", None)), p.final_grade]
             for p in rank_picks(paused)],
            headers=["Player", "Team", "Opp", "Stat", "Edge", "Line", "Edge pts", "Grade"],
            tablefmt="rounded_outline",
        ))
        lines.append("")

    # ── ON HOLD ──────────────────────────────────────────────────────
    if on_hold:
        lines.append("┌─────────────────────────────────────────────────────────────────────┐")
        lines.append("│  ⏸  ON HOLD  (no line right now — re-checked on the next run)         │")
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
        if with_edge:
            lines.append("│  👀  WATCHLIST  (B+/B: a positive edge below the A bar)              │")
        else:
            lines.append("│  👀  WATCHLIST  (60-79% conditions met)                              │")
        lines.append("└─────────────────────────────────────────────────────────────────────┘")
        lines.append("")
        lines.append(tabulate(_table_rows(rank_picks(watchlist), with_edge), headers=headers, tablefmt="rounded_outline"))
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


def _r4(x):
    try:
        return None if x is None else round(float(x), 4)
    except (TypeError, ValueError):
        return None


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
        # the designated line: sportsbooks first, DFS apps as backup (src/lines.py)
        "line": p.line,
        "line_source": (p.line_info or {}).get("source"),
        "line_book": p.side_book() or None,
        "line_books": (p.line_info or {}).get("books"),
        "line_locked": bool(getattr(getattr(p, "resolution", None), "locked", False)),
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
        # ── round 18: the edge model (src/projection.py, src/matchup.py, src/line_history.py) ──
        **_edge_fields(p),
    }


def _edge_fields(p) -> dict:
    """
    The edge model's view of a prop: the projection, the modelled and market
    probabilities of the pick side at the pick line, the edge between them,
    where the line opened and how it moved (signed toward the pick), the
    matchup z-scores, teammate outs, the pause/cap state and the guards that
    failed. model_version says which model graded the prop: "edge-1" when it
    was projected and priced, "filter-0" for the legacy filter (older boards
    and props the projection could not cover).
    """
    proj = getattr(p, "projection", None) or {}
    p_model = _num_or_none(getattr(p, "p_model", None), 4)
    guards = getattr(p, "guards", None) or []
    fair = getattr(p, "fair", None) or {}
    market = _is_market(p)
    return {
        # ── round 23: the market-anchored model (src/fair.py, src/checklist.py) ──
        # p_fair: the books' fair probability of the pick side (after any passed check); fair_over: that fair price
        # for the over as the books mean it; edge_value is then the PRICE edge, p_fair − the best price's break-even
        "p_fair": _r4(fair.get("p")) if market else None,
        "fair_over": _r4(fair.get("over")) if market else None,
        "fair_books": fair.get("books") if market else None,
        "fair_basis": fair.get("basis") if market else None,
        "fair_low": _r4(fair.get("low")) if market else None,
        "fair_high": _r4(fair.get("high")) if market else None,
        "adjustments": (fair.get("adjustments") or None) if market else None,
        "checks": (getattr(p, "checks", None) or []) if market else None,
        "support": getattr(p, "support", None),
        "proj_mu": _num_or_none(proj.get("mu"), 2),
        "proj_sd": _num_or_none(proj.get("sd"), 2),
        "proj_minutes": _num_or_none(proj.get("minutes"), 1),
        "proj_dist": proj.get("dist") or None,
        "n_this": _num_or_none(proj.get("n_this")),
        "n_last": _num_or_none(proj.get("n_last")),
        "weight_this": _num_or_none(proj.get("weight_this"), 3),
        "p_hat": _r4((getattr(p, "projection", None) or {}).get("p_hat")),
        "breakeven_price": _r4((getattr(p, "projection", None) or {}).get("breakeven")),
        "market_basis": (getattr(p, "projection", None) or {}).get("market_basis"),
        "consensus_line": (p.line_info or {}).get("consensus_line"),
        "consensus_books": (p.line_info or {}).get("consensus_books"),
        "line_shopped": bool((p.line_info or {}).get("shopped")),          # boards before round 20 only
        "alt_line": _alt_fields(getattr(p, "alt_line", None)),
        "p_model": p_model,
        "p_market": _num_or_none(getattr(p, "p_market", None), 4),
        "edge_value": _num_or_none(getattr(p, "edge_value", None), 4),
        "open_line": _num_or_none(getattr(p, "open_line", None)),
        "line_move": _num_or_none(getattr(p, "line_move", None), 2),
        "dvp_z": _num_or_none(getattr(p, "dvp_z", None), 2),
        "matchup_z": _num_or_none(getattr(p, "matchup_z", None), 2),
        "teammate_out": int(getattr(p, "teammate_out", 0) or 0),
        "paused": bool(getattr(p, "paused", False)),
        "cap_note": getattr(p, "cap_note", "") or None,
        "guards": [str(g) for g in guards],
        "model_version": MODEL_VERSION_MARKET if market else MODEL_VERSION_EDGE if p_model is not None else MODEL_VERSION_FILTER,
        # round 19: "passing" when the assists came from passing (src/passing.py), with its pieces per game
        # round 22: where and when the game is played (home/away was never on the boards before)
        "is_home": getattr(p, "is_home", None),
        "tip_utc": getattr(p, "tip_utc", None),
        # round 26: his team's point spread (negative = favoured); an over is never published at BLOWOUT_SPREAD or more
        "spread": _num_or_none(getattr(p, "spread", None), 1),
        "proj_source": proj.get("source") or None,
        "proj_mu_box": _num_or_none(proj.get("mu_box"), 2),
        "passing": _passing_fields(proj.get("passing")),
    }


def _alt_fields(alt) -> dict | None:
    """The alternate number beside a pick (round 20): book, line, price, kind and its probabilities and edge; None without one."""
    if not isinstance(alt, dict):
        return None
    out = {"book": alt.get("book"), "line": _num_or_none(alt.get("line")), "price": alt.get("price"), "kind": alt.get("kind")}
    for k in ("p_model", "p_market", "p_hat", "breakeven", "edge"):
        out[k] = _num_or_none(alt.get(k), 4)
    return out


def _passing_fields(pieces) -> dict | None:
    """The passing projection's pieces for the board (per game at the projected minutes), rounded; None without them."""
    if not isinstance(pieces, dict):
        return None
    digits = {"passes": 1, "pot_ast": 1, "pa_per_pass": 3, "conv": 3, "conv_seen": 3, "conv_prior": 3,
              "team_shooting": 3, "opp_shooting": 3, "opp_share": 3, "base": 2, "weight_this": 3}
    out = {k: _num_or_none(pieces.get(k), d) for k, d in digits.items()}
    out["gp_this"] = _num_or_none(pieces.get("gp_this"))
    out["gp_last"] = _num_or_none(pieces.get("gp_last"))
    return out


# Board lists that repeat props already in all_props, and season-long blocks that every board would repeat
_ARCHIVE_SKIP = ("valid_picks", "watchlist", "valid_combos", "watchlist_combos", "on_hold", "demoted", "paused", "capped",
                 "model_results", "learning", "season_record", "hitrate_summary", "scorecard")


def _archive_board(payload: dict, date: str) -> str | None:
    """
    Keep this run's board as it was written: logs/boards/{date}/{HHMMSS}.json.gz
    (UTC). props_{date}.json is overwritten by every later run of the day, so
    without this only the day's last board survives; a backtest needs the
    board as it stood at each run. Every prop is kept once (all_props, with
    its is_valid / watchlist flags); the published order is kept as
    [player, stat] pairs. Never raises.
    """
    import gzip
    try:
        slim = {k: v for k, v in payload.items() if k not in _ARCHIVE_SKIP}
        for key in ("valid_picks", "valid_combos"):
            slim[f"{key}_order"] = [[p.get("player_name"), p.get("stat")] for p in payload.get(key) or []]
        folder = os.path.join(LOGS_DIR, "boards", date)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"{datetime.now(timezone.utc).strftime('%H%M%S')}.json.gz")
        with gzip.open(path + ".tmp", "wt", encoding="utf-8") as f:
            json.dump(slim, f, separators=(",", ":"))
        os.replace(path + ".tmp", path)
        return path
    except Exception as e:      # an archive must never cost a board
        logger.warning(f"Board archive not written ({type(e).__name__}: {e})")
        return None


def save_json_report(prop_scores: list, date: str | None = None, run_mode: str = "full", lines_locked: int = 0,
                     caps: dict | None = None) -> str:
    """Save model results as JSON for the web dashboard.

    run_mode is "full" (the night-before and morning boards) or "fill-holds"
    (an afternoon re-check that kept `lines_locked` lines from the earlier board).
    `caps` is what src.caps.apply_caps returned for this board (None when the
    caps were not applied), stored as the payload's "caps".
    """
    if date is None:
        date = slate_today()

    # Separate individual and combo props
    individual = [p for p in prop_scores if p.stat not in COMBO_STATS]
    combos = [p for p in prop_scores if p.stat in COMBO_STATS]

    def _held(p):
        return bool(getattr(p, "on_hold", False))

    # Best first: A+ before A, then the edge over the market, then EV (learner ACTIVE only)
    # or hit rate vs the line, conditions met, books
    active = any((getattr(p, "learned", None) or {}).get("active") for p in prop_scores)
    valid = rank_picks([p for p in individual if p.is_valid], active)
    watchlist = rank_picks([p for p in individual if not p.is_valid and not _held(p) and p.final_grade in ("B+", "B")], active)
    valid_combos = rank_picks([p for p in combos if p.is_valid], active)
    watchlist_combos = rank_picks([p for p in combos if not p.is_valid and not _held(p) and p.final_grade in ("B+", "B")], active)
    on_hold = [p for p in prop_scores if _held(p)]
    # Edge model: A-grade edges in a paused stat (shown, never published) and A-grade edges the caps dropped
    paused = rank_picks([p for p in prop_scores if getattr(p, "paused", False) and p.final_grade in ("A+", "A")], active)
    capped = rank_picks([p for p in prop_scores if getattr(p, "cap_note", "")], active)

    # One dict per prop, shared by every list it appears in, so the rank tagged
    # on a board list is on the same row in all_props (and so in picks.csv).
    dicts = {id(p): _prop_to_dict(p) for p in prop_scores}

    def _dicts(ps):
        return [dicts[id(p)] for p in ps]

    def _ranked_dicts(ps):
        out = _dicts(ps)
        for i, d in enumerate(out, 1):
            d["rank"] = i
        return out

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
        "run_mode": run_mode,
        "lines_locked": lines_locked,
        "candidates_analyzed": len(individual),
        "valid_count": len(valid),
        "watchlist_count": len(watchlist),
        "combo_valid_count": len(valid_combos),
        "combo_watchlist_count": len(watchlist_combos),
        "on_hold_count": len(on_hold),
        "demoted_count": sum(1 for p in prop_scores if getattr(p, "learn_note", "")),
        "paused_count": len(paused),
        "capped_count": len(capped),
        "model_version": (MODEL_VERSION_MARKET if any(_is_market(p) for p in prop_scores)
                          else MODEL_VERSION_EDGE if _has_edge(prop_scores) else MODEL_VERSION_FILTER),
        "paused_stats": sorted(PAUSED_STATS),
        "caps": caps,
        "thresholds": {
            "condition_pass_rate": CONDITION_PASS_RATE,
            "condition_pass_rate_points": CONDITION_PASS_RATE_POINTS,
            "hitrate": HITRATE_THRESHOLD,
            # the edge model's bars (src/config.py)
            "min_edge": MIN_EDGE,
            "strong_edge": STRONG_EDGE,
            "max_price": MAX_PRICE,
            "min_books": MIN_BOOKS,
            "min_line": MIN_LINE,
            "min_proj_minutes": MIN_PROJ_MINUTES,
            "max_picks": MAX_PICKS,
            "max_per_game": MAX_PER_GAME,
            # the market-anchored model's bars (round 23)
            "min_price_edge": MIN_PRICE_EDGE,
            "strong_price_edge": STRONG_PRICE_EDGE,
            "under_min_support": UNDER_MIN_SUPPORT,
            "over_min_support": OVER_MIN_SUPPORT,
            "min_fair_books": MIN_FAIR_BOOKS,
            "blowout_spread": BLOWOUT_SPREAD,
        },
        "valid_picks": _ranked_dicts(valid),
        "watchlist": _ranked_dicts(watchlist),
        "valid_combos": _ranked_dicts(valid_combos),
        "watchlist_combos": _ranked_dicts(watchlist_combos),
        "on_hold": _dicts(on_hold),
        "demoted": _dicts([p for p in prop_scores if getattr(p, "learn_note", "")]),
        "paused": _dicts(paused),
        "capped": _dicts(capped),
        "all_props": _dicts(prop_scores),
        "hitrate_summary": hitrate_summary,
    }
    try:
        from src.learner import summary as learner_summary
        payload["learning"] = learner_summary()
    except Exception as e:
        logger.warning(f"Learning summary unavailable ({e})")
    try:
        from src.record import season_record
        payload["season_record"] = season_record(payload["season"])
    except Exception as e:
        logger.warning(f"Season record unavailable ({e})")

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
    _archive_board(payload, date)
    logger.info(f"JSON report saved to {path}")

    # Also save as latest.json for the web dashboard, with the signal scorecard
    # the last grading run wrote (site only: the day's board file stays a board)
    try:
        from src.scorecard import load as load_scorecard
        card = load_scorecard()
        if card:
            payload["scorecard"] = card
    except Exception as e:
        logger.warning(f"Scorecard unavailable ({e})")
    os.makedirs(web_data_dir, exist_ok=True)
    latest_path = os.path.join(web_data_dir, "latest.json")
    with open(latest_path, "w") as f:
        json.dump(payload, f, indent=2)
    logger.info(f"Web data saved to {latest_path}")

    return path
