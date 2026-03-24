"""
ev_bot.py — +EV Play Finder.

Compares sharp sportsbook odds from The-Odds-API against PrizePicks daily
fantasy lines. When sharp books imply a probability > 55% on an Over/Under
but PrizePicks offers the same line, it's flagged as a +EV play and sent
to Discord.

Usage:
  python ev_bot.py              # run the full pipeline once
  python ev_bot.py --dry-run    # find +EV plays but don't send to Discord
"""

import os
import sys
import json
import argparse
from datetime import datetime
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

# The-Odds-API settings
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
SPORT = "basketball_nba"
MARKETS = "player_points,player_rebounds,player_assists,player_threes,player_blocks,player_steals"
REGIONS = "us"
ODDS_FORMAT = "american"

# Sharp books — these set the most accurate lines
SHARP_BOOKS = {"pinnacle", "betonlineag", "bookmaker", "betcris"}

# EV threshold — implied probability must exceed this to flag a play
EV_THRESHOLD = 0.55

# Fuzzy match threshold (0.0–1.0) for player name matching
FUZZY_THRESHOLD = 0.85

# PrizePicks stat type mapping (PrizePicks API → normalized)
PP_STAT_MAP = {
    "Points":       "points",
    "Rebounds":     "rebounds",
    "Assists":      "assists",
    "3-Point Made": "threes",
    "3-Pointers Made": "threes",
    "Blocked Shots": "blocks",
    "Blocks":       "blocks",
    "Steals":       "steals",
    "Pts+Rebs+Asts": "pra",
    "Fantasy Score": "fantasy",
    "Turnovers":    "turnovers",
}

# Odds-API market → normalized stat type
ODDS_MARKET_MAP = {
    "player_points":    "points",
    "player_rebounds":  "rebounds",
    "player_assists":   "assists",
    "player_threes":    "threes",
    "player_blocks":    "blocks",
    "player_steals":    "steals",
}

# NBA team abbreviation → full name (matching The-Odds-API naming)
NBA_TEAM_MAP = {
    "ATL": "Atlanta Hawks",
    "BOS": "Boston Celtics",
    "BKN": "Brooklyn Nets",
    "CHA": "Charlotte Hornets",
    "CHI": "Chicago Bulls",
    "CLE": "Cleveland Cavaliers",
    "DAL": "Dallas Mavericks",
    "DEN": "Denver Nuggets",
    "DET": "Detroit Pistons",
    "GS":  "Golden State Warriors",
    "GSW": "Golden State Warriors",
    "HOU": "Houston Rockets",
    "IND": "Indiana Pacers",
    "LAC": "Los Angeles Clippers",
    "LAL": "Los Angeles Lakers",
    "MEM": "Memphis Grizzlies",
    "MIA": "Miami Heat",
    "MIL": "Milwaukee Bucks",
    "MIN": "Minnesota Timberwolves",
    "NO":  "New Orleans Pelicans",
    "NOP": "New Orleans Pelicans",
    "NY":  "New York Knicks",
    "NYK": "New York Knicks",
    "OKC": "Oklahoma City Thunder",
    "ORL": "Orlando Magic",
    "PHI": "Philadelphia 76ers",
    "PHX": "Phoenix Suns",
    "POR": "Portland Trail Blazers",
    "SA":  "San Antonio Spurs",
    "SAS": "San Antonio Spurs",
    "SAC": "Sacramento Kings",
    "TOR": "Toronto Raptors",
    "UTA": "Utah Jazz",
    "WAS": "Washington Wizards",
}


