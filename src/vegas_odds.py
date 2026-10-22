"""
Sportsbook ("Vegas") odds for the props on the board, from The Odds API
(https://the-odds-api.com, v4). Display only: picks are still graded against
Underdog's line, but the board shows what the books are pricing.

Setup: add an ODDS_API_KEY repository secret (free key at the-odds-api.com).
Without it this module does nothing.

Cost (The Odds API counts "credits"): the events list is free; each event's
player-prop odds cost (markets returned × regions). To keep usage down, only
events with a valid or on-hold prop are requested, only for the markets those
props need, and a run stops when the account's remaining credits fall below
ODDS_API_MIN_REMAINING or the run has spent ODDS_API_MAX_CREDITS_PER_RUN.
"""

import logging
import os
from collections import Counter, defaultdict
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from src.config import TEAM_ABBREVS
from src.underdog import norm_name, norm_name_base

logger = logging.getLogger(__name__)

BASE_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba"
EASTERN = ZoneInfo("America/New_York")
TIMEOUT = 20

# Internal stat -> The Odds API player-prop market key
MARKETS = {
    "points": "player_points",
    "rebounds": "player_rebounds",
    "assists": "player_assists",
    "pra": "player_points_rebounds_assists",
    "pts+reb": "player_points_rebounds",
    "pts+ast": "player_points_assists",
    "reb+ast": "player_rebounds_assists",
    "3-pointers made": "player_threes",
}
STAT_FOR_MARKET = {v: k for k, v in MARKETS.items()}

# Team names as the books write them -> abbreviations ("Los Angeles Clippers"
# and "LA Clippers" both resolve through the nickname)
_BY_NICKNAME = {full.rsplit(" ", 1)[-1].lower(): abbr for full, abbr in TEAM_ABBREVS.items()}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _team_abbrev(full_name: str) -> str | None:
    if not full_name:
        return None
    return TEAM_ABBREVS.get(full_name) or _BY_NICKNAME.get(str(full_name).rsplit(" ", 1)[-1].lower())


def _fmt_american(price) -> str | None:
    try:
        p = int(round(float(price)))
    except (TypeError, ValueError):
        return None
    return f"+{p}" if p > 0 else str(p)


def _get(path: str, params: dict) -> tuple[object, dict]:
    resp = requests.get(f"{BASE_URL}{path}", params=params, timeout=TIMEOUT)
    if resp.status_code == 401:
        raise RuntimeError("ODDS_API_KEY rejected (HTTP 401)")
    if resp.status_code == 429:
        raise RuntimeError("The Odds API: out of credits or rate limited (HTTP 429)")
    resp.raise_for_status()
    return resp.json(), resp.headers


def _remaining(headers) -> int | None:
    try:
        return int(float(headers.get("x-requests-remaining")))
    except (TypeError, ValueError):
        return None


