"""
Which season's statistics the model should use.

Early in a season the current season has no (or too little) data: on opening
night every team-defense ranking, shooting-zone split and player game log is
empty, so the model falls back to the previous season's statistics until every
team has played EARLY_SEASON_MIN_GAMES games (default 5, about two weeks).

The choice is made once per run from a single LeagueDashTeamStats probe and
cached until reset() (main.run() resets it, so long-lived schedulers re-probe daily). Override with NBA_STATS_SEASON=2025-26 to force a season.
"""

import logging
import os
import time

import src.nba_http  # noqa: F401  — the probe gets the same timeouts/retries/breaker
from src.config import SEASON, SEASON_TYPE, previous_season

logger = logging.getLogger(__name__)

EARLY_SEASON_MIN_GAMES = int(os.environ.get("NBA_MIN_GAMES", "5"))

_resolved: str | None = None
_reason: str = ""


def _games_played_min(season: str) -> int:
    """Fewest games any team has played this season (0 if no data yet)."""
    from nba_api.stats.endpoints import leaguedashteamstats

    time.sleep(0.6)
    df = leaguedashteamstats.LeagueDashTeamStats(
        season=season,
        season_type_all_star=SEASON_TYPE,
        per_mode_detailed="PerGame",
    ).get_data_frames()[0]
    if df.empty or "GP" not in df.columns or len(df) < len_all_teams():
        return 0  # no data yet, or teams still missing from the table
    return int(df["GP"].fillna(0).min())


def len_all_teams() -> int:
    from src.config import NBA_TEAM_IDS
    return len(NBA_TEAM_IDS)


def reset():
    """Forget the cached choice (called at the start of every model run)."""
    global _resolved, _reason
    _resolved, _reason = None, ""


def stats_season() -> str:
    """The season label to request statistics for (cached per run)."""
    global _resolved, _reason
    if _resolved:
        return _resolved

    override = os.environ.get("NBA_STATS_SEASON")
    if override:
        _resolved, _reason = override, "NBA_STATS_SEASON override"
        logger.info(f"Stats season: {_resolved} ({_reason})")
        return _resolved

    try:
        min_gp = _games_played_min(SEASON)
    except Exception as e:
        # A blocked/unreachable API is handled by the callers (and the circuit
        # breaker); don't mask it by silently switching seasons.
        logger.warning(f"Could not probe {SEASON} game counts ({type(e).__name__}); using {SEASON}")
        _resolved, _reason = SEASON, "probe failed"
        return _resolved

    if min_gp >= EARLY_SEASON_MIN_GAMES:
        _resolved, _reason = SEASON, f"every team has played {min_gp}+ games"
    else:
        _resolved = previous_season(SEASON)
        _reason = (
            f"early season: teams have played as few as {min_gp} games, "
            f"using {_resolved} stats until everyone reaches {EARLY_SEASON_MIN_GAMES}"
        )
    logger.info(f"Stats season: {_resolved} ({_reason})")
    return _resolved


def stats_season_reason() -> str:
    return _reason