# ---------------------------------------------------------------------------
# 1. Fetch sharp odds from The-Odds-API
# ---------------------------------------------------------------------------
def get_sharp_odds() -> List[Dict]:
    """
    Pull player prop odds from The-Odds-API, filtered to sharp books only.

    Returns a list of dicts:
      {
        "player": "LeBron James",
        "stat_type": "points",
        "line": 25.5,
        "over_odds": -115,
        "under_odds": -105,
        "book": "pinnacle",
        "game": "LAL vs BOS"
      }
    """
    if not ODDS_API_KEY:
        print("[ev_bot] ERROR: ODDS_API_KEY not set in .env")
        return []

    url = f"{ODDS_API_BASE}/sports/{SPORT}/events"
    params = {"apiKey": ODDS_API_KEY}

    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        events = resp.json()
    except requests.RequestException as e:
        print(f"[ev_bot] ERROR fetching events: {e}")
        return []

    if not events:
        print("[ev_bot] No NBA events found.")
        return []

    all_props = []

    for event in events:
        event_id = event["id"]
        home = event.get("home_team", "")
        away = event.get("away_team", "")
        game_label = f"{away} vs {home}"

        # Fetch player props for this event
        props_url = f"{ODDS_API_BASE}/sports/{SPORT}/events/{event_id}/odds"
        props_params = {
            "apiKey": ODDS_API_KEY,
            "regions": REGIONS,
            "markets": MARKETS,
            "oddsFormat": ODDS_FORMAT,
        }

        try:
            props_resp = requests.get(props_url, params=props_params, timeout=15)
            props_resp.raise_for_status()
            props_data = props_resp.json()
        except requests.RequestException as e:
            print(f"[ev_bot] ERROR fetching props for {game_label}: {e}")
            continue

        # Parse bookmaker odds
        for bookmaker in props_data.get("bookmakers", []):
            book_key = bookmaker.get("key", "")
            if book_key not in SHARP_BOOKS:
                continue

            for market in bookmaker.get("markets", []):
                market_key = market.get("key", "")
                stat_type = ODDS_MARKET_MAP.get(market_key)
                if stat_type is None:
                    continue

                outcomes = market.get("outcomes", [])
                # Group outcomes by player (description field)
                player_outcomes = {}
                for outcome in outcomes:
                    player = outcome.get("description", "")
                    if not player:
                        continue
                    if player not in player_outcomes:
                        player_outcomes[player] = {}
                    name = outcome.get("name", "").lower()
                    player_outcomes[player][name] = {
                        "price": outcome.get("price", 0),
                        "point": outcome.get("point", 0),
                    }

                for player, sides in player_outcomes.items():
                    over = sides.get("over", {})
                    under = sides.get("under", {})
                    if not over or not under:
                        continue

                    all_props.append({
                        "player": player,
                        "stat_type": stat_type,
                        "line": over.get("point", under.get("point", 0)),
                        "over_odds": over.get("price", 0),
                        "under_odds": under.get("price", 0),
                        "book": book_key,
                        "game": game_label,
                        "home_team": home,
                        "away_team": away,
                    })

    print(f"[ev_bot] Fetched {len(all_props)} sharp player props across {len(events)} games.")
    return all_props