def fetch_events(date: str, api_key: str) -> tuple[list[dict], int | None]:
    """
    NBA events tipping off on `date` (Eastern) and the plan's remaining credits.
    The events endpoint costs no credits.
    """
    day = datetime.strptime(date, "%Y-%m-%d").date()
    start = datetime.combine(day, time(0, 0), EASTERN).astimezone(timezone.utc)
    end = start + timedelta(days=1)
    data, headers = _get("/events", {
        "apiKey": api_key,
        "dateFormat": "iso",
        "commenceTimeFrom": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "commenceTimeTo": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
    })
    events = [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []
    return events, _remaining(headers)


def _started(event: dict) -> bool:
    """Has this game tipped? (Live prices are in-game, not the pre-game market.)"""
    try:
        tip = datetime.fromisoformat(str(event.get("commence_time")).replace("Z", "+00:00"))
    except ValueError:
        return False
    return tip <= datetime.now(timezone.utc)


def load_cached_vegas(date: str, logs_dir: str | None = None) -> dict:
    """Vegas prices recorded by an earlier run today: {(norm_name, stat): vegas dict}."""
    import json

    logs_dir = logs_dir or os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
    path = os.path.join(logs_dir, f"props_{date}.json")
    try:
        with open(path, encoding="utf-8") as f:
            props = json.load(f).get("all_props", [])
    except (OSError, ValueError):
        return {}
    cached = {}
    for p in props:
        if p.get("vegas_line") is None:
            continue
        cached[(norm_name(p["player_name"]), p["stat"])] = {
            "line": p.get("vegas_line"),
            "over_odds": p.get("vegas_over_odds"),
            "under_odds": p.get("vegas_under_odds"),
            "book": p.get("vegas_book"),
            "books": p.get("vegas_books"),
        }
    return cached


def parse_event_odds(data: dict) -> dict:
    """Event odds payload -> {(norm_name, stat): {(book, point): {"Over": price, "Under": price}}}."""
    offers: dict = defaultdict(lambda: defaultdict(dict))
    for book in (data or {}).get("bookmakers", []) or []:
        title = book.get("title") or book.get("key") or "?"
        for market in book.get("markets", []) or []:
            stat = STAT_FOR_MARKET.get(market.get("key"))
            if not stat:
                continue
            for o in market.get("outcomes", []) or []:
                side, player, point = o.get("name"), o.get("description"), o.get("point")
                if side not in ("Over", "Under") or not player or point is None:
                    continue
                try:
                    point = float(point)
                except (TypeError, ValueError):
                    continue
                offers[(norm_name(player), stat)][(title, point)][side] = o.get("price")
    return offers


def summarize(offers: dict, ud_line: float | None, edge: str) -> dict:
    """
    One prop's book offers -> what the board shows.

    Uses the books' prices at Underdog's line when any book hangs it; otherwise
    the line most books hang. Reports the best price on each side (the highest
    American odds a bettor can get) and which book has it for the pick's side.
    """
    if not offers:
        return {}
    points = Counter(point for (_, point) in offers)
    if ud_line is not None and float(ud_line) in points:
        line = float(ud_line)
    else:
        top = max(points.values())
        tied = [p for p, n in points.items() if n == top]
        line = min(tied, key=lambda p: (abs(p - float(ud_line)) if ud_line is not None else 0, p))
    at_line = {book: sides for (book, point), sides in offers.items() if point == line}

    def best(side):
        priced = [(float(s[side]), b) for b, s in at_line.items() if s.get(side) is not None]
        return max(priced) if priced else (None, None)

    over, over_book = best("Over")
    under, under_book = best("Under")
    pick_book = under_book if str(edge).lower() == "under" else over_book
    return {
        "line": line,
        "over_odds": _fmt_american(over),
        "under_odds": _fmt_american(under),
        "book": pick_book or over_book or under_book,
        "books": len(at_line),
    }


def attach_vegas_odds(prop_scores: list, date: str) -> int:
    """Fetch book odds for valid and on-hold props and attach them as prop.vegas. Returns props priced."""
    api_key = os.environ.get("ODDS_API_KEY", "").strip()
    if not api_key:
        logger.info("  Vegas odds off (no ODDS_API_KEY secret)")
        return 0
    wanted = [p for p in prop_scores if (p.is_valid or getattr(p, "on_hold", False)) and p.stat in MARKETS]
    if not wanted:
        return 0

    min_remaining = _env_int("ODDS_API_MIN_REMAINING", 25)
    max_per_run = _env_int("ODDS_API_MAX_CREDITS_PER_RUN", 60)
    regions = os.environ.get("ODDS_API_REGIONS", "us")
    n_regions = len([r for r in regions.split(",") if r.strip()]) or 1

    # Last pre-game prices from earlier runs. They are reused only for games
    # this run could not price (tipped, skipped to save credits, or a failed
    # request). A pre-tip game that was priced but no longer lists a player's
    # market gets no Vegas price: the books pulled it.
    cached = load_cached_vegas(date)
    keep_teams: set = set()
    fetch_failed = False

    try:
        events, remaining = fetch_events(date, api_key)
    except Exception as e:
        logger.warning(f"  Vegas odds unavailable ({type(e).__name__}: {e}); keeping earlier prices")
        events, remaining, fetch_failed = [], None, True

    offers: dict = {}
    spent = 0
    stopped = False
    for ev in events:
        teams = {_team_abbrev(ev.get("home_team")), _team_abbrev(ev.get("away_team"))} - {None}
        if _started(ev):
            keep_teams |= teams   # in-game prices aren't the pre-game market
            continue
        markets = sorted({MARKETS[p.stat] for p in wanted if p.team in teams})
        if not markets:
            continue
        cost = len(markets) * n_regions
        if not stopped and (spent + cost > max_per_run or (remaining is not None and remaining - cost < min_remaining)):
            logger.warning(
                f"  Vegas odds: stopping to save credits (spent {spent} this run, {remaining} left on the plan)"
            )
            stopped = True
        if stopped:
            keep_teams |= teams
            continue
        try:
            data, headers = _get(f"/events/{ev['id']}/odds", {
                "apiKey": api_key,
                "regions": regions,
                "markets": ",".join(markets),
                "oddsFormat": "american",
                "dateFormat": "iso",
            })
        except Exception as e:
            logger.warning(f"  Vegas odds for {ev.get('away_team')} @ {ev.get('home_team')}: {type(e).__name__}: {e}")
            keep_teams |= teams
            if "401" in str(e) or "429" in str(e):
                stopped = True
            continue
        spent += cost
        remaining = _remaining(headers) if _remaining(headers) is not None else remaining
        for key, by_book in parse_event_odds(data).items():
            offers.setdefault(key, {}).update(by_book)

    priced = 0
    for p in wanted:
        o = offers.get((norm_name(p.player_name), p.stat)) or offers.get((norm_name_base(p.player_name), p.stat))
        if not o:
            # book listings sometimes keep a suffix the model's name lacks (or vice versa)
            base = norm_name_base(p.player_name)
            o = next((v for (n, s), v in offers.items() if s == p.stat and norm_name_base(n) == base), None)
        if o:
            p.vegas = summarize(o, p.ud_line, p.edge)
            priced += 1
        elif (fetch_failed or p.team in keep_teams) and cached.get((norm_name(p.player_name), p.stat)):
            p.vegas = dict(cached[(norm_name(p.player_name), p.stat)])
            priced += 1
    logger.info(
        f"  Vegas odds: priced {priced}/{len(wanted)} board props from {len(events)} events "
        f"(~{spent} credits this run{'' if remaining is None else f', {remaining} left'})"
    )
    return priced
