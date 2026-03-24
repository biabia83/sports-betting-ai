"""
grader.py — The Truth Engine.

Compares AI predictions from daily_picks.json against actual NBA player stats
fetched from nba_api. Outputs graded results to graded_history.json.

Usage:
  python grader.py                        # grade today's picks
  python grader.py --date 2026-02-16      # grade a specific date
"""

import json
import os
import argparse
from datetime import datetime
from typing import Dict, List, Optional

from nba_api.stats.static import players as nba_players
from nba_api.stats.endpoints import playergamelog
from fuzzywuzzy import process as fuzz_process

# ---------------------------------------------------------------------------
# Stat type mapping — maps pick stat names to nba_api column names
# ---------------------------------------------------------------------------
STAT_MAP = {
    "points":       "PTS",
    "pts":          "PTS",
    "rebounds":     "REB",
    "reb":          "REB",
    "assists":      "AST",
    "ast":          "AST",
    "steals":       "STL",
    "stl":          "STL",
    "blocks":       "BLK",
    "blk":          "BLK",
    "threes":       "FG3M",
    "3pm":          "FG3M",
    "three_pointers_made": "FG3M",
    "turnovers":    "TOV",
    "tov":          "TOV",
    "pra":          "PRA",   # computed: PTS + REB + AST
    "pts+reb+ast":  "PRA",
    "pr":           "PR",    # computed: PTS + REB
    "pts+reb":      "PR",
    "pa":           "PA",    # computed: PTS + AST
    "pts+ast":      "PA",
    "ra":           "RA",    # computed: REB + AST
    "reb+ast":      "RA",
}

PICKS_FILE = os.path.join(os.path.dirname(__file__), "..", "daily_picks.json")
OUTPUT_FILE = os.path.join(os.path.dirname(__file__), "..", "graded_history.json")


