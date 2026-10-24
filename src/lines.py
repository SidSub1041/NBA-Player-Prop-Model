"""
The line each prop is measured against.

Sportsbooks first: the line most books hang for the prop (The Odds API, see
src/vegas_odds.py), with the best price on each side. The DFS apps
(PrizePicks, Underdog, Pick6… — the same API's `us_dfs` region) are the
backup when no sportsbook lists the prop; Underdog's own feed is the last
resort, used only without an API key. With no line at all, a prop that passes
the conditions goes On Hold and the reason says why.

Fill mode (main.py --fill-holds) locks the lines already on the day's board:
a locked prop keeps its line and prices exactly as the board showed them.
"""

import json
import os
from dataclasses import dataclass, field

from src.underdog import get_line_for_prop, norm_name

STAT_LABELS = {
    "points": "PTS", "rebounds": "REB", "assists": "AST", "pra": "PRA",
    "pts+reb": "PTS+REB", "pts+ast": "PTS+AST", "reb+ast": "REB+AST", "3-pointers made": "3PM",
}
LOGS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
LINE_KEYS = ("line", "source", "books", "over_odds", "over_book", "under_odds", "under_book")


@dataclass
class Resolution:
    line: dict | None                 # the designated line (None -> no line)
    vegas: dict | None = None         # the sportsbooks' consensus for this prop
    vegas_issue: str | None = None    # why the books have none: off / pulled / unpriced / not_offered
    dfs: dict | None = None           # the DFS apps' consensus (backup) for this prop
    dfs_checked: bool = False         # were the DFS apps part of the search?
    ud: dict | None = None            # Underdog's usable line (live, or captured earlier and still valid)
    ud_raw: dict | None = None        # whatever Underdog entry exists, usable or not
    ud_issue: str | None = None       # suspended / pulled
    locked: bool = False              # fill mode: kept from the board as-is
    extra: dict = field(default_factory=dict)


def lock_key(player: str, stat: str) -> tuple[str, str]:
    return (norm_name(player), stat)


def load_board_lines(date: str, logs_dir: str | None = None) -> dict[tuple, dict]:
    """
    The lines already on the day's board (logs/props_{date}.json):
    {(norm player, stat): line_info}. Props without a line are not included
    — they are the holes a fill run tries to fill.
    """
    path = os.path.join(logs_dir or LOGS_DIR, f"props_{date}.json")
    try:
        with open(path, encoding="utf-8") as f:
            board = json.load(f)
    except (OSError, ValueError):
        return {}
    locked: dict[tuple, dict] = {}
    for p in (board.get("all_props") or []) if isinstance(board, dict) else []:
        if not isinstance(p, dict) or p.get("line") is None or not p.get("player_name") or not p.get("stat"):
            continue
        locked[lock_key(p["player_name"], p["stat"])] = {
            "line": p["line"], "source": p.get("line_source") or "vegas", "books": p.get("line_books"),
            "over_odds": p.get("over_odds"), "over_book": p.get("over_book"),
            "under_odds": p.get("under_odds"), "under_book": p.get("under_book"),
        }
    return locked


def usable_underdog(ud: dict | None, team: str, not_started: set[str]) -> tuple[dict | None, str | None]:
    """
    Underdog's line if it can be bet, else (None, why not).

    "suspended": Underdog suspended the line (pending news).
    "pulled": captured earlier today but gone from a live feed while the game
              hasn't tipped — Underdog took it down.
    """
    if not ud:
        return None, None
    if ud.get("suspended"):
        return None, "suspended"
    if ud.get("cached") and team in not_started:
        return None, "pulled"
    return ud, None


def resolve(player: str, team: str, stat: str, vegas, ud_lines: dict, not_started: set[str],
            locked: dict | None = None) -> Resolution:
    """Designated line for one prop: locked (fill mode), else the books', else the DFS apps', else Underdog's, else None."""
    held = (locked or {}).get(lock_key(player, stat))
    v = vegas.lookup(player, stat) if vegas is not None else None
    tier = (v or {}).get("tier", "books")
    books = v if (v and tier == "books") else None
    dfs = v if (v and tier == "dfs") else None
    dfs_checked = bool(vegas is not None and getattr(vegas, "dfs_backup", False))
    ud_raw = get_line_for_prop(ud_lines, player, stat)
    ud, ud_issue = usable_underdog(ud_raw, team, not_started)
    if held:
        line = {k: held.get(k) for k in LINE_KEYS}
        return Resolution(line=line, vegas=books, dfs=dfs, dfs_checked=dfs_checked,
                          ud=ud, ud_raw=ud_raw, ud_issue=ud_issue, locked=True)
    if v:
        line = {
            "line": v["line"], "source": "vegas" if tier == "books" else "dfs", "books": v.get("books"),
            "over_odds": v.get("over_odds"), "over_book": v.get("over_book"),
            "under_odds": v.get("under_odds"), "under_book": v.get("under_book"),
        }
    elif ud:
        line = {
            "line": ud["line"], "source": "underdog", "books": 1,
            "over_odds": ud.get("over_odds") or None, "over_book": "Underdog",
            "under_odds": ud.get("under_odds") or None, "under_book": "Underdog",
        }
    else:
        line = None
    issue = None if v else (vegas.issue(player, team, stat) if vegas is not None else "off")
    return Resolution(line=line, vegas=books, vegas_issue=issue, dfs=dfs, dfs_checked=dfs_checked,
                      ud=ud, ud_raw=ud_raw, ud_issue=ud_issue)


def apply(prop, res: Resolution) -> None:
    """Copy a resolution onto a PropScore (designated line + both sources for display)."""
    prop.line_info = res.line or {}
    if res.ud:
        prop.ud_line = res.ud["line"]
        prop.ud_over_odds = res.ud.get("over_odds") or ""
        prop.ud_under_odds = res.ud.get("under_odds") or ""
    if res.vegas:
        prop.vegas = dict(res.vegas)
    prop.resolution = res


def hold_reason(stat: str, res: Resolution, injury: str | None) -> str:
    """
    'No PTS line: no book or DFS app lists it — GTD · Ankle', or with Underdog's own
    feed in play, 'No PTS line: books pulled theirs before tip; Underdog suspended its 24.5'.
    """
    label = STAT_LABELS.get(stat, stat.upper())
    if res.dfs_checked:
        words = {"pulled": "the books and DFS apps pulled it before tip", "off": "no sportsbook feed",
                 "unpriced": "books and DFS apps not checked this run", "not_offered": "no book or DFS app lists it"}
    else:
        words = {"pulled": "books pulled theirs before tip", "off": "no sportsbook feed",
                 "unpriced": "books not checked this run", "not_offered": "books don't list it"}
    books = words.get(res.vegas_issue, words["not_offered"])
    reason = f"No {label} line: {books}"
    if res.ud_issue == "suspended":
        reason += f"; Underdog suspended its {res.ud_raw['line']}"
    elif res.ud_issue == "pulled":
        reason += f"; Underdog pulled its {res.ud_raw['line']} before tip"
    elif not res.dfs_checked:
        reason += "; not on Underdog"
    return f"{reason} — {injury}" if injury else reason
