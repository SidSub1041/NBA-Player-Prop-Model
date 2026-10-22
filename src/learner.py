"""
The learning loop: the model learns from its own results.

Every night the grader labels EVERY prop that had a line — not just the
published picks — as a hit or a miss from the box scores (logs/training/).
A supervised model (L2-regularized logistic regression) is retrained on all
labeled props, recent nights weighted more, to predict P(hit) from what was
known at pick time:

  hit rate vs the line · season average vs the line · the book's implied
  probability · share of conditions met · stat type · over/under · line
  source · books hanging the line · injury tag · sample size

It is validated walk-forward: each night is predicted by a model trained only
on the nights BEFORE it, and compared with two baselines — the average hit
rate and the de-vigged book price. It stays in SHADOW mode (its probability is
shown, nothing changes) until, on effectively enough rows and nights, it beats
the stronger baseline with a significant margin on three retrains in a row and
its EV rule would not have cost money on past published picks. Then it turns
ACTIVE: a valid pick must also be +EV at its price.

Each graded night also gets a post-mortem (logs/postmortem_{date}.json).
"""

import glob
import hashlib
import json
import logging
import math
import os
from collections import defaultdict
from datetime import date as _date, datetime, timezone

import numpy as np

from src.config import slate_today
from src.grader import _american_odds_to_profit
from src.underdog import norm_name, norm_name_base

logger = logging.getLogger(__name__)

LOGS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
TRAIN_DIR = os.path.join(LOGS_DIR, "training")
MODEL_PATH = os.path.join(LOGS_DIR, "learner.json")
WEB_LATEST = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web", "public", "data", "latest.json")

HALF_LIFE_DAYS = 45          # a night 45 days ago counts half as much as last night
L2 = 2.0                     # ridge strength on standardized features
VIG = 0.045                  # typical hold on a two-way player prop (used to de-vig a price)
MIN_ROWS = 300               # EFFECTIVE labeled props (Kish) before the model may gate picks
MIN_DAYS = 10                # EFFECTIVE nights, by the same weights
MIN_GAIN = 0.01              # must beat the stronger baseline's log loss by 1% on unseen nights
MIN_TRAIN_ROWS = 60          # a night is scored walk-forward only with this many earlier rows
MIN_GATE_PICKS = 20          # published picks with out-of-sample predictions needed to test the EV rule
ENTER_STREAK = 3             # consecutive passing retrains before ACTIVE
EXIT_STREAK = 2              # consecutive failing retrains before back to SHADOW
MAX_LABEL_ATTEMPTS = 3       # give up labeling a night after this many incomplete box-score fetches
BREAKEVEN_110 = 110 / 210    # 52.4%: what a -110 price needs

FEATURES = [
    "hr_line", "hr_line_missing", "cushion", "cushion_missing", "implied", "implied_missing",
    "pass_rate", "log_conditions", "is_over", "from_books", "log_books", "injured", "log_gp",
    "stat_rebounds", "stat_assists", "stat_pra", "stat_combo2",
]
FEATURE_LABELS = {
    "hr_line": "hit rate against the line",
    "hr_line_missing": "no line-based hit rate (older boards)",
    "cushion": "season average vs the line, in the pick's direction",
    "cushion_missing": "no season average",
    "implied": "the price's implied probability",
    "implied_missing": "no price",
    "pass_rate": "share of matchup conditions met",
    "log_conditions": "number of matchup conditions",
    "is_over": "overs (vs unders)",
    "from_books": "sportsbook line (vs Underdog)",
    "log_books": "books hanging the line",
    "injured": "player on the injury report",
    "log_gp": "games in the hit-rate sample",
    "stat_rebounds": "rebounds props (vs points)",
    "stat_assists": "assists props (vs points)",
    "stat_pra": "PTS+REB+AST props (vs points)",
    "stat_combo2": "two-stat combos (vs points)",
}
STAT_COLS = {"points": ["pts"], "rebounds": ["reb"], "assists": ["ast"], "pra": ["pts", "reb", "ast"],
             "pts+reb": ["pts", "reb"], "pts+ast": ["pts", "ast"], "reb+ast": ["reb", "ast"]}
