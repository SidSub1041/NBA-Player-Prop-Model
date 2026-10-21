"""
Fetch player prop lines from Underdog Fantasy.

Underdog's public pick'em feed has moved between endpoints over time. In
October 2026 the versioned paths (beta/v3, v5, v6) began answering HTTP 426
"upgrade_required"; the web client now reads the unversioned v1 path, which
returns the same players/appearances/games/over_under_lines payload. The
fetcher tries each known endpoint and keeps the first payload that contains
NBA over/under lines. Lines are keyed by a normalized
(player, stat) pair so name variants ("Jaren Jackson Jr.", accents) and stat
title variants ("Pts + Rebs + Asts" vs "Points + Rebounds + Assists") match.
"""

import json
import logging
import os
import re
import unicodedata
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests

logger = logging.getLogger(__name__)

ENDPOINTS = [
    # NBA-only board (~0.6 MB); the unfiltered board is ~26 MB, so it's the fallback
    "https://api.underdogfantasy.com/v1/over_under_lines?sport_id=NBA",
    "https://api.underdogfantasy.com/v1/over_under_lines",
]

TIMEOUT = (10, 60)  # connect, read — the unfiltered fallback board is large

EASTERN = ZoneInfo("America/New_York")  # NBA slates are dated in Eastern time

# A single-game line above these is a season-long market, never tonight's prop
MAX_GAME_LINE = {
    "points": 80, "rebounds": 35, "assists": 30, "pts+ast": 95, "pts+reb": 100,
    "reb+ast": 55, "pra": 120, "3-pointers made": 15, "steals": 10, "blocks": 12,
    "turnovers": 15,
}

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


def _usable(status: int, data) -> bool:
    """Underdog answered with a real board (possibly with no NBA lines yet)."""
    return status == 200 and isinstance(data, dict) and isinstance(data.get("over_under_lines"), list)


def _fetch_payload_once(
    proxies: dict | None, date: str | None = None, stop_on_usable: bool = False
) -> tuple[dict, int, bool]:
    """
    Try each endpoint (directly or via proxies).

    Returns (best payload, NBA line count for the model, whether any endpoint
    answered with a usable board). With stop_on_usable the loop ends at the
    first usable board even if it has no NBA lines (saves proxy bandwidth).
    """
    best: dict = {}
    best_count = -1
    any_usable = False
    via = "via proxy" if proxies else "direct"
    for url in ENDPOINTS:
        short = url.split(".com/")[-1]
        try:
            resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT, proxies=proxies)
            status = resp.status_code
            data = resp.json() if resp.ok else {}
        except Exception as e:
            logger.info(f"Underdog {short} ({via}): {type(e).__name__}")
            continue
        if status == 426:
            logger.warning(f"Underdog {short} ({via}): HTTP 426 upgrade_required — endpoint retired")
            continue
        usable = _usable(status, data)
        any_usable = any_usable or usable
        if not isinstance(data, dict):
            data = {}
        raw = data.get("over_under_lines")
        raw_count = len(raw) if isinstance(raw, list) else 0
        try:
            nba_count = len({v["player"] + k[1] for k, v in parse_underdog_lines(data, date).items()})
        except Exception as e:  # unknown schema — keep looking
            logger.info(f"Underdog {short} ({via}): unparseable ({type(e).__name__})")
            nba_count = 0
        logger.info(
            f"Underdog {short} ({via}): HTTP {status}, {raw_count} lines, {nba_count} NBA lines for the model"
        )
        if nba_count > best_count:
            best, best_count = data, nba_count
        if nba_count > 0 or (stop_on_usable and usable):
            break
    return best, max(best_count, 0), any_usable


def fetch_underdog_payload(date: str | None = None) -> dict:
    """
    Raw Underdog payload (players/appearances/games/over_under_lines) or {}.

    Tries directly first. Only if Underdog can't be reached directly (no
    endpoint answered with a usable board) and NBA_PROXY is set does it retry
    through the proxy — an empty-but-valid board is the same through any IP.
    """
    best, _count, usable = _fetch_payload_once(None, date)
    if not usable and _proxies():
        logger.info("Underdog: unreachable directly — retrying through NBA_PROXY")
        proxied, _pcount, pusable = _fetch_payload_once(_proxies(), date, stop_on_usable=True)
        if pusable or not best:
            best = proxied
    return best


def _game_date_et(game: dict) -> str | None:
    """'2026-10-21T23:30:00Z' -> '2026-10-21' (Eastern); None if the game has no usable start time."""
    raw = game.get("scheduled_at") or game.get("starts_at")
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(EASTERN).date().isoformat()


