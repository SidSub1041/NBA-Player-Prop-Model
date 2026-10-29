"""
Assists from passing (round 19).

An assist is the last link of a chain the passer only partly controls:

    assists = minutes x passes per minute x potential assists per pass x conversion

  passes per minute       how much of the offence runs through his hands: a
                          role, counted in dozens of passes a game, so a few
                          games pin it down. This season (its last 10 games
                          counted twice, for form) weighs n / (n + PASS_BLEND_K)
                          against last season.
  potential assists/pass  how often one of his passes sets up a shot (NBA
                          tracking: a pass to a team-mate who shoots within one
                          dribble; an assist if it goes in). Playmaking: pulled
                          toward his last season, and that toward what players
                          who pass as often as he does manage (a line fitted
                          across last season's league: lead ball-handlers set
                          up more shots per pass than a centre's hand-offs),
                          with PA_RATIO_K passes of prior at each step.
  conversion              assists / potential assists: whether the team-mate
                          makes it. Mostly the team-mates' shooting and, over a
                          few weeks, mostly their luck, so it gets no form
                          weighting and is pulled hard toward a prior
                          (AST_CONV_K potential assists of it): the league rate
                          scaled by how well his team makes the shots passes
                          create, zone by zone.

A cold-shooting week costs a passer assists without touching the first two
links, and a hot week adds assists that will not repeat. A box-score average
(and a line set from one) carries both; this projection keeps the links that
persist and regresses the one that does not.

Tonight's opponent enters through the shots and the passes:

  shooting        FG% the opponent allows in each zone (NBA.com shot
                  locations, opponent measure), averaged over where the
                  player's team takes its assisted shots (its own FGA by zone x
                  the share of that zone's makes that are assisted), each
                  zone's deviation from the league shrunk by its attempts:
                  three-point defence is mostly noise, rim defence much less.
  ball movement   the share of the makes the opponent allows that are assisted
                  (OPP_AST / OPP_FGM), shrunk the same way.

Pace and OUT team-mates come from src/matchup.py as for every stat. The
by-position DvP table is not used for assists: it mixes these same effects
(and their noise) into one number. Last season counts LAST_SEASON_WEIGHT next
to this season in every team table.

Every table is league-wide: LeagueDashPtStats (Passing) for this season, its
last 10 games and last season, and team shot locations / opponent totals for
both seasons. A completed season's tables are kept on disk next to the game
logs (src/gamelogs.CACHE_DIR), so a run makes about five requests. Each one is
optional: without the passing tables an assists prop keeps its box-score
projection and main holds it back with the "passing" guard (shown, never
published); a missing team table is neutral (factor 1).
"""

import json
import logging
import math
import os
import time
from datetime import datetime, timedelta

from src import gamelogs
from src.blend import variance_floor
from src.config import (
    AST_CONV_K, CONV_LAST_WEIGHT, NBA_TEAM_IDS, PA_RATIO_K, PASS_BLEND_K, SEASON, SEASON_TYPE, slate_today,
)
from src.underdog import norm_name, norm_name_base

logger = logging.getLogger(__name__)

# Share of each zone's made shots that are assisted, league-wide (approximate,
# recent seasons): where passes turn into makes. Weights for the zone mix.
ASSISTED_RATE = {
    "Restricted Area": 0.55,
    "In The Paint (Non-RA)": 0.35,
    "Mid-Range": 0.40,
    "Left Corner 3": 0.92,
    "Right Corner 3": 0.92,
    "Above the Break 3": 0.80,
}
THREES = {"Left Corner 3", "Right Corner 3", "Above the Break 3"}

# Attempts of league-average prior mixed into a team's FG% in a zone: how much
# of a team's deviation is real. FG% allowed on threes is mostly the
# shooters' luck (a full season of above-the-break threes keeps under half its
# deviation); at the rim it is mostly the defence. A team's own shooting is
# more of a skill than the shooting it allows.
DEF_K = {"two": 600.0, "three": 2500.0}
OWN_K = {"two": 300.0, "three": 1000.0}
SHARE_K = 1500.0                 # allowed makes of prior behind the assisted share
LAST_SEASON_WEIGHT = 0.5         # a team's last season counts half next to this season's games

