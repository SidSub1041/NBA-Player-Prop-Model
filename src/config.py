"""
Configuration constants for the NBA Player Prop Model.
"""

import os
from datetime import date as _date


def slate_today() -> str:
    """Today's NBA slate date (YYYY-MM-DD) in US Eastern — the date the league
    uses for a night's games, whatever the server's clock (UTC on Actions) says."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo("America/New_York")).date().isoformat()


def season_for_date(d: _date) -> str:
    """
    NBA season label for a calendar date, e.g. 2026-10-20 -> "2026-27".

    A season starts in October; July-September map to the season that
    just ended (whose stats the API still serves).
    """
    start = d.year if d.month >= 10 else d.year - 1
    return f"{start}-{(start + 1) % 100:02d}"


def previous_season(season: str) -> str:
    """"2026-27" -> "2025-26"."""
    start = int(season[:4]) - 1
    return f"{start}-{(start + 1) % 100:02d}"


# Derived from today's date so the model rolls into each new season on its
# own. Override with NBA_SEASON=2025-26 for backfills and tests.
SEASON = os.environ.get("NBA_SEASON") or season_for_date(_date.today())
SEASON_NBA_API = SEASON
SEASON_TYPE = "Regular Season"

# NBA.com Stats API headers (required to avoid 403)
NBA_STATS_HEADERS = {
    "Host": "stats.nba.com",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Referer": "https://www.nba.com/",
    "Origin": "https://www.nba.com",
    "x-nba-stats-origin": "stats",
    "x-nba-stats-token": "true",
}

ESPN_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}

FANTASYPROS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}

# Position mapping
POSITION_MAP = {
    "PG": "Guard",
    "SG": "Guard",
    "SF": "Forward",
    "PF": "Forward",
    "C": "Center",
}

# Scoring thresholds
FGA_ZONE_THRESHOLD = 0.20       # >= 20% of total FGA in a zone
PLAYTYPE_FREQ_THRESHOLD = 0.15  # >= 15% frequency in a playtype
TOP_BOTTOM_RANK = 10            # Top/bottom 10 for rankings
CONDITION_PASS_RATE = 0.80      # >= 80% of conditions must be met (rebounds, assists, REB+AST)
# Points props are scored on 3-7 matchup conditions (shooting zones + playtypes)
# while rebounds/assists have one, so points use a majority bar instead.
CONDITION_PASS_RATE_POINTS = 0.60   # points, and combos that include points
POINTS_STATS = {"points", "pts+reb", "pts+ast", "pra"}
HITRATE_THRESHOLD = 0.56        # legacy A+ hit-rate bar (display + learner feature only since round 18)

# ── Round 18: the edge model ──────────────────────────────────────────────
# A pick is the side whose modelled probability beats the de-vigged market
# probability at the best price by at least MIN_EDGE. Thresholds are env-
# overridable so they can be re-tuned without a code change.
def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


MODEL_TRUST = _env_float("MODEL_TRUST", 0.5)        # weight of the model's probability vs the market's in p_hat
MIN_EDGE = _env_float("MIN_EDGE", 0.03)             # A: p_hat − the pick price's break-even (net of vig)
STRONG_EDGE = _env_float("STRONG_EDGE", 0.06)       # A+
WATCH_EDGE = _env_float("WATCH_EDGE", 0.015)        # B+ (B: any positive edge)
MAX_PRICE = _env_int("MAX_PRICE", -121)             # skip prices worse than this unless edge >= STRONG_EDGE
MIN_BOOKS = _env_int("MIN_BOOKS", 3)                # sportsbooks at the consensus line
MIN_LINE = _env_float("MIN_LINE", 4.0)              # skip lines below this (tiny-count props) unless edge >= STRONG_EDGE
MIN_PROJ_MINUTES = _env_float("MIN_PROJ_MINUTES", 24.0)
MIN_CANDIDATE_MPG = _env_float("MIN_CANDIDATE_MPG", 20.0)   # who gets projected at all
BLEND_K = _env_float("BLEND_K", 10.0)               # this season's weight = n / (n + K)
FORM_HALF_LIFE = _env_float("FORM_HALF_LIFE", 10.0) # games; recent games weigh more within a season
DVP_SHRINK = _env_float("DVP_SHRINK", 0.5)          # how much of the opponent's by-position deviation to apply
MATCHUP_WEIGHT = _env_float("MATCHUP_WEIGHT", 0.02) # points: multiplier per unit of zone/playtype z-score
PACE_WEIGHT = _env_float("PACE_WEIGHT", 1.0)        # 1 = full pace scaling, 0 = none
TEAMMATE_OUT_BOOST = _env_float("TEAMMATE_OUT_BOOST", 0.03)   # per OUT starter on the player's own team
TEAMMATE_OUT_MAX = _env_int("TEAMMATE_OUT_MAX", 2)
MAX_PICKS = _env_int("MAX_PICKS", 15)
MAX_PER_GAME = _env_int("MAX_PER_GAME", 3)
MAX_PER_PLAYER = _env_int("MAX_PER_PLAYER", 1)      # singles; one combo per player on top
PAUSED_STATS = {s.strip() for s in os.environ.get("PAUSED_STATS", "assists").split(",") if s.strip()}
SNAPSHOT_WINDOW_MINUTES = _env_int("SNAPSHOT_WINDOW_MINUTES", 45)   # --snapshot: re-price games tipping within this (runs every 30 min)
CLOSE_WINDOW_MINUTES = _env_int("CLOSE_WINDOW_MINUTES", 90)         # a close counts only if the market was re-priced this close to tip
VAR_PRIOR_K = _env_float("VAR_PRIOR_K", 8.0)        # games of 'typical spread' prior mixed into a player's sample variance
COMBO_RHO = _env_float("COMBO_RHO", 0.25)           # correlation between a player's stats when summing a combo
EDGE_STATS = ("points", "rebounds", "assists")      # single-stat markets the edge model projects

# Shooting zones from NBA.com
SHOOTING_ZONES = [
    "Restricted Area",
    "In The Paint (Non-RA)",
    "Mid-Range",
    "Left Corner 3",
    "Right Corner 3",
    "Above the Break 3",
    "Backcourt",
]

# Playtype categories from NBA.com synergy
PLAYTYPE_CATEGORIES = [
    "Transition",
    "Isolation",
    "PRBallHandler",
    "PRRollMan",
    "Postup",
    "Spotup",
    "Handoff",
    "Cut",
    "OffScreen",
    "OffRebound",
    "Misc",
]

# NBA team abbreviation mapping
TEAM_ABBREVS = {
    "Atlanta Hawks": "ATL", "Boston Celtics": "BOS", "Brooklyn Nets": "BKN",
    "Charlotte Hornets": "CHA", "Chicago Bulls": "CHI", "Cleveland Cavaliers": "CLE",
    "Dallas Mavericks": "DAL", "Denver Nuggets": "DEN", "Detroit Pistons": "DET",
    "Golden State Warriors": "GSW", "Houston Rockets": "HOU", "Indiana Pacers": "IND",
    "LA Clippers": "LAC", "Los Angeles Lakers": "LAL", "Memphis Grizzlies": "MEM",
    "Miami Heat": "MIA", "Milwaukee Bucks": "MIL", "Minnesota Timberwolves": "MIN",
    "New Orleans Pelicans": "NOP", "New York Knicks": "NYK",
    "Oklahoma City Thunder": "OKC", "Orlando Magic": "ORL",
    "Philadelphia 76ers": "PHI", "Phoenix Suns": "PHX", "Portland Trail Blazers": "POR",
    "Sacramento Kings": "SAC", "San Antonio Spurs": "SAS", "Toronto Raptors": "TOR",
    "Utah Jazz": "UTA", "Washington Wizards": "WAS",
}

ABBREV_TO_FULL = {v: k for k, v in TEAM_ABBREVS.items()}

# NBA.com team IDs
NBA_TEAM_IDS = {
    "ATL": 1610612737, "BOS": 1610612738, "BKN": 1610612751, "CHA": 1610612766,
    "CHI": 1610612741, "CLE": 1610612739, "DAL": 1610612742, "DEN": 1610612743,
    "DET": 1610612765, "GSW": 1610612744, "HOU": 1610612745, "IND": 1610612754,
    "LAC": 1610612746, "LAL": 1610612747, "MEM": 1610612763, "MIA": 1610612748,
    "MIL": 1610612749, "MIN": 1610612750, "NOP": 1610612740, "NYK": 1610612752,
    "OKC": 1610612760, "ORL": 1610612753, "PHI": 1610612755, "PHX": 1610612756,
    "POR": 1610612757, "SAC": 1610612758, "SAS": 1610612759, "TOR": 1610612761,
    "UTA": 1610612762, "WAS": 1610612764,
}
