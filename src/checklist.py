"""
The checklist (round 23): the checks behind a pick, read one prop at a time.

These are the checks the picks are cross-checked against by hand — defense
vs position, where and how the player scores against what the opponent
allows, his hit rate against tonight's line, his minutes trend, how similar
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

from src.config import OVER_MIN_SUPPORT, UNDER_MIN_SUPPORT

# (key, label, the scorecard signals whose verdict can switch the check off)
CHECKS = (
    ("dvp", "Defense vs position", ("dvp", "chk_dvp")),
    ("zones", "Shooting zones", ("zone_fit", "chk_zones")),
    ("playtypes", "Play types", ("playtype_fit", "chk_playtypes")),
    ("hit_rate", "Hit rate vs the line", ("hit_rate_weighted", "chk_hit_rate")),
    ("minutes", "Minutes trend", ("chk_minutes",)),
    ("similar", "Similar players vs this opponent", ("similar_opp_pos", "chk_similar")),
    ("situation", "Situation", ("chk_situation",)),
    ("passing", "Passing projection", ("chk_passing",)),
)
LABELS = {key: label for key, label, _ in CHECKS}

# A reading leans only past its bar. Each bar leaves a check neutral on a little over half of the props (measured on
# this season's boards), so a lean says something.
BARS = {"dvp": 0.75, "zones": 0.5, "playtypes": 0.5, "hit_rate": 0.06, "minutes": 3.0, "similar": 0.06, "situation": 0.5, "passing": 0.07}
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


def minutes_trend(minutes: list | None) -> dict | None:
    """Last 3 games' minutes against the games before them this season (newest first); None with too few games."""
    played = [m for m in (minutes or []) if isinstance(m, (int, float)) and m > 0]
    if len(played) < MIN_TREND_GAMES:
        return None
    recent, before = played[:3], played[3:]
    return {"recent": sum(recent) / len(recent), "before": sum(before) / len(before), "games": len(played)}


def read(ctx: dict, verdicts: dict | None = None) -> list[dict]:
    """
    The checks for one prop.

    ctx: {"stat", "line", "opponent", "pos_group",
          "dvp_z", "zone_z", "playtype_z",
          "over_games", "games"            — games over tonight's line / games,
          "minutes"                        — this season's minutes, newest first,
          "similar_over", "similar_n"      — earlier props by his position group vs this opponent that went over / all,
          "own_b2b", "opp_b2b", "teammate_out",
          "passing_p_over", "passing_mu"   — assists projected from passing (None otherwise)}
    verdicts: {scorecard signal key: verdict}.

    Returns [{"key", "label", "value" (toward the over; None = no reading),
              "lean" ("over" | "under" | None), "detail", "counts"}], only
    for checks that apply to the stat.
    """
    verdicts = verdicts or {}
    stat = ctx.get("stat")
    out = []

    def add(key, value, detail):
        bar = BARS[key]
        lean = None if value is None else "over" if value >= bar else "under" if value <= -bar else None
        signals = next(sig for k, _, sig in CHECKS if k == key)
        out.append({"key": key, "label": LABELS[key], "value": None if value is None else round(value, 4), "lean": lean,
                    "detail": detail, "counts": not any(verdicts.get(s) == "negative" for s in signals)})

    z = _num(ctx.get("dvp_z"))
    add("dvp", z, None if z is None else f"{ctx.get('opponent') or 'Opponent'} allows {z:+.1f} standard deviations of this stat to his position")

    if stat == "points":
        for key, field, what in (("zones", "zone_z", "where he shoots"), ("playtypes", "playtype_z", "how he scores")):
            v = _num(ctx.get(field))
            add(key, v, None if v is None else f"Opponent's defense {what}: {v:+.2f} (positive = allows more)")

    games, over_games = _num(ctx.get("games")), _num(ctx.get("over_games"))
    if games and games >= MIN_HIT_GAMES and over_games is not None:
        rate = over_games / games
        add("hit_rate", (rate - 0.5) * games / (games + HIT_RATE_K), f"Over {ctx.get('line')} in {int(over_games)} of {int(games)} games")
    else:
        add("hit_rate", None, None)

    trend = minutes_trend(ctx.get("minutes"))
    if trend:
        add("minutes", trend["recent"] - trend["before"], f"Last 3 games {trend['recent']:.1f} minutes, {trend['before']:.1f} before")
    else:
        add("minutes", None, None)

    n, k = _num(ctx.get("similar_n")), _num(ctx.get("similar_over"))
    if n and n >= MIN_SIMILAR and k is not None:
        who = POS_WORD.get(ctx.get("pos_group"), "Players at his position")
        add("similar", (k + 5.0) / (n + 10.0) - 0.5, f"{who} vs {ctx.get('opponent')}: over their line in {int(k)} of {int(n)}")
    else:
        add("similar", None, None)

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
    add("situation", v if known else None, (", ".join(notes).capitalize() if notes else "Normal rest, no starters out") if known else None)

    if stat == "assists":
        p = _num(ctx.get("passing_p_over"))
        mu = _num(ctx.get("passing_mu"))
        add("passing", None if p is None else p - 0.5, None if p is None else f"Passing projects {mu:.1f} against a line of {ctx.get('line')}")
    return out


def support(checks: list[dict] | None, side: str) -> dict:
    """{"for", "against", "net", "need"} for one side: counted checks leaning to it and against it."""
    side = str(side).lower()
    other = "under" if side == "over" else "over"
    counted = [c for c in checks or [] if c.get("counts") and c.get("lean")]
    n_for = sum(1 for c in counted if c["lean"] == side)
    n_against = sum(1 for c in counted if c["lean"] == other)
    return {"for": n_for, "against": n_against, "net": n_for - n_against,
            "need": OVER_MIN_SUPPORT if side == "over" else UNDER_MIN_SUPPORT}


def net_over(checks: list[dict] | None) -> int:
    """The checklist's net lean toward the over (what the scorecard scores as one signal)."""
    return support(checks, "over")["net"]