# What a training row keeps of the board prop, so features can be recomputed later
SNAPSHOT_KEYS = [
    "stat", "edge", "line", "over_odds", "under_odds", "ud_line", "ud_over_odds", "ud_under_odds",
    "hitrate", "hitrate_source", "season_avg", "pass_rate", "adaptive_multiplier", "total_conditions",
    "line_source", "line_books", "injury", "games_played",
]


# ── features ──────────────────────────────────────────────────────────────
def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return math.nan
    return v if math.isfinite(v) else math.nan


def implied_prob(odds) -> float:
    """American odds -> implied probability ('-120' -> 0.545, '+110' -> 0.476)."""
    o = _num(odds)
    if math.isnan(o) or o == 0:
        return math.nan
    return 100.0 / (o + 100.0) if o > 0 else -o / (-o + 100.0)


def fair_prob(odds) -> float:
    """The price with the book's hold taken out — the market's own estimate of P(hit)."""
    p = implied_prob(odds)
    return math.nan if math.isnan(p) else min(0.98, max(0.02, p / (1.0 + VIG)))


def profit_per_unit(odds) -> float:
    """Profit on a 1-unit stake — the same rule the grader settles picks with (even money if no price)."""
    return _american_odds_to_profit(str(odds) if odds is not None else None)


def prop_line(p: dict):
    """(line, pick-side price) from a board prop — current boards or pre-Oct-22 Underdog-only boards."""
    over = str(p.get("edge", "")).lower() != "under"
    if "line" in p:
        return p.get("line"), (p.get("over_odds") if over else p.get("under_odds"))
    return p.get("ud_line"), (p.get("ud_over_odds") if over else p.get("ud_under_odds"))


def _raw_pass_rate(p: dict) -> float:
    """
    Share of conditions met as the model computes it today. Boards before
    Oct 22, 2026 stored it multiplied by a retired 'adaptive' factor (clipped at 1).
    """
    pr = _num(p.get("pass_rate"))
    if math.isnan(pr):
        return 0.0
    mult = _num(p.get("adaptive_multiplier"))
    if math.isnan(mult) or mult <= 0 or abs(mult - 1.0) < 1e-9:
        return pr
    if mult > 1.0 and pr >= 1.0 - 1e-9:
        return 1.0   # clipped: the raw share was already at or near 100%
    return min(1.0, pr / mult)


def featurize(p: dict) -> dict | None:
    """Board prop -> feature dict, or None if it has no line (nothing to learn from)."""
    line, odds = prop_line(p)
    line = _num(line)
    if math.isnan(line):
        return None
    over = str(p.get("edge", "")).lower() != "under"
    hr = _num(p.get("hitrate")) if p.get("hitrate_source") == "gamelog_vs_line" else math.nan
    avg = _num(p.get("season_avg"))
    cushion = math.nan if math.isnan(avg) else max(-1.0, min(1.0, ((avg - line) if over else (line - avg)) / max(line, 1.0)))
    imp = implied_prob(odds)
    stat = p.get("stat")
    tc = _num(p.get("total_conditions")); gp = _num(p.get("games_played")); books = _num(p.get("line_books"))
    return {
        "hr_line": hr, "hr_line_missing": float(math.isnan(hr)),
        "cushion": cushion, "cushion_missing": float(math.isnan(cushion)),
        "implied": imp, "implied_missing": float(math.isnan(imp)),
        "pass_rate": _raw_pass_rate(p),
        "log_conditions": math.log1p(max(0.0, 0.0 if math.isnan(tc) else tc)),
        "is_over": float(over),
        "from_books": float(p.get("line_source") == "vegas"),
        "log_books": math.log1p(1.0 if math.isnan(books) else books),
        "injured": float(bool(p.get("injury"))),
        "log_gp": math.log1p(0.0 if math.isnan(gp) else gp),
        "stat_rebounds": float(stat == "rebounds"), "stat_assists": float(stat == "assists"),
        "stat_pra": float(stat == "pra"), "stat_combo2": float(stat in ("pts+reb", "pts+ast", "reb+ast")),
    }


def row_features(r: dict) -> dict | None:
    """A training row's features, recomputed from its board snapshot when it has one."""
    if r.get("prop"):
        f = featurize(r["prop"])
        if f is not None:
            return f
    return r.get("features")


