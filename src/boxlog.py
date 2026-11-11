"""
The box-score history (round 24): one file per night, logs/box/{date}.json.

Two writers, one shape:

  * the grading run archives last night's full box score (src/grader.py,
    round 22: every player by NBA id with team, opponent, home or away,
    starter, minutes and shooting volume);
  * backfill() writes every OLDER night that has no file yet from the league
    game-log tables (src/gamelogs.py — free NBA data, both seasons), once.
    Those rows carry what the table has: minutes, the counting stats, home or
    away and the opponent ("source": "gamelogs"; no starter flag, no shooting
    volume). A night is written only when every row knows its venue, and
    never overwritten.

One reader: History loads every file into each player's games, newest first,
so anything about a player's past — how often he has gone over tonight's
line, at home or on the road, his minutes lately, whether his team played
yesterday — can be asked AS OF any date, for tonight's board and for every
past board alike, without asking NBA.com again. That is what lets the
scorecard replay the checklist on nights from before the checklist existed.
"""

import glob
import json
import logging
import os
from collections import defaultdict

from src.config import season_for_date
from src.underdog import norm_name, norm_name_base

logger = logging.getLogger(__name__)

LOGS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
STAT_PARTS = {"points": ("pts",), "rebounds": ("reb",), "assists": ("ast",), "pts+reb": ("pts", "reb"), "pts+ast": ("pts", "ast"),
              "reb+ast": ("reb", "ast"), "pra": ("pts", "reb", "ast")}
THIN_SEASON = 5          # fewer games than this so far this season: last season's games are read too


def box_dir(logs_dir: str | None = None) -> str:
    return os.path.join(logs_dir or LOGS_DIR, "box")


def path(date: str, logs_dir: str | None = None) -> str:
    return os.path.join(box_dir(logs_dir), f"{date}.json")


def _num(v, default=0):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return default if f != f else f


def backfill(frames: list, before: str, logs_dir: str | None = None) -> int:
    """
    Write logs/box/{date}.json for every game date in the league game-log
    tables (`frames`: src.gamelogs.league_logs results, None allowed) that is
    earlier than `before` (YYYY-MM-DD) and has no file yet. Returns the number
    of nights written. Never raises; never overwrites.
    """
    written = 0
    try:
        os.makedirs(box_dir(logs_dir), exist_ok=True)
        for frame in frames:
            if frame is None or getattr(frame, "empty", True) or "HOME" not in frame.columns:
                continue
            for date, grp in frame.groupby("GAME_DATE", sort=True):
                date = str(date)
                if not date or date >= before or os.path.exists(path(date, logs_dir)):
                    continue
                if grp["HOME"].isna().any() or grp["OPP"].isna().any():
                    continue        # the venue is not known for every row yet: leave the night for a later run
                players, games = [], {}
                for r in grp.to_dict("records"):
                    home = bool(r["HOME"])
                    team, opp = r.get("TEAM_ABBREVIATION"), r.get("OPP")
                    games.setdefault(str(r["GAME_ID"]), {"game_id": str(r["GAME_ID"]), "home": team if home else opp, "away": opp if home else team})
                    players.append({"player_id": int(r["PLAYER_ID"]), "name": r.get("PLAYER_NAME"), "game_id": str(r["GAME_ID"]),
                                    "team": team, "opponent": opp, "home": home, "starter": None, "position": None,
                                    "min": round(_num(r.get("MIN")), 2), "pts": int(_num(r.get("PTS"))), "reb": int(_num(r.get("REB"))),
                                    "ast": int(_num(r.get("AST"))), "fg3m": int(_num(r.get("FG3M"))), "stl": int(_num(r.get("STL"))),
                                    "blk": int(_num(r.get("BLK"))), "tov": int(_num(r.get("TOV")))})
                tmp = path(date, logs_dir) + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump({"date": date, "source": "gamelogs", "games": sorted(games.values(), key=lambda g: g["game_id"]), "players": players},
                              f, separators=(",", ":"))
                os.replace(tmp, path(date, logs_dir))
                written += 1
        if written:
            logger.info(f"  Box-score history: {written} night(s) written from the league game logs")
    except Exception as e:      # history is never worth a board
        logger.warning(f"  Box-score backfill stopped ({type(e).__name__}: {e})")
    return written