MIN_SAVED_ROWS = {"passing": 200, "team": 25}   # a completed season's table smaller than this is not kept on disk
RATIO_FIT_MIN_PASSES = 500.0     # players in the potential-assists-per-pass fit (about 15 games of a role player)
RATIO_RANGE = (0.05, 0.45)       # the fitted prior's bounds
SHOOTING_RANGE = (0.90, 1.10)    # opponent shooting factor on conversion
SHARE_RANGE = (0.92, 1.08)       # opponent ball-movement factor
OWN_RANGE = (0.90, 1.10)         # the player's team's shooting factor on the conversion prior
MULTIPLIER_RANGE = (0.80, 1.25)  # the whole matchup (same bounds as src/matchup.py)

PASSING_MEASURE = "Passing"

_cache: dict = {}


def reset_cache() -> None:
    _cache.clear()


# ── fetching (league-wide, cached) ────────────────────────────────────────

def _date_to(date: str | None) -> str:
    """
    MM/DD/YYYY of the day before the slate, so the tables stop where the game
    logs do (before=date): a re-run after tonight's early games never counts
    them. '' (the season to date) for a future slate (the night-before run).
    """
    if not date or date > slate_today():
        return ""
    d = datetime.strptime(date, "%Y-%m-%d") - timedelta(days=1)
    return d.strftime("%m/%d/%Y")


def _disk_path(name: str) -> str:
    return os.path.join(gamelogs.CACHE_DIR, f"{name}.json")


def _cached(name: str, season: str, fetch, date_to: str = ""):
    """
    fetch() once per run per (name, season, date_to); a completed season's
    table (season < config.SEASON, no date filter) is also kept on disk and
    never requested again — but only a complete-looking one (_worth_keeping):
    an empty or truncated answer would otherwise stand in for the season for
    good. A failed fetch is cached for the run as None.
    """
    key = (name, season, date_to)
    if key in _cache:
        return _cache[key]
    completed = season < SEASON and not date_to
    path = _disk_path(f"{name}_{season}")
    if completed and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            _cache[key] = data
            return data
        except (OSError, ValueError):
            pass
    try:
        data = fetch()
    except Exception as e:
        logger.warning(f"  Passing: {name} {season} unavailable ({type(e).__name__}: {e})")
        data = None
    if data and completed and _worth_keeping(data):
        try:
            os.makedirs(gamelogs.CACHE_DIR, exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp, path)
        except OSError as e:
            logger.warning(f"  Passing: could not cache {name} {season} on disk ({e})")
    _cache[key] = data
    return data


def _worth_keeping(data) -> bool:
    """A completed season's table is saved only when it looks whole: 200+ players (passing) or 25+ teams."""
    if isinstance(data, dict) and "rows" in data:
        return len(data.get("rows") or {}) >= MIN_SAVED_ROWS["passing"]
    return isinstance(data, dict) and len(data) >= MIN_SAVED_ROWS["team"]


def _f(x) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 0.0
    return v if math.isfinite(v) else 0.0


def _fetch_passing(season: str, date_to: str = "", last_n: int = 0) -> dict:
    """
    {"rows": {player_id: {"name", "team", "gp", "min", "passes", "pot_ast", "ast"}}}
    as season totals (per-game values x GP) from LeagueDashPtStats, Passing.
    """
    from nba_api.stats.endpoints import leaguedashptstats
    time.sleep(0.6)
    df = leaguedashptstats.LeagueDashPtStats(
        player_or_team="Player",
        pt_measure_type=PASSING_MEASURE,
        per_mode_simple="PerGame",
        season=season,
        season_type_all_star=SEASON_TYPE,
        last_n_games=str(last_n),
        date_to_nullable=date_to,
    ).get_data_frames()[0]
    need = {"PLAYER_ID", "PLAYER_NAME", "GP", "MIN", "PASSES_MADE", "POTENTIAL_AST", "AST"}
    if df.empty:
        return {"rows": {}}
    if not need <= set(df.columns):
        raise ValueError(f"passing table without {sorted(need - set(df.columns))}")
    rows = {}
    for _, r in df.iterrows():
        try:
            pid = str(int(r["PLAYER_ID"]))
        except (TypeError, ValueError):
            continue
        gp = int(_f(r["GP"]))
        if gp <= 0:
            continue
        mpg = _f(r["MIN"])
        minutes = mpg if mpg > 60 else mpg * gp      # per game in PerGame mode; a total if the API ever sends one
        rows[pid] = {
            "name": str(r["PLAYER_NAME"]),
            "team": str(r.get("TEAM_ABBREVIATION") or "") or None,
            "gp": gp,
            "min": minutes,
            "passes": _f(r["PASSES_MADE"]) * gp,
            "pot_ast": _f(r["POTENTIAL_AST"]) * gp,
            "ast": _f(r["AST"]) * gp,
        }
    return {"rows": rows}


