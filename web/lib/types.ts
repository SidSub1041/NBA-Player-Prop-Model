export interface PropPick {
  player_name: string;
  team: string;
  opponent: string;
  position: string;
  stat: string;
  edge: string;
  total_points: number;
  total_conditions: number;
  pass_rate: number;
  hitrate: number | null;
  hitrate_source: string;
  season_avg: number | null;
  games_played: number | null;
  /** games that cleared the line in the pick's direction (hit rate vs the line) */
  games_hit?: number | null;
  /** the designated line: sportsbooks first, DFS apps ("dfs") as backup, Underdog's own feed last (boards before Oct 22, 2026: absent) */
  line?: number | null;
  line_source?: "vegas" | "dfs" | "underdog" | null;
  /** fill-holds run: this line was kept from the earlier board, not re-priced */
  line_locked?: boolean;
  line_book?: string | null;
  line_books?: number | null;
  over_odds?: string | null;
  over_book?: string | null;
  under_odds?: string | null;
  under_book?: string | null;
  /** Underdog's own line, when it has one */
  ud_line: number | null;
  ud_over_odds: string | null;
  ud_under_odds: string | null;
  /** sportsbook ("Vegas") odds for the same prop — display only */
  vegas_line?: number | null;
  vegas_over_odds?: string | null;
  vegas_under_odds?: string | null;
  vegas_book?: string | null;
  vegas_books?: number | null;
  /** passes the conditions but has no Underdog line yet (re-checked each run) */
  /** the learning loop's prediction (src/learner.py) */
  p_hit?: number | null;
  ev?: number | null;
  breakeven?: number | null;
  learn_note?: string | null;
  on_hold?: boolean;
  hold_reason?: string | null;
  /** injury-report label, e.g. "GTD · Ankle" */
  injury?: string | null;
  grade: string;
  is_valid: boolean;
  /** 1 = best in its list (A+ first, then the edge over the market, then hit rate vs the line, conditions met, books) */
  rank?: number;
  zone_details: string[];
  playtype_details: string[];
  /* ── round 18: the edge model (absent on boards before it) ── */
  /** projected mean / spread / minutes and the distribution used ("normal" | "nb") */
  proj_mu?: number | null;
  proj_sd?: number | null;
  proj_minutes?: number | null;
  proj_dist?: string | null;
  /** games in the two-season sample and this season's share of the blend */
  n_this?: number | null;
  n_last?: number | null;
  weight_this?: number | null;
  /** modelled probability of the pick side at the pick line */
  /** MODEL_TRUST × p_model + (1 − MODEL_TRUST) × p_market: the probability the edge is judged on */
  p_hat?: number | null;
  /** the pick price's break-even probability (what you pay); edge_value = p_hat − breakeven_price */
  breakeven_price?: number | null;
  market_basis?: "pick" | "shifted" | null;
  consensus_line?: number | null;
  /** books at the consensus line when the pick's number was shopped from one book */
  consensus_books?: number | null;
  line_shopped?: boolean;
  p_model?: number | null;
  /** de-vigged market probability of the pick side at the pick line (shifted when the market moved) */
  p_market?: number | null;
  /** p_model − p_market; the number behind the grade ("edge" is the side) */
  edge_value?: number | null;
  /** first line seen today and the move since, signed toward the pick (+ = market moved our way) */
  open_line?: number | null;
  line_move?: number | null;
  /** opponent's by-position stat allowed (z, + = allows more) and the zone/playtype matchup (points) */
  dvp_z?: number | null;
  matchup_z?: number | null;
  /** OUT starters on the player's own team */
  teammate_out?: number;
  /** stat paused (shown, never published) / dropped by a board cap */
  paused?: boolean;
  cap_note?: string | null;
  /** guards that failed: "price" | "books" | "line_size" | "minutes" | "passing" (assists without the passing tables) */
  guards?: string[];
  /** "edge-1" (projected and priced) or "filter-0" (the legacy filter) */
  model_version?: "edge-1" | "filter-0" | string;
  /* ── round 19: assists from passing (absent on boards before it) ── */
  /** "passing" when the assists were projected from passing (src/passing.py) */
  proj_source?: "passing" | string | null;
  /** the box-score projection the passing one replaced, for comparison */
  proj_mu_box?: number | null;
  passing?: PassingPieces | null;
}

/** The passing projection's pieces, per game at the projected minutes (src/passing.py) */
export interface PassingPieces {
  /** passes made */
  passes: number | null;
  /** potential assists (passes that lead to a shot within one dribble) */
  pot_ast: number | null;
  pa_per_pass: number | null;
  /** share of potential assists that become assists: the projection's, this season's so far, and the prior it is pulled toward */
  conv: number | null;
  conv_seen: number | null;
  conv_prior: number | null;
  /** his team's shooting on assisted shots vs the league (scales the conversion prior) */
  team_shooting: number | null;
  /** tonight's opponent: FG% allowed on those shots, and assisted share of makes allowed, vs the league */
  opp_shooting: number | null;
  opp_share: number | null;
  /** minutes x the per-minute rate, before the matchup */
  base: number | null;
  weight_this: number | null;
  gp_this: number | null;
  gp_last: number | null;
}

