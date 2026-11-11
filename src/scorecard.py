"""
The signal scorecard (round 22) — the rebuild's evaluation spine.

The model that picked from a projection had no predictive power beyond the
sportsbooks' own price, so the rebuild starts from that price and lets a
signal move it only after the signal has EARNED it. This module is where a
signal earns it. Every night, after grading, every candidate signal is scored
the same way on every labeled prop of the season:

  rows        one per prop with a line and a result, on its REGULAR line
              (src.lines.on_main_line), asked one question: did it go OVER?
              The baseline is the de-vigged market probability of the over at
              that line. Nothing about a row depends on which side any model
              leaned to, so the rows stay valid when the model changes.
  signal      a number per row pointed toward the over (positive = the check
              favours the over), or missing.
  weight      the signal's coefficient beside the market's log-odds in a
              logistic fit on all rows, with a 95% interval from standard
              errors clustered by night.
  vs price    walk-forward: fit on earlier nights only, score later ones,
              pool; the change in log-loss against the price alone fitted on
              the same earlier nights (negative = better than the price).
  bets        among the out-of-sample rows, bet the over where its fitted
              probability clears the over's break-even by BET_MARGIN, the
              under where the under's does; units per bet at the price
              actually offered, with an interval resampled by night.

The first entry is the price itself, re-fitted ("price_refit"): intercept and
slope only. Its intercept is how far the over's price has been off (negative
= overs overpriced), and it is the rebuilt model's starting point: every
other signal has to improve on IT, so the over's shading is never credited
to a check that merely leans under.

Verdicts, in this order:
  "beats the price"  out of sample, the fit with the signal has a lower
                     log-loss than the fit with the price alone, with a 95%
                     interval (resampled by night) that stays below zero, AND
                     its bets returned a profit. Either sign of the weight
                     counts: a check that reliably points the wrong way is as
                     usable as one that points the right way.
  "leans right"      the weight is positive and its interval excludes zero,
                     or it improves the out-of-sample log-loss without proof.
  "negative"         the weight's interval sits below zero.
  "no evidence"      none of the above.
A signal PASSES — may move a published probability in the rebuilt model —
only when it beats the price on at least PASS_MIN_NIGHTS out-of-sample nights
and PASS_MIN_ROWS rows. Those bars are fixed here, before the evidence, and
are out of reach for a few weeks of data on purpose. About twenty signals are
tested, so one may pass by luck: a passing signal is re-tested every night
and loses its place the night it stops passing.

The checklist (src/checklist.py) is scored the same way: each check's
reading and the checklist's net lean are signals. Boards from round 23 on
carry their readings; for earlier boards they are replayed from what was on
file that night (_replay_checks), so a check has evidence from the first
sportsbook board of the season rather than from the day it was written.

The market itself is summarised too: how favourites and underdogs did against
their prices, and what betting every over or every under returned by stat.

Output: logs/scorecard.json, mirrored into latest.json["scorecard"] for the
site. numpy only. Reads boards (logs/props_*.json) and labeled actuals
(logs/training/labels_*.json); never touches the network.
"""

import glob
import json
import logging
import math
import os
from collections import defaultdict
from datetime import date as _date, datetime, timezone

import numpy as np

from src.checklist import BARS as CHECK_BARS, LABELS as CHECK_LABELS
from src.config import SEASON, season_for_date
from src.lines import on_main_line
from src.projection import implied_prob, market_prob, p_sides

logger = logging.getLogger(__name__)

LOGS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
WEB_LATEST = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web", "public", "data", "latest.json")

MIN_TRAIN_NIGHTS = 5        # walk-forward starts on the 6th night that has the signal
MAX_REFITS = 40             # refits per signal (evenly spaced over the season; each uses earlier nights only)
BET_MARGIN = 0.02           # bet when the fitted probability clears break-even by this much
PASS_MIN_NIGHTS = 25        # out-of-sample nights a signal needs before it may move a probability
PASS_MIN_ROWS = 1500        # ... and out-of-sample rows
MIN_ROWS = 200              # below this a signal is "too little data"
MIN_BETS = 100              # bets a signal's betting test needs before its return counts
FIRST_SPORTSBOOK_BOARD = "2026-10-22"

SINGLES = ("points", "rebounds", "assists")
COMBOS = {"pra": ("points", "rebounds", "assists"), "pts+reb": ("points", "rebounds"),
          "pts+ast": ("points", "assists"), "reb+ast": ("rebounds", "assists")}
STAT_LABELS = {"points": "Points", "rebounds": "Rebounds", "assists": "Assists", "pts+reb": "Points + rebounds",
               "pts+ast": "Points + assists", "reb+ast": "Rebounds + assists", "pra": "Points + rebounds + assists"}


# ── numerics ──────────────────────────────────────────────────────────────

def _logit(p):
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def _sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


