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
from src.config import MIN_EDGE, MODEL_TRUST, STRONG_EDGE, WATCH_EDGE

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


def main_line(pick: dict) -> dict:
    """
    The regular ("pick'em") line a board prop is published and graded on:
    {"line", "over_odds", "under_odds", "from_alt"}.

    Since round 20 a prop's own line IS the consensus main line, and a better
    number at one book is shown beside it as "alt_line" without replacing it.
    Boards before that could publish a shopped number — often a book's
    alternate line at +200 or -250 — with the consensus kept in
    consensus_line / vegas_*: for those the consensus line and its prices are
    the regular line (from_alt True). Pre-Oct-22 Underdog-only boards carry
    ud_line. A prop with no line returns line None.
    """
    if "line" not in pick:
        return {"line": pick.get("ud_line"), "over_odds": pick.get("ud_over_odds"),
                "under_odds": pick.get("ud_under_odds"), "from_alt": False}
    if pick.get("line_shopped") and pick.get("vegas_line") is not None:
        return {"line": pick.get("vegas_line"), "over_odds": pick.get("vegas_over_odds"),
                "under_odds": pick.get("vegas_under_odds"), "from_alt": True}
    return {"line": pick.get("line"), "over_odds": pick.get("over_odds"),
            "under_odds": pick.get("under_odds"), "from_alt": False}


def alt_of(pick: dict) -> dict | None:
    """
    The alternate number a board prop carries, or None: the round-20
    "alt_line" dict, or — on an older board that published a shopped number —
    that number ({"book", "line", "price"}).
    """
    if isinstance(pick.get("alt_line"), dict):
        return pick["alt_line"]
    if pick.get("line_shopped") and pick.get("vegas_line") is not None and pick.get("line") != pick.get("vegas_line"):
        under = str(pick.get("edge", "")).lower() == "under"
        return {"book": pick.get("under_book") if under else pick.get("over_book"), "line": pick.get("line"),
                "price": pick.get("under_odds") if under else pick.get("over_odds"), "kind": "shopped"}
    return None


def grade_for_edge(edge) -> str | None:
    """The letter grade an edge earns (the rule in PropScore.compute_edge_grade); None without an edge."""
    try:
        e = float(edge)
    except (TypeError, ValueError):
        return None
    if e != e:
        return None
    if e >= STRONG_EDGE:
        return "A+"
    if e >= MIN_EDGE:
        return "A"
    if e >= WATCH_EDGE:
        return "B+"
    return "B" if e > 0 else "C"


def repair_main_line_fields(entry: dict) -> dict:
    """
    Re-derive every per-pick number of a prop dict AT ITS OWN LINE AND PRICES
    (in place, and returned): p_model from proj_mu / proj_sd / the stat's
    distribution, p_market from the de-vigged prices (a push taken out of
    both at an integer line), breakeven_price from the side's price, p_hat
    (MODEL_TRUST blend), edge_value and the grade from those; market_basis
    "pick". The hit rate was measured against another number, so it is
    cleared (hitrate None, hitrate_source "unavailable", games_hit None).
    Used when a pick that was published on a shopped alternate (boards before
    round 20) is moved to its regular line, so the numbers beside the line
    describe that line. Idempotent. Without a projection the probability
    fields are left as they are.
    """
    from src.projection import implied_prob, market_prob, p_sides
    side = str(entry.get("edge", "")).lower()
    try:
        line = float(entry.get("line"))
    except (TypeError, ValueError):
        return entry
    under = side == "under"
    side_odds = entry.get("under_odds") if under else entry.get("over_odds")
    be = implied_prob(side_odds)
    mu, sd = entry.get("proj_mu"), entry.get("proj_sd")
    p_model = p_push = None
    try:
        if mu is not None and sd:
            po, pu, p_push = p_sides(entry.get("stat", ""), float(mu), float(sd), line)
            p_model = pu if under else po
    except (TypeError, ValueError):
        p_model = p_push = None
    mp = market_prob(entry.get("over_odds"), entry.get("under_odds"))
    p_market = None
    if mp is not None:
        p_market = (mp[1] if under else mp[0]) * (1.0 - (p_push or 0.0))
    entry["breakeven_price"] = round(be, 4) if be is not None else None
    entry["hitrate"], entry["games_hit"], entry["hitrate_source"] = None, None, "unavailable"
    entry["market_basis"] = "pick" if mp is not None else None
    if p_model is None:
        return entry
    entry["p_model"] = round(p_model, 4)
    if p_market is not None:
        p_hat = MODEL_TRUST * p_model + (1.0 - MODEL_TRUST) * p_market
        entry["p_market"] = round(p_market, 4)
        entry["p_hat"] = round(p_hat, 4)
        if be is not None:
            entry["edge_value"] = round(p_hat - be, 4)
            entry["grade"] = grade_for_edge(entry["edge_value"]) or entry.get("grade")
    return entry


def on_main_line(pick: dict) -> dict:
    """
    The prop as a dict on its REGULAR line. A pick published on a shopped
    alternate (boards before round 20: line_shopped with vegas_*) gets the
    consensus line, its prices, the side's book and book count, the shopped
    number under "alt_line" (alt_of) and every per-pick number re-derived at
    that line (repair_main_line_fields). Anything else comes back as a
    shallow copy (its alt_line copied too, so callers may annotate it).
    Shared by the grader, the learner's snapshots and the CSV export, so the
    three agree field for field.
    """
    out = dict(pick)
    if isinstance(out.get("alt_line"), dict):
        out["alt_line"] = dict(out["alt_line"])
    m = main_line(pick)
    if not m["from_alt"]:
        return out
    under = str(pick.get("edge", "")).lower() == "under"
    side_book, other_book = ("under_book", "over_book") if under else ("over_book", "under_book")
    out.update({"line": m["line"], "over_odds": m["over_odds"], "under_odds": m["under_odds"],
                side_book: pick.get("vegas_book"), other_book: None, "line_book": pick.get("vegas_book"),
                "line_books": pick.get("vegas_books"), "line_shopped": False, "alt_line": alt_of(pick)})
    return repair_main_line_fields(out)


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
