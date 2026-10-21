"""
Fetch player prop lines from Underdog Fantasy.

Underdog's public pick'em feed has moved between endpoints over time, so the
fetcher tries each known one with browser-style headers and keeps the first
payload that contains NBA over/under lines. Lines are keyed by a normalized
(player, stat) pair so name variants ("Jaren Jackson Jr.", accents) and stat
title variants ("Pts + Rebs + Asts" vs "Points + Rebounds + Assists") match.
"""

import json
import logging
import os
import re
import unicodedata

import requests

logger = logging.getLogger(__name__)

ENDPOINTS = [
    "https://api.underdogfantasy.com/beta/v6/over_under_lines",
    "https://api.underdogfantasy.com/beta/v5/over_under_lines",
    "https://api.underdogfantasy.com/v2/pickem_search/search_results?sport_id=NBA",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://underdogfantasy.com",
    "Referer": "https://underdogfantasy.com/",
    "client-type": "web",
}

# Internal stat key -> canonical form of Underdog's stat title (see _norm_stat)
STAT_KEYS = {
    "points": "points",
    "rebounds": "rebounds",
    "assists": "assists",
    "pts+ast": "points+assists",
    "pts+reb": "points+rebounds",
    "reb+ast": "rebounds+assists",
    "pra": "points+rebounds+assists",
    "3-pointers made": "3pointersmade",
    "steals": "steals",
    "blocks": "blocks",
    "turnovers": "turnovers",
}

