"""
Projection -> probability -> edge over the market.

A prop is priced in three steps:

1. project(): the two-season blend (src/blend.py) gives a mean and a sample
   variance. A few games say little about a player's spread, so the sample
   variance is mixed with a typical spread for the stat (default_sd) carrying
   the weight of VAR_PRIOR_K games: three games are mostly prior, a full
   season mostly its own. The matchup multiplier (src/matchup.py) then
   scales mean and spread.
2. p_sides(): the projection becomes (P(over), P(under), P(push)) at the
   line. Points and combos are roughly normal; rebounds and assists are small
   counts and skewed, so they use a negative binomial with the projected mean
   and variance. An x.5 line cannot push. At an integer line this board's
   grader scores a push as a LOSS on either side, so both sides are win
   probabilities under that rule: P(over) = P(X > line), P(under) =
   P(X < line), and they sum to 1 - P(push).
3. edge_for(): the de-vigged market probability at the best price is the
   benchmark. When the market has moved since the pick's line was taken,
   market_prob_at() shifts the market's current probability back to the
   pick's line, so a line that moved down is read as the market favouring
   the under at the original number (and vice versa). At an integer line a
   book refunds a push, so its de-vigged prices are no-push probabilities;
   both are scaled by (1 - the model's P(push)) to put model and market on
   the same push-is-a-loss footing.

scipy is optional: the negative binomial falls back to an exact pmf sum
written with math.lgamma.
"""

import logging
import math

from src.blend import blend, variance_floor
from src.config import COMBO_RHO, VAR_PRIOR_K

logger = logging.getLogger(__name__)

try:   # optional; the lgamma fallback below gives the same numbers
    from scipy.stats import nbinom as _nbinom
except Exception:   # ImportError, or a broken install
    _nbinom = None

# Which shape each stat's per-game distribution takes. Stats not listed
# (3-pointers, steals, ...) default to normal; the edge model only projects
# EDGE_STATS and their combos.
DISTRIBUTION = {
    "points": "normal",
    "rebounds": "nb",
    "assists": "nb",
    "pra": "normal",
    "pts+reb": "normal",
    "pts+ast": "normal",
    "reb+ast": "normal",
}

P_MIN, P_MAX = 0.001, 0.999
NB_MAX_TERMS = 200            # pmf terms summed in the pure-math fallback
NB_MIN_DISPERSION = 1.05      # variance is at least mu * this (a negative binomial needs var > mu)


def _clip(p: float) -> float:
    return min(P_MAX, max(P_MIN, float(p)))


def _phi(x: float) -> float:
    """Standard normal CDF."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _nb_params(mu: float, sd: float) -> tuple[float, float, float]:
    """(r, p, var) of the negative binomial with mean mu and variance max(sd^2, mu * 1.05)."""
    var = max(float(sd) ** 2, mu * NB_MIN_DISPERSION)
    r = mu * mu / (var - mu)
    p = r / (r + mu)
    return r, p, var


def _nb_log_pmf(x: int, r: float, p: float) -> float:
    return math.lgamma(x + r) - math.lgamma(r) - math.lgamma(x + 1) + r * math.log(p) + x * math.log1p(-p)


def _nb_sf(k: int, r: float, p: float) -> float:
    """P(X >= k) for X ~ NB(r, p); scipy when present, else 1 - sum of the pmf below k."""
    if k <= 0:
        return 1.0
    if _nbinom is not None:
        return float(_nbinom.sf(k - 1, r, p))
    terms = min(k, NB_MAX_TERMS)
    cdf = 0.0
    for x in range(terms):
        cdf += math.exp(_nb_log_pmf(x, r, p))
    return max(0.0, 1.0 - cdf)


def _usable_sd(sd, mu: float) -> float:
    """sd as a positive float; missing, non-finite or non-positive -> the blend's variance floor."""
    try:
        sd = float(sd)
    except (TypeError, ValueError):
        sd = math.nan
    if not math.isfinite(sd) or sd <= 0:
        sd = math.sqrt(variance_floor(mu))
    return sd


def _is_integer_line(line: float) -> bool:
    return float(line).is_integer()


