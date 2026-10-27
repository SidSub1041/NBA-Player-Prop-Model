"""
Sportsbook ("Vegas") and DFS-app lines from The Odds API (https://the-odds-api.com, v4)
— the model's line source. Sportsbooks first; the DFS pick'em apps are the backup.

For each player prop the books hang, the model's line is the one most books
hang (the consensus), and each side carries the best price any book offers at
that line and which book has it. A prop no sportsbook lists falls back to the
DFS apps (PrizePicks, Underdog, DraftKings Pick6, Betr, Dabble — The Odds API's
`us_dfs` region): the line most of them hang, labelled as a DFS backup.

Setup: an ODDS_API_KEY repository secret. Without it (or once the plan's
credits run out) lines bought earlier the same day are kept until they can be
re-priced, and Underdog's own feed supplies candidates (shown on the board,
never published: the edge model's books guard needs 3 sportsbooks).

Credits: listing the slate is free; pricing a game costs (markets × regions)
per request. Each run prices only the markets its candidate props need, buys
the sportsbook regions first and the DFS region only for a market the books
have not posted at all in a game (the edge model wants every player, so a
single player the books skip gets no DFS line), and remembers what it bought in
logs/vegas_{date}.json, so a later run re-buys a market only when the stored
price is older than ODDS_API_REFRESH_MINUTES (default 150) — or
ODDS_API_NEAR_TIP_REFRESH_MINUTES (default 45) within 3 hours of tip-off, so
the last pre-tip run catches late injury pulls and late postings. A run
stops buying at ODDS_API_MAX_CREDITS_PER_RUN (default 150), when the plan has
fewer than ODDS_API_MIN_REMAINING (default 25) credits left, or when the first
ODDS_API_EMPTY_STOP_EVENTS (default 2) games more than ODDS_API_EMPTY_STOP_HOURS
(default 8) from tip-off come back with no props at all — nobody has posted
the slate yet, so the rest would be bought for nothing.

Fill mode (main.py --fill-holds): picks already on the day's board keep their
line (the lock is applied when the board is built, src/lines.resolve); the
cache itself always holds the market's current numbers so the day's line
history (src/line_history.py) records every move, and a locked key is never
marked "pulled". A fill run re-buys markets once they are
ODDS_API_FILL_REFRESH_MINUTES (default 60) old: the edge model wants every
player's market, so a fill run re-prices every untipped game, which is how it
finds bench and late props.

Budget (edge model, full 10–12-game day): night-before ~30–40 credits,
morning ~40–60 with combos, each fill run ~30–40, snapshot runs ~40–80 —
about 200–250 credits a day, 6,000–7,500 a month.

Line history (round 18): every save() appends the lines that changed to
logs/lines_{date}.json (src/line_history.py), so the model knows where each
line opened and moved. Snapshot runs (main.py --snapshot, `ensure_snapshot`)
re-price every game tipping within SNAPSHOT_WINDOW_MINUTES (45; the snapshot
cron runs every 30 minutes through the evening), whatever the refresh window
says, so the last price before tip-off — the close — is on file for every
player the books list; the grader scores each pick against it (closing-line
value) when the market was re-priced within CLOSE_WINDOW_MINUTES of tip. A snapshot costs (markets × regions) credits per game
about to tip, the DFS region only for a market no sportsbook has posted.
"""

import json
import logging
import os
import statistics
from collections import Counter, defaultdict
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from src import line_history
from src.config import SNAPSHOT_WINDOW_MINUTES, TEAM_ABBREVS
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


def _regions(name: str, default: str) -> str:
    """A comma-separated regions setting with blanks removed ("" disables)."""
    return ",".join(r.strip() for r in os.environ.get(name, default).split(",") if r.strip())


def dfs_backup_enabled() -> bool:
    """Do DFS-app lines come through The Odds API on this machine? (key set, DFS region on)"""
    return bool(os.environ.get("ODDS_API_KEY", "").strip()) and bool(_regions("ODDS_API_DFS_REGIONS", "us_dfs"))


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
    One prop's offers (from the sportsbooks, or from the DFS apps) -> the
    model's line and prices.

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


def tip_offs(date: str, logs_dir: str | None = None) -> dict[str, str]:
    """
    {team abbreviation: commence_time} for every game priced on `date`, read
    from the day's cache file (logs/vegas_{date}.json) without touching the
    API. (The grader reads the same file itself, since it also needs each
    market's last re-price time to verify a close.)
    """
    path = os.path.join(logs_dir or LOGS_DIR, f"vegas_{date}.json")
    try:
        with open(path, encoding="utf-8") as f:
            cache = json.load(f)
    except (OSError, ValueError):
        return {}
    events = cache.get("events") if isinstance(cache, dict) else None
    tips: dict[str, str] = {}
    for ev in (events or {}).values():
        if not isinstance(ev, dict) or not ev.get("commence_time"):
            continue
        for team in ev.get("teams") or []:
            tips[str(team)] = str(ev["commence_time"])
    return tips