def passing_table(season: str, date_to: str = "", last_n: int = 0) -> dict | None:
    """The season's passing table ({"rows": ...}), cached; None when unavailable."""
    name = "passing" if not last_n else f"passing_l{last_n}"
    return _cached(name, season, lambda: _fetch_passing(season, date_to, last_n), date_to)


def _fetch_team_opponent(season: str, date_to: str = "") -> dict:
    """{team: {"fgm", "ast"}} allowed (season totals) from LeagueDashTeamStats, Opponent."""
    from nba_api.stats.endpoints import leaguedashteamstats
    time.sleep(0.6)
    df = leaguedashteamstats.LeagueDashTeamStats(
        season=season,
        season_type_all_star=SEASON_TYPE,
        measure_type_detailed_defense="Opponent",
        per_mode_detailed="Totals",
        date_to_nullable=date_to,
    ).get_data_frames()[0]
    out = {}
    if df.empty or not {"TEAM_ID", "OPP_FGM", "OPP_AST"} <= set(df.columns):
        return out
    id_to_abbrev = {v: k for k, v in NBA_TEAM_IDS.items()}
    for _, r in df.iterrows():
        try:
            ab = id_to_abbrev.get(int(r["TEAM_ID"]))
        except (TypeError, ValueError):
            continue
        if ab:
            out[ab] = {"fgm": _f(r["OPP_FGM"]), "ast": _f(r["OPP_AST"])}
    return out


def team_opponent(season: str, date_to: str = "") -> dict:
    return _cached("team_opp", season, lambda: _fetch_team_opponent(season, date_to), date_to) or {}


def zone_counts(season: str, measure: str, date_to: str = "") -> dict:
    """{team: {zone: [FGM, FGA]}} (src/shooting_zones.team_zone_counts), disk-cached for a completed season."""
    from src.shooting_zones import team_zone_counts

    def fetch():
        data = team_zone_counts(season, measure, date_to)
        if not data:
            raise ValueError("no rows")
        return data
    return _cached(f"zones_{measure.lower()}", season, fetch, date_to) or {}


# ── team tables -> shrunk factors ────────────────────────────────────────

def _combine(this: dict, last: dict) -> dict:
    """{team: {zone: [FGM, FGA]}} with last season's counts weighted LAST_SEASON_WEIGHT."""
    out: dict = {}
    for src, w in ((this or {}, 1.0), (last or {}, LAST_SEASON_WEIGHT)):
        for team, zones in src.items():
            dst = out.setdefault(team, {})
            for zone, (m, a) in (zones or {}).items():
                cur = dst.setdefault(zone, [0.0, 0.0])
                cur[0] += w * _f(m)
                cur[1] += w * _f(a)
    return out


def _league_fg(counts: dict) -> dict:
    """{zone: league FG%} over every team in `counts`."""
    tot: dict = {}
    for zones in counts.values():
        for zone, (m, a) in zones.items():
            t = tot.setdefault(zone, [0.0, 0.0])
            t[0] += m
            t[1] += a
    return {z: m / a for z, (m, a) in tot.items() if a > 0}