# ---------------------------------------------------------------------------
# 2. Fetch PrizePicks lines
# ---------------------------------------------------------------------------
def get_prizepicks_lines() -> List[Dict]:
    """
    Fetch player props from the live PrizePicks projections endpoint.

    Uses the public JSON:API at:
      https://api.prizepicks.com/projections?league_id=20&per_page=250&...

    The response has two top-level arrays:
      - "data"     → projection objects (stat_type, line_score, player relationship)
      - "included" → related resources (players with type "new_player")

    Returns a list of dicts:
      {
        "player": "LeBron James",
        "stat_type": "points",
        "line": 25.5,
      }
    """
    url = (
        "https://api.prizepicks.com/projections"
        "?league_id=7"
        "&per_page=250"
        "&single_stat=true"
        "&in_game=true"
        "&state_code=CA"
        "&game_mode=prizepools"
    )
    headers = {
        "Accept": "application/json",
        "User-Agent": "EVBot/1.0",
    }

    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        print(f"[ev_bot] ERROR fetching PrizePicks: {e}")
        return []

    # ---- Parse the JSON:API response ----

    included = data.get("included", [])
    projections = data.get("data", [])

    # Build player ID → name lookup from "included" resources
    # Player objects have type "new_player" with attributes like display_name/name
    player_lookup = {}
    for item in included:
        if item.get("type") == "new_player":
            pid = item.get("id", "")
            attrs = item.get("attributes", {})
            name = (
                attrs.get("display_name")
                or attrs.get("name")
                or f"{attrs.get('first_name', '')} {attrs.get('last_name', '')}".strip()
            )
            team = attrs.get("team", "")
            if pid and name:
                player_lookup[pid] = {"name": name, "team": team}

    # Parse each projection in "data"
    lines = []
    for proj in projections:
        attrs = proj.get("attributes", {})

        # Extract stat type (e.g. "Points", "Rebounds", "3-Point Made")
        stat_raw = attrs.get("stat_type", "")

        # Extract the line value
        line_val = attrs.get("line_score")
        if line_val is None:
            continue
        try:
            line_val = float(line_val)
        except (ValueError, TypeError):
            continue

        # Normalize the stat type through our mapping
        stat_type = PP_STAT_MAP.get(stat_raw, stat_raw.lower().replace(" ", "_"))

        # Resolve the player name + team via the relationship → included lookup
        relationships = proj.get("relationships", {})
        player_rel = relationships.get("new_player", {}).get("data", {})
        player_id = player_rel.get("id", "")
        player_info = player_lookup.get(player_id)

        if not player_info:
            continue

        lines.append({
            "player": player_info["name"],
            "stat_type": stat_type,
            "line": line_val,
            "team": player_info["team"],
        })

    print(f"[ev_bot] Fetched {len(lines)} PrizePicks lines from {len(player_lookup)} players.")
    return lines


# ---------------------------------------------------------------------------
# 3. Fuzzy name matching
# ---------------------------------------------------------------------------
def fuzzy_match_name(name_a: str, name_b: str) -> float:
    """Return a similarity ratio (0.0–1.0) between two player names."""
    return SequenceMatcher(None, name_a.lower(), name_b.lower()).ratio()


def find_best_match(
    target: str, candidates: List[str], threshold: float = FUZZY_THRESHOLD
) -> Optional[str]:
    """
    Find the best fuzzy match for `target` among `candidates`.
    Returns the matched name or None if nothing exceeds the threshold.
    """
    best_name = None
    best_score = 0.0

    for candidate in candidates:
        score = fuzzy_match_name(target, candidate)
        if score > best_score:
            best_score = score
            best_name = candidate

    if best_score >= threshold:
        return best_name
    return None


def match_players(
    sharp_props: List[Dict], pp_lines: List[Dict]
) -> List[Dict]:
    """
    Match sharp sportsbook props to PrizePicks lines by:
      1. Player name  — exact (case-insensitive) first, then fuzzy (>= 85%)
      2. Stat type     — must match exactly after normalization
      3. Line value    — must match exactly (e.g. 24.5 == 24.5)

    Returns a list of matched dicts with odds from the sharp book attached.
    """
    # Index PrizePicks lines by (player_lower, stat_type, line) for exact lookup
    # Also keep a (player_lower, stat_type) → list index for line scanning
    pp_by_stat: Dict[Tuple[str, str], List[Dict]] = {}
    pp_names = set()
    for line in pp_lines:
        key = (line["player"].lower(), line["stat_type"])
        pp_by_stat.setdefault(key, []).append(line)
        pp_names.add(line["player"])

    pp_names_list = list(pp_names)
    # Cache fuzzy match results so we don't re-compute per player
    name_cache: Dict[str, Optional[str]] = {}

    matched = []

    for prop in sharp_props:
        sharp_player = prop["player"]
        stat_type = prop["stat_type"]
        sharp_line = prop["line"]

        resolved_name = None

        # Try exact name match first (case-insensitive)
        exact_key = (sharp_player.lower(), stat_type)
        if exact_key in pp_by_stat:
            resolved_name = sharp_player
            pp_candidates = pp_by_stat[exact_key]
        else:
            # Fuzzy match the name (>= 85% similarity)
            if sharp_player not in name_cache:
                name_cache[sharp_player] = find_best_match(
                    sharp_player, pp_names_list, FUZZY_THRESHOLD
                )
            matched_name = name_cache[sharp_player]
            if matched_name is None:
                continue

            fuzzy_key = (matched_name.lower(), stat_type)
            if fuzzy_key not in pp_by_stat:
                continue
            resolved_name = matched_name
            pp_candidates = pp_by_stat[fuzzy_key]

        # Require exact line match (24.5 == 24.5)
        pp_hit = None
        for pp in pp_candidates:
            if pp["line"] == sharp_line:
                pp_hit = pp
                break

        if pp_hit is None:
            continue

        # Verify the PrizePicks player's team matches the Odds-API game
        pp_team_abbr = pp_hit.get("team", "")
        pp_team_full = NBA_TEAM_MAP.get(pp_team_abbr, "")
        home_team = prop.get("home_team", "")
        away_team = prop.get("away_team", "")

        if pp_team_full and pp_team_full not in (home_team, away_team):
            continue  # player's team doesn't match this game — skip

        entry = {
            "player": sharp_player,
            "stat_type": stat_type,
            "line": sharp_line,
            "over_odds": prop["over_odds"],
            "under_odds": prop["under_odds"],
            "book": prop["book"],
            "game": prop["game"],
            "home_team": home_team,
            "away_team": away_team,
        }
        # Tag fuzzy matches so Discord can show "Steph Curry → Stephen Curry"
        if resolved_name and resolved_name.lower() != sharp_player.lower():
            entry["pp_player"] = resolved_name

        matched.append(entry)

    print(f"[ev_bot] Matched {len(matched)} props (name + stat + line + team).")
    return matched


