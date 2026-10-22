"""
The line each prop is measured against.

Sportsbooks first: the line most books hang for the prop (The Odds API, see
src/vegas_odds.py), with the best price on each side. Underdog is the backup,
used only when the books have no line for the prop. With neither, a prop that
passes the conditions goes On Hold and the reason says why.
"""

from dataclasses import dataclass, field

from src.underdog import get_line_for_prop

STAT_LABELS = {
    "points": "PTS", "rebounds": "REB", "assists": "AST", "pra": "PRA",
    "pts+reb": "PTS+REB", "pts+ast": "PTS+AST", "reb+ast": "REB+AST", "3-pointers made": "3PM",
}


@dataclass
class Resolution:
    line: dict | None                 # the designated line (None -> no line)
    vegas: dict | None = None         # the books' consensus for this prop
    vegas_issue: str | None = None    # why the books have none: off / pulled / unpriced / not_offered
    ud: dict | None = None            # Underdog's usable line (live, or captured earlier and still valid)
    ud_raw: dict | None = None        # whatever Underdog entry exists, usable or not
    ud_issue: str | None = None       # suspended / pulled
    extra: dict = field(default_factory=dict)


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


def resolve(player: str, team: str, stat: str, vegas, ud_lines: dict, not_started: set[str]) -> Resolution:
    """Designated line for one prop: the books' line, else Underdog's, else None."""
    v = vegas.lookup(player, stat) if vegas is not None else None
    ud_raw = get_line_for_prop(ud_lines, player, stat)
    ud, ud_issue = usable_underdog(ud_raw, team, not_started)
    if v:
        line = {
            "line": v["line"], "source": "vegas", "books": v.get("books"),
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
    return Resolution(line=line, vegas=v, vegas_issue=issue, ud=ud, ud_raw=ud_raw, ud_issue=ud_issue)


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
    """'No PTS line: books pulled theirs before tip; Underdog suspended its 24.5 — GTD · Ankle'."""
    label = STAT_LABELS.get(stat, stat.upper())
    books = {
        "pulled": "books pulled theirs before tip",
        "off": "no sportsbook feed",
        "unpriced": "books not checked this run",
        "not_offered": "books don't list it",
    }.get(res.vegas_issue, "books don't list it")
    if res.ud_issue == "suspended":
        under = f"Underdog suspended its {res.ud_raw['line']}"
    elif res.ud_issue == "pulled":
        under = f"Underdog pulled its {res.ud_raw['line']} before tip"
    else:
        under = "not on Underdog"
    reason = f"No {label} line: {books}; {under}"
    return f"{reason} — {injury}" if injury else reason