# ── labeling ──────────────────────────────────────────────────────────────
def _box_index(box: dict) -> dict:
    idx = {}
    for name, row in box.items():
        idx.setdefault(name.lower(), row)
        idx.setdefault(norm_name(name), row)
        idx.setdefault(norm_name_base(name), row)
    return idx


def actual_value(name: str, stat: str, idx: dict):
    """The player's actual stat from the box scores, or None (didn't play / not found / unknown stat)."""
    row = idx.get(name.lower()) or idx.get(norm_name(name)) or idx.get(norm_name_base(name))
    cols = STAT_COLS.get(stat)
    if not row or row.get("dnp") or not cols or not all(c in row for c in cols):
        return None
    return float(sum(row[c] for c in cols))


def label_props(date: str, props_data: dict, box: dict) -> list[dict]:
    """Every prop with a line on that night's board -> a labeled training row (pushes and DNPs skipped)."""
    idx = _box_index(box)
    rows = []
    for p in props_data.get("all_props", []) or []:
        f = featurize(p)
        if f is None:
            continue
        actual = actual_value(p.get("player_name", ""), p.get("stat"), idx)
        line, _ = prop_line(p)
        if actual is None or float(line) == actual:
            continue
        over = str(p.get("edge", "")).lower() != "under"
        rows.append({
            "date": date, "player": p.get("player_name"), "team": p.get("team"), "stat": p.get("stat"),
            "edge": p.get("edge"), "line": float(line), "actual": actual,
            "hit": int(actual > float(line) if over else actual < float(line)),
            "published": bool(p.get("is_valid")), "grade": p.get("grade"),
            "p_hit_live": p.get("p_hit"), "odds": prop_line(p)[1],
            "prop": {k: p.get(k) for k in SNAPSHOT_KEYS if k in p},
            "features": f,
        })
    return rows


def _labels_path(date: str) -> str:
    return os.path.join(TRAIN_DIR, f"labels_{date}.json")


def _read_labels(date: str) -> dict | None:
    try:
        with open(_labels_path(date), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save_labels(date: str, rows: list[dict], incomplete: bool = False, attempts: int = 0) -> None:
    os.makedirs(TRAIN_DIR, exist_ok=True)
    tmp = _labels_path(date) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"date": date, "rows": rows, "incomplete": incomplete, "attempts": attempts}, f, indent=1)
    os.replace(tmp, _labels_path(date))


def load_all_rows() -> list[dict]:
    rows = []
    for path in sorted(glob.glob(os.path.join(TRAIN_DIR, "labels_*.json"))):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            logger.warning(f"  Skipping {path}: {e}")
            continue
        if data.get("incomplete"):
            continue
        rows.extend(data.get("rows", []))
    return rows


def label_date(date: str, fetch_box, fetch_ok=None) -> list[dict] | None:
    """
    Label one night's board. fetch_box(date) -> box scores; fetch_ok(date) -> did
    every game come back? A night whose box scores are missing or partial is
    recorded as incomplete and retried on later runs (up to MAX_LABEL_ATTEMPTS).
    Returns the rows, or None if the night couldn't be labeled (no board / incomplete).
    """
    path = os.path.join(LOGS_DIR, f"props_{date}.json")
    try:
        with open(path, encoding="utf-8") as f:
            props_data = json.load(f)
    except (OSError, ValueError):
        return None
    if not any(featurize(p) for p in props_data.get("all_props", []) or []):
        save_labels(date, [])   # nothing with a line: nothing to learn, done
        return []
    prev = _read_labels(date) or {}
    attempts = int(prev.get("attempts") or 0) + 1
    box = fetch_box(date)
    complete = bool(box) and (fetch_ok(date) if fetch_ok else True)
    if not complete:
        logger.warning(f"  Learner: box scores for {date} missing or partial (attempt {attempts}); will retry")
        save_labels(date, [], incomplete=True, attempts=attempts)
        return None
    rows = label_props(date, props_data, box)
    save_labels(date, rows)
    logger.info(f"  Learner: labeled {len(rows)} props for {date} ({sum(r['hit'] for r in rows)} hits)")
    return rows