# ---------------------------------------------------------------------------
# 4. EV calculation
# ---------------------------------------------------------------------------
def american_to_implied_prob(odds: int) -> float:
    """
    Convert American odds to implied probability (0.0–1.0).
      -150 → 0.60   (risk 150 to win 100)
      +200 → 0.333  (risk 100 to win 200)
    """
    if odds < 0:
        return abs(odds) / (abs(odds) + 100)
    else:
        return 100 / (odds + 100)


def calculate_ev(matched_props: List[Dict]) -> List[Dict]:
    """
    Flag +EV plays from the matched props list.

    Logic (per your spec):
      Lines already match exactly (enforced by match_players).
      For each matched prop, convert Over and Under American odds to implied
      probability.  If either side >= 55% (~-122 or steeper), flag it as +EV.

    Returns only the +EV plays with added fields:
      "direction"     — "Over" or "Under"
      "implied_prob"  — the sharp implied probability as a percentage
      "edge"          — how far above 50/50 the sharp book leans
    """
    ev_plays = []

    for prop in matched_props:
        over_odds = prop["over_odds"]
        under_odds = prop["under_odds"]

        over_prob = american_to_implied_prob(over_odds)
        under_prob = american_to_implied_prob(under_odds)

        # Check Over side: sharp book implies >= 55% the Over hits
        if over_prob >= EV_THRESHOLD:
            ev_plays.append({
                **prop,
                "direction": "Over",
                "implied_prob": round(over_prob * 100, 1),
                "edge": round((over_prob - 0.50) * 100, 1),
            })

        # Check Under side: sharp book implies >= 55% the Under hits
        if under_prob >= EV_THRESHOLD:
            ev_plays.append({
                **prop,
                "direction": "Under",
                "implied_prob": round(under_prob * 100, 1),
                "edge": round((under_prob - 0.50) * 100, 1),
            })

    # Sort by edge descending — best plays first
    ev_plays.sort(key=lambda x: x["edge"], reverse=True)

    print(f"[ev_bot] Found {len(ev_plays)} +EV plays (threshold: {EV_THRESHOLD * 100}%).")
    return ev_plays


