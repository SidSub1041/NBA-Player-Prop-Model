# NBA Player Prop Model

An edge model for NBA player props (points, rebounds, assists and their combos): every player with a line is projected from two seasons of game logs, adjusted for the matchup, turned into a probability and compared with the de-vigged market price at the best book. A pick is a side where the model's probability beats the market's by enough to pay. Every pick is then scored against the closing line.

---

## What changed in round 18 and why

The model used to be a filter: a FantasyPros defense-vs-position colour created candidates, points props got top/bottom-10 rank conditions, and the gate was the share of **last** season's games that cleared tonight's line. Measured on the 893 props it had labeled with a line (524 this season, 369 last spring), none of that predicted anything. The published picks hit 48.0% (179 labeled, −14.0u) while the props left on the board hit 51.9%. The hit-rate gate was flat: props with a hit rate ≥ 56% hit 49.3%, props below it 51.8%, and no band above 56% beat 50%. The DvP colour was non-directional (50/50). The conditions were inert. And price was never consulted: the whole deficit sat on picks priced −121 or worse (40.8%, 71 picks, −21.7u) while picks priced better than −121 were ahead (53.3%, 107 picks, +8.7u) — a high hit rate against a line mostly flagged a line the books had already moved away from last season's numbers, and the model paid the worst price for it. Round 18 replaces the filter with a projection, a probability, the market price and closing-line value, below.

## What changed in round 29 and why

**The board no longer waits on GitHub's clock.** GitHub does not run scheduled workflows on time: on Nov 12 the 9:30 PM night-before run arrived at 5:08 AM, the 8:15 AM morning run at 3:49 PM, and several runs never arrived, so the site showed the previous night's board until mid-afternoon. Two things made a late run useless, and both are fixed:

- The **night-before run** built "tomorrow's" board by the clock, so arriving after midnight it aimed a day too far and wrote nothing. It now builds tomorrow's board from 3 PM Eastern and **today's** when it arrives earlier in the day (`main.py --night-before`).
- Only the morning run could build the day's board. Now **the first scheduled run of any kind to arrive after 8 AM Eastern builds it** when there is none: a snapshot run becomes a full board run, and a grading run runs the model after it has graded (a fill run already built a missing board from scratch). Manual runs are never rerouted.

A late board is still a late board — for an on-time one, start a run by hand (Actions → *Run NBA Prop Model* → task `model`) or use the Mac runner below, which keeps its own clock.

## What changed in round 28 and why

**More of the board, graded.** The rebuilt model's bar — the best price at least fair *and* the checklist behind the pick — leaves two to five plays a night. The board now shows every graded play down to **B**, and the grade says exactly what each one is:

| Grade | The price | The checklist |
|---|---|---|
| **A+** | two points or more better than fair | met, with a check to spare |
| **A** | at least fair | met |
| **A−** | within half a point of fair | met |
| **B+** | within a point of fair — *or* at least fair | met — *or* one check short |
| **B** | within two points of fair — *or* at least fair | met — *or* not against the pick |

("Met" is one more check for than against on an under, two on an over.) Anything further from fair, or with the checklist against it, is a C and is not shown. Below an A the pick pays slightly more than the market's own fair price — that is what the lower grade means — so the season record is cut by grade and each grade's own record is on the board beside it. The caps are unchanged and fill best grade first: 15 plays, 3 per game, one per player. Guards, the blowout rule and "a published pick stays" apply to every grade. `PUBLISH_FLOOR=A` restores the strict board.

## What changed in round 27 and why

**A published pick stays published.** The rebuilt model publishes a pick when the best price on offer is at least the market's fair price — and such a price does not last: the books correct it within hours. A later **full** run used to rebuild the board from scratch, so a pick whose price had since been corrected simply vanished, was never graded, and the record lost exactly the picks that had beaten the market (a price moving toward a pick is what a good pick looks like). Now every pick published on any board of the day stays on the board in every later run — fill or full — on the line and price it went up at, which is what it is graded at (`main._published_today` reads the day's archived boards; `published_at` is on the pick and on its card, *posted 2:19 PM ET*). A full run still re-prices and re-reads everything else. A pick comes off only when the player is ruled **OUT**, or — for an over — when its game becomes a likely **blowout** (a spread of 12.5 or more): it is then shown with that reason, unpublished and ungraded, and returns if the spread comes back down.

## What changed in round 26 and why

**No overs in a likely blowout.** In a blowout the starters sit the fourth quarter, and an over dies with their minutes. The model now reads every game's **point spread** (one request for the whole slate, 1 credit a run — `VegasLines.ensure_spreads`, the median across books, kept with the day's prices and in the odds archive) and takes **no over in a game with a spread of 12.5 or more** (`BLOWOUT_SPREAD`), on either team. It is a hard guard: nothing overrides it. Unders are untouched, and the card of a blocked over says why (*blowout risk · spread 13.5*).

The size of the effect, from this season's own results (4,368 labeled props, Oct 22 – Nov 9, final margins from the box-score history): in games decided by **18 or more**, overs won **41.1%** against a price of 49.5% — **35.2%** on the losing team, 47.3% on the winning team; in games decided by 12–17, 45.4% (41.7% on the losing team); in games decided by 11 or fewer, overs won 50–52%, slightly more than their price. In other words, the whole season's shortfall on overs sits in the lopsided games. That is hindsight — a final margin is known only afterwards, and a team that loses big does so partly *because* its players scored less — so it sizes the mechanism, not what a pre-game spread can capture. The spread is on every prop from now on (`spread`), and the scorecard scores four readings of it nightly: any spread of 12.5+, the heavy underdog, the heavy favourite, and the size of the spread.

## What changed in round 24 and why

The third step of the rebuild: **history**. The checklist arrived in round 23, so each of its checks would have started with no evidence and needed weeks to earn or lose its place. But everything a check reads was on file on the night: the board's own matchup numbers, the results of earlier props, and the players' game logs. Round 24 puts those game logs on file for good and reads the checklist back:

