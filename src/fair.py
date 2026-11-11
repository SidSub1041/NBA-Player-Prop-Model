"""
The market-anchored probability (round 23).

The projection that used to pick the plays had no predictive power beside
the sportsbooks' price, so the published probability now STARTS from that
price:

  fair price   each book's own two-way price at the regular line with the vig
               taken out (its over's implied probability divided by the sum of
               both sides'), then the MEDIAN across books — one soft book
               hanging an outlier price moves a median very little. It needs
               MIN_FAIR_BOOKS books with both sides priced at the line; with
               fewer, the best over and best under on offer are de-vigged as
               a pair and the prop is marked ("best_pair") so the books guard
               holds it back.
  adjustments  a check may move that probability only after it has passed the
               scorecard's bar (src/scorecard.py: beats the price on 25
               forward nights and 1,500 props). A passed check moves the
               log-odds by ADJUST_SHRINK x its fitted weight x its reading;
               the re-fitted price, once passed, by ADJUST_SHRINK x its
               intercept. Nothing has passed yet, so today the probability
               IS the fair price.
  price edge   fair probability of a side − the break-even of the best price
               any book offers for that side. Positive means the best price
               is better than the market's own fair price; the vig is always
               charged.

At an integer line a push is a loss on this board, so both sides are scaled
by (1 − P(push)); the push probability is the only thing still read from the
projection's distribution.
"""

import math
import statistics

from src.config import ADJUST_SHRINK, MIN_FAIR_BOOKS
from src.projection import implied_prob


def devig(over_odds, under_odds) -> float | None:
    """The over's no-vig probability from one two-way price; None unless both sides are priced."""
    a, b = implied_prob(over_odds), implied_prob(under_odds)
    if a is None or b is None or a + b <= 0:
        return None
    return a / (a + b)


def fair_over(current: dict | None, line, over_odds=None, under_odds=None, min_books: int = MIN_FAIR_BOOKS) -> dict | None:
    """
    The market's fair (no-vig, no-push) probability of the over at `line`.

    `current` is the odds cache's entry for the prop (src/vegas_odds.py):
    "offers" holds each book's main line and both prices. Books whose main
    line is `line` and that price both sides each give one de-vigged
    probability; the fair price is their median.

    Returns {"p_over", "books", "basis": "books" | "best_pair", "low", "high"}
    or None when nothing two-sided is on offer. `over_odds` / `under_odds` are
    the best prices at the line (the pair fallback).
    """
    try:
        line = float(line)
    except (TypeError, ValueError):
        return None
    probs = []
    for o in ((current or {}).get("offers") or {}).values():
        try:
            same = float(o.get("line")) == line
        except (TypeError, ValueError):
            same = False
        p = devig(o.get("over"), o.get("under")) if same else None
        if p is not None:
            probs.append(p)
    if len(probs) >= min_books:
        return {"p_over": statistics.median(probs), "books": len(probs), "basis": "books", "low": min(probs), "high": max(probs)}
    pair = devig(over_odds, under_odds)
    if pair is None:
        return None
    return {"p_over": pair, "books": len(probs), "basis": "best_pair", "low": pair, "high": pair}


def _logit(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def _sigmoid(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(min(z, 30.0), -30.0)))


def adjust(p_over: float, values: dict | None, card: dict | None, shrink: float = ADJUST_SHRINK) -> tuple[float, list[dict]]:
    """
    Move the fair probability by the checks that have PASSED the scorecard.

    `card` is the scorecard on file; `values` is {signal key: this prop's
    reading, pointed toward the over} (src/scorecard.today_values). Returns
    (probability, [{"key", "label", "shift"}]) — the shifts are in log-odds.
    With nothing passed (or no card) the probability comes back unchanged.
    """
    used = []
    if not card:
        return p_over, used
    z = _logit(p_over)
    for s in card.get("signals") or []:
        if not s.get("passes") or s.get("coef") is None:
            continue
        if s.get("key") == "price_refit":
            shift = shrink * float(s["coef"])
        else:
            v = (values or {}).get(s.get("key"))
            if v is None or (isinstance(v, float) and math.isnan(v)):
                continue
            shift = shrink * float(s["coef"]) * float(v)
        z += shift
        used.append({"key": s.get("key"), "label": s.get("label"), "shift": round(shift, 4)})
    return (_sigmoid(z) if used else p_over), used


def decide(line, over_odds, under_odds, current: dict | None, p_push: float = 0.0,
           values: dict | None = None, card: dict | None = None) -> dict:
    """
    Both sides of one prop at `line`, priced at the best odds on offer.

    Returns {"p_fair_over", "p_fair_under" (win probabilities, a push counted
    as a loss), "p_market_over", "p_market_under" (the fair price before any
    adjustment, same rule), "fair_over" (that fair price as the books mean
    it: no vig, a push refunded), "fair_books", "fair_basis", "fair_low",
    "fair_high",
    "breakeven_over", "breakeven_under", "edge_over", "edge_under",
    "adjustments"}; the probabilities and edges are None without a two-sided
    price, and a side without a price has no edge.
    """
    out = {"p_fair_over": None, "p_fair_under": None, "p_market_over": None, "p_market_under": None, "fair_over": None,
           "fair_books": 0, "fair_basis": None,
           "fair_low": None, "fair_high": None, "breakeven_over": implied_prob(over_odds), "breakeven_under": implied_prob(under_odds),
           "edge_over": None, "edge_under": None, "adjustments": []}
    fair = fair_over(current, line, over_odds, under_odds) if line is not None else None
    if fair is None:
        return out
    keep = 1.0 - min(1.0, max(0.0, float(p_push or 0.0)))
    p, used = adjust(fair["p_over"], values, card)
    out.update({"p_fair_over": p * keep, "p_fair_under": (1.0 - p) * keep,
                "p_market_over": fair["p_over"] * keep, "p_market_under": (1.0 - fair["p_over"]) * keep, "fair_over": fair["p_over"],
                "fair_books": fair["books"], "fair_basis": fair["basis"], "fair_low": fair["low"], "fair_high": fair["high"],
                "adjustments": used})
    for side in ("over", "under"):
        be = out[f"breakeven_{side}"]
        if be is not None:
            out[f"edge_{side}"] = out[f"p_fair_{side}"] - be
    return out
