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
  /** 1 = best in its list (A+ first, then hit rate vs the line, conditions met, books) */
  rank?: number;
  zone_details: string[];
  playtype_details: string[];
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
    hitrate: number;
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
