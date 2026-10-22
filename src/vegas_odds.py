"""
Sportsbook ("Vegas") lines and prices from The Odds API (https://the-odds-api.com, v4)
— the model's primary line source. Underdog is only the backup (src/lines.py).

For each player prop the books hang, the model's line is the one most books
hang (the consensus), and each side carries the best price any book offers at
that line and which book has it.

Setup: an ODDS_API_KEY repository secret. Without it (or once the plan's
credits run out) props fall back to Underdog's line, except that book lines
bought earlier the same day are kept until they can be re-priced.

Credits: listing the slate is free; pricing a game costs (markets × regions).
Each run prices only the markets its candidate props need, and remembers what
it bought in logs/vegas_{date}.json, so a later run re-buys a market only when
the stored price is older than ODDS_API_REFRESH_MINUTES (default 150) — or
ODDS_API_NEAR_TIP_REFRESH_MINUTES (default 45) within 3 hours of tip-off, so
the last pre-tip run catches late injury pulls and late postings. A run
stops buying at ODDS_API_MAX_CREDITS_PER_RUN (default 100) or when the plan has
fewer than ODDS_API_MIN_REMAINING (default 25) credits left.
"""

import json
import logging
import os
import statistics
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
LOGS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")

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


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(value) -> datetime | None:
    try:
        ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def fetch_events(date: str, api_key: str) -> tuple[list[dict], int | None]:
    """NBA events tipping off on `date` (Eastern) and the plan's remaining credits. Free."""
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
    tip = _parse_ts(event.get("commence_time"))
    return bool(tip and tip <= _now())


def parse_event_odds(data: dict) -> dict:
    """Event odds payload -> {(norm_name, stat): {"player": name, "offers": {(book, point): {"Over": p, "Under": p}}}}."""
    found: dict = {}
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
                entry = found.setdefault((norm_name(player), stat), {"player": player, "offers": defaultdict(dict)})
                entry["offers"][(title, point)][side] = o.get("price")
    return found


def consensus(offers: dict) -> dict:
    """
    One prop's book offers -> the model's line and prices.

    The line is the one most books hang (ties go to the line nearest the
    median of all books' lines). Each side gets the best price any book offers
    at that line — the highest American odds — and the book offering it.
    """
    if not offers:
        return {}
    points = Counter(point for (_, point) in offers)
    top = max(points.values())
    tied = sorted(p for p, n in points.items() if n == top)
    mid = statistics.median(point for (_, point) in offers)
    line = min(tied, key=lambda p: (abs(p - mid), p))
    at_line = {book: sides for (book, point), sides in offers.items() if point == line}

    def best(side):
        priced = []
        for book, sides in at_line.items():
            try:
                priced.append((float(sides[side]), book))
            except (KeyError, TypeError, ValueError):
                continue
        return max(priced) if priced else (None, None)

    over, over_book = best("Over")
    under, under_book = best("Under")
    return {
        "line": line,
        "over_odds": _fmt_american(over),
        "over_book": over_book,
        "under_odds": _fmt_american(under),
        "under_book": under_book,
        "books": len(at_line),
    }


def summarize(offers: dict, ud_line=None, edge: str = "over") -> dict:
    """Back-compatible summary used by probe_vegas.py: consensus plus the pick side's book."""
    c = consensus(offers)
    if c:
        c["book"] = c["under_book"] if str(edge).lower() == "under" else c["over_book"]
    return c