def fit_logistic(X, y, l2: float = 1e-6):
    """Logistic regression by damped Newton; X without the intercept column. Returns beta (intercept first)."""
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    Z = np.hstack([np.ones((X.shape[0], 1)), X])
    k = Z.shape[1]
    R = np.eye(k) * l2
    R[0, 0] = 1e-9
    beta = np.zeros(k)

    def ll(b):
        eta = np.clip(Z @ b, -30, 30)
        return float(np.sum(y * eta - np.logaddexp(0, eta)) - 0.5 * b @ R @ b)

    for _ in range(60):
        p = _sigmoid(Z @ beta)
        grad = Z.T @ (y - p) - R @ beta
        H = Z.T @ (Z * (p * (1 - p))[:, None]) + R
        try:
            step = np.linalg.solve(H, grad)
        except np.linalg.LinAlgError:
            step = np.linalg.lstsq(H, grad, rcond=None)[0]
        t, base = 1.0, ll(beta)
        while t > 1e-4 and ll(beta + t * step) < base - 1e-12:
            t *= 0.5
        beta = beta + t * step
        if np.max(np.abs(t * step)) < 1e-9:
            break
    return beta


def _predict(beta, X):
    X = np.asarray(X, dtype=float)
    return _sigmoid(beta[0] + X @ beta[1:])


def clustered_ci(X, y, groups):
    """(beta, 95% intervals) with standard errors clustered by `groups` (nights): the sandwich H^-1 (sum g g') H^-1."""
    beta = fit_logistic(X, y)
    Z = np.hstack([np.ones((len(y), 1)), np.asarray(X, dtype=float)])
    p = _sigmoid(Z @ beta)
    H = Z.T @ (Z * (p * (1 - p))[:, None]) + np.eye(Z.shape[1]) * 1e-9
    Hi = np.linalg.pinv(H)
    meat = np.zeros_like(H)
    resid = (np.asarray(y, dtype=float) - p)[:, None] * Z
    for g in np.unique(groups):
        s = resid[groups == g].sum(axis=0)
        meat += np.outer(s, s)
    n_g = len(np.unique(groups))
    adj = n_g / max(n_g - 1, 1)
    se = np.sqrt(np.maximum(np.diag(Hi @ meat @ Hi) * adj, 0.0))
    return beta, [(float(b - 1.96 * s), float(b + 1.96 * s)) for b, s in zip(beta, se)]


def _payout(odds) -> float | None:
    """Profit on a 1-unit stake at American odds; None when unpriced."""
    try:
        o = float(str(odds).replace("+", ""))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(o) or abs(o) < 100:
        return None
    return o / 100.0 if o > 0 else 100.0 / abs(o)


def night_ci(units, nights, all_nights, n_boot: int = 1000, seed: int = 22) -> list | None:
    """95% interval of units per bet, resampling NIGHTS with replacement (nights without a bet count as empty)."""
    if len(units) == 0:
        return None
    rng = np.random.default_rng(seed)
    per = defaultdict(lambda: [0.0, 0])
    for u, d in zip(units, nights):
        per[d][0] += u
        per[d][1] += 1
    all_nights = list(all_nights)
    sums = np.array([per[d][0] for d in all_nights])
    cnts = np.array([per[d][1] for d in all_nights])
    idx = rng.integers(0, len(all_nights), size=(n_boot, len(all_nights)))
    tot, n = sums[idx].sum(axis=1), cnts[idx].sum(axis=1)
    vals = tot[n > 0] / n[n > 0]
    if len(vals) == 0:
        return None
    return [round(float(np.percentile(vals, 2.5)), 4), round(float(np.percentile(vals, 97.5)), 4)]


def night_mean_ci(values, nights, n_boot: int = 1000, seed: int = 22) -> list | None:
    """95% interval of the mean of per-row `values`, resampling NIGHTS with replacement."""
    if len(values) == 0:
        return None
    rng = np.random.default_rng(seed)
    uniq, inv = np.unique(nights, return_inverse=True)
    sums = np.bincount(inv, weights=values, minlength=len(uniq))
    cnts = np.bincount(inv, minlength=len(uniq)).astype(float)
    idx = rng.integers(0, len(uniq), size=(n_boot, len(uniq)))
    vals = sums[idx].sum(axis=1) / cnts[idx].sum(axis=1)
    return [round(float(np.percentile(vals, 2.5)), 5), round(float(np.percentile(vals, 97.5)), 5)]


# ── the rows ──────────────────────────────────────────────────────────────

def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _zscore_sum(lines) -> float | None:
    """'Restricted Area: 44% of FGA × z +0.6' lines -> the share-weighted z."""
    import re
    tot, n = 0.0, 0
    for s in lines or []:
        m = re.match(r"\s*.+?: (\d+)% of (?:FGA|poss) × z ([+-]?\d+(?:\.\d+)?)", str(s))
        if m:
            tot += int(m.group(1)) / 100.0 * float(m.group(2))
            n += 1
    return tot if n else None


