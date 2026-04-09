"""
Adaptive learning module.

Analyzes historical graded picks to compute performance-based adjustments
that feed back into the scoring engine. The model learns from its own
successes and failures to sharpen future predictions.

Tracked dimensions:
  - stat  (points / rebounds / assists)
  - edge  (over / under)
  - grade (A+, A, B+, etc.)
  - hitrate bucket (high / medium / low)
  - pass_rate bucket (high / medium)

Each dimension tracks hit rate and produces a confidence multiplier:
  - If historical HR is well above baseline → boost
  - If historical HR is well below baseline → penalize
  - Insufficient sample → neutral (1.0)

The final adaptive_score is a combined multiplier applied to the raw
condition score before grading.
"""

import json
import logging
import os
from glob import glob

logger = logging.getLogger(__name__)

LOGS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
MIN_SAMPLE = 3  # minimum graded picks in a bucket to use the signal
BASELINE_HR = 0.55  # expected baseline hit rate


def _load_all_graded() -> list[dict]:
    """Load all graded pick entries from historical files."""
    graded_files = sorted(glob(os.path.join(LOGS_DIR, "graded_*.json")))
    all_picks = []
    for fpath in graded_files:
        try:
            with open(fpath) as f:
                data = json.load(f)
            for pick in data.get("graded_picks", []):
                if pick.get("result") in ("hit", "miss"):
                    pick["_date"] = data.get("date", "")
                    all_picks.append(pick)
        except Exception as e:
            logger.warning(f"Could not load {fpath}: {e}")
    return all_picks


def _bucket_hitrate(hr: float | None) -> str:
    if hr is None or hr < 0:
        return "unknown"
    if hr >= 0.56:
        return "high"
    if hr >= 0.50:
        return "medium"
    return "low"


def _bucket_pass_rate(pr: float | None) -> str:
    if pr is None:
        return "unknown"
    if pr >= 0.80:
        return "high"
    return "medium"


def _compute_multiplier(hits: int, total: int) -> float:
    """
    Convert a bucket's hit/total into a confidence multiplier.

    Returns a value typically between 0.85 and 1.15:
      - 1.0 = neutral (insufficient data or at baseline)
      - >1.0 = historically outperforming → boost
      - <1.0 = historically underperforming → penalize

    Clamped to [0.80, 1.20] to prevent extreme swings.
    """
    if total < MIN_SAMPLE:
        return 1.0
    observed_hr = hits / total
    delta = observed_hr - BASELINE_HR
    # Scale: every 10% above/below baseline = 0.10 multiplier shift
    multiplier = 1.0 + delta
    return max(0.80, min(1.20, multiplier))


def compute_adaptive_weights() -> dict:
    """
    Analyze all historical graded picks and return a weights dictionary.

    Returns:
        {
            "by_stat": {"points": mult, "rebounds": mult, "assists": mult},
            "by_edge": {"over": mult, "under": mult},
            "by_grade": {"A+": mult, "A": mult, ...},
            "by_hitrate_bucket": {"high": mult, "medium": mult, "low": mult},
            "by_stat_edge": {"points_over": mult, "assists_under": mult, ...},
            "total_graded": int,
            "overall_hr": float,
        }
    """
    picks = _load_all_graded()

    if not picks:
        logger.info("No historical grading data — adaptive weights neutral")
        return _neutral_weights()

    weights: dict = {}

    # ── by stat ──────────────────────────────────────────────────────
    weights["by_stat"] = {}
    for stat in ("points", "rebounds", "assists"):
        bucket = [p for p in picks if p.get("stat") == stat]
        hits = sum(1 for p in bucket if p["result"] == "hit")
        weights["by_stat"][stat] = _compute_multiplier(hits, len(bucket))

    # ── by edge ──────────────────────────────────────────────────────
    weights["by_edge"] = {}
    for edge in ("over", "under"):
        bucket = [p for p in picks if p.get("edge") == edge]
        hits = sum(1 for p in bucket if p["result"] == "hit")
        weights["by_edge"][edge] = _compute_multiplier(hits, len(bucket))

    # ── by grade ─────────────────────────────────────────────────────
    weights["by_grade"] = {}
    for grade in ("A+", "A", "B+", "B", "C", "D"):
        bucket = [p for p in picks if p.get("grade") == grade]
        hits = sum(1 for p in bucket if p["result"] == "hit")
        weights["by_grade"][grade] = _compute_multiplier(hits, len(bucket))

    # ── by hitrate bucket ────────────────────────────────────────────
    weights["by_hitrate_bucket"] = {}
    for hr_bucket in ("high", "medium", "low", "unknown"):
        bucket = [p for p in picks if _bucket_hitrate(p.get("hitrate")) == hr_bucket]
        hits = sum(1 for p in bucket if p["result"] == "hit")
        weights["by_hitrate_bucket"][hr_bucket] = _compute_multiplier(hits, len(bucket))

    # ── by stat+edge combo ───────────────────────────────────────────
    weights["by_stat_edge"] = {}
    for stat in ("points", "rebounds", "assists"):
        for edge in ("over", "under"):
            key = f"{stat}_{edge}"
            bucket = [p for p in picks if p.get("stat") == stat and p.get("edge") == edge]
            hits = sum(1 for p in bucket if p["result"] == "hit")
            weights["by_stat_edge"][key] = _compute_multiplier(hits, len(bucket))

    # ── metadata ─────────────────────────────────────────────────────
    total_hits = sum(1 for p in picks if p["result"] == "hit")
    weights["total_graded"] = len(picks)
    weights["overall_hr"] = round(total_hits / len(picks), 4) if picks else 0.0

    logger.info(
        f"Adaptive weights computed from {len(picks)} graded picks "
        f"(overall HR: {weights['overall_hr']:.1%})"
    )
    for dim in ("by_stat", "by_edge", "by_stat_edge"):
        for k, v in weights[dim].items():
            if v != 1.0:
                logger.info(f"  {dim}.{k}: {v:.3f}")

    return weights


def _neutral_weights() -> dict:
    return {
        "by_stat": {"points": 1.0, "rebounds": 1.0, "assists": 1.0},
        "by_edge": {"over": 1.0, "under": 1.0},
        "by_grade": {},
        "by_hitrate_bucket": {},
        "by_stat_edge": {},
        "total_graded": 0,
        "overall_hr": 0.0,
    }


def get_adaptive_score(stat: str, edge: str, hitrate: float | None,
                       weights: dict) -> float:
    """
    Compute a single adaptive multiplier for a specific pick.

    Combines signals from multiple dimensions via geometric mean-ish blend:
      final = (stat_mult + edge_mult + combo_mult + hr_bucket_mult) / 4

    Returns a value ~0.80–1.20 that adjusts the raw condition score.
    """
    if weights.get("total_graded", 0) < MIN_SAMPLE:
        return 1.0

    stat_m = weights.get("by_stat", {}).get(stat, 1.0)
    edge_m = weights.get("by_edge", {}).get(edge, 1.0)
    combo_m = weights.get("by_stat_edge", {}).get(f"{stat}_{edge}", 1.0)
    hr_bucket = _bucket_hitrate(hitrate)
    hr_m = weights.get("by_hitrate_bucket", {}).get(hr_bucket, 1.0)

    # Weighted average (combo gets extra weight as most specific signal)
    blended = (stat_m + edge_m + combo_m * 2 + hr_m) / 5

    return round(max(0.80, min(1.20, blended)), 4)
