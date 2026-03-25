"""
grader.py — Automated post-game grader for EV Bot picks.

Fetches ungraded picks from the Supabase ev_picks table, looks up actual
NBA stats via nba_api, and updates each row with the result.

Usage:
  python grader.py
"""

import os
import time
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv
from supabase import create_client
from nba_api.stats.static import players as nba_players
from nba_api.stats.endpoints import playergamelog
from fuzzywuzzy import process as fuzz_process

load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("[grader] ERROR: SUPABASE_URL and SUPABASE_KEY must be set in .env")
    exit(1)

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# Stat type → nba_api column mapping (reused from tools/grader.py)
STAT_MAP = {
    "points":       "PTS",
    "rebounds":     "REB",
    "assists":      "AST",
    "steals":       "STL",
    "blocks":       "BLK",
    "threes":       "FG3M",
    "turnovers":    "TOV",
    "pra":          "PRA",
    "pts+reb+ast":  "PRA",
    "pr":           "PR",
    "pts+reb":      "PR",
    "pa":           "PA",
    "pts+ast":      "PA",
    "ra":           "RA",
    "reb+ast":      "RA",
}


# ---------------------------------------------------------------------------
# NBA API helpers
# ---------------------------------------------------------------------------
ALL_PLAYERS = nba_players.get_players()
PLAYER_NAMES = [p["full_name"] for p in ALL_PLAYERS]


def find_player_id(name: str, threshold: int = 80) -> Optional[int]:
    """Resolve a player name to an nba_api player_id using fuzzy matching."""
    match, score = fuzz_process.extractOne(name, PLAYER_NAMES)
    if score < threshold:
        return None
    for p in ALL_PLAYERS:
        if p["full_name"] == match:
            return p["id"]
    return None


def date_to_season(game_date: str) -> str:
    """Convert 'YYYY-MM-DD' to nba_api season string like '2025-26'."""
    dt = datetime.strptime(game_date, "%Y-%m-%d")
    start_year = dt.year - 1 if dt.month < 10 else dt.year
    end_year = start_year + 1
    return f"{start_year}-{str(end_year)[-2:]}"


def get_actual_stat(player_id: int, stat_type: str, game_date: str) -> Optional[float]:
    """Fetch a player's actual stat for a specific game date."""
    season = date_to_season(game_date)

    try:
        log = playergamelog.PlayerGameLog(
            player_id=player_id,
            season=season,
            season_type_all_star="Regular Season",
        )
        df = log.get_data_frames()[0]
    except Exception as e:
        print(f"    [error] Failed to fetch game log: {e}")
        return None

    if df.empty:
        return None

    target = datetime.strptime(game_date, "%Y-%m-%d")
    df["GAME_DATE_PARSED"] = df["GAME_DATE"].apply(
        lambda d: datetime.strptime(d, "%b %d, %Y")
    )
    row = df[df["GAME_DATE_PARSED"] == target]

    if row.empty:
        return None

    row = row.iloc[0]
    col = STAT_MAP.get(stat_type.lower())
    if col is None:
        print(f"    [error] Unknown stat_type: '{stat_type}'")
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


# ---------------------------------------------------------------------------
# Grading logic
# ---------------------------------------------------------------------------
def grade_pick(pick: dict) -> dict:
    """
    Grade a single pick. Returns a dict with 'result' and 'actual_value'.
    """
    player_name = pick.get("player_name", "")
    stat_type = pick.get("stat_type", "")
    line = pick.get("line")
    direction = pick.get("direction", "Over")
    created_at = pick.get("created_at", "")

    # Extract game date from created_at timestamp
    if created_at:
        game_date = created_at[:10]  # 'YYYY-MM-DD' from ISO timestamp
    else:
        return {"result": "Void", "actual_value": None}

    # Resolve player
    player_id = find_player_id(player_name)
    if player_id is None:
        print(f"    Could not resolve player: {player_name}")
        return {"result": "Void", "actual_value": None}

    # Fetch actual stat
    actual = get_actual_stat(player_id, stat_type, game_date)
    if actual is None:
        print(f"    No game data found for {player_name} on {game_date}")
        return {"result": "Void", "actual_value": None}

    # Compare
    if actual == line:
        result = "Push"
    elif direction == "Over":
        result = "Win" if actual > line else "Loss"
    elif direction == "Under":
        result = "Win" if actual < line else "Loss"
    else:
        result = "Win" if actual > line else "Loss"

    return {"result": result, "actual_value": actual}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("""
╔══════════════════════════════════════════════╗
║       GRADER  —  Post-Game Pick Grader       ║
║  Fetches actual stats · Updates Supabase     ║
╚══════════════════════════════════════════════╝
    """)

    # Fetch ungraded picks
    print("[Step 1] Fetching ungraded picks from Supabase...")
    resp = supabase.table("ev_picks").select("*").is_("result", "null").execute()
    picks = resp.data

    if not picks:
        print("[grader] No ungraded picks found. All caught up.")
        return

    print(f"[grader] Found {len(picks)} ungraded picks.\n")

    wins = losses = pushes = voids = 0

    for i, pick in enumerate(picks, 1):
        player = pick.get("player_name", "?")
        stat = pick.get("stat_type", "?")
        line = pick.get("line", "?")
        direction = pick.get("direction", "Over")
        pick_id = pick.get("id")

        print(f"  [{i}/{len(picks)}] {player} — {stat} {direction} {line}")

        result = grade_pick(pick)

        # Update Supabase
        update_data = {"result": result["result"]}
        if result["actual_value"] is not None:
            update_data["actual_value"] = result["actual_value"]

        try:
            supabase.table("ev_picks").update(update_data).eq("id", pick_id).execute()
        except Exception as e:
            print(f"    [error] Failed to update pick {pick_id}: {e}")
            continue

        status = result["result"]
        actual_display = result["actual_value"] if result["actual_value"] is not None else "N/A"
        print(f"    → {status} (actual: {actual_display})")

        if status == "Win":
            wins += 1
        elif status == "Loss":
            losses += 1
        elif status == "Push":
            pushes += 1
        else:
            voids += 1

        # Rate limit nba_api calls
        time.sleep(0.6)

    print(f"\n{'='*50}")
    print(f"  RESULTS: {wins}W - {losses}L - {pushes}P - {voids}V")
    total = wins + losses
    if total > 0:
        print(f"  Win Rate: {wins / total * 100:.1f}%")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