def _line_rank(line: dict, two_sided: bool) -> tuple:
    """Prefer the standard line: two-sided, balanced, not a discounted promo."""
    return (
        two_sided,
        line.get("line_type") in (None, "", "balanced"),
        line.get("non_discounted_stat_value") in (None, ""),
    )


def parse_underdog_lines(data: dict, date: str | None = None) -> dict:
    """
    Payload -> {(norm_name, internal_stat): {line, over_odds, under_odds, ...}}.

    Only pre-game lines for NBA games are kept — on `date` (YYYY-MM-DD,
    Eastern) when given — so tomorrow's back-to-back lines, live lines and
    season-long markets never attach to tonight's props. When a player has
    several lines for one stat, the standard two-sided line wins.
    """
    if not data or not isinstance(data, dict):
        return {}
    players = {p["id"]: p for p in data.get("players", []) or [] if isinstance(p, dict) and "id" in p}
    appearances = {a["id"]: a for a in data.get("appearances", []) or [] if isinstance(a, dict) and "id" in a}
    games = [g for g in data.get("games", []) or [] if isinstance(g, dict)]

    nba_games = [g for g in games if g.get("sport_id") == "NBA"]
    if date:
        nba_games = [g for g in nba_games if _game_date_et(g) in (None, date)]
    nba_game_ids = {str(g.get("id")) for g in nba_games}
    if not nba_game_ids:
        return {}  # game props need a game: nothing on the board for this slate
    nba_appearance_ids = {
        a_id for a_id, a in appearances.items()
        if str(a.get("match_id")) in nba_game_ids and "solo" not in str(a.get("match_type") or "").lower()
    }
    stat_by_canonical = {v: k for k, v in STAT_KEYS.items()}

    exact: dict = {}
    base: dict = {}
    ranks: dict = {}

    def _put(table: dict, key: tuple, entry: dict, rank: tuple) -> None:
        if key not in table or rank > ranks[(id(table), key)]:
            table[key] = entry
            ranks[(id(table), key)] = rank

    for line in data.get("over_under_lines", []) or []:
        if not isinstance(line, dict):
            continue
        status = line.get("status")
        if line.get("live_event") or (status and str(status).lower() != "active"):
            continue  # in-game or suspended line
        ou = line.get("over_under", {}) or {}
        app_stat = ou.get("appearance_stat", {}) or {}
        app_id = app_stat.get("appearance_id")
        if app_id not in nba_appearance_ids:
            continue
        stat_key = stat_by_canonical.get(_norm_stat(app_stat.get("display_stat") or app_stat.get("stat", "")))
        if not stat_key:
            continue  # a stat the model doesn't use
        player = players.get(appearances[app_id].get("player_id"), {})
        if player.get("sport_id") not in (None, "NBA"):
            continue
        player_name = f"{player.get('first_name', '')} {player.get('last_name', '')}".strip()
        if not player_name:
            continue

        options = {}
        for opt in line.get("options", []) or []:
            if isinstance(opt, dict) and opt.get("choice") in ("higher", "lower"):
                options[opt["choice"]] = opt
        two_sided = len(options) == 2
        discounted = line.get("non_discounted_stat_value") not in (None, "")
        try:
            # a promo line's standard value is its non-discounted one
            stat_value = float(line.get("non_discounted_stat_value") if discounted else line.get("stat_value"))
        except (TypeError, ValueError):
            continue
        if stat_value > MAX_GAME_LINE.get(stat_key, float("inf")):
            continue  # season-long market

        hi, lo = options.get("higher", {}), options.get("lower", {})
        entry = {
            "line": stat_value,
            # a discounted promo's prices belong to the promo, not the standard line
            "over_odds": "" if discounted else str(hi.get("american_price") or ""),
            "under_odds": "" if discounted else str(lo.get("american_price") or ""),
            "over_multiplier": "" if discounted else str(hi.get("payout_multiplier") or ""),
            "under_multiplier": "" if discounted else str(lo.get("payout_multiplier") or ""),
            "player": player_name,
        }
        rank = _line_rank(line, two_sided)
        _put(exact, (norm_name(player_name), stat_key), entry, rank)
        _put(base, (norm_name_base(player_name), stat_key), entry, rank)

    # An exact-name key always beats another player's suffix-free key
    return {**base, **exact}


def fetch_underdog_lines(date: str | None = None) -> dict:
    """Live Underdog NBA lines for `date`'s slate, keyed by (normalized player, internal stat); {} on any failure."""
    try:
        lines = parse_underdog_lines(fetch_underdog_payload(date), date)
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