def _pos_group(pos) -> str:
    p = str(pos or "").upper()
    if p in ("PG", "SG", "G"):
        return "G"
    if p in ("SF", "PF", "F", "G-F", "F-G"):
        return "F"
    if p in ("C", "C-F", "F-C"):
        return "C"
    return "?"


def _actuals(logs_dir: str, season: str) -> dict:
    """{(date, player, stat): actual} from the labeled rows, with combos and missing parts filled from each other."""
    out: dict = {}
    for path in sorted(glob.glob(os.path.join(logs_dir, "training", "labels_*.json"))):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or data.get("incomplete"):
            continue
        for r in data.get("rows", []) or []:
            if r.get("actual") is not None and r.get("date") and season_for_date(_date.fromisoformat(r["date"])) == season:
                out[(r["date"], r.get("player"), r.get("stat"))] = float(r["actual"])
    by: dict = defaultdict(dict)
    for (d, pl, st), v in out.items():
        by[(d, pl)][st] = v
    for (d, pl), stats in by.items():
        for combo, parts in COMBOS.items():
            if combo not in stats and all(p in stats for p in parts):
                out[(d, pl, combo)] = sum(stats[p] for p in parts)
            elif combo in stats:
                missing = [p for p in parts if p not in stats]
                if len(missing) == 1:
                    out.setdefault((d, pl, missing[0]), stats[combo] - sum(stats[p] for p in parts if p in stats))
    return out


def _row(raw: dict, d: str, actual, rest) -> dict | None:
    """
    One prop as a scorecard row, or None when it cannot be one (not a stat the
    board grades, no regular line, a one-sided price). `actual` is the stat
    total (None for a prop that has not been played: the row then has no
    result and is only ever READ by the history signals).
    """
    stat = raw.get("stat")
    if stat not in SINGLES and stat not in COMBOS:
        return None
    side = str(raw.get("edge", "")).lower()
    shopped = bool(raw.get("line_shopped"))
    p = on_main_line(raw)
    line = _num(p.get("line"))
    if line is None:
        return None
    if implied_prob(p.get("over_odds")) is None or implied_prob(p.get("under_odds")) is None:
        return None        # one side unpriced: there is no two-way market to take the vig out of
    mp = market_prob(p.get("over_odds"), p.get("under_odds"))
    sign = 1.0 if side == "over" else -1.0 if side == "under" else None
    mu, sd = _num(p.get("proj_mu")), _num(p.get("proj_sd"))
    p_model = p_sides(stat, mu, sd, line)[0] if mu is not None and sd else None
    over_hit = None if actual is None else (1 if actual > line else 0)
    toward = lambda v: None if v is None or sign is None else v * sign      # noqa: E731 — board numbers signed toward the pick
    hr = None if shopped or p.get("hitrate_source") != "gamelog_vs_line" else _num(raw.get("hitrate"))
    # round 23 boards: the checklist's readings (already toward the over) and the books' median fair price
    checks = {c.get("key"): _num(c.get("value")) for c in (raw.get("checks") or []) if isinstance(c, dict) and c.get("key")}
    counted = [c for c in (raw.get("checks") or []) if isinstance(c, dict) and c.get("counts") and c.get("lean")]
    fair = _num(raw.get("fair_over")) if raw.get("fair_basis") == "books" and not shopped else None
    return {
        "date": d, "player": p.get("player_name"), "team": p.get("team"), "opponent": p.get("opponent"),
        "pos": _pos_group(p.get("position")), "stat": stat, "line": line,
        "over_odds": p.get("over_odds"), "under_odds": p.get("under_odds"), "p_over": mp[0],
        "be_over": implied_prob(p.get("over_odds")), "be_under": implied_prob(p.get("under_odds")),
        "over_hit": over_hit,
        # the board's own lean, for the record of what it published
        "side": side if sign is not None else None, "published": bool(raw.get("is_valid")) and sign is not None,
        "hit": None if sign is None or over_hit is None else (over_hit if sign > 0 else 1 - over_hit),
        # pre-tip numbers, each pointed toward the over
        "p_model_over": p_model, "dvp_z": _num(p.get("dvp_z")),
        "zone_z": _zscore_sum(p.get("zone_details")), "playtype_z": _zscore_sum(p.get("playtype_details")),
        "hitrate_over": None if hr is None else toward(hr - 0.5),
        "games_played": _num(p.get("games_played")), "season_avg": _num(p.get("season_avg")),
        "minutes": _num(p.get("proj_minutes")), "teammate_out": _num(p.get("teammate_out")),
        "injury": bool(p.get("injury")), "line_rise": toward(_num(p.get("line_move"))),
        "is_home": p.get("is_home"), "rest": rest(p.get("team"), d), "opp_rest": rest(p.get("opponent"), d),
        "fair_over": fair, "checks": checks, "proj_source": raw.get("proj_source"), "proj_mu": mu,
        "chk_net": (sum(1 for c in counted if c["lean"] == "over") - sum(1 for c in counted if c["lean"] == "under")) if raw.get("checks") else None,
    }