def shrunk_fg(counts: dict, k: dict) -> tuple[dict, dict]:
    """
    ({team: {zone: FG%}}, {zone: league FG%}) with each team's deviation from
    the league kept in proportion attempts / (attempts + K), K = k["three"]
    for three-point zones and k["two"] for the rest.
    """
    league = _league_fg(counts)
    out: dict = {}
    for team, zones in counts.items():
        row = {}
        for zone, (m, a) in zones.items():
            lg = league.get(zone)
            if lg is None:
                continue
            kz = k["three"] if zone in THREES else k["two"]
            dev = (m / a - lg) if a > 0 else 0.0
            row[zone] = lg + dev * (a / (a + kz)) if a > 0 else lg
        out[team] = row
    return out, league


def zone_mix(counts: dict, team: str) -> dict:
    """{zone: weight} for where a team's passes become shots: its FGA share in each zone x the zone's assisted rate."""
    zones = (counts or {}).get(team) or {}
    total = sum(_f(a) for z, (_m, a) in zones.items() if z in ASSISTED_RATE)
    if total <= 0:
        return {}
    return {z: (_f(a) / total) * ASSISTED_RATE[z] for z, (_m, a) in zones.items() if z in ASSISTED_RATE}


def _clip(x: float, bounds: tuple[float, float]) -> float:
    lo, hi = bounds
    return min(hi, max(lo, x))


def shooting_factor(fg: dict, league: dict, mix: dict, team: str, bounds) -> float:
    """sum(mix_z x fg[team][z]) / sum(mix_z x league_z), clipped; 1.0 when the team or the mix is unknown."""
    row = (fg or {}).get(team)
    if not row or not mix:
        return 1.0
    num = sum(w * row[z] for z, w in mix.items() if z in row and z in league)
    den = sum(w * league[z] for z, w in mix.items() if z in row and z in league)
    if den <= 0:
        return 1.0
    return _clip(num / den, bounds)


def assisted_share(this: dict, last: dict) -> tuple[dict, float | None]:
    """({team: shrunk OPP_AST / OPP_FGM}, league share), last season weighted LAST_SEASON_WEIGHT."""
    comb: dict = {}
    for src, w in ((this or {}, 1.0), (last or {}, LAST_SEASON_WEIGHT)):
        for team, row in src.items():
            c = comb.setdefault(team, [0.0, 0.0])
            c[0] += w * _f(row.get("ast"))
            c[1] += w * _f(row.get("fgm"))
    tot_a = sum(a for a, _m in comb.values())
    tot_m = sum(m for _a, m in comb.values())
    if tot_m <= 0:
        return {}, None
    lg = tot_a / tot_m
    out = {}
    for team, (a, m) in comb.items():
        out[team] = lg + ((a / m - lg) * (m / (m + SHARE_K)) if m > 0 else 0.0)
    return out, lg


# ── the context for a run ─────────────────────────────────────────────────

def _index(table: dict | None) -> dict:
    """{"full": {norm_name: [ids]}, "base": {norm_name_base: [ids]}} of a passing table."""
    full, base = {}, {}
    for pid, row in ((table or {}).get("rows") or {}).items():
        full.setdefault(norm_name(row["name"]), []).append(pid)
        base.setdefault(norm_name_base(row["name"]), []).append(pid)
    return {"full": full, "base": base}


def ratio_fit(table: dict | None) -> tuple[float, float] | None:
    """
    (a, b) of potential assists per pass ~ a + b x passes per minute, least
    squares weighted by passes over the table's players with at least
    RATIO_FIT_MIN_PASSES passes; None with fewer than 10 of them or no
    spread in passes per minute.
    """
    pts = [(r["passes"] / r["min"], r["pot_ast"] / r["passes"], r["passes"])
           for r in ((table or {}).get("rows") or {}).values()
           if r["passes"] >= RATIO_FIT_MIN_PASSES and r["min"] > 0]
    if len(pts) < 10:
        return None
    sw = sum(w for _x, _y, w in pts)
    mx = sum(w * x for x, _y, w in pts) / sw
    my = sum(w * y for _x, y, w in pts) / sw
    sxx = sum(w * (x - mx) ** 2 for x, _y, w in pts)
    if sxx <= 0:
        return None
    b = sum(w * (x - mx) * (y - my) for x, y, w in pts) / sxx
    return my - b * mx, b


