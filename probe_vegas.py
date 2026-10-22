#!/usr/bin/env python3
"""
probe_vegas.py — is the ODDS_API_KEY working, and what do the books return?

Lists today's NBA events (free), then prices ONE event's player_points market
(1 credit) and prints a few offers plus the credits left on the plan. Run it
locally with ODDS_API_KEY set, or from GitHub Actions: Run workflow → task: probe.
"""

import os
import sys
from datetime import datetime

from src.vegas_odds import EASTERN, _get, _remaining, fetch_events, parse_event_odds, summarize


def main() -> int:
    key = os.environ.get("ODDS_API_KEY", "").strip()
    if not key:
        print("RESULT: SKIP — no ODDS_API_KEY set (Vegas odds are optional)")
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
    if not events:
        print("RESULT: PASS — key works; no NBA events today")
        return 0
    ev = events[0]
    try:
        data, headers = _get(f"/events/{ev['id']}/odds", {
            "apiKey": key, "regions": os.environ.get("ODDS_API_REGIONS", "us"),
            "markets": "player_points", "oddsFormat": "american", "dateFormat": "iso",
        })
    except Exception as e:
        print(f"RESULT: FAIL — event odds request failed: {type(e).__name__}: {e}")
        return 1
    offers = parse_event_odds(data)
    books = sorted({b.get('title') for b in (data or {}).get('bookmakers', []) or []})
    print(f"\n{ev.get('away_team')} @ {ev.get('home_team')}: {len(books)} books {books[:8]}, {len(offers)} player point props")
    for (player, stat), by_book in list(offers.items())[:6]:
        s = summarize(by_book, None, "over")
        print(f"  {player:26s} {stat:8s} {s['line']:>5}  O {s['over_odds']} / U {s['under_odds']}  best over @ {s['book']} ({s['books']} books)")
    print(f"\nCredits left on the plan: {_remaining(headers)} (used: {headers.get('x-requests-used')})")
    ok = bool(offers)
    print("\nRESULT:", "PASS — Vegas player props available" if ok else "FAIL — key works but no player-prop offers parsed")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