- **Home and away in the game logs.** The league game-log tables (`src/gamelogs.py`) now keep where each game was played and against whom. A table saved before this round is fetched whole once more (two requests, once), then served from disk as before.
- **A box score for every night of both seasons** (`logs/box/{date}.json`, `src/boxlog.py`). The grading run already archives last night's full box score (round 22); every older night without a file is now written once from the league tables — free NBA data, both seasons. One reader, `boxlog.History`, answers anything about a player's past **as of any date**: how often he has gone over tonight's line, at home or on the road, his minutes lately, whether his team played yesterday.
- **A new check: the home/away split** — games over tonight's line at tonight's venue (home or road), over both seasons, weighted like the hit rate; at least 5 games.
- **The live checklist reads the same history** for the hit rate, the split, the minutes trend and back-to-backs, so what the scorecard measures is exactly what a card shows.
- **The checklist replayed on past boards.** For every labeled prop from a board older than the checklist, the scorecard reads each check as it would have read that night — nothing from that night's game or later — and scores it (`scorecard._replay_checks`). Each check, and the checklist's net lean, has evidence from the season's first sportsbook board (Oct 22) instead of from the day it was written; *Playing at home* is filled in the same way.

Picks are made exactly as in round 23, with the one added check.

## What changed in round 23 and why

The second step of the rebuild: **the way a pick is made**. The projection no longer decides anything. A pick now starts from the sportsbooks' own **fair price**, has to be offered at a price at least that good, and has to have the **checklist** behind it — the checks the picks were being cross-checked against by hand (defense vs position, shot zones and playtypes against what the opponent allows, hit rate against tonight's line, minutes trend, how similar players did against this opponent, rest and starters out, and the passing projection for assists). Each check is shown on the card with the number behind it. None of them moves the probability until it has beaten the price on the scorecard; until then they decide which fairly priced props are worth publishing. **Overs are still on the board, with a higher bar**: overs have won 47% against a price of 49% this season, so an over needs two more checks for it than against it, an under one. See *How a pick is made* below. This is a model that has not yet proved itself — the board says so — and the scorecard now also scores every check, the checklist as a whole and the fair price itself, every night.

## What changed in round 22 and why

The first step of a rebuild. Sixteen nights and about 3,800 labeled props said the same thing every way they were cut: the sportsbooks' price is close to right, the model's own probability added nothing beside it, and none of the checks the picks leaned on — defense vs position, zone and playtype fit, hit rate against the line — has yet shown it can beat that price. The one clear pattern was in the price itself: overs won 47% against a price of 49%. So the rebuilt model will start from the price and let a check move it only after the check has **earned** it, and this round builds what that needs. Picks are chosen exactly as before; nothing here changes the board.

- **The signal scorecard** (`src/scorecard.py`, `logs/scorecard.json`, *The Scorecard* on the site) — every night after grading, every candidate check is scored the same way on every labeled prop of the season. See *8 — The scorecard* below.
- **The full market, kept** (`logs/odds/{date}/*.jsonl.gz`) — every purchase from The Odds API is archived whole: every book's number and both prices for every player and stat, the alternate ladders included, with the game's home and away teams and tip time. The day's price cache keeps only the latest state; the archive keeps what the market said each time it was asked.
- **Every board, kept** (`logs/boards/{date}/{time}.json.gz`) — the day's board file is overwritten by each later run; a slim copy of every run's board is now kept beside it.
- **Home and away, and the tip time, on every prop** (`is_home`, `tip_utc`) — never recorded before, so a home/away split could not be tested.
- **The box score, kept by player id** (`logs/box/{date}.json`) — grading reads points, rebounds and assists and used to throw the rest away. Every player's line (team, opponent, home or away, starter, minutes, shooting volume) is now saved on nights when every game came back, so hit rates, minutes and usage trends can be rebuilt point in time without asking NBA.com again.

## What changed in round 21 and why

Two things the round-20 review found. First, moving a pick from a shopped alternate to its regular line moved the line and the price but not the numbers beside them: the re-settled picks, the CSVs and 494 training rows still carried the alternate's model probability, break-even, edge, grade and hit rate. `src/lines.on_main_line` now re-derives all of them at the regular line (`repair_main_line_fields`: the model's probability from the stored projection, the market from the consensus prices, the break-even from the price taken, the edge and grade from those; the hit rate, measured against another number, is cleared), and the grader, the learner's labels and snapshots and the CSV export all go through it. The one-time migration runs again once (a sentinel file, `logs/main_lines_migrated.json` and `logs/training/main_lines_migrated.json`, records that it did, so every later run skips it in one file check), repairs what the first pass left, keeps the night's post-mortem in step with its graded file, and retries a night whose board could not be read instead of marking it done. Second, the board now carries a **calibration warning**: this season the model's probabilities have not predicted results (see *Where It's Ahead* for the record), a market-anchored rebuild is under way, and the ALT LINE row shows a number and a price only — no model verdict — until the rebuilt model earns one.

## What changed in round 19 and why

Assists were paused after a losing start (36–45 on labeled props), but a few months of results say little about a stat, and the projection behind them was the weak part: a box-score average carries the team-mates' shooting luck on every pass. Round 19 projects assists from **passing** instead — passes made, the potential assists they create and how many of those go in — and adds the shooting matchup the chain depends on (*2a* below). Assists publish again like any other stat (`PAUSED_STATS` is empty). Two fixes came with it: the team "FG% allowed by zone" table was the teams' **own** shooting (NBA.com's shot-location endpoint answers for the team's offence unless asked for the opponent measure), so the points zone fit read the opponent's offence; it now reads its defence. And the new tables never abort a board: if they fail, assists are held back with a *passing* guard instead.

## Methodology