# ---------------------------------------------------------------------------
# 5. Discord webhook
# ---------------------------------------------------------------------------
def send_discord_alert(ev_plays: List[Dict]) -> bool:
    """
    Send +EV plays to the configured Discord webhook.
    Splits into multiple messages if needed (Discord embed limit: 4096 chars).
    """
    if not DISCORD_WEBHOOK_URL:
        print("[ev_bot] WARNING: DISCORD_WEBHOOK_URL not set — skipping send.")
        return False

    if not ev_plays:
        print("[ev_bot] No +EV plays to send.")
        return True

    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    # Build all play lines
    all_lines = []
    for i, play in enumerate(ev_plays, 1):
        pp_player = play.get("pp_player")
        name_display = play["player"]
        if pp_player:
            name_display += f" (PP: {pp_player})"

        all_lines.append(
            f"**{i}. {name_display}**\n"
            f"   {play['stat_type'].title()} **{play['direction']}** {play['line']}  |  "
            f"Prob: **{play['implied_prob']}%**  |  Edge: **+{play['edge']}%**\n"
            f"   {play['book']} — {play['game']}"
        )

    # Chunk lines to stay under Discord's 4096-char embed description limit
    chunks = []
    current_chunk = []
    current_len = 0
    for line in all_lines:
        entry_len = len(line) + 2  # +2 for "\n\n" separator
        if current_len + entry_len > 3900 and current_chunk:
            chunks.append(current_chunk)
            current_chunk = []
            current_len = 0
        current_chunk.append(line)
        current_len += entry_len
    if current_chunk:
        chunks.append(current_chunk)

    success = True
    for idx, chunk in enumerate(chunks):
        part_label = f" (Part {idx + 1}/{len(chunks)})" if len(chunks) > 1 else ""
        embed = {
            "title": f"🎯 +EV Plays{part_label} — {now}",
            "description": "\n\n".join(chunk),
            "color": 0x00FF88,
        }
        # Add footer only on the last chunk
        if idx == len(chunks) - 1:
            embed["footer"] = {
                "text": (
                    f"EV Bot | {EV_THRESHOLD * 100:.0f}% threshold | "
                    f"{len(ev_plays)} total plays across {len(chunks)} message(s)"
                ),
            }

        payload = {"username": "EV Bot", "embeds": [embed]}

        try:
            resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"[ev_bot] ERROR sending chunk {idx + 1} to Discord: {e}")
            success = False

    if success:
        print(f"[ev_bot] Sent {len(ev_plays)} +EV plays to Discord ({len(chunks)} message(s)).")
    return success