/** The season's closing-line value (src/record.py) */
export interface SeasonClv {
  /** decided picks the grader found a close for */
  n: number;
  /** mean points in the pick's favour between the line taken and the close */
  avg_line: number | null;
  /** share of those picks that beat the close */
  beat_close: number | null;
  /** mean probability CLV (de-vigged close − pick-time price, at the pick line) */
  avg_prob: number | null;
}

/** What src/caps.py did to the board */
export interface BoardCaps {
  kept: number;
  dropped: number;
  by_reason?: { per_player?: number; per_game?: number; board?: number };
  limits?: { max_picks?: number; max_per_game?: number; max_per_player?: number };
}

/** One cut of the season's graded record (src/record.py) */
export interface RecordCut {
  label: string;
  hits: number;
  misses: number;
  units: number;
  n: number;
  rate: number | null;
  record: string;
}

export interface NightRecord {
  date: string;
  hits: number;
  misses: number;
  units: number;
  rate: number | null;
  record: string;
}

export interface SeasonRecord {
  season: string;
  nights: number;
  hits: number;
  misses: number;
  units: number;
  rate: number | null;
  record: string;
  voided: number;
  breakeven: number;
  min_sample: number;
  segments: Record<string, RecordCut>;
  /** cuts with min_sample+ picks that are ahead of break-even or in profit, best first */
  highlights: RecordCut[];
  best_night: NightRecord | null;
  last_night: NightRecord | null;
  /** +k winning nights in a row, -k losing, 0 none */
  streak: number;
  /** closing-line value over the season's decided picks (boards before round 18: absent) */
  clv?: SeasonClv | null;
}

export interface HitrateSummary {
  avg: number | null;
  above_threshold: number;
  below_threshold: number;
  unavailable: number;
  threshold: number;
  distribution: {
    "60_plus": number;
    "56_to_60": number;
    "50_to_56": number;
    below_50: number;
  };
}

export interface DayResult {
  date: string;
  season?: string;
  total_picks: number;
  hits: number;
  misses: number;
  voided?: number;
  units?: number;
  /** the night's mean closing-line value and the share of picks that beat the close (when closes were on file) */
  clv_avg_line?: number | null;
  clv_beat?: number | null;
}

export interface PropsData {
  date: string;
  season?: string;
  stats_season?: string;
  stats_season_reason?: string;
  run_at: string;
  run_at_iso: string;
  /** "full" (night-before / morning board) or "fill-holds" (afternoon re-check that kept the board's lines) */
  run_mode?: "full" | "fill-holds";
  lines_locked?: number;
  candidates_analyzed: number;
  valid_count: number;
  watchlist_count: number;
  thresholds: {
    condition_pass_rate: number;
    condition_pass_rate_points?: number;
    hitrate: number;
    /** the edge model's bars (round 18): edges as probabilities, price as American odds */
    min_edge?: number;
    strong_edge?: number;
    max_price?: number;
    min_books?: number;
    min_line?: number;
    min_proj_minutes?: number;
    max_picks?: number;
    max_per_game?: number;
  };
  valid_picks: PropPick[];
  watchlist: PropPick[];
  all_props: PropPick[];
  valid_combos?: PropPick[];
  watchlist_combos?: PropPick[];
  on_hold?: PropPick[];
  on_hold_count?: number;
  /** valid picks the learned model cut as -EV (ACTIVE mode only) */
  demoted?: PropPick[];
  demoted_count?: number;
  /** round 18: "edge-1" once the board is projected and priced, "filter-0" before */
  model_version?: "edge-1" | "filter-0" | string;
  /** stats evaluated but never published (PAUSED_STATS), e.g. ["assists"] */
  paused_stats?: string[];
  /** what the board caps did (null when they were not applied) */
  caps?: BoardCaps | null;
  /** A-grade edges in a paused stat, and A-grade edges a cap dropped (best first) */
  paused?: PropPick[];
  capped?: PropPick[];
  paused_count?: number;
  capped_count?: number;
  combo_valid_count?: number;
  combo_watchlist_count?: number;
  hitrate_summary?: HitrateSummary;
  model_results?: DayResult[];
  learning?: Learning | null;
  season_record?: SeasonRecord | null;
}

export interface SegmentRecord {
  hits: number;
  misses: number;
  rate: number | null;
  /** sample size and a Wilson 95% interval on the rate (round 18) */
  n?: number;
  ci?: [number, number] | null;
}

export interface Postmortem {
  date: string;
  published: { total_picks?: number; hits?: number; misses?: number; voided?: number; units?: number };
  all_props: SegmentRecord;
  went_right: (SegmentRecord & { segment: string })[];
  went_wrong: (SegmentRecord & { segment: string })[];
  biggest_misses: { player: string; stat: string; edge: string; line: number; actual: number }[];
  near_misses?: number;
  picks_cut_by_model?: number;
  scope?: string;
}

export interface Learning {
  mode: "active" | "shadow";
  trusted: boolean;
  trained_at?: string;
  n_rows: number;
  n_days: number;
  note?: string | null;
  base_rate?: number;
  pass_streak?: number;
  cv?: {
    logloss?: number;
    baseline_logloss?: number;
    baseline?: string;
    gain?: number;
    effective_rows?: number;
    effective_nights?: number;
    scored_nights?: number;
    note?: string;
    gate_check?: { verdict?: string; passed?: boolean | null };
  } | null;
  factors?: { feature: string; label: string; weight: number; effect: string }[] | null;
  postmortem?: Postmortem | null;
}
