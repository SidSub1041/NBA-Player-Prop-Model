"""
Two-season blend of a player's per-game rates.

Early in a season a player has a handful of games and last season is the
better guide; by mid-season his own current numbers should dominate. The
blend weights this season n/(n+K) (K = BLEND_K games, so 10 games = 50/50)
and, within this season, weights recent games more (a half-life of
FORM_HALF_LIFE games) so form shows through. Last season counts as one flat
sample. The result is a mean, a spread and a minutes figure that
src/projection.py turns into a distribution.

The spread here is the sample's own (floored). A handful of games says
little about a player's spread, so project() mixes the raw sample variance
(`var_sample`) with a per-stat prior before using it (src/projection.py,
VAR_PRIOR_K); the floor is the last resort after that.

Pure functions over lists of numbers — numpy only, no network.
"""

import math

import numpy as np

from src.config import BLEND_K, FORM_HALF_LIFE


def weight_this(n_this, k=BLEND_K) -> float:
    """This season's share of the blend: n/(n+K). 0 games -> 0, K games -> 0.5."""
    n = max(0.0, float(n_this or 0))
    k = max(0.0, float(k))
    if n + k <= 0:
        return 0.0
    return n / (n + k)


def form_weights(n, half_life=FORM_HALF_LIFE) -> np.ndarray:
    """
    Weights for n games ordered newest first: w_i = 0.5 ** (i / half_life),
    normalised to sum 1 (n = 0 -> an empty array). A half-life of 0 or less
    switches form weighting off (equal weights).
    """
    n = int(n or 0)
    if n <= 0:
        return np.empty(0, dtype=float)
    if not half_life or half_life <= 0:
        return np.full(n, 1.0 / n)
    w = 0.5 ** (np.arange(n, dtype=float) / float(half_life))
    return w / w.sum()


def variance_floor(mean) -> float:
    """
    The smallest variance the model will believe: max(0.25 * mean, 0.5).

    A last resort: a player with a handful of near-identical games is not
    that predictable, and project() already pulls a small sample's variance
    toward a typical per-stat spread (src/projection.py); the floor only
    stops what is left from turning into a near-certain probability. Used by
    blend() and again after a multiplier rescales sd.
    """
    try:
        m = float(mean)
    except (TypeError, ValueError):
        m = 0.0
    if not math.isfinite(m):
        m = 0.0
    return max(0.25 * m, 0.5)


def _clean(values, minutes=None):
    """Float arrays with non-finite values (and their minutes) dropped; minutes None unless aligned."""
    if values is None:
        return np.empty(0), None
    v = np.asarray([float(x) if x is not None else math.nan for x in list(values)], dtype=float)
    m = None
    if minutes is not None:
        m = np.asarray([float(x) if x is not None else math.nan for x in list(minutes)], dtype=float)
        if m.shape != v.shape:
            m = None
    keep = np.isfinite(v)
    v = v[keep]
    if m is not None:
        m = m[keep]
    return v, m


def _weighted_mean_var(values, weights):
    """
    (mean, variance) under reliability weights that sum to 1. The variance
    uses the unbiased correction 1 / (1 - sum(w^2)), which is n/(n-1) for
    equal weights; None with fewer than 2 games.
    """
    n = len(values)
    if n == 0:
        return None, None
    mean = float(np.dot(weights, values))
    if n < 2:
        return mean, None
    denom = 1.0 - float(np.dot(weights, weights))
    if denom <= 1e-12:
        return mean, None
    var = float(np.dot(weights, (values - mean) ** 2)) / denom
    return mean, var


def blend(this_values, last_values, this_minutes=None, last_minutes=None, k=BLEND_K, half_life=FORM_HALF_LIFE) -> dict | None:
    """
    Blend this season (form-weighted, newest first) with last season (flat).

    mean = w * mean_this + (1 - w) * mean_last with w = weight_this(n_this);
    var is blended the same way and floored at variance_floor(mean). A
    season with fewer than 2 games has no variance of its own and borrows
    the other season's. When one season is empty the other carries the
    whole weight (so a player with no last-season games is not pulled
    toward nothing). Minutes blend the same way when both lists line up
    with their values; per_minute = mean / minutes when minutes > 0.

    `var_sample` is the blended variance before the floor (None when
    neither season has 2 games); project() mixes it with a prior.

    Returns {"mean", "sd", "var_sample", "minutes", "per_minute", "n_this",
             "n_last", "weight_this", "mean_this", "mean_last"} or None when
    both seasons are empty.
    """
    v_this, m_this = _clean(this_values, this_minutes)
    v_last, m_last = _clean(last_values, last_minutes)
    n_this, n_last = len(v_this), len(v_last)
    if n_this == 0 and n_last == 0:
        return None

    w_form = form_weights(n_this, half_life)
    mean_this, var_this = _weighted_mean_var(v_this, w_form)
    if n_last:
        mean_last = float(v_last.mean())
        var_last = float(v_last.var(ddof=1)) if n_last >= 2 else None
    else:
        mean_last, var_last = None, None

    if n_last == 0:
        w = 1.0
    elif n_this == 0:
        w = 0.0
    else:
        w = weight_this(n_this, k)

    mean = w * (mean_this or 0.0) + (1.0 - w) * (mean_last or 0.0)

    if var_this is None and var_last is None:
        var_sample = None
    else:
        vt = var_this if var_this is not None else var_last
        vl = var_last if var_last is not None else var_this
        var_sample = w * vt + (1.0 - w) * vl
    var = max(var_sample if var_sample is not None else 0.0, variance_floor(mean))

    minutes_this = float(np.dot(w_form, m_this)) if m_this is not None and n_this else None
    minutes_last = float(m_last.mean()) if m_last is not None and n_last else None
    if minutes_this is not None and minutes_last is not None:
        minutes = w * minutes_this + (1.0 - w) * minutes_last
    elif minutes_this is not None:
        minutes = minutes_this
    else:
        minutes = minutes_last
    per_minute = mean / minutes if minutes is not None and minutes > 0 else None

    return {
        "mean": float(mean),
        "sd": float(math.sqrt(var)),
        "var_sample": float(var_sample) if var_sample is not None else None,
        "minutes": float(minutes) if minutes is not None else None,
        "per_minute": float(per_minute) if per_minute is not None else None,
        "n_this": n_this,
        "n_last": n_last,
        "weight_this": float(w),
        "mean_this": float(mean_this) if mean_this is not None else None,
        "mean_last": float(mean_last) if mean_last is not None else None,
    }