# ---------------------------------------------------------------------------
# 6. Main pipeline
# ---------------------------------------------------------------------------
def main():
    print("""
╔══════════════════════════════════════════════╗
║          EV BOT  —  +EV Play Finder          ║
║  Sharp Odds × PrizePicks  →  Discord Alerts  ║
╚══════════════════════════════════════════════╝
    """)

    parser = argparse.ArgumentParser(description="EV Bot — Find +EV plays")
    parser.add_argument("--dry-run", action="store_true", help="Find plays but don't send to Discord")
    parser.add_argument("--threshold", type=float, default=None, help="Override EV threshold (e.g. 0.57)")
    parser.add_argument("--test-pp", action="store_true", help="Test PrizePicks fetch only and print sample lines")
    args = parser.parse_args()

    if args.threshold is not None:
        global EV_THRESHOLD
        EV_THRESHOLD = args.threshold
        print(f"[ev_bot] Overriding EV threshold to {EV_THRESHOLD * 100}%")

    # --test-pp: quick test of the PrizePicks endpoint
    if args.test_pp:
        print("\n[Test] Fetching PrizePicks lines...")
        pp_lines = get_prizepicks_lines()
        if not pp_lines:
            print("[ev_bot] No PrizePicks lines returned. The endpoint may be down or empty right now.")
            return
        # Collect unique stat types for summary
        stat_types = set(l["stat_type"] for l in pp_lines)
        print(f"\n  Total lines: {len(pp_lines)}")
        print(f"  Stat types found: {', '.join(sorted(stat_types))}")
        print(f"\n  {'Player':<28} {'Stat Type':<18} {'Line':>6}")
        print(f"  {'-'*28} {'-'*18} {'-'*6}")
        for line in pp_lines[:20]:
            print(f"  {line['player']:<28} {line['stat_type']:<18} {line['line']:>6}")
        if len(pp_lines) > 20:
            print(f"\n  ... and {len(pp_lines) - 20} more lines.")
        return

    # ----------------------------------------------------------------
    # Step 1: Pull sharp sportsbook odds from The-Odds-API
    # ----------------------------------------------------------------
    print("\n[Step 1] Fetching sharp sportsbook odds...")
    sharp_props = get_sharp_odds()
    if not sharp_props:
        print("[ev_bot] No sharp props found. Exiting.")
        return

    # ----------------------------------------------------------------
    # Step 2: Pull PrizePicks lines
    # ----------------------------------------------------------------
    print("\n[Step 2] Fetching PrizePicks lines...")
    pp_lines = get_prizepicks_lines()
    if not pp_lines:
        print("[ev_bot] No PrizePicks lines found. Exiting.")
        return

    # ----------------------------------------------------------------
    # Step 3: Match — fuzzy name (>=85%) + same stat type + exact line
    # ----------------------------------------------------------------
    print("\n[Step 3] Comparing player props (name ≥85% + stat + exact line + team)...")
    matched = match_players(sharp_props, pp_lines)

    if matched:
        print(f"\n  {'Player':<26} {'Stat':<12} {'Line':>6}  {'Over':>7}  {'Under':>7}  {'Book':<12}")
        print(f"  {'-'*26} {'-'*12} {'-'*6}  {'-'*7}  {'-'*7}  {'-'*12}")
        for m in matched[:30]:
            pp_note = f" ~{m['pp_player']}" if m.get("pp_player") else ""
            print(
                f"  {(m['player'] + pp_note):<26} "
                f"{m['stat_type']:<12} "
                f"{m['line']:>6}  "
                f"{m['over_odds']:>+7}  "
                f"{m['under_odds']:>+7}  "
                f"{m['book']:<12}"
            )
        if len(matched) > 30:
            print(f"\n  ... and {len(matched) - 30} more matched props.")
    else:
        print("[ev_bot] No matching props found (name + stat + line). Exiting.")
        return

    # ----------------------------------------------------------------
    # Step 4: Flag +EV plays (implied prob >= 55% / ~-122)
    # ----------------------------------------------------------------
    print(f"\n[Step 4] Scanning for +EV plays (implied prob >= {EV_THRESHOLD*100}%)...")
    ev_plays = calculate_ev(matched)

    if ev_plays:
        print(f"\n{'='*70}")
        print(f"  +EV PLAYS — {len(ev_plays)} FOUND")
        print(f"{'='*70}")
        for i, play in enumerate(ev_plays, 1):
            pp_note = f" → {play['pp_player']}" if play.get("pp_player") else ""
            print(
                f"  {i:>2}. {play['player']}{pp_note}\n"
                f"      {play['stat_type'].title()} {play['direction']} {play['line']}  |  "
                f"Implied: {play['implied_prob']}%  |  Edge: +{play['edge']}%  |  "
                f"{play['book']}  |  {play['game']}"
            )
        print(f"{'='*70}\n")
    else:
        print("\n[ev_bot] No +EV plays found today. All matched lines are close to 50/50.")

    # ----------------------------------------------------------------
    # Step 5: Send +EV plays to Discord
    # ----------------------------------------------------------------
    if ev_plays and not args.dry_run:
        print("[Step 5] Sending +EV plays to Discord...")
        send_discord_alert(ev_plays)
    elif args.dry_run:
        print("[ev_bot] Dry run — skipping Discord send.")

    # ----------------------------------------------------------------
    # Save full results locally for debugging / backtesting
    # ----------------------------------------------------------------
    output = {
        "timestamp": datetime.now().isoformat(),
        "threshold": EV_THRESHOLD,
        "sharp_props_count": len(sharp_props),
        "pp_lines_count": len(pp_lines),
        "matched_count": len(matched),
        "ev_plays": ev_plays,
    }
    output_path = os.path.join(os.path.dirname(__file__), "ev_results.json")
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"[ev_bot] Results saved to {output_path}")


if __name__ == "__main__":
    main()