### How a pick is made (round 23)
1. **The fair price.** For every prop on its regular line (*Lines and odds*), each sportsbook's two-way price is de-vigged on its own — the over's implied probability divided by the sum of both sides' — and the **median** across books is the market's fair probability (`src/fair.py`). A median barely moves when one book hangs an outlier. It needs **3 books** pricing both sides at the line (`MIN_FAIR_BOOKS`); with fewer the prop is shown but held back by the books guard. At a whole-number line a push is a loss on this board, so both sides are scaled by 1 − P(push).
2. **The best price.** The best price any book offers on each side is compared with that fair price: **price edge = fair probability − the price's break-even**. A pick needs it to be **≥ 0** (`MIN_PRICE_EDGE`): the board never pays more than the market's own fair price. On a typical night about one prop in ten has a side that clears this.
3. **The checklist** (`src/checklist.py`). Each check reads as a lean to the over, a lean to the under, or neither; it leans only past a bar that leaves it neutral on a little over half of all props:
   - *Defense vs position* — the opponent's by-position z-score, ±0.75.
   - *Shooting zones* and *Play types* (points) — the share-weighted z-score of what the opponent allows where and how the player scores, ±0.5.
   - *Hit rate vs the line* — games over tonight's line this season (last season's too while this one has fewer than 5), weighted by the games behind it (a rate over *n* games counts *n / (n + 20)* of itself), ±0.06; at least 5 games.
   - *Home/away split* — the same count at tonight's venue, home or road, over both seasons, weighted the same way, ±0.06; at least 5 games.
   - *Minutes trend* — the last 3 games against the earlier ones this season, ±3 minutes; at least 5 games.
   - *Similar players vs this opponent* — every earlier prop this season by the same position group against tonight's opponent, same stat: the share that went over, shrunk toward half, ±0.06; at least 8 props.
   - *Situation* — the opponent on a back-to-back or starters out on his own team (toward the over), his own team on a back-to-back (toward the under).
   The player's own history (hit rate, split, minutes, who played yesterday) is read from the box-score history (`logs/box/`, `src/boxlog.py`), as of tonight.
   - *Passing projection* (assists) — the passing model's probability of the over (*2a*), ±7 points from even.
   **Support** for a side is the checks leaning to it minus the checks leaning against it. An **under needs 1** (`UNDER_MIN_SUPPORT`), an **over needs 2** (`OVER_MIN_SUPPORT`): the over has been overpriced, so the checks have to outweigh that. A check the scorecard has found pointing the wrong way (verdict *negative*) is still shown, struck through, and not counted.
4. **The grade.** Two gates — the price and the checklist. **A**: both. **A+**: both, a price two points better than fair and a check to spare. **A−**: the checklist met, the price within half a point of fair. **B+**: the checklist met and the price within a point — or the price at least fair and the checklist one check short. **B**: the checklist met and the price within two points — or the price at least fair and the checklist not against it. **C**: anything else. Everything from **B** up is shown (`PUBLISH_FLOOR`, round 28), subject to the guards and caps (*5*), best grade first. The side shown for a prop is the one that clears more gates, then the one the checklist favours, then the better price.
5. **What moves the probability.** Nothing yet. A check may move the fair probability only after it passes the scorecard's bar (*8*: beats the price on 25 forward nights and 1,500 props), and then by half its fitted weight (`ADJUST_SHRINK`). The same holds for marking overs down by their measured shading.

The projection (*2*, *2a*, *3*, *4*) is still built for every prop: it supplies P(push) at whole-number lines, the passing check, the minutes guard and the number carried to an alternate line, and the scorecard keeps testing it as *the old model*. It is on the board as `p_model` and is not used to pick.


### 1 — Candidates: every player with a line
Every player on tonight's slate who averages 20+ minutes and has a line (see *Lines and odds*) is a candidate for every stat the model projects — points, rebounds, assists — on **both** sides. No colour, no rank cut: the market decides what is on the board, the model decides what is worth playing. Players listed OUT on the injury report are skipped (there is no backup swap any more: a backup with his own line is already a candidate). A starter's position comes from the depth chart; anyone else's from NBA.com's roster list (one request per run), and a listed "G" uses the average of the PG and SG defense-vs-position rows ("F-C": PF and C, and so on). A player with no known position gets no DvP adjustment rather than a guess. Book names are matched to NBA.com names with and without suffixes ("Jimmy Butler" / "Jimmy Butler III"), and a player belongs to whichever of the line's two teams his most recent game, this season's stats, the depth charts or the injury report put him on — a team that is not in the line's game (a trade the tables have not caught up with) is never used. Without an Odds API key, the players Underdog's own feed prices become candidates too; their lines are shown but never published (see the books guard).