def backfill(fetch_box, before: str, max_dates: int = 40, fetch_ok=None) -> int:
    """
    Label past boards that have no complete labels yet: never-tried nights first
    (newest first), then incomplete ones still worth retrying. At most max_dates per run.
    """
    fresh, retry = [], []
    for path in sorted(glob.glob(os.path.join(LOGS_DIR, "props_*.json"))):
        d = os.path.basename(path)[6:16]
        if d >= before:
            continue
        prev = _read_labels(d)
        if prev is None:
            fresh.append(d)
        elif prev.get("incomplete") and int(prev.get("attempts") or 0) < MAX_LABEL_ATTEMPTS:
            retry.append(d)
    todo = sorted(fresh, reverse=True) + sorted(retry)
    done = 0
    for d in todo:
        if done >= max_dates:
            logger.info("  Learner: backfill paused (limit reached); continues next run")
            break
        try:
            label_date(d, fetch_box, fetch_ok)
        except Exception as e:   # one bad night never blocks the rest
            logger.warning(f"  Learner: could not label {d} ({type(e).__name__}: {e})")
        done += 1
    return done


def labels_fingerprint() -> str:
    """Changes whenever any complete labels file changes — retraining is skipped otherwise."""
    h = hashlib.sha256()
    for path in sorted(glob.glob(os.path.join(TRAIN_DIR, "labels_*.json"))):
        try:
            st = os.stat(path)
            h.update(f"{os.path.basename(path)}:{st.st_size}:{int(st.st_mtime)};".encode())
        except OSError:
            continue
    return h.hexdigest()[:16]


# ── model ─────────────────────────────────────────────────────────────────
def _matrix(rows: list[dict]) -> np.ndarray:
    return np.array([[(row_features(r) or {}).get(k, math.nan) for k in FEATURES] for r in rows], dtype=float)


def _weights(rows: list[dict], today: _date) -> np.ndarray:
    ages = np.array([(today - _date.fromisoformat(r["date"])).days for r in rows], dtype=float)
    return 0.5 ** (np.clip(ages, 0, None) / HALF_LIFE_DAYS)


def _fit(X: np.ndarray, y: np.ndarray, w: np.ndarray) -> dict:
    """Weighted ridge logistic regression (Newton/IRLS) on standardized features with mean imputation."""
    wsum = w.sum()
    fill = np.array([np.nansum(X[:, j] * w) / max(np.sum(w[~np.isnan(X[:, j])]), 1e-9) for j in range(X.shape[1])])
    fill = np.where(np.isfinite(fill), fill, 0.0)
    Xf = np.where(np.isnan(X), fill, X)
    mu = (Xf * w[:, None]).sum(0) / wsum
    sd = np.sqrt(((Xf - mu) ** 2 * w[:, None]).sum(0) / wsum)
    constant = sd <= 1e-9
    sd = np.where(constant, 1.0, sd)
    Z = np.hstack([np.ones((len(y), 1)), (Xf - mu) / sd])
    beta = np.zeros(Z.shape[1])
    R = np.eye(Z.shape[1]); R[0, 0] = 1e-6   # a whisper of ridge on the intercept keeps H invertible
    for _ in range(50):
        p = 1.0 / (1.0 + np.exp(-np.clip(Z @ beta, -30, 30)))
        grad = Z.T @ (w * (y - p)) - L2 * R @ beta
        H = Z.T @ (Z * (w * p * (1 - p))[:, None]) + L2 * R
        try:
            step = np.linalg.solve(H, grad)
        except np.linalg.LinAlgError:
            step = np.linalg.lstsq(H, grad, rcond=None)[0]
        beta += step
        if np.max(np.abs(step)) < 1e-8:
            break
    coef = beta[1:]
    coef[constant] = 0.0   # a feature that never varies can't carry a weight
    return {"fill": fill.tolist(), "mean": mu.tolist(), "std": sd.tolist(),
            "intercept": float(beta[0]), "coef": coef.tolist(), "constant": constant.tolist()}


def _predict(model: dict, X: np.ndarray) -> np.ndarray:
    fill, mu, sd = (np.array(model[k]) for k in ("fill", "mean", "std"))
    Xf = np.where(np.isnan(X), fill, X)
    z = model["intercept"] + ((Xf - mu) / sd) @ np.array(model["coef"])
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


