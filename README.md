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
This installs everything, verifies imports, and registers a LaunchAgent that runs at **9:00 AM daily**. To trigger manually anytime:
```bash
launchctl start com.nba.prop.model
```
To remove:
```bash
launchctl unload ~/Library/LaunchAgents/com.nba.prop.model.plist
rm ~/Library/LaunchAgents/com.nba.prop.model.plist
```

### Linux — cron
```bash
chmod +x setup_cron.sh
./setup_cron.sh          # Installs a 9:00 AM cron job
./setup_cron.sh --remove # Removes it
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
├── scheduler.py         # Python-based daily scheduler
├── setup_mac.sh         # One-command Mac setup + LaunchAgent installer
├── setup_cron.sh        # Linux cron installer
├── requirements.txt
├── src/
│   ├── config.py        # Constants and thresholds
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