### 2 — Projection: two seasons, blended
For each player and stat the model builds a per-game mean and spread from NBA.com game logs — one league-wide request per season, cached on disk in `cache/gamelogs/` (last season once, this season one new slate at a time; kept between Actions runs with `actions/cache`) instead of one request per player: this season weighted by form (a game 10 games ago counts half as much as last night) and last season as the prior. The spread is mixed with a typical spread for the stat (8 games' worth, `VAR_PRIOR_K`), so a hot three-game start never reads as a near-certainty. The two are blended by sample size — this season's weight is *n / (n + 10)*, so a 10-game sample is half the projection and a 40-game sample four fifths. Minutes are blended the same way, giving a per-minute rate that is re-scaled when the projected minutes change.

### 2a — Assists: passes made, potential assists, conversion
An assist is the end of a chain the passer only partly controls, so the model projects each link from NBA.com's tracking tables (`src/passing.py`):

*assists = minutes × passes per minute × potential assists per pass × conversion*

- **Passes per minute** — how much of the offence runs through his hands. A role, counted in dozens of passes a game, so it settles fast: this season (its last 10 games counted twice, for form) weighs *n / (n + 5)* against last season (`PASS_BLEND_K`).
- **Potential assists per pass** — how often his pass leads to a shot within one dribble. Playmaking: pulled toward his last season, and that toward what players who pass as often as he does manage (a line fitted across last season's league, since lead ball-handlers set up more shots per pass than a centre's hand-offs), with 400 passes of prior at each step (`PA_RATIO_K`).
- **Conversion** — the share of potential assists that go in. Mostly the team-mates' shooting, and over a few weeks mostly their luck, so it gets no form weighting and is pulled hard toward a prior: 300 potential assists of the league rate (`AST_CONV_K`), scaled by how well his team makes the shots passes create. Last season's potential assists count half (`CONV_LAST_WEIGHT`).

A cold-shooting week costs a passer assists without touching the first two links; a hot week adds assists that won't repeat. A box-score average, and a line set from one, carries both. This projection keeps the links that persist and regresses the one that doesn't.

Tonight's opponent enters through the shots and the passes. **Shooting**: the FG% the opponent allows in each zone (NBA.com shot locations, opponent measure), weighted by where the player's team takes its assisted shots (its own attempts by zone × the share of that zone's makes that are assisted, about 55% at the rim and 80–90% from three). Each zone's deviation is shrunk by its attempts, because three-point defence is mostly noise and rim defence much less (a full season at the rim keeps about two thirds of its deviation, above-the-break threes about a third). **Ball movement**: the share of the makes the opponent allows that are assisted, shrunk the same way. Last season counts half next to this season in both. With pace and OUT team-mates these form the assists multiplier (clipped 0.80–1.25); the by-position DvP is not applied to assists, because it mixes these same effects and their noise. The spread keeps the box-score projection's variance-to-mean ratio. Each assists card shows the chain: *PASSING 54 passes · 11.7 potential · 57% made (42% so far) → 6.7 AST (box 5.6)*, with chips for the opponent's shooting and ball movement.

The tables are league-wide: about five requests a run (last season's are kept on disk with the game logs). They are optional — if they fail, assists keep their box-score projection and the *Passing projection* check on the card simply has no reading (before round 23 a *passing* guard held such props back).

### 3 — Matchup: a multiplier, not a vote
The projection is scaled by what tonight is likely to do to it:
- **Defense vs position** — the opponent's stat allowed to the player's position (FantasyPros, read as a number, not a colour), as a z-score against the league, applied at half strength.
- **Pace** — the two teams' blended pace against the league average.
- **Points only: shot zones and playtypes** — every zone the player shoots from, weighted by his share of attempts, against the opponent's FG% allowed there (z-score; the opponent measure since round 19, the team's own shooting before); every playtype by frequency against the opponent's points per possession allowed. A continuous score, ±2% per unit of z.
- **Assists** use their own matchup (see *2a*): the opponent's shooting allowed and ball movement allowed, pace and OUT team-mates, without DvP.
- **Teammates out** — +3% per OUT starter on the player's own team, up to two.

The multiplier is clipped to 0.80–1.25. Combos sum the component projections; their spreads add with a 0.25 correlation (see below).

### 4 — Probability and the market
*(Rounds 18–22. The blend described here no longer picks: see* How a pick is made*.)*

Points and combos use a **normal** distribution; rebounds and assists a **negative binomial** (counts are right-skewed). On an integer line a push loses on either side (that is how this board grades), so the push probability is taken out of both sides. Combos add the component spreads with a 0.25 correlation (`COMBO_RHO`): a player's points, rebounds and assists rise and fall together with his minutes. The model's probability of the pick side at tonight's line is compared with the **market's**: the best price on each side across the books, de-vigged multiplicatively. When the line has moved since the pick's line was taken, the current consensus is shifted to the pick's line first — a line that moved **down** favours the **under at the original number** (the books now expect less), and vice versa — so the comparison is always at the number the pick is on. The two are blended — `MODEL_TRUST` (0.5 at launch) of the model's probability plus the rest of the market's — and the **edge** is that blended probability minus the **break-even of the price actually taken** (−110 needs 52.4%). So a confident model still has to clear the vig, and a number the market has moved away from counts as edge on its own. In raw terms, at −110 on both sides an **A** (3 points) needs the model at about **61%** where the market says 50%, and an **A+** (6 points) about **67%**: the model has to disagree with the market by roughly 11 and 17 points before anything is published, which is deliberate while it has no track record. Each card shows all four numbers: *MODEL · MARKET · BLEND · NEEDS*, then the edge.

**The regular line, and alternates beside it.** Every pick is made, priced, published and graded on the **regular line**: the main ("pick'em") line most books hang, with the best price any book offers at that number. Some books list a whole ladder of alternate lines inside the main market (Bovada: the same player at 10.5, 12.5, 13.5… priced from −280 to +210); a book's main line is the number whose two prices are closest to even, one vote per book, so a ladder never decides the line. Other numbers are still worth knowing: a book whose own main line lags a move (the consensus moved to 23.5, one book still hangs 24.5 under), or an alternate at a price the model likes. The model reads each the same way (the market shifted to that number, its price's break-even) and, when one beats the regular line by half a point of probability, shows it under the pick as **ALT LINE** — *U 24.5 −110 · DraftKings off the consensus · MODEL 58% · EDGE +5.1 · for reference, graded on 23.5* — never in its place. Until round 20 that number replaced the pick's line (mostly Bovada alternates at +165 to +210 or −245 to −280); the grader now settles those picks at the consensus line and price they were shopped from, keeps the alternate's own result beside it for reference, and the learner's labels moved with them (44 published picks re-settled: the season record went from 144–171, −33.3u, to 148–167, −27.8u).

### 5 — Guards, caps and paused stats
A valid pick must also clear the guards: a price no worse than **−121**, at least **3 sportsbooks** at the consensus line (so DFS-app and Underdog lines are shown and graded for the record but never published), a line of at least **4.0** (tiny-count props are noise), at least **24 projected minutes** and, for an over, a game that is not a likely **blowout** (a point spread under **12.5**, round 26). Books, minutes and blowout are hard; the price and line-size guards can be overridden by a price two points better than fair. (The passing guard is gone: the passing projection is a check on the card now.) The board is then capped to the strongest edges: **15 plays**, **3 per game**, **1 single-stat play per player** (plus one combo per player). Dropped plays are listed under the board with the cap that dropped them. A stat can be **paused** (`PAUSED_STATS`, e.g. `PAUSED_STATS=assists`; none since round 19): it is projected and shown, but never published. The afternoon fill runs never touch a published pick: its line, probabilities and grade are frozen from the board it was published on (only its line-movement chip and injury tag update), and new picks — including plays the morning caps dropped — fill whatever room the caps leave. One exception: a player ruled **OUT** after his pick was published is withdrawn from the board (the log names him; your book will void the bet).

### Grading
| Grade | Edge (blended probability − price break-even, pick side) | Valid? |
|-------|------|------|
| A+    | ≥ 6 points | yes, if the hard guards pass (price and line size may be overridden) |
| A     | ≥ 3 points | yes, if every guard passes |
| B+    | ≥ 1.5 points | watchlist |
| B     | > 0 | watchlist |
| C     | ≤ 0 | no |
| HOLD  | no line | On Hold (below) |

The thresholds live in `src/config.py` (`MIN_EDGE`, `STRONG_EDGE`, `WATCH_EDGE`, `MAX_PRICE`, `MIN_BOOKS`, `MIN_LINE`, `MIN_PROJ_MINUTES`, `MAX_PICKS`, `MAX_PER_GAME`, `MAX_PER_PLAYER`, `PAUSED_STATS`) and can be overridden from the environment. Only **A+ and A** picks that pass the guards, are not paused and survive the caps are flagged as valid. Every card shows the model's probability, the market's, the edge in points, where the line opened and which way it moved, and any guard that failed.

### Closing-line value
The sportsbook cache is snapshotted every time it is saved (`logs/lines_{date}.json`: line, both prices, books, time), so the model knows where every line **opened**, how it **moved** and where it **closed**. **Snapshot runs** every 30 minutes from late morning to the last late game (11:15 AM–11:45 PM ET in summer time, 10:15 AM–10:45 PM in winter) re-price the games tipping within the next 45 minutes, so each game from a noon matinee to a 10:30 PM tip is re-priced once or twice just before tip. A close only counts when the market was actually re-priced within 90 minutes of tip (`CLOSE_WINDOW_MINUTES`); otherwise the pick's CLV is left blank and counted as *unverified*, never as a zero. When a pick is graded it also gets its **CLV**: the closing line minus the line taken (in the pick's favour) and the de-vigged closing probability minus the pick-time probability at the pick's line. The night's and the season's CLV — average points, share of picks that beat the close — are on the site and in the CSVs. Beating the close is the only evidence of an edge that does not need the result to land.