def _season_boards(logs_dir: str, season: str) -> dict:
    boards = {}
    for path in sorted(glob.glob(os.path.join(logs_dir, "props_*.json"))):
        try:
            with open(path, encoding="utf-8") as f:
                b = json.load(f)
        except (OSError, ValueError):
            continue
        d = b.get("date")
        if d and d >= FIRST_SPORTSBOOK_BOARD and season_for_date(_date.fromisoformat(d)) == season:
            boards[d] = b
    return boards


def _rest_fn(boards: dict, extra: dict | None = None):
    """rest(team, date) -> days off before that date (0 = a back-to-back), from the nights the team was on a board."""
    team_nights = defaultdict(set)
    for d, props in list((d, b.get("all_props", []) or []) for d, b in boards.items()) + list((extra or {}).items()):
        for p in props:
            team_nights[p.get("team")].add(d)
            team_nights[p.get("opponent")].add(d)

    def rest(team, d):
        prev = [x for x in team_nights.get(team, ()) if x < d]
        return (_date.fromisoformat(d) - _date.fromisoformat(max(prev))).days - 1 if prev else None
    return rest


def build_rows(logs_dir: str | None = None, season: str | None = None) -> list[dict]:
    """
    One row per labeled prop of the season, oldest night first: the board's
    prop on its regular line, the de-vigged market probability of the over,
    whether it went over, and every pre-tip number the board recorded, each
    pointed toward the over. Pushes, props without a result and props priced
    on one side only are left out.
    """
    logs_dir, season = logs_dir or LOGS_DIR, season or SEASON
    actual = _actuals(logs_dir, season)
    boards = _season_boards(logs_dir, season)
    rest = _rest_fn(boards)
    rows = []
    for d in sorted(boards):
        for raw in boards[d].get("all_props", []) or []:
            a = actual.get((d, raw.get("player_name"), raw.get("stat")))
            if a is None:
                continue
            row = _row(raw, d, a, rest)
            if row is not None and a != row["line"]:
                rows.append(row)
    _replay_checks(rows, logs_dir)
    return rows


def _replay_checks(rows: list[dict], logs_dir: str) -> int:
    """
    Read the checklist for the rows whose board did not carry it, as it would
    have read that night (round 24).

    The checklist arrived in round 23, so earlier boards have no readings and
    each check would start with no evidence. But everything a check reads is
    on file as of any date: the board's own matchup numbers, the results of
    earlier props (similar players — only nights before the row's), and the
    box-score history (src/boxlog.py: games over the line, the same at the
    night's venue, minutes, who played the day before). Rows are filled in
    place: "checks" ({key: value toward the over}), "chk_net" (every check
    counting), "is_home" where the board did not record it. A round-23 board
    lacks only the home/away split; that one reading is added. Rows are in
    date order; returns how many were replayed in full.
    """
    from datetime import timedelta
    from src import boxlog, checklist
    hist = boxlog.History(logs_dir)
    if not hist:
        return 0
    similar: dict = defaultdict(lambda: [0, 0])
    replayed, i = 0, 0
    while i < len(rows):
        j, d = i, rows[i]["date"]
        while j < len(rows) and rows[j]["date"] == d:
            j += 1
        y = (_date.fromisoformat(d) - timedelta(days=1)).isoformat()
        for r in rows[i:j]:
            if r["is_home"] is None:
                r["is_home"] = hist.home_on(r["player"], d, r["team"])
            if r["checks"] and "split" in r["checks"]:
                continue                                    # the board carried its whole checklist
            own = boxlog.readings(hist.games(r["player"], d, r["team"]), r["stat"], r["line"], d, r["is_home"])
            if r["checks"]:                                 # a round-23 board: only the split is missing
                one = checklist.read({"stat": r["stat"], "line": r["line"], "split_over": own["split_over"], "split_games": own["split_games"],
                                      "is_home": r["is_home"]})
                r["checks"]["split"] = next(c["value"] for c in one if c["key"] == "split")
                continue
            sim = similar.get((r["opponent"], r["pos"], r["stat"]))
            passing = r["stat"] == "assists" and r.get("proj_source") == "passing" and r["p_model_over"] is not None
            checks = checklist.read({
                "stat": r["stat"], "line": r["line"], "opponent": r["opponent"], "pos_group": r["pos"], "dvp_z": r["dvp_z"],
                "zone_z": r["zone_z"], "playtype_z": r["playtype_z"], "over_games": own["over_games"], "games": own["games"],
                "split_over": own["split_over"], "split_games": own["split_games"], "is_home": r["is_home"], "minutes": own["minutes"],
                "similar_over": sim[0] if sim else None, "similar_n": sim[1] if sim else None,
                "own_b2b": hist.played(r["team"], y), "opp_b2b": hist.played(r["opponent"], y), "teammate_out": r["teammate_out"],
                "passing_p_over": r["p_model_over"] if passing else None, "passing_mu": r.get("proj_mu") if passing else None,
            })
            r["checks"] = {c["key"]: c["value"] for c in checks}
            r["chk_net"] = checklist.net_over(checks)
            r["replayed"] = True
            replayed += 1
        for r in rows[i:j]:
            if r["over_hit"] is not None:
                cell = similar[(r["opponent"], r["pos"], r["stat"])]
                cell[0] += r["over_hit"]
                cell[1] += 1
        i = j
    return replayed


