#!/usr/bin/env python3
"""
probe_vegas.py — is the ODDS_API_KEY working, do the sportsbooks hang every
market the model uses, and do the DFS apps (the backup) answer too?

Lists today's NBA events (free), then prices ONE upcoming event for the seven
markets the model needs: once for the sportsbook regions (ODDS_API_REGIONS,
default "us": 7 credits) and once for the DFS-app regions (ODDS_API_DFS_REGIONS,
default "us_dfs": 7 more). Prints the books and apps that answered, how many
players each market covers per tier, a few consensus lines, and the credits
left on the plan. Run it locally with ODDS_API_KEY set, or from GitHub
Actions: Run workflow → task: probe.
"""

import os
import sys
from collections import Counter
from datetime import datetime

from src.vegas_odds import (EASTERN, MARKETS, _get, _regions, _remaining, _started, consensus, fetch_events,
                            parse_event_odds)

MODEL_STATS = ["points", "rebounds", "assists", "pra", "pts+reb", "pts+ast", "reb+ast"]


def _price(ev: dict, key: str, regions: str, markets: list[str]):
    data, headers = _get(f"/events/{ev['id']}/odds", {
        "apiKey": key, "regions": regions, "markets": ",".join(markets),
        "oddsFormat": "american", "dateFormat": "iso",
    })
    found = parse_event_odds(data)
    books = sorted({b.get("title") or b.get("key") for b in (data or {}).get("bookmakers", []) or []})
    return found, books, headers


def _show(label: str, found: dict, books: list) -> list[str]:
    per_stat = Counter(stat for (_, stat) in found)
    print(f"\n{label}: {len(books)} answered {books[:8]}")
    print("players per market: " + ", ".join(f"{s} {per_stat.get(s, 0)}" for s in MODEL_STATS))
    for (player, stat), entry in list(found.items())[:6]:
        c = consensus(entry["offers"])
        print(f"  {entry['player']:26s} {stat:8s} {c['line']:>5}  O {c['over_odds']} @ {c['over_book']} / "
              f"U {c['under_odds']} @ {c['under_book']}  ({c['books']} at that line)")
    return [s for s in MODEL_STATS if not per_stat.get(s)]


def main() -> int:
    key = os.environ.get("ODDS_API_KEY", "").strip()
    if not key:
        print("RESULT: SKIP — no ODDS_API_KEY set (the model falls back to Underdog's own feed)")
        return 0
    date = datetime.now(EASTERN).date().isoformat()
    try:
        events, remaining = fetch_events(date, key)
    except Exception as e:
        print(f"RESULT: FAIL — events request failed: {type(e).__name__}: {e}")
        return 1
    print(f"NBA events on {date}: {len(events)} (credits left on the plan: {remaining})")
    for e in events[:12]:
        print(f"  {e.get('commence_time')}  {e.get('away_team')} @ {e.get('home_team')}")
    upcoming = [e for e in events if not _started(e)]
    if not upcoming:
        print("RESULT: PASS — key works; no upcoming NBA games today to price")
        return 0
    ev = upcoming[0]
    markets = [MARKETS[s] for s in MODEL_STATS]
    book_regions = _regions("ODDS_API_REGIONS", "us") or "us"
    dfs_regions = _regions("ODDS_API_DFS_REGIONS", "us_dfs")
    print(f"\nPricing {ev.get('away_team')} @ {ev.get('home_team')} — sportsbooks [{book_regions}]"
          + (f", then DFS apps [{dfs_regions}]" if dfs_regions else ", DFS backup off"))
    try:
        found, books, headers = _price(ev, key, book_regions, markets)
    except Exception as e:
        print(f"RESULT: FAIL — event odds request failed: {type(e).__name__}: {e}")
        return 1
    missing = _show("Sportsbooks", found, books)
    dfs_found, dfs_books, dfs_missing = {}, [], MODEL_STATS
    if dfs_regions:
        try:
            dfs_found, dfs_books, headers = _price(ev, key, dfs_regions, markets)
            dfs_missing = _show("DFS apps (backup)", dfs_found, dfs_books)
        except Exception as e:
            print(f"\nDFS apps: request failed ({type(e).__name__}: {e}) — the model will still use the sportsbooks")
    print(f"\nCredits left on the plan: {_remaining(headers)} (used: {headers.get('x-requests-used')})")
    if not found and not dfs_found:
        print("\nRESULT: FAIL — key works but no player-prop offers parsed from any book or DFS app")
        return 1
    gaps = [s for s in missing if s in dfs_missing]
    if gaps:
        print(f"\nRESULT: PASS — props are up (no book or DFS app has these yet: {', '.join(gaps)}; "
              "those go On Hold until a later run finds them)")
    elif missing:
        print(f"\nRESULT: PASS — books hang most markets; the DFS apps cover the rest ({', '.join(missing)})")
    else:
        print("\nRESULT: PASS — books hang every market the model uses")
    return 0


if __name__ == "__main__":
    sys.exit(main())