def _logloss_vec(y, p):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def _wmean(v, w):
    return float((v * w).sum() / max(w.sum(), 1e-12))


def _record(rows: list[dict]) -> dict:
    hits = sum(r["hit"] for r in rows)
    return {"hits": hits, "misses": len(rows) - hits, "rate": round(hits / len(rows), 3) if rows else None}


def _segments(rows: list[dict]) -> dict:
    def seg(key):
        groups = defaultdict(list)
        for r in rows:
            groups[key(r)].append(r)
        return {k: _record(v) for k, v in sorted(groups.items(), key=lambda kv: str(kv[0]))}

    def feat(r, k):
        f = row_features(r) or {}
        v = f.get(k)
        return None if v is None or (isinstance(v, float) and math.isnan(v)) else v

    def cush(r):
        c = feat(r, "cushion")
        return "n/a" if c is None else ("average favors the pick" if c > 0 else "average against the pick")

    def hrb(r):
        h = feat(r, "hr_line")
        if h is None:
            return "no line-based hit rate"
        return "≥56%" if h >= 0.56 else "50–56%" if h >= 0.50 else "<50%"

    return {
        "stat": seg(lambda r: r["stat"]),
        "edge": seg(lambda r: r["edge"]),
        "line source": seg(lambda r: "sportsbooks" if feat(r, "from_books") else "Underdog"),
        "hit rate vs line": seg(hrb),
        "season average vs line": seg(cush),
    }


def _walk_forward(rows, X, y, dates, days, today_d):
    """Out-of-sample predictions: each night scored by a model fit only on earlier nights."""
    oof = np.full(len(rows), np.nan); base_const = np.full(len(rows), np.nan)
    for d in days:
        test = dates == d
        train = dates < d
        if train.sum() < MIN_TRAIN_ROWS:
            continue
        w_d = _weights([r for r, t in zip(rows, train) if t], _date.fromisoformat(d))
        m = _fit(X[train], y[train], w_d)
        oof[test] = _predict(m, X[test])
        base_const[test] = _wmean(y[train], w_d)
    return oof, base_const


def _gate_check(rows, oof, scored, w):
    """Would the EV rule have paid on past published picks? (out-of-sample)"""
    kept, demoted = [], []
    for i, r in enumerate(rows):
        if not scored[i] or not r.get("published"):
            continue
        win = profit_per_unit(r.get("odds"))
        ev = oof[i] * win - (1.0 - oof[i])
        units = win if r["hit"] else -1.0
        (kept if ev >= 0 else demoted).append(units)
    n = len(kept) + len(demoted)
    out = {"published_scored": n, "kept": len(kept), "demoted": len(demoted),
           "units_kept": round(sum(kept), 2), "units_demoted": round(sum(demoted), 2)}
    if n < MIN_GATE_PICKS:
        out["verdict"] = "not enough published picks to judge yet"
        out["passed"] = None
    else:
        out["passed"] = sum(demoted) <= 0.0   # the picks it would cut did not make money
        out["verdict"] = "cutting -EV picks would have saved units" if out["passed"] else "cutting -EV picks would have cost units"
    return out