### 6 — On Hold: no line, no pick
Every candidate starts from a line, so holds are rare on edge boards: they are props kept from an earlier board whose line has since disappeared. A prop with **no line** can't be measured, so it goes **On Hold** instead of being graded. Each hold says why, e.g. *No PTS line: no book or DFS app lists it — GTD · Ankle* or *No REB line: the books and DFS apps pulled it before tip* (injury designations come from the CBS injury report). The morning run and the two afternoon fill runs re-check the holds, and a hold becomes a pick (or drops) as soon as a line appears. Holds are never graded.

### 7 — The learning loop: it learns from its own results
Every morning, after grading, the model studies the night before:

1. **Label** — every prop that had a line (not just the published picks, ~100 a night) is marked hit or miss from the box scores (`logs/training/`). Older boards are backfilled automatically; a night whose box scores came back incomplete is retried on later runs rather than saved half-labeled.
2. **Retrain** — a supervised model (L2-regularized logistic regression, recent nights weighted more, always anchored to today) learns P(hit) from what was known at pick time: hit rate against the line, season average vs the line, the price's implied probability, share of conditions met, stat type, over/under, line source, books hanging the line, injury tag and sample size — and, since round 18, the edge model's own view: its probability, the market's, the edge, the line's move toward the pick, the DvP and matchup z-scores, projected minutes and this season's share of the projection (`logs/learner.json`). Features are recomputed from each stored board, so a definition change never mixes old and new encodings. It retrains only when new labels exist. Its segment cuts are pre-registered — stat, side, price band (better than −121 / −121 or worse), books (1–2 / 3–5 / 6+), edge band, line size — and every cut carries its sample size and a Wilson 95% interval.
3. **Check itself honestly** — walk-forward: each night is predicted by a model trained only on the nights *before* it, and compared with two baselines, the average hit rate and the de-vigged book price. To turn **active** it must, on *effective* evidence of ≥ 300 props over ≥ 10 nights (recent nights count most), beat the stronger baseline by ≥ 1% with a statistically clear margin, its EV rule must not have cost units on past published picks, and all of that must hold on three retrains in a row. Until then it runs in **shadow** mode: its probability shows on every card, nothing changes. Once active it only drops back after two failing retrains in a row.
4. **Act** — once active, a valid pick must also be **+EV**: the model's P(hit) must beat the break-even probability of the pick's price (settled the same way the grader pays), or the pick is cut — and listed on the board under *Cut by the model* with the reason. Valid picks are then listed best expected value first.
5. **Post-mortem** — what went right (a segment that would beat a −110 price), what went wrong and the biggest misses for each night (`logs/postmortem_{date}.json`), shown on the site in *The Film Room* with the model's current top factors (real, varying inputs only).

Retraining after every run does not overfit: each retrain refits from scratch on all labels, and the gate above decides whether it may act. It replaces the earlier adaptive multipliers (`src/adaptive.py`, no longer used).

