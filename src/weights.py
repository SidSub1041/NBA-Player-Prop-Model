"""
What each check is worth, by stat (round 31).

The checklist used to count every check the same: a lean was a lean. Whether
a check deserves that is an empirical question, and it has a different
answer for points, rebounds and assists. This module asks it every night, on
every labeled prop of the season (src.scorecard.build_rows: each prop on its
regular line, its checks read as of that night), separately for each stat:

  the margin     how far the player finished from the line, in units that are
                 comparable across lines: (actual − line) / sqrt(line + 1),
                 divided by its spread for the stat and clipped at 3. This is
                 the question "does the check move the points / rebounds /
                 assists he actually gets" — and it uses every game's full
                 result, not just which side of the line it fell on.
  the effect     the slope of the margin on the check's reading (in units of
                 the check's own bar), with the market's price in the fit so
                 that what the books already charge for is not credited to
                 the check; standard errors treat a night, not a prop, as the
                 unit of evidence. t = effect / its standard error.
  the record     betting every prop the check leaned on, on the side it
                 leaned to, at the best price posted: how often it won, how
                 often the price said it would, and the return per bet.

A check's WEIGHT for a stat is

    weight = 1 + t / 2,   kept between 0 and 2

so a check with no evidence either way keeps the weight it always had (1), a
check that has moved the stat its way at two standard errors counts double,
and one that has pointed the WRONG way at two standard errors counts for
nothing. It needs MIN_ROWS readings over MIN_NIGHTS nights for the stat;
until then it stays at 1. Combos use the weights fitted on every prop
together ("all"). The weights are refitted from scratch each night, so they
move as the evidence does.

Nothing here looks ahead: tonight's weights come from graded nights only, and
walk_forward_net() — what the scorecard scores as "the weighted checklist" —
gives each night the weights fitted on the nights before it.
"""

import json
import logging
import os
from collections import defaultdict

import numpy as np

from src.checklist import BARS, LABELS

logger = logging.getLogger(__name__)

LOGS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
FILE = "check_weights.json"

STATS = ("points", "rebounds", "assists")
GROUPS = STATS + ("all",)
GROUP_LABELS = {"points": "Points", "rebounds": "Rebounds", "assists": "Assists", "all": "Every prop (used for combos)"}
MIN_ROWS = 300           # readings a check needs for a stat before its weight leaves 1
MIN_NIGHTS = 10          # ... over at least this many nights
T_SCALE = 2.0            # weight = 1 + t / T_SCALE
W_MIN, W_MAX = 0.0, 2.0
CLIP = 3.0               # margins beyond 3 spreads are pulled in (a 50-point night should not decide a weight)


def group_of(stat: str) -> str:
    """The weight group a stat reads: its own for points, rebounds and assists; "all" for a combo."""
    return stat if stat in STATS else "all"


def _logit(p):
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def _payout(odds) -> float:
    try:
        o = float(str(odds).replace("+", ""))
    except (TypeError, ValueError):
        return float("nan")
    if not np.isfinite(o) or abs(o) < 100:
        return float("nan")
    return o / 100.0 if o > 0 else 100.0 / abs(o)


def _ols(X: np.ndarray, y: np.ndarray, nights: np.ndarray) -> tuple:
    """Least squares with an intercept; standard errors clustered by night. Returns (beta, se), intercept first."""
    Z = np.column_stack([np.ones(len(y)), X])
    beta = np.linalg.lstsq(Z, y, rcond=None)[0]
    resid = y - Z @ beta
    bread = np.linalg.pinv(Z.T @ Z)
    uniq, inv = np.unique(nights, return_inverse=True)
    sums = np.zeros((len(uniq), Z.shape[1]))
    np.add.at(sums, inv, Z * resid[:, None])
    meat = sums.T @ sums
    g = len(uniq)
    cov = bread @ meat @ bread * (g / max(g - 1, 1))
    return beta, np.sqrt(np.maximum(np.diag(cov), 0.0))


