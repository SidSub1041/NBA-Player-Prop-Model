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
If ≥ 80% of all conditions pass, the model checks the player's season hit rate on [linemate.com](https://linemate.com). A hit rate ≥ 56% is required for a **valid pick**.

### Grading
| Grade | Conditions met | Hit rate |
|-------|---------------|----------|
| A+    | ≥ 80%         | ≥ 56%    |
| A     | ≥ 80%         | ≥ 50%    |
| B+    | ≥ 60%         | ≥ 56%    |
| B     | ≥ 60%         | any      |
| C/D   | < 60%         | —        |

Only **A+ and A** picks are flagged as valid.

---

## Where the Model Runs

`stats.nba.com` silently blocks most cloud datacenter IP ranges (GitHub Actions runners included): requests hang until they time out. The model and the grader both need it, so the pipeline separates **producing the data** from **publishing the site** (GitHub Actions always handles publishing: any data push redeploys within ~2 minutes). Pick one producer:

| Option | What it is | Cost / effort |
|--------|------------|---------------|
| **A. GitHub Actions + proxy** (recommended) | The existing schedule becomes the live server: add an `NBA_PROXY` repository secret (`http://user:pass@host:port` from a residential or ISP proxy plan — datacenter proxies are usually blocked too) and the scheduled runs execute the model 4×/day plus the 5:30 AM ET grading run. No machine to maintain. | A proxy plan (typically a few dollars a month); 2 minutes to set up |
| **B. Any always-on Linux box** | A VPS, Raspberry Pi or home server with an unblocked IP runs `./setup_cron.sh`, which schedules `run_daily.sh` at 9:15, 12:00, 15:00 and 18:00 and pushes the data. | The box; needs non-interactive `git push` (deploy key or token) |
| **C. Your Mac** | `./setup_mac.sh` installs a LaunchAgent for `run_daily.sh` at the same times. Works only while the Mac is awake (launchd runs missed jobs on wake). | Free, but tied to the laptop |

**Test before you trust**: `python3 probe_nba.py` reports PASS/FAIL for the NBA API from any host (or through `NBA_PROXY`). On GitHub, use **Actions → Run workflow → task: probe** to test a proxy secret from the runner itself in about a minute.

**Setting up option A**: repo → Settings → Secrets and variables → Actions → *New repository secret* → name `NBA_PROXY`, value the proxy URL. Run the probe task; once it passes, the next scheduled run produces the board. Retire the Mac runner with `launchctl unload ~/Library/LaunchAgents/com.nba.prop.model.plist` so two producers don't race.

Safety net on every path: if `stats.nba.com` stops answering mid-run, the model aborts **before writing anything** (it never publishes a board built from fallbacks alone), the failing step shows red on the run, and no data is committed.

**Lines on the board**: Underdog lines are fetched live from whichever of its pick'em endpoints currently answers (directly, then through `NBA_PROXY` if the runner is blocked), matched on normalized names (initials, apostrophes, accents and Jr./III suffixes all match), and lines captured by an earlier run that day are kept when a later run can't see them (Underdog pulls a line at tip-off). If a board shows `—` for every line, run `python3 probe_underdog.py` — also part of the Actions **probe** task — to see exactly what each endpoint returns.

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
│   ├── hitrate.py       # Linemate.com hit rate fetcher
│   ├── scoring_engine.py# Core scoring logic
│   └── output.py        # Report formatting
└── logs/                # Daily reports and run logs
```

---

## Live Dashboard

- **Tableau Public** — interactive results dashboard (cumulative units, daily win rate, splits, pick explorer): [View on Tableau](https://public.tableau.com/app/profile/sidharth.subramanian7031/viz/NBAPlayerPropModel/Dashboard1)
- **Site** — daily picks and graded results: https://sidsub1041.github.io/NBA-Player-Prop-Model/
- **Data feeds** — CSV fact tables in `web/public/data/` (`picks.csv`, `graded_picks.csv`, `daily_results.csv`), refreshed automatically by GitHub Actions. They are written as UTF-8 with a BOM (`utf-8-sig`) so accented player names open cleanly in Excel and Tableau; in pandas read them with `encoding="utf-8-sig"`. Every row carries a `season` column (e.g. `2026-27`).

---

## Credits

Built by **Sid Subramanian**.
