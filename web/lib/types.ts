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
  on_hold?: boolean;
  hold_reason?: string | null;
  /** injury-report label, e.g. "GTD · Ankle" */
  injury?: string | null;
  grade: string;
  is_valid: boolean;
  zone_details: string[];
  playtype_details: string[];
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
  combo_valid_count?: number;
  combo_watchlist_count?: number;
  hitrate_summary?: HitrateSummary;
  model_results?: DayResult[];
}