def p_sides(stat: str, mu, sd, line) -> tuple[float, float, float]:
    """
    (p_over, p_under, p_push) at `line` for a projection with mean mu and
    spread sd, as win probabilities under this board's grading rule (a push
    is a loss on either side).

    Not an integer (x.5) line: no push; p_over = P(X > line), p_under =
    1 - p_over. normal: 1 - Phi((line - mu) / sd) (an x.5 line is already the
    continuity-corrected boundary between two counts). nb: P(X >= ceil(line))
    for a negative binomial with mean mu and variance max(sd^2, 1.05 mu).

    Integer line L: nb: p_over = P(X > L), p_push = P(X = L), p_under =
    P(X < L). normal (continuity correction): p_over = P(Y > L + 0.5),
    p_under = P(Y < L - 0.5) and p_push the mass between. The two sides sum
    to 1 - p_push.

    Each side is clipped to [0.001, 0.999] and p_push is what is left, so
    the three always sum to 1. A non-positive or missing sd is lifted to the
    blend's variance floor; mu and line must be finite.
    """
    mu, line = float(mu), float(line)
    if not (math.isfinite(mu) and math.isfinite(line)):
        raise ValueError(f"p_sides needs finite mu and line (got mu={mu}, line={line})")
    sd = _usable_sd(sd, mu)
    integer = _is_integer_line(line)

    if DISTRIBUTION.get(stat, "normal") == "nb":
        k = math.floor(line) + 1          # smallest integer strictly above the line
        if mu <= 0:                       # X = 0 for certain
            po = 1.0 if k <= 0 else 0.0
            pu = 1.0 if line > 0 else 0.0
        else:
            r, p, _ = _nb_params(mu, sd)
            po = _nb_sf(k, r, p)
            pu = (1.0 - _nb_sf(int(line), r, p)) if integer else 1.0 - po
    elif integer:
        po = 1.0 - _phi((line + 0.5 - mu) / sd)
        pu = _phi((line - 0.5 - mu) / sd)
    else:
        po = 1.0 - _phi((line - mu) / sd)
        pu = 1.0 - po

    if not integer:
        po = _clip(po)
        return po, 1.0 - po, 0.0
    po, pu = _clip(po), _clip(pu)
    pp = 1.0 - po - pu
    return po, pu, pp if pp > 1e-12 else 0.0


def p_over(stat: str, mu, sd, line) -> float:
    """
    P(stat > line) for a projection with mean mu and spread sd: the first
    element of p_sides(). At an x.5 line the under is 1 - this; at an
    integer line a push takes P(X = line) out of both sides (normal with a
    continuity correction: P(Y > line + 0.5)). Clipped to [0.001, 0.999].
    """
    return p_sides(stat, mu, sd, line)[0]


def default_sd(stat: str, mu) -> float:
    """
    A typical game-to-game spread for a stat with mean mu: the prior that
    project() mixes with a player's own sample variance, and the stand-in for
    callers that have a line but no projection (CLV on boards older than
    round 18). points ~ 0.2 mu + 2.5 (25 ppg -> 7.5, 10 ppg -> 4.5),
    rebounds/assists ~ 0.3 mu + 0.8 (8 rpg -> 3.2, 4 apg -> 2.0, a little
    wider than Poisson), combos ~ 0.2 mu + 3 (40 PRA -> 11); anything else
    sqrt(max(mu, 1)). Never below the blend's variance floor.
    """
    try:
        m = max(0.0, float(mu))
    except (TypeError, ValueError):
        m = 0.0
    if not math.isfinite(m):
        m = 0.0
    if stat == "points":
        sd = 0.2 * m + 2.5
    elif stat in ("rebounds", "assists"):
        sd = 0.3 * m + 0.8
    elif stat in ("pra", "pts+reb", "pts+ast", "reb+ast"):
        sd = 0.2 * m + 3.0
    else:
        sd = math.sqrt(max(m, 1.0))
    return max(sd, math.sqrt(variance_floor(m)))