def similar_index(rows: list[dict]) -> dict:
    """{(opponent, position group, stat): [went over, props]} over every labeled prop — tonight's "similar players" check."""
    out: dict = defaultdict(lambda: [0, 0])
    for r in rows:
        cell = out[(r["opponent"], r["pos"], r["stat"])]
        cell[0] += r["over_hit"]
        cell[1] += 1
    return dict(out)


def today_values(props: list[dict], date: str, logs_dir: str | None = None, season: str | None = None) -> dict:
    """
    Tonight's readings of every scorecard signal, by the same code that scores
    them: {(player, stat): {signal key: value toward the over (NaN = missing)}}.
    `props` are tonight's board dicts. The history signals read every earlier
    night and nothing of tonight. Used by the model only for signals that
    have passed the bar (src/fair.adjust).
    """
    logs_dir, season = logs_dir or LOGS_DIR, season or SEASON
    history = [r for r in build_rows(logs_dir, season) if r["date"] < date]
    boards = {d: b for d, b in _season_boards(logs_dir, season).items() if d < date}
    rest = _rest_fn(boards, {date: props})
    tonight = [r for r in (_row(p, date, None, rest) for p in props) if r is not None]
    _replay_checks(tonight, logs_dir)       # a board without its checklist is read the same way
    rows = history + tonight
    out: dict = {(r["player"], r["stat"]): {} for r in tonight}
    for sig in signals(rows):
        for r, v in zip(tonight, sig["values"][len(history):]):
            out[(r["player"], r["stat"])][sig["key"]] = float(v)
    return out


# ── the signals ───────────────────────────────────────────────────────────

def _prior_rates(rows: list[dict], key, prior: float = 10.0, window_days: int | None = None, min_prior: int = 3) -> np.ndarray:
    """
    For each row, the OVER rate of EARLIER nights' props sharing key(row),
    shrunk toward 50% as if `prior` props at 50% stood behind it, minus 0.5;
    NaN with fewer than min_prior earlier props. Strictly point in time: a
    night's rows are scored before that night's results are added.
    """
    out = np.full(len(rows), np.nan)
    hist: dict = defaultdict(list)         # key -> [(date, over_hit)]
    i = 0
    while i < len(rows):
        j = i
        d = rows[i]["date"]
        while j < len(rows) and rows[j]["date"] == d:
            j += 1
        for k in range(i, j):
            h = hist.get(key(rows[k]), [])
            if window_days:
                cut = (_date.fromisoformat(d).toordinal() - window_days)
                h = [x for x in h if _date.fromisoformat(x[0]).toordinal() >= cut]
            if len(h) >= min_prior:
                out[k] = (sum(x[1] for x in h) + prior * 0.5) / (len(h) + prior) - 0.5
        for k in range(i, j):
            if rows[k]["over_hit"] is not None:      # tonight's props have no result yet: read, never added
                hist[key(rows[k])].append((d, rows[k]["over_hit"]))
        i = j
    return out


def _col(rows, name) -> np.ndarray:
    return np.array([np.nan if r.get(name) is None else float(r[name]) for r in rows], dtype=float)