def _prepare(rows: list[dict]) -> dict:
    """The graded rows as arrays: stat, night, market log-odds, over result, prices, and the standardised margin."""
    use = [r for r in rows if r.get("actual") is not None and r.get("over_hit") is not None]
    stat = np.array([r["stat"] for r in use])
    line = np.array([r["line"] for r in use], dtype=float)
    raw = (np.array([r["actual"] for r in use], dtype=float) - line) / np.sqrt(line + 1.0)
    margin, spread = np.zeros(len(use)), {}
    for s in np.unique(stat):
        m = stat == s
        sd = float(raw[m].std()) or 1.0
        spread[str(s)] = sd
        margin[m] = np.clip(raw[m] / sd, -CLIP, CLIP)
    return {
        "rows": use, "stat": stat, "line": line, "margin": margin, "spread": spread,
        "night": np.array([r["date"] for r in use]),
        "lm": _logit([r["p_over"] for r in use]),
        "over": np.array([r["over_hit"] for r in use], dtype=float),
        "pay_over": np.array([_payout(r.get("over_odds")) for r in use]),
        "pay_under": np.array([_payout(r.get("under_odds")) for r in use]),
        "x": {key: np.array([np.nan if (r.get("checks") or {}).get(key) is None else r["checks"][key] / BARS[key] for r in use], dtype=float)
              for key in BARS},
    }


def weight_from_t(t: float | None) -> float:
    """1 + t/2 between 0 and 2; 1 without a t."""
    if t is None or not np.isfinite(t):
        return 1.0
    return round(float(min(W_MAX, max(W_MIN, 1.0 + t / T_SCALE))), 2)


def _check(d: dict, mask: np.ndarray, key: str, sd: float | None, median_line: float | None) -> dict | None:
    """One check within one group of rows: its effect on the margin, its record when followed, its weight."""
    x = d["x"][key]
    ok = mask & ~np.isnan(x)
    n = int(ok.sum())
    if n == 0:
        return None
    nights = d["night"][ok]
    out = {"label": LABELS[key], "n": n, "nights": int(len(np.unique(nights))), "weight": 1.0, "basis": "too little data: counts as 1"}
    over, lm = d["over"], d["lm"]
    lo, lu = ok & (x >= 1), ok & (x <= -1)
    bets = int(lo.sum() + lu.sum())
    out["leans"] = bets
    if bets:
        won = np.concatenate([over[lo], 1 - over[lu]])
        priced = np.concatenate([1 / (1 + np.exp(-lm[lo])), 1 - 1 / (1 + np.exp(-lm[lu]))])
        units = np.concatenate([np.where(over[lo] == 1, d["pay_over"][lo], -1.0), np.where(over[lu] == 0, d["pay_under"][lu], -1.0)])
        out["followed"] = {"bets": bets, "won": round(float(won.mean()), 4), "priced": round(float(priced.mean()), 4),
                           "per_bet": None if np.isnan(units).all() else round(float(np.nanmean(units)), 4)}
    if n >= MIN_ROWS and out["nights"] >= MIN_NIGHTS and float(np.std(x[ok])) > 0:
        beta, se = _ols(np.column_stack([lm[ok], x[ok]]), d["margin"][ok], nights)
        if se[2] > 0:
            t = float(beta[2] / se[2])
            out.update({"effect": round(float(beta[2]), 4), "se": round(float(se[2]), 4), "t": round(t, 2),
                        "weight": weight_from_t(t), "basis": "1 + t/2"})
            if sd and median_line is not None:      # the same effect in the stat's own units, at the typical line
                out["effect_units"] = round(float(beta[2]) * sd * float(np.sqrt(median_line + 1.0)), 2)
    return out