def project(stat: str, sample: dict, multiplier: float = 1.0, minutes_override: float | None = None) -> dict | None:
    """
    The player's projection for `stat` tonight from a two-season sample
    ({"this": {"values", "minutes", ...}, "last": {...}} as
    src.hitrate.player_sample returns it).

    mu = blended mean x multiplier; with `minutes_override` and a per-minute
    rate, mu = per_minute x minutes_override x multiplier.

    The variance: the blend's sample variance (unfloored) is mixed with a
    prior, prior_var = default_sd(stat, blended mean)^2, as if the prior
    were VAR_PRIOR_K games:
        var = (n_eff * var_sample + K * prior_var) / (n_eff + K),
    n_eff = n_this + n_last (the games behind the sample variance). A
    3-game rookie is mostly prior; a player with a full last season (~70
    games) barely moves. With no sample variance at all (no season has 2
    games) the prior stands alone. That variance then scales with the total
    scale applied to the mean (multiplier, and the minutes ratio when minutes
    are overridden) and the blend's floor max(0.25 mu, 0.5) applies last.

    Returns {"mu", "sd", "sd_sample", "sd_prior", "minutes", "dist",
             "n_this", "n_last", "weight_this", "mean_this", "mean_last",
             "multiplier"} or None without any games. sd_sample (None
    without a sample variance) and sd_prior are the two ingredients on the
    same scale as sd, so sd^2 is their n_eff : K mix (before the floor).
    """
    sample = sample or {}
    this, last = sample.get("this") or {}, sample.get("last") or {}
    b = blend(this.get("values"), last.get("values"), this.get("minutes"), last.get("minutes"))
    if b is None:
        return None
    try:
        mult = float(multiplier)
    except (TypeError, ValueError):
        mult = 1.0
    if not math.isfinite(mult) or mult <= 0:
        mult = 1.0

    scale = mult
    minutes = b["minutes"]
    if minutes_override and b["per_minute"] and b["minutes"] and b["minutes"] > 0:
        scale = mult * float(minutes_override) / b["minutes"]
        minutes = float(minutes_override)
    mu = b["mean"] * scale

    prior_var = default_sd(stat, b["mean"]) ** 2
    var_sample = b.get("var_sample")
    k = max(0.0, float(VAR_PRIOR_K))
    if var_sample is None:
        var_mix = prior_var
    else:
        n_eff = float(b["n_this"] + b["n_last"])
        var_mix = (n_eff * var_sample + k * prior_var) / (n_eff + k)
    var = max(var_mix * scale, variance_floor(mu))
    return {
        "mu": float(mu),
        "sd": float(math.sqrt(var)),
        "sd_sample": float(math.sqrt(max(0.0, var_sample) * scale)) if var_sample is not None else None,
        "sd_prior": float(math.sqrt(prior_var * scale)),
        "minutes": float(minutes) if minutes is not None else None,
        "dist": DISTRIBUTION.get(stat, "normal"),
        "n_this": b["n_this"],
        "n_last": b["n_last"],
        "weight_this": b["weight_this"],
        "mean_this": b["mean_this"],
        "mean_last": b["mean_last"],
        "multiplier": mult,
    }


def implied_prob(odds) -> float | None:
    """American odds ('-110', '+120', -110) -> implied probability with the vig in; None when unpriced."""
    if odds is None:
        return None
    try:
        text = str(odds).strip().replace("+", "")
        if not text:
            return None
        o = float(text)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(o) or abs(o) < 100:
        return None
    return 100.0 / (o + 100.0) if o > 0 else -o / (-o + 100.0)


def market_prob(over_odds, under_odds) -> tuple[float, float] | None:
    """
    (P_over, P_under) with the vig removed multiplicatively: each implied
    probability divided by their sum. One side missing -> assume it is
    priced like the other (a 50/50 market); both missing -> None.

    The two always sum to 1: at an integer line they are the book's no-push
    probabilities (a push is refunded). edge_for scales them for this
    board's push-is-a-loss rule.
    """
    po, pu = implied_prob(over_odds), implied_prob(under_odds)
    if po is None and pu is None:
        return None
    if po is None:
        po = pu
    if pu is None:
        pu = po
    total = po + pu
    if total <= 0:
        return None
    return (po / total, pu / total)


def _p_over_no_push(stat: str, mu: float, sd, line: float) -> float:
    """The over's share of the outcomes that are not a push: what a de-vigged price at `line` means."""
    po, pu, _ = p_sides(stat, mu, sd, line)
    if not _is_integer_line(line):
        return po
    return po / (po + pu)