def signals(rows: list[dict]) -> list[dict]:
    """Every candidate signal as {"key", "label", "family", "values"} — values pointed toward the over, NaN = missing."""
    pm = _col(rows, "p_over")
    is_points = np.array([r["stat"] in ("points", "pts+reb", "pts+ast", "pra") for r in rows])
    hr, gp = _col(rows, "hitrate_over"), _col(rows, "games_played")
    avg, line = _col(rows, "season_avg"), _col(rows, "line")
    home = np.array([np.nan if r.get("is_home") is None else (1.0 if r["is_home"] else -1.0) for r in rows])
    rest, opp_rest = _col(rows, "rest"), _col(rows, "opp_rest")
    zone = np.where(is_points, _col(rows, "zone_z"), np.nan)
    play = np.where(is_points, _col(rows, "playtype_z"), np.nan)
    tag = np.array([1.0 if r["injury"] else 0.0 for r in rows])

    def s(key, label, family, values):
        return {"key": key, "label": label, "family": family, "values": np.asarray(values, dtype=float)}
    return [
        s("model_prob", "The old projection's probability", "The old model", _logit(_col(rows, "p_model_over")) - _logit(pm)),
        s("dvp", "Defense vs position", "Defense vs position", _col(rows, "dvp_z")),
        s("zone_fit", "Shooting zones: opponent's FG% allowed where he shoots", "Zones and playtypes", zone),
        s("playtype_fit", "Playtypes: opponent's points per possession allowed", "Zones and playtypes", play),
        s("hit_rate", "Hit rate against tonight's line", "Hit rate", hr),
        s("hit_rate_weighted", "Hit rate, weighted by the games behind it", "Hit rate", hr * gp / (gp + 20.0)),
        s("season_avg", "Season average above or below the line", "Hit rate", np.clip((avg - line) / np.maximum(line, 1.0), -1, 1)),
        s("own_streak", "Player's own results against his lines so far", "Hit rate", _prior_rates(rows, lambda r: (r["player"], r["stat"]), prior=5.0) * 10),
        s("minutes", "Projected minutes", "Minutes and usage", (_col(rows, "minutes") - 30.0) / 5.0),
        s("teammates_out", "Starters out on his team", "Minutes and usage", _col(rows, "teammate_out")),
        s("injury_tag", "Injury tag (game-time decision), toward the under", "Minutes and usage", -tag),
        s("home", "Playing at home", "Situation", home),
        s("b2b_own", "Own team on a back-to-back, toward the under", "Situation", np.where(np.isnan(rest), np.nan, -(rest == 0).astype(float))),
        s("b2b_opp", "Opponent on a back-to-back, toward the over", "Situation", np.where(np.isnan(opp_rest), np.nan, (opp_rest == 0).astype(float))),
        s("line_move", "Line rose since the open", "The market", _col(rows, "line_rise")),
        s("similar_opp", "Similar players: every prop vs this opponent, same stat", "Similar players", _prior_rates(rows, lambda r: (r["opponent"], r["stat"])) * 10),
        s("similar_opp_pos", "Similar players: same position group vs this opponent", "Similar players", _prior_rates(rows, lambda r: (r["opponent"], r["pos"], r["stat"])) * 10),
        s("similar_league_7d", "Same position and stat league-wide, last 7 days", "Similar players", _prior_rates(rows, lambda r: (r["pos"], r["stat"]), window_days=7) * 10),
        # boards from round 23 on: what the model publishes from
        s("fair_gap", "The books' median fair price against the best prices' own", "The market", _logit(_col(rows, "fair_over")) - _logit(pm)),
        s("checklist", "The checklist's net lean (checks for the over minus checks against)", "The checklist", _col(rows, "chk_net")),
        *[s(f"chk_{key}", f"Checklist: {label}", "The checklist",
            np.array([np.nan if (r.get("checks") or {}).get(key) is None else r["checks"][key] / CHECK_BARS[key] for r in rows], dtype=float))
          for key, label in CHECK_LABELS.items()],
    ]


# ── scoring ───────────────────────────────────────────────────────────────