def train(today: str | None = None, force: bool = False) -> dict:
    """Retrain on every labeled prop (unless nothing changed), validate walk-forward, save logs/learner.json."""
    today_d = _date.fromisoformat(today) if today else _date.fromisoformat(slate_today())
    prev = _read_model()
    fp = labels_fingerprint()
    if (not force and prev and prev.get("labels_fingerprint") == fp and prev.get("anchor_date") == today_d.isoformat()):
        logger.info("  Learner: no new labels since the last training — model unchanged")
        return prev

    rows = [r for r in load_all_rows() if r.get("hit") in (0, 1) and row_features(r)]
    days = sorted({r["date"] for r in rows})
    model = {"trained_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "anchor_date": today_d.isoformat(),
             "labels_fingerprint": fp, "features": FEATURES, "n_rows": len(rows), "n_days": len(days),
             "mode": "shadow", "trusted": False, "pass_streak": 0, "fail_streak": 0}
    if len(rows) < 30 or len(days) < 3:
        model["note"] = "shadow mode: not enough labeled props yet"
        _save(model)
        return model

    X = _matrix(rows); y = np.array([r["hit"] for r in rows], dtype=float); w = _weights(rows, today_d)
    dates = np.array([r["date"] for r in rows])
    model.update(_fit(X, y, w))

    # Walk-forward validation against two baselines
    oof, base_const = _walk_forward(rows, X, y, dates, days, today_d)
    scored = ~np.isnan(oof)
    fair = np.array([fair_prob(r.get("odds")) for r in rows], dtype=float)
    base_market = np.where(np.isnan(fair), base_const, fair)
    cv: dict = {"scored_rows": int(scored.sum()), "scored_nights": int(len(set(dates[scored])))}
    if scored.sum() >= 30 and len(set(dates[scored])) >= 2:
        ws, ys = w[scored], y[scored]
        ll_m = _logloss_vec(ys, oof[scored]); ll_c = _logloss_vec(ys, base_const[scored]); ll_k = _logloss_vec(ys, base_market[scored])
        L_m, L_c, L_k = _wmean(ll_m, ws), _wmean(ll_c, ws), _wmean(ll_k, ws)
        stronger = "market price" if L_k <= L_c else "average hit rate"
        ll_b = ll_k if L_k <= L_c else ll_c
        L_b = min(L_c, L_k)
        gain = (L_b - L_m) / L_b if L_b else 0.0
        # Effective evidence, by the same weights the metric uses
        ess = float(ws.sum() ** 2 / (ws ** 2).sum())
        night_w = defaultdict(float)
        for d_, wi in zip(dates[scored], ws):
            night_w[d_] += float(wi)
        eff_days = sum(night_w.values()) ** 2 / sum(v * v for v in night_w.values())
        # Significance: per-night paired loss difference (baseline minus model), weighted t-style bound
        diffs, dw = [], []
        for d_ in sorted(night_w):
            sel = dates[scored] == d_
            diffs.append(_wmean(ll_b[sel] - ll_m[sel], ws[sel])); dw.append(night_w[d_])
        diffs, dw = np.array(diffs), np.array(dw)
        mean_diff = _wmean(diffs, dw)
        if len(diffs) >= 3:
            var = _wmean((diffs - mean_diff) ** 2, dw) * len(diffs) / (len(diffs) - 1)
            lower = mean_diff - 1.64 * math.sqrt(var / len(diffs))
        else:
            lower = -1.0
        calib = []
        for lo, hi in ((0, .45), (.45, .5), (.5, .55), (.55, .6), (.6, 1.01)):
            sel = (oof[scored] >= lo) & (oof[scored] < hi)
            if sel.sum():
                calib.append({"band": f"{lo:.0%}–{min(hi, 1):.0%}", "n": int(sel.sum()),
                              "predicted": round(float(oof[scored][sel].mean()), 3), "actual": round(float(ys[sel].mean()), 3)})
        gate = _gate_check(rows, oof, scored, w)
        cv.update({"logloss": round(L_m, 4), "baseline_logloss": round(L_b, 4), "baseline": stronger,
                   "logloss_vs_average": round(L_c, 4), "logloss_vs_market": round(L_k, 4),
                   "gain": round(gain, 4), "gain_lower_bound": round(lower, 4),
                   "effective_rows": round(ess, 1), "effective_nights": round(eff_days, 1),
                   "calibration": calib, "gate_check": gate})
        criteria = {
            "effective_rows": ess >= MIN_ROWS, "effective_nights": eff_days >= MIN_DAYS,
            "gain": gain >= MIN_GAIN, "significant": lower > 0,
            "pick_gate": gate["passed"] is not False,
        }
        pass_now = all(criteria.values())
        fail_now = not (ess >= MIN_ROWS and eff_days >= MIN_DAYS and gain >= MIN_GAIN / 2 and lower > -0.005)
    else:
        criteria = {"scored": False}; pass_now = False; fail_now = True; gain = 0.0
        cv["note"] = "too few nights to validate walk-forward yet"

    # Hysteresis: enter ACTIVE after ENTER_STREAK passes in a row; leave after EXIT_STREAK fails
    prev_active = bool(prev and prev.get("mode") == "active")
    pass_streak = (int(prev.get("pass_streak", 0)) + 1) if (prev and pass_now) else (1 if pass_now else 0)
    fail_streak = (int(prev.get("fail_streak", 0)) + 1) if (prev and fail_now) else (1 if fail_now else 0)
    if prev_active:
        active = fail_streak < EXIT_STREAK
    else:
        active = pass_streak >= ENTER_STREAK
    model.update({"cv": cv, "criteria": criteria, "pass_streak": pass_streak, "fail_streak": fail_streak,
                  "trusted": active, "mode": "active" if active else "shadow",
                  "base_rate": round(_wmean(y, w), 3)})
    # Factors: real, varying features only (no missing-indicators), once there's enough evidence
    coef = np.array(model["coef"]); constant = np.array(model.get("constant", [False] * len(FEATURES)))
    ess_all = float(w.sum() ** 2 / (w ** 2).sum())
    if ess_all >= 100:
        idx = [i for i in np.argsort(-np.abs(coef)) if not constant[i] and not FEATURES[i].endswith("_missing") and abs(coef[i]) > 1e-3]
        model["factors"] = [{"feature": FEATURES[i], "label": FEATURE_LABELS[FEATURES[i]], "weight": round(float(coef[i]), 3),
                             "effect": "more hits" if coef[i] > 0 else "more misses"} for i in idx[:6]]
    else:
        model["factors"] = []
    model["segments"] = _segments([r for r in rows if (today_d - _date.fromisoformat(r["date"])).days <= 60] or rows)
    if not active:
        why = []
        if "scored" in criteria:
            why.append(cv.get("note", "not validated yet"))
        else:
            if not criteria["effective_rows"]: why.append(f"{cv['effective_rows']:.0f}/{MIN_ROWS} effective labeled props")
            if not criteria["effective_nights"]: why.append(f"{cv['effective_nights']:.1f}/{MIN_DAYS} effective nights")
            if not criteria["gain"]: why.append(f"beats the {cv['baseline']} by {gain:.1%} on unseen nights (needs {MIN_GAIN:.0%})")
            elif not criteria["significant"]: why.append("the edge isn't statistically clear yet")
            if criteria["pick_gate"] is False: why.append("its EV rule would have cost units on past picks")
            if pass_now: why.append(f"passing {pass_streak}/{ENTER_STREAK} retrains in a row")
        model["note"] = "shadow mode: " + "; ".join(why)
    _save(model)
    logger.info(f"  Learner trained on {len(rows)} props / {len(days)} nights — {model['mode'].upper()}"
                + (f" (walk-forward log loss {cv['logloss']} vs {cv['baseline']} {cv['baseline_logloss']}; "
                   f"effective {cv['effective_rows']} rows / {cv['effective_nights']} nights)" if "logloss" in cv else ""))
    return model