def ratio_prior(ctx: dict, passes_pm: float | None) -> float:
    """The potential-assists-per-pass prior for a player passing passes_pm a minute (the league rate without a fit)."""
    fit = (ctx or {}).get("ratio_fit")
    if fit is None or passes_pm is None:
        return ctx["lg_ratio"]
    a, b = fit
    return _clip(a + b * passes_pm, RATIO_RANGE)


def context(date: str, season_now: str, season_prev: str) -> dict:
    """
    Everything the assists projection needs for a slate, fetched once:
    the passing tables (this season through the day before `date`, its last
    10 games, last season), league rates, and per-team shooting and
    ball-movement tables. "available" is False without any passing table;
    "this_loaded" says whether this season's table answered (with or without
    rows: before opening night it has none).
    """
    date_to = _date_to(date)
    this = passing_table(season_now, date_to)
    last = passing_table(season_prev)
    l10 = None
    if this and this.get("rows"):
        if max(r["gp"] for r in this["rows"].values()) > 10:
            l10 = passing_table(season_now, date_to, last_n=10)
        else:
            l10 = this       # nobody has more than 10 games: the last 10 ARE the season

    rows = [r for t in (this, last) for r in ((t or {}).get("rows") or {}).values()]
    passes = sum(r["passes"] for r in rows)
    pot = sum(r["pot_ast"] for r in rows)
    ast = sum(r["ast"] for r in rows)
    lg_ratio = pot / passes if passes > 0 else None
    lg_conv = ast / pot if pot > 0 else None

    allowed = _combine(zone_counts(season_now, "Opponent", date_to), zone_counts(season_prev, "Opponent"))
    own = _combine(zone_counts(season_now, "Base", date_to), zone_counts(season_prev, "Base"))
    def_fg, def_lg = shrunk_fg(allowed, DEF_K)
    own_fg, own_lg = shrunk_fg(own, OWN_K)
    share, lg_share = assisted_share(team_opponent(season_now, date_to), team_opponent(season_prev))

    available = bool(rows) and lg_ratio is not None and lg_conv is not None
    ctx = {
        "available": available,
        "this_loaded": this is not None,
        "this": this or {"rows": {}}, "last": last or {"rows": {}}, "l10": l10 or this or {"rows": {}},
        "idx": {"this": _index(this), "last": _index(last), "l10": _index(l10)},
        "lg_ratio": lg_ratio, "lg_conv": lg_conv,
        "ratio_fit": ratio_fit(last) or ratio_fit(this),
        "own_counts": own, "def_fg": def_fg, "def_lg": def_lg, "own_fg": own_fg, "own_lg": own_lg,
        "share": share, "lg_share": lg_share,
    }
    return ctx


def describe(ctx: dict) -> str:
    if not ctx or not ctx.get("available"):
        return "passing tables unavailable — assists keep the box-score projection and are held back (guard: passing)"
    n_this, n_last = len(ctx["this"]["rows"]), len(ctx["last"]["rows"])
    return (f"{n_this} players this season, {n_last} last season; league {ctx['lg_ratio']:.3f} potential assists per pass, "
            f"{ctx['lg_conv']:.1%} converted; opponent shooting for {len(ctx['def_fg'])} teams, ball movement for {len(ctx['share'])}")


# ── one player ────────────────────────────────────────────────────────────

def _find(ctx: dict, which: str, name: str) -> dict | None:
    """
    The player's row in one passing table: the live id main registered
    (authoritative for an exact name: a rookie never inherits a namesake's
    season), else a unique normalised / suffix-free name, else nba_api's id.
    """
    from src import hitrate
    rows = (ctx.get(which) or {}).get("rows") or {}
    exact = hitrate._live_ids.get(norm_name(name))
    if exact is not None:
        return rows.get(str(int(exact)))
    alias = hitrate._live_ids.get(norm_name_base(name))
    if alias is not None and str(int(alias)) in rows:
        return rows[str(int(alias))]
    idx = (ctx.get("idx") or {}).get(which) or {}
    for key, bucket in ((norm_name(name), "full"), (norm_name_base(name), "base")):
        ids = (idx.get(bucket) or {}).get(key) or []
        if len(ids) == 1:
            return rows[ids[0]]
    try:
        pid = hitrate._find_player_id(name)
    except Exception:
        pid = None
    return rows.get(str(int(pid))) if pid is not None else None