def score_signal(rows: list[dict], values: np.ndarray | None) -> dict:
    """
    One signal against the price under the module's protocol (see the
    docstring). values=None scores the price re-fitted on its own (intercept
    and slope) against the price exactly as posted; its "coef" is the
    intercept — how far the over's price has been off.
    """
    refit = values is None
    ok = np.ones(len(rows), dtype=bool) if refit else ~np.isnan(values)
    n = int(ok.sum())
    dates = np.array([r["date"] for r in rows])[ok]
    nights = sorted(set(dates))
    if n < MIN_ROWS or len(nights) <= MIN_TRAIN_NIGHTS + 1:
        return {"n": n, "nights": len(nights), "verdict": "too little data", "passes": False}
    y = _col(rows, "over_hit")[ok]
    lm = _logit(_col(rows, "p_over")[ok])
    be_o, be_u = _col(rows, "be_over")[ok], _col(rows, "be_under")[ok]
    pay_o = np.array([np.nan if _payout(r["over_odds"]) is None else _payout(r["over_odds"]) for r in rows], dtype=float)[ok]
    pay_u = np.array([np.nan if _payout(r["under_odds"]) is None else _payout(r["under_odds"]) for r in rows], dtype=float)[ok]
    X = lm[:, None] if refit else np.column_stack([lm, values[ok]])
    beta, ci = clustered_ci(X, y, dates)

    eval_nights = nights[MIN_TRAIN_NIGHTS:]
    step = max(1, math.ceil(len(eval_nights) / MAX_REFITS))
    p_sig, p_mkt = np.full(n, np.nan), np.full(n, np.nan)
    b_sig = b_mkt = None
    for i, d in enumerate(eval_nights):
        if i % step == 0:
            tr = dates < d
            if tr.sum() >= 30:
                b_mkt = fit_logistic(lm[tr][:, None], y[tr])
                b_sig = b_mkt if refit else fit_logistic(X[tr], y[tr])
        if b_sig is None:
            continue
        te = dates == d
        p_sig[te], p_mkt[te] = _predict(b_sig, X[te]), _predict(b_mkt, lm[te][:, None])
    sc = ~np.isnan(p_sig)
    if sc.sum() == 0:
        return {"n": n, "nights": len(nights), "verdict": "too little data", "passes": False}

    def rowloss(p):
        p = np.clip(p, 1e-6, 1 - 1e-6)
        return -(y[sc] * np.log(p) + (1 - y[sc]) * np.log(1 - p))
    posted = rowloss(_sigmoid(lm[sc]))
    diff_posted = rowloss(p_sig[sc]) - posted
    # a signal is compared with the price fitted on the same earlier nights, so the over's shading is not credited to
    # it; the re-fitted price itself is compared with the price exactly as posted
    diff = diff_posted if refit else rowloss(p_sig[sc]) - rowloss(p_mkt[sc])
    dll, dll_ci, dll_raw = float(diff.mean()), night_mean_ci(diff, dates[sc]), float(diff_posted.mean())
    bet_o = sc & ~np.isnan(be_o) & ~np.isnan(pay_o) & (p_sig > be_o + BET_MARGIN)
    bet_u = sc & ~bet_o & ~np.isnan(be_u) & ~np.isnan(pay_u) & ((1 - p_sig) > be_u + BET_MARGIN)
    units = np.concatenate([np.where(y[bet_o] == 1, pay_o[bet_o], -1.0), np.where(y[bet_u] == 0, pay_u[bet_u], -1.0)])
    bet_dates = np.concatenate([dates[bet_o], dates[bet_u]])
    won = int((y[bet_o] == 1).sum() + (y[bet_u] == 0).sum())
    oos_nights = sorted(set(dates[sc]))
    n_bets = int(bet_o.sum() + bet_u.sum())
    bets = {"n": n_bets, "overs": int(bet_o.sum()), "unders": int(bet_u.sum()),
            "hit_rate": round(won / n_bets, 4) if n_bets else None,
            "per_bet": round(float(units.mean()), 4) if n_bets else None, "ci": night_ci(units, bet_dates, oos_nights)}
    k = 0 if refit else 2
    coef, lo, hi = float(beta[k]), ci[k][0], ci[k][1]
    pays = bets["n"] >= MIN_BETS and bets["per_bet"] is not None and bets["per_bet"] > 0
    if dll_ci is not None and dll_ci[1] < 0 and dll_raw < 0 and pays:
        verdict = "beats the price"
    elif refit:
        verdict = "leans right" if (hi < 0 or lo > 0) else "no evidence"
    elif hi < 0:
        verdict = "negative"
    elif lo > 0 or (coef > 0 and dll < 0):
        verdict = "leans right"
    else:
        verdict = "no evidence"
    out = {"n": n, "nights": len(nights), "n_oos": int(sc.sum()), "oos_nights": len(oos_nights),
           "coef": round(coef, 4), "ci": [round(lo, 4), round(hi, 4)],
           "dll": round(dll, 5), "dll_ci": dll_ci, "dll_vs_posted": round(dll_raw, 5),
           "bets": bets, "verdict": verdict,
           "passes": bool(verdict == "beats the price" and len(oos_nights) >= PASS_MIN_NIGHTS and int(sc.sum()) >= PASS_MIN_ROWS)}
    if refit:
        out["slope"], out["slope_ci"] = round(float(beta[1]), 4), [round(ci[1][0], 4), round(ci[1][1], 4)]
    return out


