"""
grader.py — Automated post-game grader for EV Bot picks.

Fetches ungraded picks from the Supabase ev_picks table, looks up actual
NBA stats via nba_api, and updates each row with the result.

Usage:
  python grader.py
"""

import os
import sys
import time
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv
from supabase import create_client
from nba_api.stats.static import players as nba_players
from nba_api.stats.endpoints import playergamelog
from fuzzywuzzy import process as fuzz_process

load_dotenv()

# Force unbuffered output so GitHub Actions logs show prints in real time
sys.stdout.reconfigure(line_buffering=True)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

NBA_API_TIMEOUT = 5  # seconds — drop connection fast if NBA.com tarpits

# Custom headers to avoid NBA.com blocking cloud IPs
NBA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.nba.com/",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "application/json, text/plain, */*",
}

if not SUPABASE_URL or not SUPABASE_KEY:
    print("[grader] ERROR: SUPABASE_URL and SUPABASE_KEY must be set in .env")
    exit(1)

print("[grader] Connecting to Supabase...")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
print("[grader] Supabase client ready.")

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
print("[grader] Loading nba_api player list...")
ALL_PLAYERS = nba_players.get_players()
PLAYER_NAMES = [p["full_name"] for p in ALL_PLAYERS]
print(f"[grader] Loaded {len(ALL_PLAYERS)} players from nba_api.")


def find_player_id(name: str, threshold: int = 80) -> Optional[int]:
    """Resolve a player name to an nba_api player_id using fuzzy matching."""
    print(f"    [fuzzy] Resolving '{name}'...")
    match, score = fuzz_process.extractOne(name, PLAYER_NAMES)
    print(f"    [fuzzy] Best match: '{match}' (score: {score})")
    if score < threshold:
        print(f"    [fuzzy] Below threshold ({threshold}), skipping.")
        return None
    for p in ALL_PLAYERS:
        if p["full_name"] == match:
            print(f"    [fuzzy] Resolved to player_id={p['id']}")
            return p["id"]
    return None


def date_to_season(game_date: str) -> str:
    """Convert 'YYYY-MM-DD' to nba_api season string like '2025-26'."""
    dt = datetime.strptime(game_date, "%Y-%m-%d")
    start_year = dt.year - 1 if dt.month < 10 else dt.year
    end_year = start_year + 1
    return f"{start_year}-{str(end_year)[-2:]}"


def get_actual_stat(player_id: int, stat_type: str, game_date: str, player_name: str = "") -> Optional[float]:
    """Fetch a player's actual stat for a specific game date."""
    season = date_to_season(game_date)

    print(f"    [nba_api] Fetching game log: player_id={player_id}, season={season}, timeout={NBA_API_TIMEOUT}s...")
    print(f"    [nba_api] Using custom headers to bypass cloud IP blocking...")
    try:
        log = playergamelog.PlayerGameLog(
            player_id=player_id,
            season=season,
            season_type_all_star="Regular Season",
            headers=NBA_HEADERS,
            timeout=NBA_API_TIMEOUT,
        )
        print(f"    [nba_api] Request sent, parsing data frames...")
        df = log.get_data_frames()[0]
        print(f"    [nba_api] Got {len(df)} game log rows.")
    except Exception as e:
        print(f"    [nba_api] WARNING: Blocked by NBA API for [{player_name or player_id}] — {e}")
        return None

    if df.empty:
        print(f"    [nba_api] Empty game log — player may not have played this season.")
        return None

    target = datetime.strptime(game_date, "%Y-%m-%d")
    df["GAME_DATE_PARSED"] = df["GAME_DATE"].apply(
        lambda d: datetime.strptime(d, "%b %d, %Y")
    )
    row = df[df["GAME_DATE_PARSED"] == target]

    if row.empty:
        print(f"    [nba_api] No game found on {game_date} (DNP or no game).")
        return None

    row = row.iloc[0]
    col = STAT_MAP.get(stat_type.lower())
    if col is None:
        print(f"    [nba_api] Unknown stat_type: '{stat_type}'")
        return None

    if col == "PRA":
        val = float(row["PTS"] + row["REB"] + row["AST"])
    elif col == "PR":
        val = float(row["PTS"] + row["REB"])
    elif col == "PA":
        val = float(row["PTS"] + row["AST"])
    elif col == "RA":
        val = float(row["REB"] + row["AST"])
    else:
        val = float(row[col])

    print(f"    [nba_api] Actual {stat_type} = {val}")
    return val


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
        print(f"    [grade] No created_at timestamp, marking Void.")
        return {"result": "Void", "actual_value": None}

    print(f"    [grade] Game date: {game_date}, direction: {direction}, line: {line}")

    # Resolve player
    player_id = find_player_id(player_name)
    if player_id is None:
        print(f"    [grade] Could not resolve player: {player_name} — Void")
        return {"result": "Void", "actual_value": None}

    # Fetch actual stat
    actual = get_actual_stat(player_id, stat_type, game_date, player_name)
    if actual is None:
        print(f"    [grade] No game data for {player_name} on {game_date} — Void")
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

    print(f"    [grade] actual={actual} vs line={line} ({direction}) → {result}")
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
    print("[Step 1] Querying Supabase for ungraded picks (result IS NULL)...")
    resp = supabase.table("ev_picks").select("*").is_("result", "null").execute()
    picks = resp.data
    print(f"[Step 1] Supabase returned {len(picks)} ungraded picks.")

    if not picks:
        print("[grader] No ungraded picks found. All caught up.")
        return

    wins = losses = pushes = voids = 0

    for i, pick in enumerate(picks, 1):
        player = pick.get("player_name", "?")
        stat = pick.get("stat_type", "?")
        line = pick.get("line", "?")
        direction = pick.get("direction", "Over")
        pick_id = pick.get("id")

        print(f"\n  [{i}/{len(picks)}] {player} — {stat} {direction} {line} (id={pick_id})")

        result = grade_pick(pick)

        # Update Supabase
        update_data = {"result": result["result"]}
        if result["actual_value"] is not None:
            update_data["actual_value"] = result["actual_value"]

        print(f"    [supabase] Updating pick {pick_id} with {update_data}...")
        try:
            supabase.table("ev_picks").update(update_data).eq("id", pick_id).execute()
            print(f"    [supabase] Update successful.")
        except Exception as e:
            print(f"    [supabase] ERROR: Failed to update pick {pick_id}: {e}")
            continue

        status = result["result"]
        actual_display = result["actual_value"] if result["actual_value"] is not None else "N/A"
        print(f"    => {status} (actual: {actual_display})")

        if status == "Win":
            wins += 1
        elif status == "Loss":
            losses += 1
        elif status == "Push":
            pushes += 1
        else:
            voids += 1

        # Rate limit nba_api calls
        print(f"    [sleep] Waiting 0.6s before next pick...")
        time.sleep(0.6)

    print(f"\n{'='*50}")
    print(f"  RESULTS: {wins}W - {losses}L - {pushes}P - {voids}V")
    total = wins + losses
    if total > 0:
        print(f"  Win Rate: {wins / total * 100:.1f}%")
    print(f"{'='*50}")
    print("[grader] Done.")


if __name__ == "__main__":
    main()