def _read_model() -> dict | None:
    try:
        with open(MODEL_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _save(model: dict) -> None:
    os.makedirs(LOGS_DIR, exist_ok=True)
    tmp = MODEL_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(model, f, indent=1)
    os.replace(tmp, MODEL_PATH)


def load_model() -> dict | None:
    """The trained model, or None if there isn't one or it was trained on a different feature set."""
    m = _read_model()
    if not m or not m.get("coef"):
        return None
    if m.get("features") != FEATURES or len(m["coef"]) != len(FEATURES):
        logger.warning("  Learner: saved model was trained on a different feature set; ignoring it until the next retrain")
        return None
    return m


def predict_prop(model: dict, p: dict) -> dict | None:
    """P(hit), break-even and EV for a board prop dict (None if it has no line or there's no model)."""
    if not model or model.get("features") != FEATURES:
        return None
    f = featurize(p)
    if f is None:
        return None
    ph = float(_predict(model, np.array([[f.get(k, math.nan) for k in FEATURES]], dtype=float))[0])
    _, odds = prop_line(p)
    win = profit_per_unit(odds)
    return {"p_hit": round(ph, 3), "breakeven": round(1.0 / (1.0 + win), 3), "ev": round(ph * win - (1.0 - ph), 3),
            "active": bool(model.get("trusted"))}


# ── post-mortem ───────────────────────────────────────────────────────────
def postmortem(date: str, rows: list[dict], graded: dict | None) -> dict:
    """What went right and wrong on one night; saved to logs/postmortem_{date}.json."""
    pub = [r for r in rows if r.get("published")]
    segs = _segments(rows)
    flat = [(f"{dim}: {k}", v) for dim, d in segs.items() for k, v in d.items() if (v["hits"] + v["misses"]) >= 4]
    flat.sort(key=lambda kv: kv[1]["rate"] or 0)
    misses = sorted((r for r in pub if not r["hit"]), key=lambda r: abs(r["actual"] - r["line"]), reverse=True)
    live = [r for r in rows if r.get("p_hit_live") is not None]
    cut = 0
    try:
        with open(os.path.join(LOGS_DIR, f"props_{date}.json"), encoding="utf-8") as f:
            cut = sum(1 for p in json.load(f).get("all_props", []) if p.get("learn_note"))
    except (OSError, ValueError):
        pass
    out = {
        "date": date,
        "published": {k: graded.get(k) for k in ("total_picks", "hits", "misses", "voided", "units")} if graded else _record(pub),
        "all_props": _record(rows),
        "scope": "segments cover every prop with a line that night, not only the published picks",
        # a segment went right only if it would beat a -110 price, not just a coin flip
        "went_right": [{"segment": k, **v} for k, v in flat[::-1][:3] if (v["rate"] or 0) >= BREAKEVEN_110],
        "went_wrong": [{"segment": k, **v} for k, v in flat[:3] if (v["rate"] or 0) < 0.5],
        "biggest_misses": [{"player": r["player"], "stat": r["stat"], "edge": r["edge"], "line": r["line"],
                            "actual": r["actual"]} for r in misses[:5]],
        "near_misses": sum(1 for r in pub if not r["hit"] and abs(r["actual"] - r["line"]) <= 1.0),
        "picks_cut_by_model": cut,
    }
    if len(pub) >= 8:
        out["published_segments"] = _segments(pub)
    if live:
        hits = [r["p_hit_live"] for r in live if r["hit"]]; miss = [r["p_hit_live"] for r in live if not r["hit"]]
        out["live_model"] = {"n": len(live), "avg_p_on_hits": round(sum(hits) / len(hits), 3) if hits else None,
                             "avg_p_on_misses": round(sum(miss) / len(miss), 3) if miss else None}
    os.makedirs(LOGS_DIR, exist_ok=True)
    with open(os.path.join(LOGS_DIR, f"postmortem_{date}.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    return out


def latest_postmortem() -> dict | None:
    paths = sorted(glob.glob(os.path.join(LOGS_DIR, "postmortem_*.json")))
    if not paths:
        return None
    try:
        with open(paths[-1], encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def summary() -> dict | None:
    """What the site shows: mode, evidence, honest accuracy, top factors and the latest post-mortem."""
    m = _read_model()
    if not m:
        return None
    return {k: m.get(k) for k in ("mode", "trusted", "trained_at", "n_rows", "n_days", "cv", "criteria", "pass_streak",
                                  "factors", "segments", "note", "base_rate")} | {"postmortem": latest_postmortem()}


def refresh_site_summary() -> None:
    """Push the latest learning summary into the site's data file (the grading run has no model run)."""
    try:
        with open(WEB_LATEST, encoding="utf-8") as f:
            latest = json.load(f)
    except (OSError, ValueError):
        return
    latest["learning"] = summary()
    tmp = WEB_LATEST + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(latest, f, indent=2)
    os.replace(tmp, WEB_LATEST)


def nightly_update(date: str, graded: dict | None, fetch_box, fetch_ok=None) -> dict:
    """Grading-run hook: label last night, backfill older boards, retrain, write the post-mortem, refresh the site."""
    rows = label_date(date, fetch_box, fetch_ok)
    backfill(fetch_box, before=date, max_dates=int(os.environ.get("LEARN_BACKFILL_MAX_DATES", "40")), fetch_ok=fetch_ok)
    model = train()   # weights anchored to today, whatever date was graded
    if rows:
        postmortem(date, rows, graded)
    refresh_site_summary()
    return model
