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

import requests

from src.underdog import ENDPOINTS, HEADERS, parse_underdog_lines


def probe(proxies: dict | None) -> bool:
    any_lines = False
    for url in ENDPOINTS:
        print(f"\n{url}")
        try:
            resp = requests.get(url, headers=HEADERS, timeout=20, proxies=proxies)
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
        print(f"  keys: {sorted(data.keys())[:12]}")
        sports = sorted({str(g.get('sport_id')) for g in data.get('games', []) if isinstance(g, dict)})
        print(f"  games: {len(data.get('games', []))} (sports: {sports[:10]}) | over_under_lines: {len(data.get('over_under_lines', []))}")
        lines = parse_underdog_lines(data)
        distinct = {(v["player"], k[1]): v for k, v in lines.items()}
        print(f"  parsed NBA lines for the model's stats: {len(distinct)}")
        for (player, stat), v in list(distinct.items())[:5]:
            print(f"    {player:28s} {stat:10s} {v['line']:>6}  {v['over_odds']}/{v['under_odds']}")
        if not distinct and data.get("over_under_lines"):
            sample = data["over_under_lines"][0]
            print("  sample raw line:", json.dumps(sample)[:400])
        elif not distinct:
            print("  unknown shape, head of payload:", json.dumps(data)[:600])
        any_lines = any_lines or bool(distinct)
    return any_lines


def main() -> int:
    proxy = os.environ.get("NBA_PROXY") or None
    print("Probing Underdog directly ...")
    ok = probe(None)
    if not ok and proxy:
        print(f"\nNo lines directly — probing via proxy {proxy.split('@')[-1]} ...")
        ok = probe({"http": proxy, "https": proxy})
    print("\nRESULT:", "PASS — Underdog NBA lines available" if ok else "FAIL — no NBA lines parsed from any endpoint")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