### 8 — The scorecard: every check, against the price
The scorecard is how a check earns a place in the model. It never looks at which side the model leaned to — each prop with a regular line, a two-way price and a result is one row asking one question, *did it go over?*, and the baseline is the sportsbooks' own de-vigged probability of the over. For each check (a number pointed toward the over):

- **Weight** — its coefficient beside the price in a logistic fit on every row, with a 95% interval that treats a night, not a prop, as the unit of evidence.
- **Against the price, forward only** — fitted on earlier nights, scored on later ones (from the sixth night on). The comparison is with the price re-fitted on the same earlier nights, so a check that merely leans under is not credited with the overs' shading; that shading has its own row (*the price re-fitted*).
- **Bets** — on those later nights, bet the over or the under wherever the fitted probability clears that side's break-even by 2 points, at the price actually offered.

Verdicts: **beats the price** (the forward log-loss improves with an interval clear of zero, it also beats the price as posted, and its bets paid), **leans right**, **no evidence**, **points the wrong way**. A check that reliably points the wrong way can beat the price too — the fit simply gives it a negative weight. A check may move a published probability only after it beats the price on **25 forward nights and 1,500 props**; those bars were set before the evidence and are out of reach for a few weeks of data on purpose. About twenty checks are tested, so one may clear the bar by luck: each is re-tested every night and loses its place the night it stops passing. Checks whose inputs were not recorded before round 22 (home/away) start counting from the first board that carries them.