# ---------------------------------------------------------------------------
# GradeManager
# ---------------------------------------------------------------------------
class GradeManager:
    """Look up real NBA stats and grade Over/Under picks."""

    def __init__(self):
        self._all_players = nba_players.get_players()
        self._name_list = [p["full_name"] for p in self._all_players]

    # ---- Player lookup with fuzzy matching --------------------------------

    def find_player_id(self, name: str, threshold: int = 80) -> Optional[int]:
        """
        Resolve a player name to an nba_api player_id.
        Uses fuzzywuzzy to handle accent marks, abbreviations, etc.
        """
        match, score = fuzz_process.extractOne(name, self._name_list)
        if score < threshold:
            print(f"  [fuzzy] No confident match for '{name}' (best: '{match}' @ {score})")
            return None

        for p in self._all_players:
            if p["full_name"] == match:
                return p["id"]
        return None

    # ---- Fetch game log for a specific date -------------------------------

    def get_stat(
        self, player_id: int, stat_type: str, game_date: str
    ) -> Optional[float]:
        """
        Fetch a player's stat for a specific date.
        Returns None if the player didn't play (DNP).
        game_date format: 'YYYY-MM-DD'
        """
        # nba_api expects season like '2025-26'
        season = self._date_to_season(game_date)

        log = playergamelog.PlayerGameLog(
            player_id=player_id,
            season=season,
            season_type_all_star="Regular Season",
        )
        df = log.get_data_frames()[0]

        if df.empty:
            return None

        # nba_api GAME_DATE format: 'FEB 17, 2026'
        target = datetime.strptime(game_date, "%Y-%m-%d")
        df["GAME_DATE_PARSED"] = df["GAME_DATE"].apply(
            lambda d: datetime.strptime(d, "%b %d, %Y")
        )
        row = df[df["GAME_DATE_PARSED"] == target]

        if row.empty:
            return None  # DNP or no game that day

        row = row.iloc[0]

        # Handle combo stats
        col = STAT_MAP.get(stat_type.lower())
        if col is None:
            print(f"  [stat] Unknown stat_type: '{stat_type}'")
            return None

        if col == "PRA":
            return float(row["PTS"] + row["REB"] + row["AST"])
        if col == "PR":
            return float(row["PTS"] + row["REB"])
        if col == "PA":
            return float(row["PTS"] + row["AST"])
        if col == "RA":
            return float(row["REB"] + row["AST"])

        return float(row[col])

    # ---- Grade a single pick ----------------------------------------------

    def grade_pick(self, pick: dict) -> dict:
        """
        Grade one pick dict. Expected keys:
          player_name, stat_type, line, prediction ('Over'/'Under'), date

        Returns the pick dict updated with:
          actual_value, status ('WIN'/'LOSS'/'VOID'), graded_at
        """
        player_name = pick.get("player_name", "")
        stat_type = pick.get("stat_type", "")
        line = pick.get("line")
        prediction = pick.get("prediction", "").capitalize()
        game_date = pick.get("date", "")

        pick["graded_at"] = datetime.now().isoformat()

        # Resolve player
        player_id = self.find_player_id(player_name)
        if player_id is None:
            pick["actual_value"] = None
            pick["status"] = "VOID"
            return pick

        # Fetch actual stat
        try:
            actual = self.get_stat(player_id, stat_type, game_date)
        except Exception as e:
            print(f"  [error] Failed to fetch stats for {player_name}: {e}")
            pick["actual_value"] = None
            pick["status"] = "VOID"
            return pick

        if actual is None:
            pick["actual_value"] = None
            pick["status"] = "VOID"
            return pick

        pick["actual_value"] = actual

        # Grade
        if prediction == "Over" and actual > line:
            pick["status"] = "WIN"
        elif prediction == "Under" and actual < line:
            pick["status"] = "WIN"
        elif actual == line:
            pick["status"] = "PUSH"
        else:
            pick["status"] = "LOSS"

        return pick

    # ---- Grade all picks from daily_picks.json ----------------------------

    def grade_all(self, picks_file: str = PICKS_FILE) -> List[Dict]:
        """Load daily_picks.json, grade every pick, return graded list."""
        with open(picks_file, "r") as f:
            data = json.load(f)

        graded = []

        for result in data.get("results", []):
            model = result.get("model", "unknown")
            for pick in result.get("picks", []):
                pick["model"] = model
                print(f"  Grading: {pick.get('player_name', '?')} "
                      f"({pick.get('stat_type', '?')}) — {model}")
                graded_pick = self.grade_pick(pick)
                graded.append(graded_pick)

        return graded

    # ---- Helpers ----------------------------------------------------------

    @staticmethod
    def _date_to_season(game_date: str) -> str:
        """Convert 'YYYY-MM-DD' to nba_api season string like '2025-26'."""
        dt = datetime.strptime(game_date, "%Y-%m-%d")
        year = dt.year
        # NBA season spans two calendar years. Games before October
        # belong to the season that started the prior year.
        if dt.month < 10:
            start_year = year - 1
        else:
            start_year = year
        end_year = start_year + 1
        return f"{start_year}-{str(end_year)[-2:]}"


# ---------------------------------------------------------------------------
# Save results
# ---------------------------------------------------------------------------
def save_graded(graded: List[Dict], output_file: str = OUTPUT_FILE):
    """Append graded picks to graded_history.json."""
    history = []
    if os.path.exists(output_file):
        with open(output_file, "r") as f:
            history = json.load(f)

    history.extend(graded)

    with open(output_file, "w") as f:
        json.dump(history, f, indent=2)

    print(f"\n  Saved {len(graded)} graded picks to {output_file}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Grade AI picks against real NBA stats")
    parser.add_argument("--file", type=str, default=PICKS_FILE, help="Path to picks JSON")
    parser.add_argument("--output", type=str, default=OUTPUT_FILE, help="Path to output JSON")
    args = parser.parse_args()

    grader = GradeManager()
    graded = grader.grade_all(args.file)

    if graded:
        save_graded(graded, args.output)

        wins = sum(1 for p in graded if p.get("status") == "WIN")
        losses = sum(1 for p in graded if p.get("status") == "LOSS")
        voids = sum(1 for p in graded if p.get("status") == "VOID")
        pushes = sum(1 for p in graded if p.get("status") == "PUSH")

        print(f"\n  Results: {wins}W - {losses}L - {pushes}P - {voids}V")
    else:
        print("  No picks to grade.")