def _covered(found: dict, stat: str, pnorm: str) -> bool:
    """Does `found` (parsed event odds) hold a line for this player+stat? Exact name, then suffix-free."""
    if (pnorm, stat) in found:
        return True
    base = norm_name_base(pnorm)
    return any(s == stat and norm_name_base(n) == base for (n, s) in found)


class VegasLines:
    """
    The day's lines: what earlier runs bought (logs/vegas_{date}.json) plus
    whatever this run buys. One instance per model run.
    """

    def __init__(self, date: str, logs_dir: str | None = None, locked: set | None = None, fill: bool = False):
        self.date = date
        self.logs_dir = logs_dir or LOGS_DIR
        self.path = os.path.join(self.logs_dir, f"vegas_{date}.json")
        self.api_key = os.environ.get("ODDS_API_KEY", "").strip()
        self.refresh = timedelta(minutes=_env_int("ODDS_API_REFRESH_MINUTES", 150))
        self.near_tip_refresh = timedelta(minutes=_env_int("ODDS_API_NEAR_TIP_REFRESH_MINUTES", 45))
        self.near_tip_hours = _env_int("ODDS_API_NEAR_TIP_HOURS", 3)
        self.fill = fill                                                     # fill-holds run
        self.fill_refresh = timedelta(minutes=_env_int("ODDS_API_FILL_REFRESH_MINUTES", 60))
        self.max_per_run = _env_int("ODDS_API_MAX_CREDITS_PER_RUN", 150)
        self.min_remaining = _env_int("ODDS_API_MIN_REMAINING", 25)
        self.empty_stop_events = _env_int("ODDS_API_EMPTY_STOP_EVENTS", 2)
        self.empty_stop_hours = _env_int("ODDS_API_EMPTY_STOP_HOURS", 8)
        self.regions = _regions("ODDS_API_REGIONS", "us") or "us"          # sportsbooks
        self.dfs_regions = _regions("ODDS_API_DFS_REGIONS", "us_dfs")      # DFS apps ("" = off)
        self.locked: set[str] = set(locked or ())   # "normname|stat" keys never re-priced (fill mode)
        self.events: list[dict] = []
        self.remaining: int | None = None
        self.spent = 0
        self.bought = {"books": 0, "dfs": 0}       # market purchases this run, per tier
        self.stopped = False
        self.nothing_posted = False                # the slate's props aren't up yet (empty-stop fired)
        self.events_failed = False
        self.unpriced_teams: set[str] = set()   # games this run wanted but could not (fully) price
        self.pulled: set[tuple] = set()          # props taken down pre-tip on a re-price
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

    @property
    def dfs_backup(self) -> bool:
        """Are the DFS apps part of this run's line search?"""
        return bool(self.api_key and self.dfs_regions)

    # ── buying ────────────────────────────────────────────────────────────
    @staticmethod
    def _wanted(wants: dict, teams: set) -> dict:
        """{stat: {norm player, ...}} wanted for one game. A stat with no player list (None) wants everyone."""
        wanted: dict = {}
        for t in teams:
            stats = wants.get(t) or {}
            items = stats.items() if isinstance(stats, dict) else ((s, None) for s in stats)
            for s, players in items:
                if s not in MARKETS:
                    continue
                if players is None:
                    wanted[s] = None
                elif wanted.get(s, set()) is not None:
                    wanted.setdefault(s, set()).update(norm_name(p) for p in players)
        return wanted

    def _buy(self, ev: dict, markets: list[str], regions: str, tier: str) -> dict | None:
        """Price `markets` for one event in `regions`. Parsed offers, or None if not bought."""
        cost = len(markets) * (len(regions.split(",")) or 1)
        if not self.stopped and (
            self.spent + cost > self.max_per_run
            or (self.remaining is not None and self.remaining - cost < self.min_remaining)
        ):
            logger.warning(
                f"  Vegas: stopping to save credits (spent {self.spent} this run, {self.remaining} left on the plan)"
            )
            self.stopped = True
        if self.stopped:
            return None
        try:
            data, headers = _get(f"/events/{ev.get('id')}/odds", {
                "apiKey": self.api_key,
                "regions": regions,
                "markets": ",".join(markets),
                "oddsFormat": "american",
                "dateFormat": "iso",
            })
        except Exception as e:
            logger.warning(f"  Vegas: {ev.get('away_team')} @ {ev.get('home_team')} unavailable ({type(e).__name__}: {e})")
            if "401" in str(e) or "429" in str(e):
                self.stopped = True
            return None
        self.spent += cost
        self.bought[tier] += len(markets)
        r = _remaining(headers)
        self.remaining = r if r is not None else self.remaining
        return parse_event_odds(data)

    def ensure(self, wants: dict) -> None:
        """
        Make sure the day's lines cover `wants` ({team: {stat: {player, ...}}},
        or {team: {stat, ...}} to want every player), buying only markets with
        no price yet or one older than the refresh window. Sportsbooks first;
        the DFS apps only for markets where a wanted player still has no
        sportsbook line. Locked props (fill mode) are never wanted.
        """
        if not self.api_key:
            return
        if self.events_failed:
            self.unpriced_teams |= set(wants)
            return
        now = _now()
        empty_streak = 0
        for ev in sorted(self.events, key=lambda e: str(e.get("commence_time"))):
            teams = {_team_abbrev(ev.get("home_team")), _team_abbrev(ev.get("away_team"))} - {None}
            wanted = self._wanted(wants, teams)
            if not wanted or _started(ev):
                continue   # nothing wanted, or tipped: keep the last pre-game prices
            ev_id = str(ev.get("id"))
            ev_cache = self.cache["events"].setdefault(ev_id, {"teams": sorted(teams), "markets": {}})
            ev_cache["commence_time"] = ev.get("commence_time")
            # Within NEAR_TIP_HOURS of tip-off, prices are re-bought sooner, so the
            # last pre-tip run sees late injury pulls and late postings.
            tip = _parse_ts(ev.get("commence_time"))
            window = min(self.refresh, self.fill_refresh) if self.fill else self.refresh
            if tip is not None and tip - now <= timedelta(hours=self.near_tip_hours):
                window = min(window, self.near_tip_refresh)
            due = sorted(
                MARKETS[s] for s in wanted
                if not (_parse_ts(ev_cache["markets"].get(MARKETS[s])) and now - _parse_ts(ev_cache["markets"][MARKETS[s]]) < window)
            )
            if not due:
                continue
            far_off = tip is None or tip - now > timedelta(hours=self.empty_stop_hours)
            if self.nothing_posted:
                self.unpriced_teams |= teams
                continue
            # Pass 1: the sportsbooks
            found = self._buy(ev, due, self.regions, "books")
            if found is None:
                self.unpriced_teams |= teams
                continue
            # Pass 2: the DFS apps, only for markets where a wanted player has no book line yet
            missing = [m for m in due if self._missing(found, STAT_FOR_MARKET[m], wanted[STAT_FOR_MARKET[m]])]
            dfs_found: dict = {}
            if missing and self.dfs_regions:
                bought = self._buy(ev, missing, self.dfs_regions, "dfs")
                if bought is None:
                    self.unpriced_teams |= teams   # books checked, DFS apps not: re-checked next run
                else:
                    dfs_found = bought
            self._store(ev_id, due, found, dfs_found, now)
            empty_streak = empty_streak + 1 if not found and not dfs_found else 0
            if empty_streak >= self.empty_stop_events and far_off and not self.nothing_posted:
                self.nothing_posted = True
                logger.warning(
                    f"  Vegas: no book or DFS app has posted props for the first {empty_streak} games "
                    f"(tip-off more than {self.empty_stop_hours}h away) — stopping to save credits; the rest is re-checked next run"
                )

    def ensure_snapshot(self, games: list[dict] | None = None, window_minutes: int = SNAPSHOT_WINDOW_MINUTES,
                        stats: tuple = ("points", "rebounds", "assists")) -> int:
        """
        Snapshot run: re-price every game that tips within `window_minutes`
        (and has not tipped), ignoring the refresh window, so the close is on
        file for every player the books list. Buys the `stats` markets for all
        players plus any market already bought for the game today (combos on
        the board get a close too); the DFS apps only for a market no
        sportsbook has posted. `games` (games_today shape) narrows the slate to
        the listed games and skips ones already started. Returns the number of
        games priced. The credit guards apply as in ensure(); ensure() itself
        is unchanged.
        """
        if not self.api_key or self.events_failed:
            return 0
        now = _now()
        window = timedelta(minutes=max(int(window_minutes), 0))
        allowed: dict[frozenset, dict] | None = None
        if games is not None:
            allowed = {}
            for g in games:
                if isinstance(g, dict) and g.get("home") and g.get("away"):
                    allowed[frozenset((str(g["home"]), str(g["away"])))] = g
        priced = 0
        for ev in sorted(self.events, key=lambda e: str(e.get("commence_time"))):
            teams = {_team_abbrev(ev.get("home_team")), _team_abbrev(ev.get("away_team"))} - {None}
            tip = _parse_ts(ev.get("commence_time"))
            if allowed is not None:
                game = allowed.get(frozenset(teams))
                if game is None or game.get("status") in (2, 3):   # not on the slate, or in progress / final
                    continue
                if tip is None:
                    tip = _parse_ts(game.get("start_utc"))
            if tip is None or tip <= now or tip - now > window:
                continue
            ev_id = str(ev.get("id"))
            ev_cache = self.cache["events"].setdefault(ev_id, {"teams": sorted(teams), "markets": {}})
            ev_cache["commence_time"] = ev.get("commence_time")
            wanted = {s: None for s in stats if s in MARKETS}
            wanted.update({STAT_FOR_MARKET[m]: None for m in ev_cache["markets"] if m in STAT_FOR_MARKET})
            due = sorted(MARKETS[s] for s in wanted)
            if not due:
                continue
            found = self._buy(ev, due, self.regions, "books")
            if found is None:
                self.unpriced_teams |= teams
                if self.stopped:
                    break
                continue
            missing = [m for m in due if self._missing(found, STAT_FOR_MARKET[m], None)]
            dfs_found: dict = {}
            if missing and self.dfs_regions:
                bought = self._buy(ev, missing, self.dfs_regions, "dfs")
                if bought is not None:
                    dfs_found = bought
            self._store(ev_id, due, found, dfs_found, now)
            priced += 1
        if priced:
            logger.info(f"  Vegas: snapshot priced {priced} game(s) tipping within {int(window_minutes)} min")
        return priced

    def tip_for_team(self, team: str) -> str | None:
        """Commence time (ISO, UTC) of the cached event that includes `team`, else from the live slate, else None."""
        for ev in self.cache["events"].values():
            if isinstance(ev, dict) and team in (ev.get("teams") or []) and ev.get("commence_time"):
                return str(ev["commence_time"])
        for ev in self.events:
            if team in {_team_abbrev(ev.get("home_team")), _team_abbrev(ev.get("away_team"))} and ev.get("commence_time"):
                return str(ev["commence_time"])
        return None

    @staticmethod
    def _missing(found: dict, stat: str, players: set | None) -> bool:
        """Does a wanted player still lack a line for `stat`? (No player list: is the market empty?)"""
        if players is None:
            return not any(s == stat for (_, s) in found)
        return any(not _covered(found, stat, p) for p in players)

    def _store(self, ev_id: str, markets: list[str], found: dict, dfs_found: dict, now: datetime) -> None:
        stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        lines = self.cache["lines"]
        for market in markets:
            stat = STAT_FOR_MARKET[market]
            before = {k for k, v in lines.items() if v.get("event_id") == ev_id and v.get("stat") == stat}
            fresh = {}
            for tier, src in (("books", found), ("dfs", dfs_found)):   # books first, DFS fills the gaps
                for (pname, s), entry in src.items():
                    key = f"{pname}|{stat}"
                    if s != stat or key in fresh:
                        continue
                    c = consensus(entry["offers"])
                    if c:
                        fresh[key] = {**c, "player": entry["player"], "stat": stat, "tier": tier,
                                      "event_id": ev_id, "fetched_at": stamp,
                                      "offers": {book: {"line": point, "over": _fmt_american(sides.get("Over")),
                                                        "under": _fmt_american(sides.get("Under"))}
                                                 for (book, point), sides in entry["offers"].items()}}
            # Fill mode: a locked pick keeps ITS line on the board (src/lines.resolve), but the
            # cache always reflects the market now, so the day's line history records the move.
            for k in before - set(fresh):
                if k not in self.locked:
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
        # The day's line history (open / moves / close) — never lets a failure break the save
        try:
            appended = line_history.record(self.date, self.cache["lines"], _now(), self.logs_dir)
            if appended:
                logger.info(f"  Line history: {appended} snapshot(s) appended to lines_{self.date}.json")
        except Exception as e:
            logger.warning(f"  Line history not updated ({type(e).__name__}: {e})")

    # ── reading ───────────────────────────────────────────────────────────
    def lookup(self, player_name: str, stat: str) -> dict | None:
        """The line on file for this player+stat (exact name, then suffix-free), or None.
        The entry's "tier" says who hangs it: "books" (sportsbooks) or "dfs" (DFS apps)."""
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
        """Why there is no line for this prop: off / pulled / unpriced / not_offered."""
        if (norm_name(player_name), stat) in self.pulled or (norm_name_base(player_name), stat) in self.pulled:
            return "pulled"
        if not self.api_key:
            return "off"
        if team in self.unpriced_teams or self.events_failed:
            return "unpriced"
        return "not_offered"

    def report(self) -> str:
        tiers = Counter(v.get("tier", "books") for v in self.cache["lines"].values())
        return (f"{len(self.cache['lines'])} lines on file today ({tiers.get('books', 0)} from sportsbooks, "
                f"{tiers.get('dfs', 0)} from DFS apps); bought {self.bought['books']} book + {self.bought['dfs']} DFS markets, "
                f"~{self.spent} credits this run"
                + ("" if self.remaining is None else f", {self.remaining} left on the plan"))