def market_prob_at(stat: str, current_line, p_over_current, sd, pick_line, p_push=None) -> float:
    """
    The market's probability of OVER at `pick_line`, read off its current
    price: find the mean mu_m under which the over at current_line has the
    de-vigged probability p_over_current (bisection, the model's own sd; at
    an integer current_line that probability is the over's share of the
    non-push outcomes, since books refund pushes), then evaluate the over at
    pick_line under mu_m. This is how a moved line is turned into a
    probability at the original number: a line that moved down makes the
    over at the old line less likely than the current price says.

    x.5 pick_line: P(over pick_line) under mu_m, unchanged by any push
    (and p_over_current itself, exactly, when the line has not moved).

    Integer pick_line: a win probability under this board's rule (a push is
    a loss): the market's no-push probability of the over at pick_line times
    (1 - p_push). edge_for passes the model's P(push) so the shifted market
    is scaled exactly like a de-vigged price at the pick line; without
    p_push the push is the one implied by mu_m, which is just P(X > line)
    under mu_m. A caller that wants the book's own no-push probability
    (CLV against a de-vigged pick-time price) passes p_push=0.
    """
    current_line, pick_line = float(current_line), float(pick_line)
    integer_pick = _is_integer_line(pick_line)
    keep = None if p_push is None else 1.0 - min(1.0, max(0.0, float(p_push)))
    if pick_line == current_line:
        if not integer_pick:
            return float(p_over_current)
        if keep is not None:
            return float(p_over_current) * keep
    target = _clip(p_over_current)
    lo, hi = 0.0, max(50.0, 3.0 * current_line)
    if _p_over_no_push(stat, lo, sd, current_line) >= target:
        mu_m = lo
    elif _p_over_no_push(stat, hi, sd, current_line) <= target:
        mu_m = hi
    else:
        for _ in range(80):
            mid = 0.5 * (lo + hi)
            if _p_over_no_push(stat, mid, sd, current_line) < target:
                lo = mid
            else:
                hi = mid
            if hi - lo < 1e-9:
                break
        mu_m = 0.5 * (lo + hi)
    po, pu, _ = p_sides(stat, mu_m, sd, pick_line)
    if not integer_pick or keep is None:
        return po
    return po / (po + pu) * keep


def _no_market(p_model_over: float, p_model_under: float, p_push: float) -> dict:
    return {
        "p_model_over": p_model_over,
        "p_model_under": p_model_under,
        "p_push": p_push,
        "p_market_over": None,
        "p_market_under": None,
        "edge_over": None,
        "edge_under": None,
        "side": None,
        "edge": None,
        "p_model": None,
        "p_market": None,
        "market_basis": None,
    }


def edge_for(
    stat: str,
    proj: dict,
    line,
    over_odds,
    under_odds,
    current_line=None,
    current_over_odds=None,
    current_under_odds=None,
) -> dict:
    """
    Model vs market at the pick's line.

    The model's (p_model_over, p_model_under, p_push) come from p_sides():
    win probabilities with a push counted as a loss on either side (an x.5
    line has p_push 0 and complementary sides). The market probability is
    the de-vigged price at the pick line; when `current_line` differs from
    `line` (the market moved after the line was taken) it is the current
    consensus shifted to the pick line with market_prob_at(). At an integer
    line both de-vigged market probabilities are no-push probabilities (a
    book refunds a push), so both are scaled by (1 - p_push) with the
    model's p_push: the market's sides then sum to 1 - p_push like the
    model's, and edge_over = -edge_under still. The side is whichever of
    over/under has the larger edge (model - market).

    Returns {"p_model_over", "p_model_under", "p_push", "p_market_over",
             "p_market_under", "edge_over", "edge_under", "side", "edge",
             "p_model", "p_market", "market_basis"}; without any priced side
             the market fields, side and edge are None (never raises on
             None odds). market_basis is "pick" (the pick's own odds) or
             "shifted" (current consensus moved to the pick line).
    """
    line = float(line)
    p_model_over, p_model_under, p_push = p_sides(stat, proj["mu"], proj["sd"], line)
    keep = 1.0 - p_push            # 1 at an x.5 line

    p_market_over, p_market_under, basis = None, None, None
    if current_line is not None and float(current_line) != line:
        now = market_prob(current_over_odds, current_under_odds)
        if now is not None:
            p_market_over = market_prob_at(stat, float(current_line), now[0], proj["sd"], line, p_push=p_push)
            p_market_under = keep - p_market_over
            basis = "shifted"
    if p_market_over is None:
        own = market_prob(over_odds, under_odds)
        if own is not None:
            p_market_over, p_market_under, basis = own[0] * keep, own[1] * keep, "pick"
    if p_market_over is None:
        return _no_market(p_model_over, p_model_under, p_push)

    edge_over = p_model_over - p_market_over
    edge_under = p_model_under - p_market_under
    side = "over" if edge_over >= edge_under else "under"
    return {
        "p_model_over": p_model_over,
        "p_model_under": p_model_under,
        "p_push": p_push,
        "p_market_over": p_market_over,
        "p_market_under": p_market_under,
        "edge_over": edge_over,
        "edge_under": edge_under,
        "side": side,
        "edge": edge_over if side == "over" else edge_under,
        "p_model": p_model_over if side == "over" else p_model_under,
        "p_market": p_market_over if side == "over" else p_market_under,
        "market_basis": basis,
    }


