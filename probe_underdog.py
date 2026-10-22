#!/usr/bin/env python3
"""
probe_underdog.py — what does Underdog's pick'em feed return from here?

Prints, for each known endpoint: HTTP status, top-level keys, how many NBA
over/under lines parsed, and a few examples. Run it on the machine that runs
the model (or from GitHub Actions: Run workflow → task: probe) when boards
come out without lines.
"""

import json
import os
import sys
from collections import Counter
from datetime import datetime

import requests

from src.underdog import EASTERN, ENDPOINTS, HEADERS, STAT_KEYS, TIMEOUT, _game_date_et, _norm_stat, _usable, parse_underdog_lines


def _print_stat_titles(data: dict) -> None:
    """Every stat title on the NBA board, and which ones the model can't map."""
    nba_games = {str(g.get("id")) for g in data.get("games") or [] if isinstance(g, dict) and g.get("sport_id") == "NBA"}
    nba_apps = {a.get("id") for a in data.get("appearances") or [] if isinstance(a, dict) and str(a.get("match_id")) in nba_games}
    titles = Counter()
    for line in data.get("over_under_lines") or []:
        if not isinstance(line, dict):
            continue
        st = ((line.get("over_under") or {}).get("appearance_stat") or {})
        if st.get("appearance_id") in nba_apps:
            titles[st.get("display_stat") or st.get("stat") or "?"] += 1
    known = set(STAT_KEYS.values())
    mapped = {t: n for t, n in titles.items() if _norm_stat(t) in known}
    other = {t: n for t, n in titles.items() if _norm_stat(t) not in known}
    print(f"  NBA stat titles used by the model: {dict(sorted(mapped.items(), key=lambda kv: -kv[1]))}")
    print(f"  NBA stat titles not used: {dict(sorted(other.items(), key=lambda kv: -kv[1])[:25])}")


def probe(proxies: dict | None, date: str, stop_on_usable: bool = False) -> tuple[bool, bool]:
    """Print what each endpoint returns; (any NBA lines for `date`, any usable board)."""
    any_lines = any_usable = False
    for url in ENDPOINTS:
        print(f"\n{url}")
        try:
            resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT, proxies=proxies)
        except Exception as e:
            print(f"  request failed: {type(e).__name__}: {e}")
            continue
        print(f"  HTTP {resp.status_code}, {len(resp.content)} bytes")
        try:
            data = resp.json()
        except ValueError:
            print(f"  not JSON: {resp.text[:160]!r}")
            continue
        if not isinstance(data, dict):
            print(f"  unexpected JSON type: {type(data).__name__}")
            continue
        usable = _usable(resp.status_code, data)
        any_usable = any_usable or usable
        print(f"  keys: {sorted(data.keys())[:12]}")
        games = [g for g in data.get("games") or [] if isinstance(g, dict)]
        sports = sorted({str(g.get('sport_id')) for g in games})
        nba_dates = Counter(_game_date_et(g) for g in games if g.get("sport_id") == "NBA")
        print(f"  games: {len(games)} (sports: {sports[:10]}) | over_under_lines: {len(data.get('over_under_lines') or [])}")
        print(f"  NBA games by date (ET): {dict(sorted(nba_dates.items(), key=lambda kv: str(kv[0])))}")
        lines = parse_underdog_lines(data, date)
        distinct = {(v["player"], k[1]): v for k, v in lines.items()}
        print(f"  NBA lines for {date}'s slate (model stats only): {len(distinct)}")
        _print_stat_titles(data)
        per_stat = Counter(stat for (_, stat) in distinct)
        print(f"  by model stat: {dict(per_stat)}")
        for (player, stat), v in list(distinct.items())[:5]:
            print(f"    {player:28s} {stat:16s} {v['line']:>6}  {v['over_odds']}/{v['under_odds']}")
        if not distinct and isinstance(data.get("over_under_lines"), list) and data["over_under_lines"]:
            print("  sample raw line:", json.dumps(data["over_under_lines"][0])[:400])
        elif not distinct:
            print("  head of payload:", json.dumps(data)[:600])
        any_lines = any_lines or bool(distinct)
        if distinct or (stop_on_usable and usable):
            break
    return any_lines, any_usable


def main() -> int:
    date = datetime.now(EASTERN).date().isoformat()
    proxy = os.environ.get("NBA_PROXY") or None
    print(f"Probing Underdog directly for the {date} slate ...")
    ok, usable = probe(None, date)
    if not usable and proxy:
        print(f"\nUnderdog unreachable directly — probing via proxy {proxy.split('@')[-1]} ...")
        ok, usable = probe({"http": proxy, "https": proxy}, date, stop_on_usable=True)
    if ok:
        verdict = "PASS — Underdog NBA lines available"
    elif usable:
        verdict = "FAIL — Underdog answered, but no NBA lines for today's slate (none posted yet, or the feed changed)"
    else:
        verdict = "FAIL — no usable answer from any Underdog endpoint"
    print("\nRESULT:", verdict)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
