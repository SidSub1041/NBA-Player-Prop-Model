"""
Convert the archived props_2026-04-08.txt into latest.json for the web dashboard.
"""
import json, os, re

# Parsed data from props_2026-04-08.txt
valid_picks = [
    {"player_name": "Kawhi Leonard",   "team": "LAC", "opponent": "OKC", "position": "SG", "stat": "assists",  "edge": "under", "total_points": 1, "total_conditions": 1, "pass_rate": 1.0, "hitrate": 0.508, "hitrate_source": "nba_api_gamelog", "season_avg": 3.6, "games_played": None, "ud_line": 3.5,  "ud_over_odds": "+126", "ud_under_odds": "-157", "grade": "A",  "is_valid": True,  "zone_details": [], "playtype_details": []},
    {"player_name": "Cooper Flagg",    "team": "DAL", "opponent": "PHX", "position": "SG", "stat": "assists",  "edge": "under", "total_points": 1, "total_conditions": 1, "pass_rate": 1.0, "hitrate": 0.507, "hitrate_source": "nba_api_gamelog", "season_avg": 4.5, "games_played": None, "ud_line": 5.5,  "ud_over_odds": "+123", "ud_under_odds": "-152", "grade": "A",  "is_valid": True,  "zone_details": [], "playtype_details": []},
    {"player_name": "Devin Booker",    "team": "PHX", "opponent": "DAL", "position": "SF", "stat": "assists",  "edge": "over",  "total_points": 1, "total_conditions": 1, "pass_rate": 1.0, "hitrate": 0.556, "hitrate_source": "nba_api_gamelog", "season_avg": 6.0, "games_played": None, "ud_line": 6.5,  "ud_over_odds": "+112", "ud_under_odds": "-134", "grade": "A",  "is_valid": True,  "zone_details": [], "playtype_details": []},
    {"player_name": "Cade Cunningham", "team": "DET", "opponent": "MIL", "position": "SF", "stat": "assists",  "edge": "over",  "total_points": 1, "total_conditions": 1, "pass_rate": 1.0, "hitrate": 0.574, "hitrate_source": "nba_api_gamelog", "season_avg": 9.9, "games_played": None, "ud_line": 7.5,  "ud_over_odds": "+129", "ud_under_odds": "-159", "grade": "A+", "is_valid": True,  "zone_details": [], "playtype_details": []},
    {"player_name": "James Harden",    "team": "CLE", "opponent": "ATL", "position": "PF", "stat": "rebounds", "edge": "over",  "total_points": 1, "total_conditions": 1, "pass_rate": 1.0, "hitrate": 0.544, "hitrate_source": "nba_api_gamelog", "season_avg": 4.9, "games_played": None, "ud_line": 4.5,  "ud_over_odds": "+103", "ud_under_odds": "-125", "grade": "A",  "is_valid": True,  "zone_details": [], "playtype_details": []},
    {"player_name": "Jalen Johnson",   "team": "ATL", "opponent": "CLE", "position": "PF", "stat": "assists",  "edge": "over",  "total_points": 1, "total_conditions": 1, "pass_rate": 1.0, "hitrate": 0.543, "hitrate_source": "nba_api_gamelog", "season_avg": 8.0, "games_played": None, "ud_line": 7.5,  "ud_over_odds": "-136", "ud_under_odds": "+111", "grade": "A",  "is_valid": True,  "zone_details": [], "playtype_details": []},
    {"player_name": "Anthony Edwards", "team": "MIN", "opponent": "ORL", "position": "PF", "stat": "assists",  "edge": "under", "total_points": 1, "total_conditions": 1, "pass_rate": 1.0, "hitrate": 0.517, "hitrate_source": "nba_api_gamelog", "season_avg": 3.7, "games_played": None, "ud_line": None, "ud_over_odds": None,   "ud_under_odds": None,   "grade": "A",  "is_valid": True,  "zone_details": [], "playtype_details": []},
    {"player_name": "Deni Avdija",     "team": "POR", "opponent": "SAS", "position": "PF", "stat": "rebounds", "edge": "under", "total_points": 1, "total_conditions": 1, "pass_rate": 1.0, "hitrate": 0.524, "hitrate_source": "nba_api_gamelog", "season_avg": 7.0, "games_played": None, "ud_line": 7.5,  "ud_over_odds": "+105", "ud_under_odds": "-129", "grade": "A",  "is_valid": True,  "zone_details": [], "playtype_details": []},
]