def _sum_sd(sds: list[float], rho: float) -> float:
    """The sd of a sum of parts with these sds and one pairwise correlation rho (variance floored at 0)."""
    var = sum(s * s for s in sds)
    var += 2.0 * rho * sum(sds[i] * sds[j] for i in range(len(sds)) for j in range(i + 1, len(sds)))
    return math.sqrt(max(0.0, var))


def combo_projection(components: list[dict], rho: float = COMBO_RHO) -> dict | None:
    """
    A combo (PRA, PTS+REB, ...) from its single-stat projections: mu is the
    sum of the means and the variance that of a sum of correlated parts,
        var = sum(var_i) + 2 rho sum_{i<j} sd_i sd_j.
    rho (COMBO_RHO, 0.25) is the correlation between any two of a player's
    stats: points, rebounds and assists rise and fall together with his
    minutes (foul trouble, a blowout, overtime move all three), so the sum
    spreads wider than independence (rho = 0) would say. Normal; the
    blend's floor applies last. Each component's sd already carries its own
    variance prior (project()); sd_sample / sd_prior combine the
    components' the same way (None unless every component has one).
    Sample counts take the smallest of the components; "multiplier" is the
    effective one (mu / the sum of the unscaled means).
    """
    comps = [c for c in (components or []) if c and c.get("mu") is not None]
    if not comps:
        return None
    try:
        rho = float(rho)
    except (TypeError, ValueError):
        rho = 0.0
    if not math.isfinite(rho):
        rho = 0.0
    rho = min(1.0, max(-1.0, rho))
    mu = sum(float(c["mu"]) for c in comps)
    sds = [float(c.get("sd") or 0.0) for c in comps]
    var = max(_sum_sd(sds, rho) ** 2, variance_floor(mu))
    parts = {}
    for key in ("sd_sample", "sd_prior"):
        vals = [c.get(key) for c in comps]
        parts[key] = _sum_sd([float(v) for v in vals], rho) if all(v is not None for v in vals) else None
    minutes = next((c.get("minutes") for c in comps if c.get("minutes") is not None), None)
    base = sum(float(c["mu"]) / float(c.get("multiplier") or 1.0) for c in comps)
    sums = {}
    for key in ("mean_this", "mean_last"):
        vals = [c.get(key) for c in comps]
        sums[key] = float(sum(vals)) if all(v is not None for v in vals) else None
    n_this = min(int(c.get("n_this") or 0) for c in comps)
    return {
        "mu": float(mu),
        "sd": float(math.sqrt(var)),
        "sd_sample": parts["sd_sample"],
        "sd_prior": parts["sd_prior"],
        "rho": rho,
        "minutes": float(minutes) if minutes is not None else None,
        "dist": "normal",
        "n_this": n_this,
        "n_last": min(int(c.get("n_last") or 0) for c in comps),
        "weight_this": min(float(c.get("weight_this") or 0.0) for c in comps),
        "mean_this": sums["mean_this"],
        "mean_last": sums["mean_last"],
        "multiplier": float(mu / base) if base > 0 else 1.0,
    }
