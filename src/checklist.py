"""
The checklist (round 23): the checks behind a pick, read one prop at a time.

These are the checks the picks are cross-checked against by hand — defense
vs position, where and how the player scores against what the opponent
allows, his hit rate against tonight's line (overall and at tonight's venue,
home or road), his minutes trend, how similar
players did against this opponent, and the situation (rest, starters out) —
plus, for assists, the passing projection. Each is read as a lean toward the
OVER, toward the UNDER, or neither, with the number behind it in plain words.

None of them moves the published probability: that is the market's fair
price until a check passes the scorecard's bar (src/fair.py). The checklist
decides which fairly priced props are worth publishing:

  support(side) = checks leaning to the side − checks leaning against it
  an under needs UNDER_MIN_SUPPORT net checks, an over OVER_MIN_SUPPORT —
  overs have been overpriced, so the checks have to outweigh that.

A check whose scorecard verdict is "negative" (it has pointed the wrong way,
with an interval clear of zero) is still shown but does not count. Every
reading is stored on the board, and the scorecard scores each check and the
checklist's net lean every night, so these bars answer to the evidence too.
"""

import math

from src.config import OVER_MIN_SUPPORT, POINTS_STATS, UNDER_MIN_SUPPORT

# (key, label, the scorecard signals whose verdict can switch the check off)
CHECKS = (
    ("dvp", "Defense vs position", ("dvp", "chk_dvp")),
    ("zones", "Shooting zones", ("zone_fit", "chk_zones")),
    ("playtypes", "Play types", ("playtype_fit", "chk_playtypes")),
    ("hit_rate", "Hit rate vs the line", ("hit_rate_weighted", "chk_hit_rate")),
    ("split", "Home/away split", ("chk_split",)),
    ("minutes", "Minutes trend", ("chk_minutes",)),
    ("similar", "Similar players vs this opponent", ("similar_opp_pos", "chk_similar")),
    ("situation", "Situation", ("chk_situation",)),
    ("passing", "Passing projection", ("chk_passing",)),
)
LABELS = {key: label for key, label, _ in CHECKS}

# A reading leans only past its bar. Each bar leaves a check neutral on a little over half of the props (measured on
# this season's boards), so a lean says something.
BARS = {"dvp": 0.75, "zones": 0.5, "playtypes": 0.5, "hit_rate": 0.06, "split": 0.06, "minutes": 3.0, "similar": 0.06, "situation": 1.0,
        "passing": 0.07}      # situation counts whole flags: one flag is one bar (the lean), two the end of the scale
HIT_RATE_K = 20.0        # a hit rate over n games counts n / (n + 20) of itself
MIN_HIT_GAMES = 5
MIN_TREND_GAMES = 5      # games this season before a minutes trend is read (the last 3 against the ones before)
MIN_SIMILAR = 8          # earlier props by the position group against this opponent
POS_WORD = {"G": "Guards", "F": "Forwards", "C": "Centers"}


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def score(value: float, bar: float, side: str) -> int:
    """
    A reading as a score out of 30 toward `side` (round 30), on the scale a
    defensive rank is read on — 30 the most favourable to the side, 1 the
    least:
      23-30  behind the side  (the check leans to it: the reading is past its bar)
      15-22  neutral, on the side's half      ("high neutral")
       8-14  neutral, on the other half       ("low neutral")
       1-7   against the side (the check leans the other way)
    So a score of 23 or more is exactly a check that counts for the side and
    7 or less one that counts against it; the two neutral bands say which way
    a reading that counts for nothing is tilted. Both ends are reached at two
    bars (30 for, 1 against).
    """
    x = (value if side == "over" else -value) / bar
    if x >= 1:
        return 30 if x >= 2 else 23 + int((x - 1) * 7)
    if x >= 0:
        return 15 + min(7, int(x * 8))
    if x > -1:
        return 14 - min(6, int(-x * 7))
    return 1 if x <= -2 else 7 - int((-x - 1) * 6)


