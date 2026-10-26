# NBA Player Prop Model

A score-based predictive model that evaluates NBA player props (points, rebounds, assists) each morning using a structured multi-factor methodology.

---

## Methodology

### 1 — Defense vs. Position (DvP)
Scrapes [FantasyPros DvP](https://www.fantasypros.com/daily-fantasy/nba/fanduel-defense-vs-position.php) to find teams highlighted in **green** (easy matchup → over) or **gold** (tough matchup → under) for each position (PG/SG/SF/PF/C) and stat (PTS/REB/AST). Only today's playing teams are considered.

### 2 — Depth Charts
For each highlighted defensive team, the model finds the **starter at that position on the opposing team** via the [ESPN depth chart](https://www.espn.com/nba/depth).

### 3 — Points: Shooting Zone Analysis
- Player's primary shooting zones (≥ 20% of total FGA) via [NBA.com shooting zones](https://www.nba.com/stats/players/shooting?DistanceRange=By+Zone)
- Checks whether the opponent is **top 10** in FG% allowed (for overs) or **bottom 10** (for unders) in each of those zones
- Each qualifying zone = **+1 point**

### 4 — Points: Playtype Analysis
- Player's primary playtypes (≥ 15% frequency) via [NBA.com synergy](https://www.nba.com/stats/players/transition)
- Checks whether the opponent is **top 10** in PPP allowed (for overs) or **bottom 10** (for unders) in each playtype
- Each qualifying playtype = **+1 point**

### 5 — Hit Rate Gate
If ≥ 80% of all conditions pass, the model checks the player's season hit rate **against tonight's line** (see *Lines and odds*): the share of the player's games (NBA.com game logs for the stats season) that cleared the line in the pick's direction — over 24.5 means 25+, under 24.5 means 24 or fewer. Combos use the summed stats against the combo line. A hit rate ≥ 56% is required for an **A+** pick (≥ 50% for **A**). A prop with no line can't be measured against one: if it passes the conditions it goes **On Hold** (below) until a line appears; otherwise it shows an estimate (games beyond the player's own average) on the watchlist.

### Grading
| Grade | Conditions met (REB / AST / REB+AST) | Conditions met (PTS and combos with PTS) | Hit rate |
|-------|------|------|----------|
| A+    | ≥ 80% | ≥ 60% | ≥ 56%    |
| A     | ≥ 80% | ≥ 60% | ≥ 50%    |
| B+    | ≥ 60% | ≥ 45% | ≥ 56%    |
| B     | ≥ 60% | ≥ 45% | any      |
| C/D   | < 60% | < 45% | —        |

Rebounds and assists props carry a single condition (the DvP matchup), so 80% means "the matchup is favorable". Points props are scored on three to seven conditions (shooting zones and playtypes), so they qualify on a **60% majority** of favorable conditions instead; the hit-rate gate against the line is the same for every stat.

Only **A+ and A** picks are flagged as valid.

### 6 — On Hold: no line, no pick
A prop that meets its conditions but has **no line** — no sportsbook hangs it and no DFS app does either — can't be measured, so it goes **On Hold** instead of being graded. Each hold says why, e.g. *No PTS line: no book or DFS app lists it — GTD · Ankle* or *No REB line: the books and DFS apps pulled it before tip* (injury designations come from the CBS injury report). The morning run and the two afternoon fill runs re-check the holds, and a hold becomes a pick (or drops) as soon as a line appears. A starter listed **OUT** is replaced by his backup before any of this, and the backup is evaluated against his own line. Holds are never graded.

### 7 — The learning loop: it learns from its own results
Every morning, after grading, the model studies the night before:

1. **Label** — every prop that had a line (not just the published picks, ~100 a night) is marked hit or miss from the box scores (`logs/training/`). Older boards are backfilled automatically; a night whose box scores came back incomplete is retried on later runs rather than saved half-labeled.
2. **Retrain** — a supervised model (L2-regularized logistic regression, recent nights weighted more, always anchored to today) learns P(hit) from what was known at pick time: hit rate against the line, season average vs the line, the price's implied probability, share of conditions met, stat type, over/under, line source, books hanging the line, injury tag and sample size (`logs/learner.json`). Features are recomputed from each stored board, so a definition change never mixes old and new encodings. It retrains only when new labels exist.
3. **Check itself honestly** — walk-forward: each night is predicted by a model trained only on the nights *before* it, and compared with two baselines, the average hit rate and the de-vigged book price. To turn **active** it must, on *effective* evidence of ≥ 300 props over ≥ 10 nights (recent nights count most), beat the stronger baseline by ≥ 1% with a statistically clear margin, its EV rule must not have cost units on past published picks, and all of that must hold on three retrains in a row. Until then it runs in **shadow** mode: its probability shows on every card, nothing changes. Once active it only drops back after two failing retrains in a row.
4. **Act** — once active, a valid pick must also be **+EV**: the model's P(hit) must beat the break-even probability of the pick's price (settled the same way the grader pays), or the pick is cut — and listed on the board under *Cut by the model* with the reason. Valid picks are then listed best expected value first.
5. **Post-mortem** — what went right (a segment that would beat a −110 price), what went wrong and the biggest misses for each night (`logs/postmortem_{date}.json`), shown on the site in *The Film Room* with the model's current top factors (real, varying inputs only).

Retraining after every run does not overfit: each retrain refits from scratch on all labels, and the gate above decides whether it may act. It replaces the earlier adaptive multipliers (`src/adaptive.py`, no longer used).

### Lines and odds: sportsbooks first, DFS apps as backup
Every prop — points, rebounds, assists and all four combos (PTS+REB+AST, PTS+REB, PTS+AST, REB+AST) — is measured against the **sportsbook line** from [The Odds API](https://the-odds-api.com): the line most books hang for that prop, with the best price on each side and the book offering it (DraftKings, FanDuel, BetMGM, BetRivers, Bovada, BetOnline — the API's `us` region). When no sportsbook lists a prop, the model falls back to the **DFS pick'em apps** from the same API — PrizePicks, Underdog, DraftKings Pick6, Betr, Dabble (the `us_dfs` region): the line most of them hang, labelled *dfs backup* on the card. Only without an API key does the model read Underdog's own feed. Picks are graded against that same line and price.

Prices are pre-game: once a game tips, the board keeps the last price bought before tip-off. If a prop is pulled before tip (usually injury news), it goes On Hold.

**Setup**: get a key at the-odds-api.com, add it as the `ODDS_API_KEY` repository secret, and run **Actions → Run workflow → task: probe** (its Vegas step prices one game for the seven markets the model uses, once for the sportsbooks and once for the DFS apps: 14 credits). Without a key — or once the plan's credits run out — lines already bought earlier that day are kept until they can be re-priced, and props without one go On Hold. `ODDS_API_REGIONS` (default `us`) and `ODDS_API_DFS_REGIONS` (default `us_dfs`; blank turns the DFS backup off) choose the regions; `us2` adds Hard Rock Bet and ReBet to the books.

**Credits**: listing the slate is free; pricing a game costs one credit per market per region requested. Each run buys only the markets its candidate props need, sportsbooks first, and then the DFS apps *only for the markets where a wanted player still has no sportsbook line* — so a star-heavy slate costs little extra and a bench-heavy one roughly doubles. Everything bought is saved in `logs/vegas_{date}.json`, so a later run re-buys a market only when its price is more than 2½ hours old (`ODDS_API_REFRESH_MINUTES`; 45 minutes within 3 hours of tip). Fill runs (below) buy only markets that still have a hole, and re-buy those once they are an hour old (`ODDS_API_FILL_REFRESH_MINUTES`), since their last purchase is what left the hole. If the first two games of a slate more than 8 hours from tip-off come back with nothing from any book or app, the run stops buying — nobody has posted the slate yet (`ODDS_API_EMPTY_STOP_EVENTS`, `ODDS_API_EMPTY_STOP_HOURS`). Expect roughly 80–200 credits per full 10–12-game day across the four runs, far fewer on light nights — a few thousand a month, so the 500-credit free plan lasts about three days; a paid plan covers the season. A run that aborts still keeps what it bought. A run stops buying at `ODDS_API_MAX_CREDITS_PER_RUN` (default 150) or when fewer than `ODDS_API_MIN_REMAINING` (default 25) credits are left.

---

## Where the Model Runs

`stats.nba.com` silently blocks most cloud datacenter IP ranges (GitHub Actions runners included): requests hang until they time out. The model and the grader both need it, so the pipeline separates **producing the data** from **publishing the site** (GitHub Actions always handles publishing: any data push redeploys within ~2 minutes). Pick one producer:

| Option | What it is | Cost / effort |
|--------|------------|---------------|
| **A. GitHub Actions + proxy** (recommended) | The existing schedule becomes the live server: add an `NBA_PROXY` repository secret (`http://user:pass@host:port` from a residential or ISP proxy plan — datacenter proxies are usually blocked too) and the scheduled runs build each slate's board twice — the night before (~10:30 PM ET) and the morning of (~9:15 AM ET) — plus the 5:30 AM ET grading run. No machine to maintain. | A proxy plan (typically a few dollars a month); 2 minutes to set up |
| **B. Any always-on Linux box** | A VPS, Raspberry Pi or home server with an unblocked IP runs `./setup_cron.sh`, which schedules `run_daily.sh` at 9:15, 12:00, 15:00 and 18:00 and pushes the data. | The box; needs non-interactive `git push` (deploy key or token) |
| **C. Your Mac** | `./setup_mac.sh` installs a LaunchAgent for `run_daily.sh` at the same times. Works only while the Mac is awake (launchd runs missed jobs on wake). | Free, but tied to the laptop |

**Test before you trust**: `python3 probe_nba.py` reports PASS/FAIL for the NBA API from any host (or through `NBA_PROXY`). On GitHub, use **Actions → Run workflow → task: probe** to test a proxy secret from the runner itself in about a minute.

**Setting up option A**: repo → Settings → Secrets and variables → Actions → *New repository secret* → name `NBA_PROXY`, value the proxy URL. Run the probe task; once it passes, the next scheduled run produces the board. Retire the Mac runner with `launchctl unload ~/Library/LaunchAgents/com.nba.prop.model.plist` so two producers don't race.

**Schedule (Actions)**: the board for each slate is built **the night before** (~10:30 PM ET, `python main.py --tomorrow`, on the lines the books post for the next day) and **refreshed the morning of** (~9:15 AM ET: books re-priced, holds and injuries re-checked). Picks made by then are left alone — what you bet on is what the board showed. Two **fill runs** (~1 PM and ~5 PM ET, `python main.py --fill-holds`) then only look for lines for the props still On Hold, as the books and DFS apps post bench and late props through the day: every line already on the board is locked (never re-priced, never dropped because a book moved or pulled it), and the board's header says how many lines were kept. Grading runs at ~5:30 AM ET. Crons are UTC, so winter times are an hour earlier. From **Actions → Run workflow**, `task: fill` runs a fill pass by hand.

Safety net on every path: if `stats.nba.com` stops answering mid-run, the model aborts **before writing anything** (it never publishes a board built from fallbacks alone), the failing step shows red on the run, and no data is committed.

**Underdog's own feed (last resort)**: without an `ODDS_API_KEY`, Underdog lines come from its public board, fetched directly and through `NBA_PROXY` only if Underdog can't be reached. Its versioned paths (`beta/v5`, `v6`) were retired in October 2026 and the unversioned `v1/over_under_lines` path followed on October 24 (all answer HTTP 426), so this path currently yields no lines — the DFS region of The Odds API carries Underdog's lines instead. When the feed does answer: only pre-game lines for the run date's NBA games are used — tomorrow's back-to-back lines, live and season-long markets are skipped, a suspended or pulled line doesn't count, and the standard two-sided line wins over promo or alternate lines. Players match on normalized names (initials, apostrophes, accents and Jr./III suffixes all match). `python3 probe_underdog.py` — also part of the Actions **probe** task — lists every NBA stat Underdog is offering.

---

## Season Handling

The season is derived from the run date (October starts a new season), so the model rolls over on its own each fall — no constants to bump. Override for backfills or tests with `NBA_SEASON=2025-26 python main.py`. The site splits results by season and shows the previous season's record in a side panel.

**Early season:** until every team has played 5 games (`NBA_MIN_GAMES`), the new season has no usable statistics, so the model builds its boards from the previous season's stats and says so in the site's verification strip (`STATS 2025-26 · EARLY SEASON`). Rosters always come from the current season. Force a statistics season with `NBA_STATS_SEASON=2025-26`.

---

## Setup

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run the model manually
python main.py

# 3. Run for a specific date
python main.py --date 2026-04-08

# 4. Fast mode (skips playtype rankings — fewer API calls)
python main.py --fast
```

---

## Automated Daily Run

### Mac (recommended) — LaunchAgent
Uses macOS's native LaunchAgent system (survives reboots, no terminal needed):
```bash
chmod +x setup_mac.sh
./setup_mac.sh
```
This installs everything, verifies imports, and registers a LaunchAgent that runs `run_daily.sh` at **9:15 AM, 12 PM, 3 PM and 6 PM daily** (set `MODEL_ARGS=--fast` to skip playtype analysis). To trigger manually anytime:
```bash
launchctl start com.nba.prop.model
```
To remove:
```bash
launchctl unload ~/Library/LaunchAgents/com.nba.prop.model.plist
rm ~/Library/LaunchAgents/com.nba.prop.model.plist
```

### Linux — cron (VPS, Raspberry Pi, home server)
```bash
python3 probe_nba.py     # confirm this host can reach the NBA API first
chmod +x setup_cron.sh
./setup_cron.sh          # schedules run_daily.sh at 9:15, 12:00, 15:00, 18:00
./setup_cron.sh --remove # removes the jobs
```

### Any OS — Python scheduler (keep terminal open)
```bash
python scheduler.py                  # Runs at 9:00 AM daily
python scheduler.py --time 08:30     # Custom time
python scheduler.py --run-now        # Run immediately, then schedule
```

---

## Output

Reports are saved to `logs/props_YYYY-MM-DD.txt` and printed to stdout. Example:

```
════════════════════════════════════════════════════════════════════════
  NBA PLAYER PROP MODEL — 2026-04-08
════════════════════════════════════════════════════════════════════════

  Valid picks (A/A+)  : 3
  Watchlist (B/B+)    : 5

  ✅  VALID PICKS
  ╭──────────────────┬──────┬─────┬─────┬───────┬───────┬───────┬─────┬────╮
  │ Player           │ Team │ Opp │ Pos │ Stat  │ Edge  │ Score │ HR  │ Gr │
  ├──────────────────┼──────┼─────┼─────┼───────┼───────┼───────┼─────┼────┤
  │ Jayson Tatum     │ BOS  │ MIA │ SF  │ POINTS│ OVER  │ 7/8   │ 62% │ A+ │
  ╰──────────────────┴──────┴─────┴─────┴───────┴───────┴───────┴─────┴────╯
```

---

## Project Structure

```
NBA-Player-Prop-Model/
├── main.py              # Main pipeline orchestrator
├── grade.py             # Grades yesterday's picks against box scores
├── export_csv.py        # Flattens JSON output to CSV for BI dashboards
├── run_daily.sh         # Game-day runner: grade → model → export → push
├── probe_nba.py         # Can this host / proxy reach stats.nba.com?
├── probe_underdog.py    # What does Underdog's lines feed return from here?
├── probe_vegas.py       # Is ODDS_API_KEY working; do the books hang every market the model uses?
├── scheduler.py         # Python-based daily scheduler
├── setup_mac.sh         # One-command Mac setup + LaunchAgent installer
├── setup_cron.sh        # Linux cron installer
├── requirements.txt
├── src/
│   ├── config.py        # Constants, thresholds, season derivation
│   ├── season.py        # Which season's stats to use (early-season fallback)
│   ├── nba_http.py      # Timeouts, retries, proxy, circuit breaker for the NBA API
│   ├── games_today.py   # Today's NBA schedule (NBA API)
│   ├── dvp_scraper.py   # FantasyPros DvP scraper
│   ├── depth_charts.py  # ESPN depth chart scraper
│   ├── shooting_zones.py# Player/team shooting zone analysis
│   ├── playtypes.py     # Player/team playtype analysis
│   ├── hitrate.py       # Hit rate vs the line, from NBA.com game logs
│   ├── vegas_odds.py    # Sportsbook lines + prices (The Odds API) — primary line source
│   ├── lines.py         # The line each prop is measured against: books first, DFS apps backup
│   ├── learner.py       # The learning loop: labels every prop nightly, retrains, gates -EV picks, post-mortems
│   ├── scoring_engine.py# Core scoring logic
│   └── output.py        # Report formatting
└── logs/                # Daily reports and run logs
```

---

## Live Dashboard

- **Tableau Public** — interactive results dashboard (cumulative units, daily win rate, splits, pick explorer): [View on Tableau](https://public.tableau.com/app/profile/sidharth.subramanian7031/viz/NBAPlayerPropModel/Dashboard1)
- **Site** — daily picks and graded results: https://sidsub1041.github.io/NBA-Player-Prop-Model/. The page leads with **Where It's Ahead** — every cut of the season's graded record (by grade, side, stat, single vs combo) that is above break-even (52.4%, what a −110 price needs) or in profit on 8+ picks, plus the best night, any winning streak and a profitable last season — each shown as its W–L with hit rate, units and sample size, under a header that states the full season record and the break-even figure. Each grade group on the board also shows that grade's season record. Then the KPI row (plays today, last night, season W–L with units, nights graded), then **the board ranked best first**: A+ plays before A, and within a grade by hit rate against the line, then conditions met, then books hanging the line (expected value instead, once the learner is active); every card carries its rank and the #1 play is tagged. The same order is in `latest.json` (`rank`) and `picks.csv`.
- **Data feeds** — CSV fact tables in `web/public/data/` (`picks.csv`, `graded_picks.csv`, `daily_results.csv`), refreshed automatically by GitHub Actions. They are written as UTF-8 with a BOM (`utf-8-sig`) so accented player names open cleanly in Excel and Tableau; in pandas read them with `encoding="utf-8-sig"`. Every row carries a `season` column (e.g. `2026-27`).

---

## Credits

Built by **Sid Subramanian**.
