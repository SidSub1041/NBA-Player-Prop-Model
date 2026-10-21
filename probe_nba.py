#!/usr/bin/env python3
"""
probe_nba.py — can this machine (or this proxy) reach stats.nba.com?

stats.nba.com silently blocks most cloud/datacenter IP ranges: requests hang
until they time out. Run this on any host you are considering as the model's
server, or from GitHub Actions ("Run workflow" → task: probe) to test a proxy
set in the NBA_PROXY secret.

    python probe_nba.py                      # direct
    NBA_PROXY=http://user:pass@host:port python probe_nba.py

Exit code 0 = PASS (both endpoints answered with JSON), 1 = FAIL.
"""

import os
import sys
import time
from datetime import date

import src.nba_http  # noqa: F401  — same timeouts/retries/proxy as the model
from nba_api.stats.endpoints import leaguedashteamstats, scoreboardv3

from src.config import SEASON, SEASON_TYPE, previous_season

CHECKS = [
    ("ScoreboardV3 (today's games)", lambda: scoreboardv3.ScoreboardV3(game_date=date.today().isoformat()).get_dict()),
    (
        f"LeagueDashTeamStats ({previous_season(SEASON)})",
        lambda: leaguedashteamstats.LeagueDashTeamStats(
            season=previous_season(SEASON),
            season_type_all_star=SEASON_TYPE,
            per_mode_detailed="PerGame",
        ).get_data_frames()[0],
    ),
]


def main() -> int:
    proxy = os.environ.get("NBA_PROXY")
    print(f"Probing stats.nba.com {'via proxy ' + proxy.split('@')[-1] if proxy else 'directly'} ...")
    ok = True
    for name, fn in CHECKS:
        t0 = time.time()
        try:
            result = fn()
            rows = len(result) if hasattr(result, "__len__") else "?"
            print(f"  PASS  {name}: answered in {time.time() - t0:.1f}s ({rows} rows)")
        except Exception as e:
            ok = False
            print(f"  FAIL  {name}: {type(e).__name__} after {time.time() - t0:.1f}s — {str(e)[:120]}")
    print("\nRESULT:", "PASS — this host/proxy can run the model" if ok else
          "FAIL — stats.nba.com is unreachable from here; use a different host or a residential proxy")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