def market_summary(rows: list[dict]) -> dict:
    """How the price itself did: the side it favoured against its price, and every over / every under by stat."""
    p_over, over = _col(rows, "p_over"), _col(rows, "over_hit")
    lean = p_over != 0.5
    fav_p = np.maximum(p_over, 1 - p_over)[lean]
    fav_hit = np.where(p_over > 0.5, over, 1 - over)[lean]
    nights = sorted({r["date"] for r in rows})

    def side_tab(sub):
        d = np.array([r["date"] for r in sub])
        o, po_mkt = _col(sub, "over_hit"), _col(sub, "p_over")
        po = np.array([np.nan if _payout(r["over_odds"]) is None else _payout(r["over_odds"]) for r in sub])
        pu = np.array([np.nan if _payout(r["under_odds"]) is None else _payout(r["under_odds"]) for r in sub])
        both = ~np.isnan(po) & ~np.isnan(pu)
        uo = np.where(o[both] == 1, po[both], -1.0)
        uu = np.where(o[both] == 0, pu[both], -1.0)
        return {"n": int(len(sub)), "over_hit": round(float(o.mean()), 4), "over_priced": round(float(po_mkt.mean()), 4),
                "over_per_bet": round(float(uo.mean()), 4) if both.any() else None, "over_ci": night_ci(uo, d[both], nights),
                "under_per_bet": round(float(uu.mean()), 4) if both.any() else None, "under_ci": night_ci(uu, d[both], nights)}
    by_stat = []
    for stat in list(SINGLES) + list(COMBOS):
        sub = [r for r in rows if r["stat"] == stat]
        if len(sub) >= 30:
            by_stat.append({"stat": stat, "label": STAT_LABELS.get(stat, stat), **side_tab(sub)})
    trailed = 0
    for d in nights:
        sub = [r for r in rows if r["date"] == d]
        trailed += np.mean([r["over_hit"] for r in sub]) < np.mean([r["p_over"] for r in sub])
    return {
        "favourite": {"n": int(lean.sum()), "hit": round(float(fav_hit.mean()), 4) if lean.any() else None,
                      "priced": round(float(fav_p.mean()), 4) if lean.any() else None},
        "over_under": by_stat, "all": side_tab(rows) if rows else None,
        "overs_trailed_nights": int(trailed), "nights": len(nights),
    }


def build(logs_dir: str | None = None, season: str | None = None) -> dict:
    """The whole scorecard as a JSON-ready dict (see the module docstring)."""
    season = season or SEASON
    rows = build_rows(logs_dir, season)
    out = {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "season": season,
           "n_props": len(rows), "nights": len({r["date"] for r in rows}),
           "first_date": rows[0]["date"] if rows else None, "last_date": rows[-1]["date"] if rows else None,
           "bar": {"bet_margin": BET_MARGIN, "pass_min_nights": PASS_MIN_NIGHTS, "pass_min_rows": PASS_MIN_ROWS},
           "market": None, "signals": []}
    if len(rows) < MIN_ROWS:
        return out
    out["market"] = market_summary(rows)
    out["replayed"] = sum(1 for r in rows if r.get("replayed"))      # rows whose checklist was read back from the history (round 24)
    pub = [r for r in rows if r["published"]]
    if pub:
        odds = [r["over_odds"] if r["side"] == "over" else r["under_odds"] for r in pub]
        pay = [(_payout(o) if r["hit"] else -1.0) for r, o in zip(pub, odds) if _payout(o) is not None]
        out["published"] = {"n": len(pub), "hit": round(float(np.mean([r["hit"] for r in pub])), 4), "per_bet": round(float(np.mean(pay)), 4) if pay else None}
    refit = {"key": "price_refit", "label": "The price re-fitted: overs against their price", "family": "The market", "values": None}
    for sig in [refit] + signals(rows):
        try:
            res = score_signal(rows, sig["values"])
        except Exception as e:      # one signal failing never costs the scorecard
            logger.warning(f"  Scorecard: {sig['key']} failed ({type(e).__name__}: {e})")
            res = {"n": 0, "verdict": "too little data", "passes": False}
        out["signals"].append({"key": sig["key"], "label": sig["label"], "family": sig["family"], **res})
    return out


def load(logs_dir: str | None = None) -> dict | None:
    """The scorecard on file (logs/scorecard.json), or None."""
    try:
        with open(os.path.join(logs_dir or LOGS_DIR, "scorecard.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def run(logs_dir: str | None = None) -> dict:
    """Build the scorecard, save it to logs/scorecard.json and mirror it into the site's latest.json. Never raises."""
    logs_dir = logs_dir or LOGS_DIR
    try:
        card = build(logs_dir)
        tmp = os.path.join(logs_dir, "scorecard.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(card, f, indent=1)
        os.replace(tmp, os.path.join(logs_dir, "scorecard.json"))
        if os.path.abspath(logs_dir) == os.path.abspath(LOGS_DIR) and os.path.exists(WEB_LATEST):
            with open(WEB_LATEST, encoding="utf-8") as f:
                latest = json.load(f)
            latest["scorecard"] = card
            with open(WEB_LATEST + ".tmp", "w", encoding="utf-8") as f:
                json.dump(latest, f, indent=2)
            os.replace(WEB_LATEST + ".tmp", WEB_LATEST)
        passed = [s["label"] for s in card["signals"] if s.get("passes")]
        logger.info(f"Scorecard: {card['n_props']} props over {card['nights']} nights, {len(card['signals'])} signals scored; "
                    + (f"passing: {', '.join(passed)}" if passed else "none passes the bar yet"))
        return card
    except Exception as e:
        logger.warning(f"Scorecard skipped ({type(e).__name__}: {e})")
        return {}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    run()