### Lines and odds: sportsbooks first, DFS apps as backup
Every prop — points, rebounds, assists and all four combos (PTS+REB+AST, PTS+REB, PTS+AST, REB+AST) — is measured against the **sportsbook line** from [The Odds API](https://the-odds-api.com): the line most books hang for that prop, with the best price on each side and the book offering it (DraftKings, FanDuel, BetMGM, BetRivers, Bovada, BetOnline — the API's `us` region). When the sportsbooks post nothing at all for a market in a game (a stat for the whole game, not a single player), the model buys that market from the **DFS pick'em apps** in the same API — PrizePicks, Underdog, DraftKings Pick6, Betr, Dabble (the `us_dfs` region) — and labels those lines *dfs backup*. A single player the books skip while pricing his team-mates gets no DFS line. DFS lines are shown and graded for the record but never published (the books guard needs 3 sportsbooks), and the night-before run never buys them. Only without an API key does the model read Underdog's own feed. Picks are graded against that same line and price.

Prices are pre-game: once a game tips, the board keeps the last price bought before tip-off. If a prop is pulled before tip (usually injury news), it goes On Hold.

**Line history and the close**: every save of the day's price cache appends a snapshot of each prop whose line or price changed to `logs/lines_{date}.json` (first sight always recorded, identical snapshots never duplicated), so each prop has an opening line, its moves and a close. **Snapshot runs** (`python main.py --snapshot`, one cron `0,30 22,23,0,1,2,3 * * *`: every 30 minutes from 22:00 to 03:30 UTC) re-price only the games tipping within the next 45 minutes (`SNAPSHOT_WINDOW_MINUTES`), for the three single-stat markets plus any market already on the board for that game (combos), so the grader can verify a close for every game that tips from noon to the last late game. A snapshot costs one credit per market per region per game in the window — three to seven credits a game, each game priced once or twice: about 40–80 credits across a full evening; the credit guards below apply to snapshot runs too. The line history is committed with the rest of `logs/`.

**Setup**: get a key at the-odds-api.com, add it as the `ODDS_API_KEY` repository secret, and run **Actions → Run workflow → task: probe** (its Vegas step prices one game for the seven markets the model uses, once for the sportsbooks and once for the DFS apps: 14 credits). Without a key — or once the plan's credits run out — lines already bought earlier that day are kept until they can be re-priced, Underdog's own feed supplies candidates (shown, never published), and props without a line go On Hold. `ODDS_API_REGIONS` (default `us`) and `ODDS_API_DFS_REGIONS` (default `us_dfs`; blank turns the DFS backup off) choose the regions; `us2` adds Hard Rock Bet and ReBet to the books.

**Credits**: listing the slate is free; pricing a game costs one credit per market per region requested. Each board run buys the points, rebounds and assists markets for every untipped game, sportsbooks first, and the DFS apps only for a market the books have not posted at all in that game. Everything bought is saved in `logs/vegas_{date}.json`, so a later run re-buys a market only when its price is more than 2½ hours old (`ODDS_API_REFRESH_MINUTES`; 45 minutes within 3 hours of tip). Every board run wants every player's points, rebounds and assists markets (the whole board is the candidate list), so each run re-buys a game's three markets once they are older than the refresh window; fill runs use a one-hour window (`ODDS_API_FILL_REFRESH_MINUTES`), so they re-price every untipped game — that is how they find bench and late props. If the first two games of a slate more than 8 hours from tip-off come back with nothing from any book or app, the run stops buying — nobody has posted the slate yet (`ODDS_API_EMPTY_STOP_EVENTS`, `ODDS_API_EMPTY_STOP_HOURS`). The night-before run buys sportsbook markets only; when the books haven't posted tomorrow's props yet (the usual case at 10:30 PM ET), it stops after the first two empty games and writes no board, so tonight's board stays up until the morning run. Budget for a full 10–12-game day: night-before ~30–40, morning ~40–60 with combos, each fill run ~30–40, snapshots ~40–80 — about **200–250 credits a day, 6,000–7,500 a month**. The 20,000-credit plan covers the season with room for a second sportsbook region; the 500-credit free plan lasts two days. A run that aborts still keeps what it bought. A run stops buying at `ODDS_API_MAX_CREDITS_PER_RUN` (default 150) or when fewer than `ODDS_API_MIN_REMAINING` (default 25) credits are left.

---

## Where the Model Runs

`stats.nba.com` silently blocks most cloud datacenter IP ranges (GitHub Actions runners included): requests hang until they time out. The model and the grader both need it, so the pipeline separates **producing the data** from **publishing the site** (GitHub Actions always handles publishing: any data push redeploys within ~2 minutes). Pick one producer:

| Option | What it is | Cost / effort |
|--------|------------|---------------|
| **A. GitHub Actions + proxy** (recommended) | The existing schedule becomes the live server: add an `NBA_PROXY` repository secret (`http://user:pass@host:port` from a residential or ISP proxy plan — datacenter proxies are usually blocked too) and the scheduled runs build each slate's board twice — the night before (~10:30 PM ET) and the morning of (~9:15 AM ET) — plus the 5:30 AM ET grading run. No machine to maintain. | A proxy plan (typically a few dollars a month); 2 minutes to set up |
| **B. Any always-on Linux box** | A VPS, Raspberry Pi or home server with an unblocked IP runs `./setup_cron.sh`, which schedules `run_daily.sh` at 9:15, 12:00, 15:00 and 18:00 (the first run of the day builds the board, later ones are fill runs) plus closing-line snapshots every 30 minutes from 11:15 to 23:45 and pushes the data. | The box; needs non-interactive `git push` (deploy key or token) |
| **C. Your Mac** | `./setup_mac.sh` installs a LaunchAgent for `run_daily.sh` at the same times (later runs are fill runs). It has no snapshot schedule: for closing-line value, also run `MODEL_ARGS=--snapshot ./run_daily.sh` every 30 minutes in the afternoon and evening, or use option A. Works only while the Mac is awake (launchd runs missed jobs on wake). | Free, but tied to the laptop |

**Test before you trust**: `python3 probe_nba.py` reports PASS/FAIL for the NBA API from any host (or through `NBA_PROXY`). On GitHub, use **Actions → Run workflow → task: probe** to test a proxy secret from the runner itself in about a minute.

**Setting up option A**: repo → Settings → Secrets and variables → Actions → *New repository secret* → name `NBA_PROXY`, value the proxy URL. Run the probe task; once it passes, the next scheduled run produces the board. Retire the Mac runner with `launchctl unload ~/Library/LaunchAgents/com.nba.prop.model.plist` so two producers don't race.

**Schedule (Actions)**: the board for each slate is built **the night before** (~10:30 PM ET, `python main.py --tomorrow`) when the books have posted the next day's props — they usually haven't yet, and then the run writes nothing — and **refreshed the morning of** (~9:15 AM ET: books re-priced, holds and injuries re-checked). Picks made by then are left alone — what you bet on is what the board showed. Two **fill runs** (~1 PM and ~5 PM ET, `python main.py --fill-holds`) then re-price the board as the books post bench and late props through the day: every pick already published is pinned (its line, probabilities and grade frozen; withdrawn only if the player is ruled OUT), and new picks fill the room the caps leave. **Snapshot runs** at :15 and :45 from ~11:15 AM to ~11:45 PM ET (`python main.py --snapshot`, one cron; an hour earlier in winter) re-price only the games tipping within the next 45 minutes, so every game from noon on gets a verified closing line; they never touch the board, skip the CSV export and the site deploy, commit only the day's price cache and line history, and run in their own concurrency group (they can never cancel a board run); when one finishes together with a board run, `scripts/commit_data.sh` merges the two runs' logs instead of failing the push. The league game-log cache (`cache/gamelogs/`) is kept between runs with `actions/cache`. Grading runs at ~5:30 AM ET and attaches each pick's closing-line value. Crons are UTC, so winter times are an hour earlier. From **Actions → Run workflow**, `task: fill` runs a fill pass by hand and `task: snapshot` a snapshot.

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
  Capped (board)      : 2

  ✅  VALID PICKS
  ╭──────────────────┬──────┬─────┬─────┬───────┬───────┬──────┬───────────┬─────────┬──────────┬──────────┬─────┬────╮
  │ Player           │ Team │ Opp │ Pos │ Stat  │ Edge  │ Line │ Odds      │ Model % │ Market % │ Edge pts │ HR  │ Gr │
  ├──────────────────┼──────┼─────┼─────┼───────┼───────┼──────┼───────────┼─────────┼──────────┼──────────┼─────┼────┤
  │ Jayson Tatum     │ BOS  │ MIA │ SF  │ POINTS│ OVER  │ 26.5 │ -110 DK   │ 67%     │ 50%      │ +6.1     │ 62% │ A+ │
  ╰──────────────────┴──────┴─────┴─────┴───────┴───────┴──────┴───────────┴─────────┴──────────┴──────────┴─────┴────╯
```

`logs/props_YYYY-MM-DD.json` (and the site's `latest.json`) carries every prop with its projection (`proj_mu`, `proj_sd`, `proj_minutes`, `proj_dist`, `n_this`, `n_last`, `weight_this`), the probabilities and edge (`p_model`, `p_market`, `edge_value` — `edge` remains the side), the line's history (`open_line`, `line_move`, signed toward the pick), the matchup (`dvp_z`, `matchup_z`, `teammate_out`), the guards that failed, the pause/cap state and `model_version` (`edge-1`, or `filter-0` for boards before round 18). A better number at one book for the pick's side is under `alt_line` (book, line, price, kind — `alternate` or `lagging` — and its model / market probabilities and edge); the pick's own `line` is always the regular line. Assists projected from passing add `proj_source` (`passing`), `proj_mu_box` (the box-score projection it replaced) and `passing` (passes, potential assists, conversion projected / so far / prior, the team's and the opponent's shooting factors, the ball-movement factor). Graded files add `closing_line`, `closing_odds`, `clv_line` and `clv_prob` per pick and the night's `clv` summary. Every prop also carries `is_home`, `tip_utc`, `spread` (his team's point spread: negative = favoured) and, once published, `published_at`, and from round 23 (`model_version` `market-2`) the market-anchored fields: `p_fair` (the fair probability of the pick side), `fair_over`, `fair_books`, `fair_basis`, `fair_low` / `fair_high` (the books' range), `checks` (each check's `value` and `lean` toward the over, its `detail` and whether it `counts`), `support` (`for`, `against`, `net`, `need`) and `adjustments` (passed checks that moved the probability; none yet). `edge_value` is then the price edge, `p_hat` the fair probability and `p_market` the fair price before adjustments. Beside the day's files: `logs/odds/{date}/` (the full market at every purchase, one gzipped JSON-lines file per run: `t`, `event_id`, `home`, `away`, `commence_time`, `tier`, `markets` and `rungs` — `[player, stat, book, number, over price, under price]`), `logs/boards/{date}/` (every run's board), `logs/box/{date}.json` (the night's box score by player id) and `logs/scorecard.json` (the signal scorecard, also under `scorecard` in the site's `latest.json`).

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
│   ├── hitrate.py       # Game logs: hit rate vs the line and the two-season sample behind the projection
│   ├── blend.py         # Two-season blend: form-weighted mean / variance, this season's weight n/(n+K)
│   ├── projection.py    # Projection → probability (normal / negative binomial), de-vig, market at the pick line, edge
│   ├── matchup.py       # Matchup multiplier: DvP z-score, pace, zone/playtype scores, teammate outs
│   ├── passing.py       # Assists from passing: passes made, potential assists, conversion; opponent shooting / ball movement
│   ├── pace.py          # Team pace, blended across seasons
│   ├── caps.py          # Board caps: 15 plays, 3 per game, 1 per player
│   ├── vegas_odds.py    # Sportsbook lines + prices (The Odds API) — primary line source; snapshot runs
│   ├── line_history.py  # Opening line, moves and the close for every prop; closing-line value
│   ├── lines.py         # The line each prop is measured against: books first, DFS apps backup
│   ├── learner.py       # The learning loop: labels every prop nightly, retrains, gates -EV picks, post-mortems
│   ├── record.py        # The season's graded record, its cuts and its CLV
│   ├── scorecard.py     # The signal scorecard: every check scored against the price, walk-forward, nightly
│   ├── fair.py          # The market-anchored probability: per-book fair price, passed-check adjustments, price edge
│   ├── boxlog.py        # The box-score history: one file per night (both seasons), each player's games as of any date
│   ├── checklist.py     # The checks behind a pick, read per prop; support for each side
│   ├── scoring_engine.py# PropScore and the edge grade
│   └── output.py        # Report formatting, the board JSON (paused / capped lists, thresholds)
└── logs/                # Daily reports, boards, graded results, line history (lines_*.json), the archives (odds/, boards/, box/) and scorecard.json
```

---

## Live Dashboard

- **Tableau Public** — interactive results dashboard (cumulative units, daily win rate, splits, pick explorer): [View on Tableau](https://public.tableau.com/app/profile/sidharth.subramanian7031/viz/NBAPlayerPropModel/Dashboard1)
- **Site** — daily picks and graded results: https://sidsub1041.github.io/NBA-Player-Prop-Model/. Since the rebuild (round 23) the page announces it under the hero — **The Model, Rebuilt**: four tiles on what changed, linking to the board, the scorecard and *The System* — and each card shows the price row (*FAIR 50% · NEEDS 49% · EDGE +1.0*) and the checklist (*3 FOR · 1 AGAINST* with a chip per check) in place of the older edge row described below. The page leads with **Where It's Ahead** — every cut of the season's graded record (by grade, side, stat, single vs combo) that is above break-even (52.4%, what a −110 price needs) or in profit on 8+ picks, plus the best night, any winning streak and a profitable last season — each shown as its W–L with hit rate, units and sample size, under a header that states the full season record and the break-even figure. Each grade group on the board also shows that grade's season record. Then the KPI row (plays today, last night, season W–L with units, nights graded), then **the board ranked best first**: A+ plays before A, and within a grade by the edge over the market, then (on boards without one) hit rate against the line, conditions met and books hanging the line (expected value instead, once the learner is active); every card carries its rank and the #1 play is tagged. Each card shows the edge row — *MODEL 67% · MARKET 50% · BLEND 59% · NEEDS 52% · EDGE +6.1* (the blend minus the break-even of −110) — with a chip for a line that moved since it opened (green arrow when the market moved toward the pick, red when against) and a grey chip for any guard that failed. Assists cards add the passing row (passes → potential assists → conversion → the projection, with the box-score number beside it). Under the board header: any paused stats and, when the caps bit, "15 of 23 A-grade plays shown (3 per game)"; the capped and paused plays are listed under the board. The Receipts carry the season's CLV tile (average points vs the close, share of picks that beat it). The same order is in `latest.json` (`rank`) and `picks.csv`.
- **Data feeds** — CSV fact tables in `web/public/data/` (`picks.csv`, `graded_picks.csv`, `daily_results.csv`), refreshed automatically by GitHub Actions. They are written as UTF-8 with a BOM (`utf-8-sig`) so accented player names open cleanly in Excel and Tableau; in pandas read them with `encoding="utf-8-sig"`. Every row carries a `season` column (e.g. `2026-27`). `picks.csv` adds the edge model's columns (`proj_mu`, `proj_sd`, `p_model`, `p_market`, `edge_value`, `open_line`, `line_move`, `dvp_z`, `matchup_z`, `guards`, `paused`, `cap_note`, `model_version`, for assists `proj_source`, `pot_ast`, `ast_conv`, and `alt_line`, `alt_odds`, `alt_book` for the alternate number shown beside a pick — `line` and the odds are always the regular line); `graded_picks.csv` adds `closing_line`, `clv_line` and `clv_prob`, plus `alt_result` / `alt_units` (the alternate's own outcome, for reference — `result` and `units` are on the regular line); `daily_results.csv` adds `clv_avg_line` and `clv_beat`.

---

## Credits

Built by **Sid Subramanian**.