def assist_rate(ctx: dict, name: str, team: str) -> dict | None:
    """
    The player's assists per minute as passes/min x potential assists per
    pass x conversion, each link shrunk toward its prior (module docstring),
    with the pieces. None when neither season's passing table has him.
    """
    if not ctx or not ctx.get("available"):
        return None
    t, l = _find(ctx, "this", name), _find(ctx, "last", name)
    if t is None and l is None:
        return None
    r10 = _find(ctx, "l10", name) if t is not None else None
    if t is not None and r10 is None:
        r10 = t
    lg_conv = ctx["lg_conv"]

    # 1. passes per minute: this season (last 10 counted twice) against last season, n / (n + K)
    this_pm = (t["passes"] + r10["passes"]) / (t["min"] + r10["min"]) if t is not None and t["min"] > 0 else None
    last_pm = l["passes"] / l["min"] if l is not None and l["min"] > 0 else None
    gp = t["gp"] if t is not None else 0
    if this_pm is not None and last_pm is not None:
        w = gp / (gp + PASS_BLEND_K) if gp + PASS_BLEND_K > 0 else 1.0
        passes_pm = w * this_pm + (1.0 - w) * last_pm
    elif this_pm is not None:
        w, passes_pm = 1.0, this_pm
    elif last_pm is not None:
        w, passes_pm = 0.0, last_pm
    else:
        return None

    # 2. potential assists per pass: last season pulled toward players who pass as much, this season toward that
    if l is not None and l["passes"] > 0:
        base = ratio_prior(ctx, last_pm)
        prior = (l["pot_ast"] + PA_RATIO_K * base) / (l["passes"] + PA_RATIO_K)
    else:
        prior = ratio_prior(ctx, passes_pm)
    ratio = prior
    if t is not None and t["passes"] > 0:
        seen = (t["pot_ast"] + r10["pot_ast"]) / (t["passes"] + r10["passes"])   # form-weighted rate ...
        n = t["passes"]                                                          # ... on the passes actually seen
        ratio = (n * seen + PA_RATIO_K * prior) / (n + PA_RATIO_K)

    # 3. conversion: both seasons' assists / potential assists, no form weighting, toward the league x his team's shooting
    own = shooting_factor(ctx["own_fg"], ctx["own_lg"], zone_mix(ctx["own_counts"], team), team, OWN_RANGE)
    conv_prior = lg_conv * own
    s = (t["ast"] if t is not None else 0.0) + CONV_LAST_WEIGHT * (l["ast"] if l is not None else 0.0)
    a = (t["pot_ast"] if t is not None else 0.0) + CONV_LAST_WEIGHT * (l["pot_ast"] if l is not None else 0.0)
    conv = (s + AST_CONV_K * conv_prior) / (a + AST_CONV_K)
    conv_seen = (t["ast"] / t["pot_ast"]) if t is not None and t["pot_ast"] > 0 else None

    return {
        "per_min": passes_pm * ratio * conv,
        "passes_pm": passes_pm,
        "pa_per_pass": ratio,
        "conv": conv,
        "conv_seen": conv_seen,
        "conv_prior": conv_prior,
        "team_shooting": own,
        "weight_this": w,
        "gp_this": gp,
        "gp_last": l["gp"] if l is not None else 0,
    }