class VegasLines:
    """
    The day's sportsbook lines: what earlier runs bought (logs/vegas_{date}.json)
    plus whatever this run buys. One instance per model run.
    """

    def __init__(self, date: str, logs_dir: str | None = None):
        self.date = date
        self.path = os.path.join(logs_dir or LOGS_DIR, f"vegas_{date}.json")
        self.api_key = os.environ.get("ODDS_API_KEY", "").strip()
        self.refresh = timedelta(minutes=_env_int("ODDS_API_REFRESH_MINUTES", 150))
        self.near_tip_refresh = timedelta(minutes=_env_int("ODDS_API_NEAR_TIP_REFRESH_MINUTES", 45))
        self.near_tip_hours = _env_int("ODDS_API_NEAR_TIP_HOURS", 3)
        self.max_per_run = _env_int("ODDS_API_MAX_CREDITS_PER_RUN", 100)
        self.min_remaining = _env_int("ODDS_API_MIN_REMAINING", 25)
        self.regions = os.environ.get("ODDS_API_REGIONS", "us")
        self.n_regions = len([r for r in self.regions.split(",") if r.strip()]) or 1
        self.events: list[dict] = []
        self.remaining: int | None = None
        self.spent = 0
        self.stopped = False
        self.events_failed = False
        self.unpriced_teams: set[str] = set()   # games this run wanted but could not price
        self.pulled: set[tuple] = set()          # props the books took down pre-tip on a re-price
        self.cache = {"date": date, "events": {}, "lines": {}}
        try:
            with open(self.path, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict) and loaded.get("date") == date:
                self.cache["events"] = loaded.get("events") or {}
                self.cache["lines"] = loaded.get("lines") or {}
        except (OSError, ValueError):
            pass
        if self.api_key:
            try:
                self.events, self.remaining = fetch_events(date, self.api_key)
            except Exception as e:
                logger.warning(f"  Vegas: slate unavailable ({type(e).__name__}: {e}); using lines bought earlier today")
                self.events_failed = True
        else:
            logger.info("  Vegas lines off (no ODDS_API_KEY secret) — Underdog lines only")

    # ── buying ────────────────────────────────────────────────────────────
    def ensure(self, wants: dict) -> None:
        """
        Make sure the day's lines cover `wants` ({team: {stat, ...}}), buying
        only markets with no price yet or one older than the refresh window.
        """
        if not self.api_key:
            return
        if self.events_failed:
            self.unpriced_teams |= set(wants)
            return
        now = _now()
        for ev in sorted(self.events, key=lambda e: str(e.get("commence_time"))):
            teams = {_team_abbrev(ev.get("home_team")), _team_abbrev(ev.get("away_team"))} - {None}
            stats = {s for t in teams for s in wants.get(t, ()) if s in MARKETS}
            if not stats or _started(ev):
                continue   # nothing wanted, or tipped: keep the last pre-game prices
            ev_id = str(ev.get("id"))
            ev_cache = self.cache["events"].setdefault(ev_id, {"teams": sorted(teams), "markets": {}})
            ev_cache["commence_time"] = ev.get("commence_time")
            # Within NEAR_TIP_HOURS of tip-off, prices are re-bought sooner, so the
            # last pre-tip run sees late injury pulls and late postings.
            tip = _parse_ts(ev.get("commence_time"))
            window = self.refresh
            if tip is not None and tip - now <= timedelta(hours=self.near_tip_hours):
                window = min(self.refresh, self.near_tip_refresh)
            due = sorted(
                MARKETS[s] for s in stats
                if not (_parse_ts(ev_cache["markets"].get(MARKETS[s])) and now - _parse_ts(ev_cache["markets"][MARKETS[s]]) < window)
            )
            if not due:
                continue
            cost = len(due) * self.n_regions
            if not self.stopped and (
                self.spent + cost > self.max_per_run
                or (self.remaining is not None and self.remaining - cost < self.min_remaining)
            ):
                logger.warning(
                    f"  Vegas: stopping to save credits (spent {self.spent} this run, {self.remaining} left on the plan)"
                )
                self.stopped = True
            if self.stopped:
                self.unpriced_teams |= teams
                continue
            try:
                data, headers = _get(f"/events/{ev_id}/odds", {
                    "apiKey": self.api_key,
                    "regions": self.regions,
                    "markets": ",".join(due),
                    "oddsFormat": "american",
                    "dateFormat": "iso",
                })
            except Exception as e:
                logger.warning(f"  Vegas: {ev.get('away_team')} @ {ev.get('home_team')} unavailable ({type(e).__name__}: {e})")
                self.unpriced_teams |= teams
                if "401" in str(e) or "429" in str(e):
                    self.stopped = True
                continue
            self.spent += cost
            r = _remaining(headers)
            self.remaining = r if r is not None else self.remaining
            self._store(ev_id, due, parse_event_odds(data), now)

    def _store(self, ev_id: str, markets: list[str], found: dict, now: datetime) -> None:
        stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        lines = self.cache["lines"]
        for market in markets:
            stat = STAT_FOR_MARKET[market]
            before = {k for k, v in lines.items() if v.get("event_id") == ev_id and v.get("stat") == stat}
            fresh = {}
            for (pname, s), entry in found.items():
                if s != stat:
                    continue
                c = consensus(entry["offers"])
                if c:
                    fresh[f"{pname}|{stat}"] = {**c, "player": entry["player"], "stat": stat,
                                                "event_id": ev_id, "fetched_at": stamp}
            for k in before - set(fresh):
                self.pulled.add(tuple(k.rsplit("|", 1)))   # was hung earlier today, gone now (pre-tip)
                del lines[k]
            lines.update(fresh)
            self.cache["events"][ev_id]["markets"][market] = stamp

    def save(self) -> None:
        if not self.cache["lines"] and not self.cache["events"]:
            return
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.cache, f, indent=1, sort_keys=True)
        os.replace(tmp, self.path)

    # ── reading ───────────────────────────────────────────────────────────
    def lookup(self, player_name: str, stat: str) -> dict | None:
        """The books' line for this player+stat (exact name, then suffix-free), or None."""
        lines = self.cache["lines"]
        hit = lines.get(f"{norm_name(player_name)}|{stat}")
        if hit:
            return hit
        base = norm_name_base(player_name)
        for key, v in lines.items():
            name, s = key.rsplit("|", 1)
            if s == stat and norm_name_base(name) == base:
                return v
        return None

    def issue(self, player_name: str, team: str, stat: str) -> str:
        """Why the books have no line for this prop: off / pulled / unpriced / not_offered."""
        if (norm_name(player_name), stat) in self.pulled or (norm_name_base(player_name), stat) in self.pulled:
            return "pulled"
        if not self.api_key:
            return "off"
        if team in self.unpriced_teams or self.events_failed:
            return "unpriced"
        return "not_offered"

    def report(self) -> str:
        return (f"{len(self.cache['lines'])} book lines on file today; spent ~{self.spent} credits this run"
                + ("" if self.remaining is None else f", {self.remaining} left on the plan"))
