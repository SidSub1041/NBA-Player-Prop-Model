"""
Team pace (possessions per 48 minutes) for the edge model.

A player's counting stats scale with the number of possessions his team
gets, and tonight's possessions are set by BOTH teams. `pace_factor` is the
two teams' average pace over the league average, applied to the projection
through `matchup.matchup_multiplier` (PACE_WEIGHT). Early in a season the
current season's pace is a handful of games, so `blended_pace` weights it by
games played against last season (same n/(n+K) rule as the stat blend).

One LeagueDashTeamStats (Advanced) call per season per run; `reset_cache()`
at the start of a run (main does this for every per-run cache).
"""

import logging
import time
from statistics import fmean

import pandas as pd
from nba_api.stats.endpoints import leaguedashteamstats

from src.config import BLEND_K, NBA_TEAM_IDS, SEASON_TYPE

logger = logging.getLogger(__name__)

_cache: dict[str, dict[str, dict]] = {}   # season -> {abbrev: {"pace": float, "gp": int}}


def reset_cache() -> None:
    _cache.clear()


def _team_pace_rows(season: str) -> dict[str, dict]:
    """{abbrev: {"pace", "gp"}} for a season, fetched once per run.

    A failed or empty fetch is cached as {} so a blocked endpoint costs one
    attempt per run, not one per caller.
    """
    if season in _cache:
        return _cache[season]
    rows: dict[str, dict] = {}
    try:
        time.sleep(0.6)
        df = leaguedashteamstats.LeagueDashTeamStats(
            season=season,
            season_type_all_star=SEASON_TYPE,
            measure_type_detailed_defense="Advanced",
            per_mode_detailed="PerGame",
        ).get_data_frames()[0]
        if df.empty or "PACE" not in df.columns or "TEAM_ID" not in df.columns:
            logger.warning(f"Pace: no PACE column / no rows for {season} (early season?)")
        else:
            id_to_abbrev = {v: k for k, v in NBA_TEAM_IDS.items()}
            for _, row in df.iterrows():
                try:
                    abbrev = id_to_abbrev.get(int(row["TEAM_ID"]))
                    pace = float(row["PACE"])
                    gp = row["GP"] if "GP" in df.columns else 0
                    gp = int(gp) if pd.notna(gp) else 0
                except (TypeError, ValueError):
                    continue
                if abbrev and pace == pace and pace > 0:
                    rows[abbrev] = {"pace": pace, "gp": gp}
            logger.info(f"Pace: {len(rows)} teams for {season}")
    except Exception as e:
        logger.warning(f"Pace unavailable for {season}: {type(e).__name__}: {e}")
    _cache[season] = rows
    return rows


def team_pace(season: str) -> dict[str, float]:
    """{abbrev: PACE} for a season (per game, Advanced measure); {} when unavailable."""
    return {team: row["pace"] for team, row in _team_pace_rows(season).items()}


def blended_pace(this_season: str, last_season: str, k: float = BLEND_K) -> dict[str, float]:
    """Per team: w·this + (1−w)·last with w = GP_this / (GP_this + k).

    A team missing from one season takes the other season's pace outright.
    """
    this, last = _team_pace_rows(this_season), _team_pace_rows(last_season)
    out: dict[str, float] = {}
    for team in sorted(set(this) | set(last)):
        t, l = this.get(team), last.get(team)
        if t and l:
            gp = max(t["gp"], 0)
            w = gp / (gp + k) if (gp + k) > 0 else 0.0
            out[team] = w * t["pace"] + (1.0 - w) * l["pace"]
        elif t:
            out[team] = t["pace"]
        elif l:
            out[team] = l["pace"]
    return out


def pace_factor(pace: dict[str, float], team: str, opponent: str) -> float:
    """((pace[team] + pace[opponent]) / 2) / league mean; 1.0 when either team is unknown."""
    if not pace or team not in pace or opponent not in pace:
        return 1.0
    try:
        mean = fmean(float(v) for v in pace.values())
    except (TypeError, ValueError):
        return 1.0
    if mean <= 0:
        return 1.0
    return ((float(pace[team]) + float(pace[opponent])) / 2.0) / mean