def opponent_factors(ctx: dict, team: str, opponent: str) -> dict:
    """
    {"shooting", "share"}: tonight's opponent's FG% allowed over the player's
    team's assisted-shot zones vs the league, and its assisted share of makes
    allowed vs the league, both shrunk and clipped. 1.0 when unknown.
    """
    if not ctx:
        return {"shooting": 1.0, "share": 1.0}
    mix = zone_mix(ctx.get("own_counts") or {}, team)
    shooting = shooting_factor(ctx.get("def_fg") or {}, ctx.get("def_lg") or {}, mix, opponent, SHOOTING_RANGE)
    share = 1.0
    lg = ctx.get("lg_share")
    s = (ctx.get("share") or {}).get(opponent)
    if lg and s is not None:
        share = _clip(s / lg, SHARE_RANGE)
    return {"shooting": shooting, "share": share}


def project_assists(ctx: dict, name: str, team: str, opponent: str, box: dict, adjustments: dict) -> dict | None:
    """
    The assists projection from passing, built on the box-score projection
    `box` (src/projection.project) for its minutes and spread.

    mu = box minutes x assist_rate() per minute x pace x OUT team-mates x
    opponent shooting x opponent ball movement (the matchup clipped to
    MULTIPLIER_RANGE; `adjustments` is src/matchup.matchup_multiplier's dict,
    whose pace and teammate parts are reused and whose DvP part is not).
    The spread keeps the box projection's variance-to-mean ratio at the new
    mean (floored like every projection). None when the player is in no
    passing table, the box projection has no minutes, or he has games this
    season (box n_this) but no row in this season's passing table (the
    request failed or lags: last season alone would stand in for his current
    role); the caller then keeps the box projection and the "passing" guard
    holds the prop back.

    Returns the box dict with mu, sd, sd_sample, sd_prior and multiplier
    replaced, plus "source": "passing", "mu_box" and "passing" (the pieces,
    per game at the projected minutes) and "adjustments" for the board.
    """
    if not box or not box.get("minutes") or box.get("mu") is None:
        return None
    rate = assist_rate(ctx, name, team)
    if rate is None:
        return None
    if int(box.get("n_this") or 0) > 0 and rate["gp_this"] == 0:
        # He has played this season but this season's passing table has no row for him (the request failed, or the
        # table lags the game logs): last season alone would stand in for a role that may have changed. Hold back.
        return None
    minutes = float(box["minutes"])
    pace = _f((adjustments or {}).get("pace")) or 1.0
    mate = _f((adjustments or {}).get("teammate")) or 1.0
    opp = opponent_factors(ctx, team, opponent)
    mult = _clip(pace * mate * opp["shooting"] * opp["share"], MULTIPLIER_RANGE)
    base = rate["per_min"] * minutes
    mu = base * mult

    mu_box = float(box["mu"])
    ratio = mu / mu_box if mu_box > 0 else 1.0
    var = float(box.get("sd") or 0.0) ** 2 * ratio if mu_box > 0 else 0.0
    var = max(var, variance_floor(mu))
    scale = math.sqrt(ratio) if ratio > 0 else 1.0
    passes_pg = rate["passes_pm"] * minutes
    pot_pg = passes_pg * rate["pa_per_pass"]
    return {
        **box,
        "mu": float(mu),
        "sd": float(math.sqrt(var)),
        "sd_sample": float(box["sd_sample"]) * scale if box.get("sd_sample") is not None else None,
        "sd_prior": float(box["sd_prior"]) * scale if box.get("sd_prior") is not None else None,
        "multiplier": float(mult),
        "source": "passing",
        "mu_box": mu_box,
        "passing": {
            "passes": passes_pg,
            "pot_ast": pot_pg,
            "pa_per_pass": rate["pa_per_pass"],
            "conv": rate["conv"],
            "conv_seen": rate["conv_seen"],
            "conv_prior": rate["conv_prior"],
            "team_shooting": rate["team_shooting"],
            "opp_shooting": opp["shooting"],
            "opp_share": opp["share"],
            "base": base,
            "weight_this": rate["weight_this"],
            "gp_this": rate["gp_this"],
            "gp_last": rate["gp_last"],
        },
        "adjustments": {**(adjustments or {}), "multiplier": float(mult), "dvp": 1.0,
                        "shooting": opp["shooting"], "share": opp["share"]},
    }