_STAT_WORDS = {
    "pts": "points", "pt": "points", "point": "points",
    "rebs": "rebounds", "reb": "rebounds", "rebound": "rebounds",
    "asts": "assists", "ast": "assists", "assist": "assists",
    "3pt": "3pointers", "threes": "3pointers", "3pointer": "3pointers",
}

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def norm_name(name: str) -> str:
    """
    'Jaren Jackson Jr.' -> 'jaren jackson jr'; 'P.J. Washington' -> 'pj washington';
    "De'Aaron Fox" -> 'deaaron fox'. Accents stripped, hyphens become spaces.
    """
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[.'\u2019`]", "", s)          # initials and apostrophes join up
    s = re.sub(r"[^a-z0-9 ]+", " ", s)         # everything else separates
    return " ".join(s.split())


def norm_name_base(name: str) -> str:
    """Normalized name without a generational suffix ('... jr' -> '...')."""
    parts = norm_name(name).split()
    while len(parts) > 2 and parts[-1] in _SUFFIXES:
        parts.pop()
    return " ".join(parts)


# Underdog's raw stat ids (used when a line carries no display title)
_RAW_STAT_IDS = {
    "points": "points", "rebounds": "rebounds", "assists": "assists",
    "pts_rebs_asts": "points+rebounds+assists", "pts_rebs": "points+rebounds",
    "pts_asts": "points+assists", "rebs_asts": "rebounds+assists",
    "three_points_made": "3pointersmade", "steals": "steals", "blocks": "blocks",
    "turnovers": "turnovers",
}


def _norm_stat(title: str) -> str:
    """'Pts + Rebs + Asts' / 'Points + Rebounds + Assists' -> 'points+rebounds+assists'."""
    raw = str(title).strip().lower()
    if raw in _RAW_STAT_IDS:
        return _RAW_STAT_IDS[raw]
    raw = raw.replace("&", "+").replace(" and ", "+")
    parts = [p for p in re.split(r"[+]", raw) if p.strip()]
    words = []
    for p in parts:
        w = re.sub(r"[^a-z0-9]", "", p)
        words.append(_STAT_WORDS.get(w, w))
    return "+".join(words)


def _proxies() -> dict | None:
    """Same residential proxy the NBA API uses (NBA_PROXY), if one is configured."""
    proxy = os.environ.get("NBA_PROXY") or None
    return {"http": proxy, "https": proxy} if proxy else None


def _fetch_payload_once(proxies: dict | None) -> tuple[dict, int]:
    """Try each endpoint (directly or via proxies); return (best payload, NBA line count)."""
    best: dict = {}
    best_count = -1
    via = "via proxy" if proxies else "direct"
    for url in ENDPOINTS:
        short = url.split(".com/")[-1]
        try:
            resp = requests.get(url, headers=HEADERS, timeout=20, proxies=proxies)
            status = resp.status_code
            data = resp.json() if resp.ok else {}
        except Exception as e:
            logger.info(f"Underdog {short} ({via}): {type(e).__name__}")
            continue
        if not isinstance(data, dict):
            data = {}
        raw = data.get("over_under_lines")
        raw_count = len(raw) if isinstance(raw, list) else 0
        try:
            nba_count = len({v["player"] + k[1] for k, v in parse_underdog_lines(data).items()})
        except Exception as e:  # unknown schema — keep looking
            logger.info(f"Underdog {short} ({via}): unparseable ({type(e).__name__})")
            nba_count = 0
        logger.info(
            f"Underdog {short} ({via}): HTTP {status}, {raw_count} lines, {nba_count} NBA lines for the model"
        )
        if nba_count > best_count:
            best, best_count = data, nba_count
        if nba_count > 0:
            break
    return best, max(best_count, 0)


def fetch_underdog_payload() -> dict:
    """
    Raw Underdog payload (players/appearances/games/over_under_lines) or {}.

    Tries directly first; if that yields no NBA lines and NBA_PROXY is set
    (hosted runners are sometimes blocked at the edge), retries through it.
    """
    best, count = _fetch_payload_once(None)
    if count == 0 and _proxies():
        logger.info("Underdog: no NBA lines directly — retrying through NBA_PROXY")
        proxied, pcount = _fetch_payload_once(_proxies())
        if pcount > 0 or not best:
            best = proxied
    return best


def parse_underdog_lines(data: dict) -> dict:
    """Payload -> {(norm_name, internal_stat): {line, over_odds, under_odds, ...}}."""
    if not data:
        return {}
    if not isinstance(data, dict):
        return {}
    players = {p["id"]: p for p in data.get("players", []) or [] if isinstance(p, dict) and "id" in p}
    appearances = {a["id"]: a for a in data.get("appearances", []) or [] if isinstance(a, dict) and "id" in a}
    games = [g for g in data.get("games", []) or [] if isinstance(g, dict)]

    nba_game_ids = {g.get("id") for g in games if g.get("sport_id") == "NBA"}
    if games and not nba_game_ids:
        return {}  # the feed has games but none are NBA: nothing for us tonight
    nba_appearance_ids = {
        a_id for a_id, a in appearances.items()
        if a.get("match_id") in nba_game_ids or a.get("sport_id") == "NBA"
    }
    stat_by_canonical = {v: k for k, v in STAT_KEYS.items()}

    lines: dict = {}
    for line in data.get("over_under_lines", []) or []:
        if not isinstance(line, dict):
            continue
        ou = line.get("over_under", {}) or {}
        app_stat = ou.get("appearance_stat", {}) or {}
        app_id = app_stat.get("appearance_id")
        if nba_appearance_ids and app_id not in nba_appearance_ids:
            continue
        stat_key = stat_by_canonical.get(_norm_stat(app_stat.get("display_stat") or app_stat.get("stat", "")))
        if not stat_key:
            continue  # a stat the model doesn't use (or another sport)
        appearance = appearances.get(app_id, {})
        player = players.get(appearance.get("player_id"), {})
        if player.get("sport_id") not in (None, "NBA"):
            continue
        player_name = f"{player.get('first_name', '')} {player.get('last_name', '')}".strip()
        try:
            stat_value = float(line.get("stat_value"))
        except (TypeError, ValueError):
            continue
        if not player_name:
            continue

        over_odds = under_odds = over_mult = under_mult = ""
        for opt in line.get("options", []) or []:
            choice = opt.get("choice", "")
            if choice == "higher":
                over_odds, over_mult = opt.get("american_price", ""), opt.get("payout_multiplier", "")
            elif choice == "lower":
                under_odds, under_mult = opt.get("american_price", ""), opt.get("payout_multiplier", "")

        entry = {
            "line": stat_value,
            "over_odds": str(over_odds or ""),
            "under_odds": str(under_odds or ""),
            "over_multiplier": str(over_mult or ""),
            "under_multiplier": str(under_mult or ""),
            "player": player_name,
        }
        lines[(norm_name(player_name), stat_key)] = entry
        lines.setdefault((norm_name_base(player_name), stat_key), entry)
    return lines


def fetch_underdog_lines() -> dict:
    """Live Underdog NBA lines keyed by (normalized player, internal stat); {} on any failure."""
    try:
        lines = parse_underdog_lines(fetch_underdog_payload())
    except Exception as e:
        logger.warning(f"Underdog lines unavailable ({type(e).__name__}: {e})")
        return {}
    distinct = len({v["player"] + k[1] for k, v in lines.items()})
    logger.info(f"Fetched {distinct} Underdog NBA lines")
    return lines


def load_cached_lines(date: str, logs_dir: str | None = None) -> dict:
    """
    Lines already recorded in today's props file (logs/props_{date}.json).

    Underdog removes a line once its game tips off, so the afternoon and
    evening re-runs would otherwise strip lines the morning run captured.
    """
    logs_dir = logs_dir or os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
    path = os.path.join(logs_dir, f"props_{date}.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            props = json.load(f).get("all_props", [])
    except (OSError, ValueError):
        return {}
    cached: dict = {}
    for p in props:
        if p.get("ud_line") is None:
            continue
        entry = {
            "line": float(p["ud_line"]),
            "over_odds": p.get("ud_over_odds") or "",
            "under_odds": p.get("ud_under_odds") or "",
            "over_multiplier": "",
            "under_multiplier": "",
            "player": p["player_name"],
        }
        cached[(norm_name(p["player_name"]), p["stat"])] = entry
        cached.setdefault((norm_name_base(p["player_name"]), p["stat"]), entry)
    if cached:
        logger.info(f"Loaded {len(cached)} line(s) from earlier run today")
    return cached


def merge_lines(cached: dict, fresh: dict) -> dict:
    """
    Today's lines: fresh ones win, cached ones fill in for lines Underdog has
    since pulled (game tipped off).

    A cached entry is dropped whenever the fresh feed has the same player
    (suffix-free) and stat, so a morning line stored under 'Jimmy Butler III'
    can't shadow the afternoon line Underdog lists as 'Jimmy Butler'.
    """
    fresh_base = {(norm_name_base(v["player"]), k[1]) for k, v in fresh.items()}
    kept = {k: v for k, v in cached.items() if (norm_name_base(v["player"]), k[1]) not in fresh_base}
    return {**kept, **fresh}


def get_line_for_prop(lines: dict, player_name: str, stat: str) -> dict | None:
    """Look up the line for a player+stat; exact normalized name first, then suffix-free."""
    if not lines:
        return None
    return lines.get((norm_name(player_name), stat)) or lines.get((norm_name_base(player_name), stat))