def analyse(rows: list[dict]) -> dict:
    """
    The whole analysis as a JSON-ready dict:
    {"groups": {group: {"label", "n", "nights", "over_won", "over_priced",
    "median_line", "checks": {key: {"label", "n", "nights", "leans",
    "followed": {"bets", "won", "priced", "per_bet"}, "effect", "se", "t",
    "effect_units", "weight", "basis"}}}}, "rule": {...}}.
    """
    d = _prepare(rows)
    out = {"rule": {"weight": "1 + t/2, between 0 and 2", "min_rows": MIN_ROWS, "min_nights": MIN_NIGHTS}, "groups": {}}
    for g in GROUPS:
        mask = np.ones(len(d["rows"]), dtype=bool) if g == "all" else d["stat"] == g
        if not mask.any():
            continue
        single = g != "all"
        median_line = float(np.median(d["line"][mask])) if single else None
        grp = {"label": GROUP_LABELS[g], "n": int(mask.sum()), "nights": int(len(np.unique(d["night"][mask]))),
               "over_won": round(float(d["over"][mask].mean()), 4), "over_priced": round(float((1 / (1 + np.exp(-d["lm"][mask]))).mean()), 4),
               "median_line": median_line, "checks": {}}
        for key in BARS:
            c = _check(d, mask, key, d["spread"].get(g) if single else None, median_line)
            if c is not None:
                grp["checks"][key] = c
        out["groups"][g] = grp
    return out


def weights_of(analysis: dict | None) -> dict:
    """{group: {check key: weight}} from analyse()."""
    return {g: {k: c["weight"] for k, c in (grp.get("checks") or {}).items()} for g, grp in ((analysis or {}).get("groups") or {}).items()}


def for_stat(weights: dict | None, stat: str) -> dict:
    """The weights a prop of `stat` reads ({} = every check counts 1)."""
    return dict((weights or {}).get(group_of(stat)) or {})


def save(analysis: dict, logs_dir: str | None = None) -> str:
    path = os.path.join(logs_dir or LOGS_DIR, FILE)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump({"weights": weights_of(analysis), "rule": analysis.get("rule")}, f, indent=1)
    os.replace(path + ".tmp", path)
    return path


def load(logs_dir: str | None = None) -> dict:
    """{group: {check: weight}} on file, or {} (every check counts 1)."""
    try:
        with open(os.path.join(logs_dir or LOGS_DIR, FILE), encoding="utf-8") as f:
            w = json.load(f).get("weights") or {}
        return {g: {k: float(v) for k, v in ks.items() if k in BARS} for g, ks in w.items() if isinstance(ks, dict)}
    except (OSError, ValueError, AttributeError, TypeError):
        return {}


def walk_forward_net(rows: list[dict]) -> np.ndarray:
    """
    The weighted checklist's net lean toward the over for every row, each
    night weighted by what the checks had done on the nights BEFORE it (the
    weights are 1 until a check has MIN_ROWS readings over MIN_NIGHTS nights).
    Rows are in date order; a row with no reading at all is NaN.
    """
    out = np.full(len(rows), np.nan)
    nights = sorted({r["date"] for r in rows})
    lean = {key: np.array([0.0 if (r.get("checks") or {}).get(key) is None else
                           (1.0 if r["checks"][key] >= BARS[key] else -1.0 if r["checks"][key] <= -BARS[key] else 0.0) for r in rows]) for key in BARS}
    has = np.array([any(v is not None for v in (r.get("checks") or {}).values()) for r in rows])
    group = np.array([group_of(r["stat"]) for r in rows])
    date = np.array([r["date"] for r in rows])
    graded = [r for r in rows if r.get("actual") is not None]
    for night in nights:
        earlier = [r for r in graded if r["date"] < night]
        w = weights_of(analyse(earlier)) if len({r["date"] for r in earlier}) >= MIN_NIGHTS else {}
        tonight = date == night
        for g in GROUPS:
            m = tonight & (group == g) & has
            if m.any():
                wg = w.get(g) or {}
                out[m] = sum(wg.get(key, 1.0) * lean[key][m] for key in BARS)
    return out