def lean_of(value: float | None, bar: float) -> str | None:
    """"over" / "under" when the reading is past its bar, else None — read off the score, so the two can never disagree."""
    if value is None:
        return None
    s = score(value, bar, "over")
    return "over" if s >= 23 else "under" if s <= 7 else None


def minutes_trend(minutes: list | None) -> dict | None:
    """Last 3 games' minutes against the games before them this season (newest first); None with too few games."""
    played = [m for m in (minutes or []) if isinstance(m, (int, float)) and m > 0]
    if len(played) < MIN_TREND_GAMES:
        return None
    recent, before = played[:3], played[3:]
    return {"recent": sum(recent) / len(recent), "before": sum(before) / len(before), "games": len(played)}


def read(ctx: dict, verdicts: dict | None = None, weights: dict | None = None) -> list[dict]:
    """
    The checks for one prop.

    ctx: {"stat", "line", "opponent", "pos_group",
          "dvp_z", "zone_z", "playtype_z",
          "over_games", "games"            — games over tonight's line / games,
          "split_over", "split_games", "is_home" — the same at tonight's venue (home or road), both seasons,
          "minutes"                        — this season's minutes, newest first,
          "similar_over", "similar_n"      — earlier props by his position group vs this opponent that went over / all,
          "own_b2b", "opp_b2b", "teammate_out",
          "passing_p_over", "passing_mu"   — assists projected from passing (None otherwise)}
    verdicts: {scorecard signal key: verdict}.
    weights: {check key: weight} for this prop's stat (src/weights.py, round
      31): what a lean of each check counts for. A check not named counts 1;
      a weight of 0 means the check has pointed the wrong way for this stat
      and counts for nothing.

    Returns [{"key", "label", "value" (toward the over; None = no reading),
              "lean" ("over" | "under" | None), "detail", "counts",
              "score" ({"over", "under"}: the reading out of 30 toward each
              side, see score(); None without a reading), "missing" (why
              there is no reading)}] — one entry for EVERY check that applies
    to the stat, read or not: shot zones and play types for points and the
    combos that include points, passing for assists, the rest for all.
    """
    verdicts, weights = verdicts or {}, weights or {}
    stat = ctx.get("stat")
    out = []

    def add(key, value, detail, missing=None):
        bar = BARS[key]
        if value is not None and not math.isfinite(value):
            value, detail = None, None          # a reading that is not a number is no reading
        if value is not None:
            value = round(value, 4)             # what is stored is what the lean and the score are read from
        weight = round(max(0.0, float(weights.get(key, 1.0))), 2)
        signals = next(sig for k, _, sig in CHECKS if k == key)
        out.append({"key": key, "label": LABELS[key], "value": value, "lean": lean_of(value, bar), "detail": detail,
                    # a check counts unless the scorecard has found it pointing the wrong way, overall or (weight 0) for this stat
                    "counts": weight > 0 and not any(verdicts.get(s) == "negative" for s in signals),
                    "weight": weight,
                    # round 30: the reading out of 30 toward each side, and — when there is no reading — why
                    "score": None if value is None else {"over": score(value, bar, "over"), "under": score(value, bar, "under")},
                    "missing": missing if value is None else None})

    z = _num(ctx.get("dvp_z"))
    add("dvp", z, None if z is None else f"{ctx.get('opponent') or 'Opponent'} allows {z:+.1f} standard deviations of this stat to his position",
        "no by-position defense number for this matchup (his position is not known, or the table was not available)")

    if stat in POINTS_STATS:
        for key, field, what, why in (("zones", "zone_z", "where he shoots", "no shot-location data for him or this opponent"),
                                      ("playtypes", "playtype_z", "how he scores", "no play-type data for him or this opponent")):
            v = _num(ctx.get(field))
            add(key, v, None if v is None else f"Opponent's defense {what}: {v:+.2f} (positive = allows more)", why)

    games, over_games = _num(ctx.get("games")), _num(ctx.get("over_games"))
    if games and games >= MIN_HIT_GAMES and over_games is not None:
        rate = over_games / games
        add("hit_rate", (rate - 0.5) * games / (games + HIT_RATE_K), f"Over {ctx.get('line')} in {int(over_games)} of {int(games)} games")
    else:
        add("hit_rate", None, None, f"fewer than {MIN_HIT_GAMES} of his games on file to set against the line")

    sg, so = _num(ctx.get("split_games")), _num(ctx.get("split_over"))
    if sg and sg >= MIN_HIT_GAMES and so is not None and ctx.get("is_home") is not None:
        where = "home" if ctx.get("is_home") else "road"
        add("split", (so / sg - 0.5) * sg / (sg + HIT_RATE_K), f"Over {ctx.get('line')} in {int(so)} of {int(sg)} {where} games")
    else:
        add("split", None, None, "the venue is not known" if ctx.get("is_home") is None
            else f"fewer than {MIN_HIT_GAMES} of his {'home' if ctx.get('is_home') else 'road'} games on file")

    trend = minutes_trend(ctx.get("minutes"))
    if trend:
        add("minutes", trend["recent"] - trend["before"], f"Last 3 games {trend['recent']:.1f} minutes, {trend['before']:.1f} before")
    else:
        add("minutes", None, None, f"fewer than {MIN_TREND_GAMES} of his games this season on file")

    n, k = _num(ctx.get("similar_n")), _num(ctx.get("similar_over"))
    if n and n >= MIN_SIMILAR and k is not None:
        who = POS_WORD.get(ctx.get("pos_group"), "Players at his position")
        add("similar", (k + 5.0) / (n + 10.0) - 0.5, f"{who} vs {ctx.get('opponent')}: over their line in {int(k)} of {int(n)}")
    else:
        add("similar", None, None, f"fewer than {MIN_SIMILAR} earlier props by his position group against this opponent")

    notes, v = [], 0.0
    if ctx.get("opp_b2b") and not ctx.get("own_b2b"):
        v += 1.0
        notes.append("opponent on a back-to-back")
    if ctx.get("own_b2b") and not ctx.get("opp_b2b"):
        v -= 1.0
        notes.append("own team on a back-to-back")
    out_n = int(_num(ctx.get("teammate_out")) or 0)
    if out_n > 0:
        v += 1.0
        notes.append(f"{out_n} starter{'s' if out_n != 1 else ''} out on his team")
    known = ctx.get("own_b2b") is not None or ctx.get("opp_b2b") is not None or ctx.get("teammate_out") is not None
    add("situation", v if known else None, (", ".join(notes).capitalize() if notes else "Normal rest, no starters out") if known else None,
        "yesterday's schedule and the injury report are not known")

    if stat == "assists":
        p = _num(ctx.get("passing_p_over"))
        mu = _num(ctx.get("passing_mu"))
        add("passing", None if p is None else p - 0.5, None if p is None else f"Passing projects {mu:.1f} against a line of {ctx.get('line')}",
            "no passing projection for him tonight")
    return out


def _n(x: float):
    """A whole number as an int (so an unweighted board reads 3, not 3.0), anything else to two decimals."""
    x = round(float(x), 2)
    return int(x) if x.is_integer() else x


def support(checks: list[dict] | None, side: str) -> dict:
    """
    {"for", "against", "net", "need"} for one side: the weight of the counted
    checks leaning to it and of those leaning against it (round 31: each
    check's own weight for the stat; a check without one, as on boards from
    before round 31, weighs 1, which is the old count).
    """
    side = str(side).lower()
    other = "under" if side == "over" else "over"
    counted = [c for c in checks or [] if c.get("counts") and c.get("lean")]
    w = lambda c: float(c.get("weight", 1.0) if c.get("weight") is not None else 1.0)      # noqa: E731
    n_for = sum(w(c) for c in counted if c["lean"] == side)
    n_against = sum(w(c) for c in counted if c["lean"] == other)
    return {"for": _n(n_for), "against": _n(n_against), "net": _n(n_for - n_against),
            "need": OVER_MIN_SUPPORT if side == "over" else UNDER_MIN_SUPPORT}


def net_over(checks: list[dict] | None):
    """The checklist's net lean toward the over (what the scorecard scores as one signal)."""
    return support(checks, "over")["net"]