watchlist = [
    {"player_name": "James Harden",            "team": "CLE", "opponent": "ATL", "position": "SG", "stat": "assists",  "edge": "under", "total_points": 1, "total_conditions": 1, "pass_rate": 1.0, "hitrate": 0.397, "hitrate_source": "nba_api_gamelog", "season_avg": 8.1, "games_played": None, "ud_line": 8.5,  "ud_over_odds": "+115", "ud_under_odds": "-141", "grade": "B", "is_valid": False, "zone_details": [], "playtype_details": []},
    {"player_name": "Devin Booker",            "team": "PHX", "opponent": "DAL", "position": "SG", "stat": "rebounds", "edge": "over",  "total_points": 1, "total_conditions": 1, "pass_rate": 1.0, "hitrate": 0.476, "hitrate_source": "nba_api_gamelog", "season_avg": 3.9, "games_played": None, "ud_line": 3.5,  "ud_over_odds": "-139", "ud_under_odds": "+114", "grade": "B", "is_valid": False, "zone_details": [], "playtype_details": []},
    {"player_name": "Toby Okani",              "team": "MEM", "opponent": "DEN", "position": "SG", "stat": "rebounds", "edge": "under", "total_points": 1, "total_conditions": 1, "pass_rate": 1.0, "hitrate": None,  "hitrate_source": "unavailable",    "season_avg": None,"games_played": None, "ud_line": None, "ud_over_odds": None,   "ud_under_odds": None,   "grade": "B", "is_valid": False, "zone_details": [], "playtype_details": []},
    {"player_name": "Kevin Porter Jr.",        "team": "MIL", "opponent": "DET", "position": "SG", "stat": "rebounds", "edge": "under", "total_points": 1, "total_conditions": 1, "pass_rate": 1.0, "hitrate": 0.47,  "hitrate_source": "nba_api_gamelog", "season_avg": None,"games_played": None, "ud_line": None, "ud_over_odds": None,   "ud_under_odds": None,   "grade": "B", "is_valid": False, "zone_details": [], "playtype_details": []},
    {"player_name": "Kevin Porter Jr.",        "team": "MIL", "opponent": "DET", "position": "SG", "stat": "assists",  "edge": "under", "total_points": 1, "total_conditions": 1, "pass_rate": 1.0, "hitrate": 0.42,  "hitrate_source": "nba_api_gamelog", "season_avg": None,"games_played": None, "ud_line": None, "ud_over_odds": None,   "ud_under_odds": None,   "grade": "B", "is_valid": False, "zone_details": [], "playtype_details": []},
    {"player_name": "Shai Gilgeous-Alexander", "team": "OKC", "opponent": "LAC", "position": "SG", "stat": "rebounds", "edge": "under", "total_points": 1, "total_conditions": 1, "pass_rate": 1.0, "hitrate": 0.48,  "hitrate_source": "nba_api_gamelog", "season_avg": None,"games_played": None, "ud_line": 3.5,  "ud_over_odds": None,   "ud_under_odds": "+132", "grade": "B", "is_valid": False, "zone_details": [], "playtype_details": []},
    {"player_name": "Kawhi Leonard",           "team": "LAC", "opponent": "OKC", "position": "SG", "stat": "rebounds", "edge": "over",  "total_points": 1, "total_conditions": 1, "pass_rate": 1.0, "hitrate": 0.46,  "hitrate_source": "nba_api_gamelog", "season_avg": None,"games_played": None, "ud_line": 6.5,  "ud_over_odds": "-105", "ud_under_odds": None,   "grade": "B", "is_valid": False, "zone_details": [], "playtype_details": []},
    {"player_name": "Anthony Edwards",         "team": "MIN", "opponent": "ORL", "position": "SG", "stat": "rebounds", "edge": "under", "total_points": 1, "total_conditions": 1, "pass_rate": 1.0, "hitrate": 0.42,  "hitrate_source": "nba_api_gamelog", "season_avg": None,"games_played": None, "ud_line": None, "ud_over_odds": None,   "ud_under_odds": None,   "grade": "B", "is_valid": False, "zone_details": [], "playtype_details": []},
    {"player_name": "Jalen Johnson",           "team": "ATL", "opponent": "CLE", "position": "SF", "stat": "rebounds", "edge": "under", "total_points": 1, "total_conditions": 1, "pass_rate": 1.0, "hitrate": 0.47,  "hitrate_source": "nba_api_gamelog", "season_avg": None,"games_played": None, "ud_line": 10.5, "ud_over_odds": None,   "ud_under_odds": "-137", "grade": "B", "is_valid": False, "zone_details": [], "playtype_details": []},
    {"player_name": "Jamal Murray",            "team": "DEN", "opponent": "MEM", "position": "SF", "stat": "rebounds", "edge": "over",  "total_points": 1, "total_conditions": 1, "pass_rate": 1.0, "hitrate": 0.49,  "hitrate_source": "nba_api_gamelog", "season_avg": None,"games_played": None, "ud_line": 4.5,  "ud_over_odds": "+108", "ud_under_odds": None,   "grade": "B", "is_valid": False, "zone_details": [], "playtype_details": []},
    {"player_name": "Jamal Murray",            "team": "DEN", "opponent": "MEM", "position": "SF", "stat": "assists",  "edge": "over",  "total_points": 1, "total_conditions": 1, "pass_rate": 1.0, "hitrate": 0.39,  "hitrate_source": "nba_api_gamelog", "season_avg": None,"games_played": None, "ud_line": 6.5,  "ud_over_odds": None,   "ud_under_odds": "-130", "grade": "B", "is_valid": False, "zone_details": [], "playtype_details": []},
    {"player_name": "Cade Cunningham",         "team": "DET", "opponent": "MIL", "position": "SF", "stat": "rebounds", "edge": "over",  "total_points": 1, "total_conditions": 1, "pass_rate": 1.0, "hitrate": 0.48,  "hitrate_source": "nba_api_gamelog", "season_avg": None,"games_played": None, "ud_line": 3.5,  "ud_over_odds": "-112", "ud_under_odds": None,   "grade": "B", "is_valid": False, "zone_details": [], "playtype_details": []},
    {"player_name": "De'Aaron Fox",            "team": "SAS", "opponent": "POR", "position": "SF", "stat": "rebounds", "edge": "over",  "total_points": 1, "total_conditions": 1, "pass_rate": 1.0, "hitrate": 0.49,  "hitrate_source": "nba_api_gamelog", "season_avg": None,"games_played": None, "ud_line": 3.5,  "ud_over_odds": "-113", "ud_under_odds": None,   "grade": "B", "is_valid": False, "zone_details": [], "playtype_details": []},
    {"player_name": "Toby Okani",              "team": "MEM", "opponent": "DEN", "position": "PF", "stat": "assists",  "edge": "over",  "total_points": 1, "total_conditions": 1, "pass_rate": 1.0, "hitrate": None,  "hitrate_source": "unavailable",    "season_avg": None,"games_played": None, "ud_line": None, "ud_over_odds": None,   "ud_under_odds": None,   "grade": "B", "is_valid": False, "zone_details": [], "playtype_details": []},
    {"player_name": "Paolo Banchero",          "team": "ORL", "opponent": "MIN", "position": "PF", "stat": "assists",  "edge": "under", "total_points": 1, "total_conditions": 1, "pass_rate": 1.0, "hitrate": 0.44,  "hitrate_source": "nba_api_gamelog", "season_avg": None,"games_played": None, "ud_line": 4.5,  "ud_over_odds": None,   "ud_under_odds": "+118", "grade": "B", "is_valid": False, "zone_details": [], "playtype_details": []},
]