class History:
    """Every player's games on file, newest first, and the nights each team played."""

    def __init__(self, logs_dir: str | None = None):
        self.games_by_id: dict = defaultdict(list)
        self.ids_by_name: dict = defaultdict(set)
        self.ids_by_base: dict = defaultdict(set)
        self.team_nights: dict = defaultdict(set)
        self.nights: set = set()
        for file in sorted(glob.glob(os.path.join(box_dir(logs_dir), "*.json"))):
            try:
                with open(file, encoding="utf-8") as f:
                    night = json.load(f)
            except (OSError, ValueError):
                continue
            date = night.get("date")
            if not date:
                continue
            self.nights.add(date)
            for g in night.get("games") or []:
                for t in (g.get("home"), g.get("away")):
                    if t:
                        self.team_nights[t].add(date)
            for r in night.get("players") or []:
                pid, name = r.get("player_id"), r.get("name") or ""
                if pid is None:
                    continue
                self.games_by_id[pid].append({"date": date, "team": r.get("team"), "opp": r.get("opponent"), "home": r.get("home"),
                                              "min": _num(r.get("min")), "pts": _num(r.get("pts")), "reb": _num(r.get("reb")), "ast": _num(r.get("ast"))})
                self.ids_by_name[norm_name(name)].add(pid)
                self.ids_by_base[norm_name_base(name)].add(pid)
                if r.get("team"):
                    self.team_nights[r["team"]].add(date)
        for games in self.games_by_id.values():
            games.sort(key=lambda g: g["date"], reverse=True)

    def __bool__(self) -> bool:
        return bool(self.nights)

    def _player(self, name: str, team: str | None):
        ids = self.ids_by_name.get(norm_name(name)) or self.ids_by_base.get(norm_name_base(name)) or set()
        if len(ids) == 1:
            return next(iter(ids))
        on_team = [i for i in ids if self.games_by_id[i] and self.games_by_id[i][0]["team"] == team]
        return on_team[0] if len(on_team) == 1 else None      # two players share the name and the team does not settle it

    def games(self, name: str, before: str, team: str | None = None) -> list[dict] | None:
        """The player's games before `before`, newest first; None when the history does not know him."""
        pid = self._player(name, team)
        if pid is None:
            return None
        return [g for g in self.games_by_id[pid] if g["date"] < before]

    def played(self, team: str, date: str) -> bool | None:
        """Did the team play on `date`? None when no night on file is that recent (the history cannot say)."""
        if not self.nights or date not in self.nights and date > max(self.nights):
            return None
        return date in self.team_nights.get(team, ())

    def home_on(self, name: str, date: str, team: str | None = None) -> bool | None:
        """Was the player's team at home on `date` (from that night's own file)?"""
        pid = self._player(name, team)
        for g in self.games_by_id.get(pid, []) if pid is not None else []:
            if g["date"] == date:
                return g["home"]
        return None


def stat_value(game: dict, stat: str) -> float | None:
    parts = STAT_PARTS.get(stat)
    return None if not parts else sum(game[p] for p in parts)


def readings(games: list[dict] | None, stat: str, line, date: str, is_home=None) -> dict:
    """
    What the checklist reads from a player's history, as of `date`:
      over_games / games    games over `line` among the games he PLAYED this
                            season — with fewer than THIN_SEASON of them,
                            last season's are read too
      split_over / split_games   the same count over both seasons, only the
                            games at tonight's venue (home or road)
      minutes               this season's minutes, newest first
    Missing history gives None for each (a check then has no reading).
    """
    out = {"over_games": None, "games": None, "split_over": None, "split_games": None, "minutes": None}
    if games is None or stat not in STAT_PARTS:
        return out
    try:
        line = float(line)
        from datetime import date as _d
        season = season_for_date(_d.fromisoformat(date))
    except (TypeError, ValueError):
        return out
    played = [g for g in games if g["min"] > 0]
    this = [g for g in played if season_for_date(_d.fromisoformat(g["date"])) == season]
    pool = this if len(this) >= THIN_SEASON else played
    out["games"], out["over_games"] = len(pool), sum(1 for g in pool if stat_value(g, stat) > line)
    out["minutes"] = [g["min"] for g in this]
    if is_home is not None:
        venue = [g for g in played if g["home"] is not None and bool(g["home"]) == bool(is_home)]
        out["split_games"], out["split_over"] = len(venue), sum(1 for g in venue if stat_value(g, stat) > line)
    return out
