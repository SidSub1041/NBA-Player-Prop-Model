#!/usr/bin/env python3
"""
probe_vegas.py — is the ODDS_API_KEY working, and do the books hang every
market the model uses?

Lists today's NBA events (free), then prices ONE upcoming event for the seven
markets the model needs (7 credits): points, rebounds, assists and the four
combos. Prints how many players each market covers, a few consensus lines, and
the credits left on the plan. Run it locally with ODDS_API_KEY set, or from
GitHub Actions: Run workflow → task: probe.
"""

import os
import sys
from collections import Counter
from datetime import datetime

from src.vegas_odds import EASTERN, MARKETS, _get, _remaining, _started, consensus, fetch_events, parse_event_odds

MODEL_STATS = ["points", "rebounds", "assists", "pra", "pts+reb", "pts+ast", "reb+ast"]


def main() -> int:
    key = os.environ.get("ODDS_API_KEY", "").strip()
    if not key:
        print("RESULT: SKIP — no ODDS_API_KEY set (the model falls back to Underdog lines)")
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
    try:
        data, headers = _get(f"/events/{ev['id']}/odds", {
            "apiKey": key, "regions": os.environ.get("ODDS_API_REGIONS", "us"),
            "markets": ",".join(markets), "oddsFormat": "american", "dateFormat": "iso",
        })
    except Exception as e:
        print(f"RESULT: FAIL — event odds request failed: {type(e).__name__}: {e}")
        return 1
    found = parse_event_odds(data)
    books = sorted({b.get('title') for b in (data or {}).get('bookmakers', []) or []})
    per_stat = Counter(stat for (_, stat) in found)
    print(f"\n{ev.get('away_team')} @ {ev.get('home_team')}: {len(books)} books {books[:8]}")
    print("players per market: " + ", ".join(f"{s} {per_stat.get(s, 0)}" for s in MODEL_STATS))
    missing = [s for s in MODEL_STATS if not per_stat.get(s)]
    for (player, stat), entry in list(found.items())[:8]:
        c = consensus(entry["offers"])
        print(f"  {entry['player']:26s} {stat:8s} {c['line']:>5}  O {c['over_odds']} @ {c['over_book']} / "
              f"U {c['under_odds']} @ {c['under_book']}  ({c['books']} books)")
    print(f"\nCredits left on the plan: {_remaining(headers)} (used: {headers.get('x-requests-used')})")
    if not found:
        print("\nRESULT: FAIL — key works but no player-prop offers parsed")
        return 1
    if missing:
        print(f"\nRESULT: PASS — books are hanging player props (none yet for: {', '.join(missing)}; "
              "those fall back to Underdog or go On Hold)")
    else:
        print("\nRESULT: PASS — books hang every market the model uses")
    return 0


if __name__ == "__main__":
    sys.exit(main())