all_props = valid_picks + watchlist

# Hitrate summary
all_hr = [p["hitrate"] for p in all_props if p["hitrate"] is not None]
payload = {
    "date": "2026-04-08",
    "run_at": "01:37 PM",
    "run_at_iso": "2026-04-08T13:37:00",
    "candidates_analyzed": 36,
    "valid_count": len(valid_picks),
    "watchlist_count": len(watchlist),
    "thresholds": {"condition_pass_rate": 0.8, "hitrate": 0.56},
    "valid_picks": valid_picks,
    "watchlist": watchlist,
    "all_props": all_props,
    "hitrate_summary": {
        "avg": round(sum(all_hr) / len(all_hr), 3) if all_hr else None,
        "above_threshold": sum(1 for h in all_hr if h >= 0.56),
        "below_threshold": sum(1 for h in all_hr if h < 0.56),
        "unavailable": sum(1 for p in all_props if p["hitrate"] is None),
        "threshold": 0.56,
        "distribution": {
            "60_plus": sum(1 for h in all_hr if h >= 0.60),
            "56_to_60": sum(1 for h in all_hr if 0.56 <= h < 0.60),
            "50_to_56": sum(1 for h in all_hr if 0.50 <= h < 0.56),
            "below_50": sum(1 for h in all_hr if h < 0.50),
        },
    },
}

out = os.path.join(os.path.dirname(__file__), "web", "public", "data", "latest.json")
os.makedirs(os.path.dirname(out), exist_ok=True)
with open(out, "w") as f:
    json.dump(payload, f, indent=2)
print(f"Wrote {out}")
