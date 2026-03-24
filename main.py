"""
main.py — Entry point for the Sports Betting AI system.

Usage:
  python main.py                  # start the scheduler (runs forever)
  python main.py --once           # fetch picks now and exit
  python main.py --grade          # grade yesterday's picks and exit
  python main.py --status         # show today's pick summary and exit
  python main.py --league NBA     # fetch picks for a single league and exit
  python main.py --collect        # run collect_daily_picks() and exit
"""

import sys
import os
import argparse
import json
from datetime import date, datetime

# Ensure tools/ is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "tools"))

from dotenv import load_dotenv

load_dotenv()

from model_interface import (
    SportsPredictor,
    OpenAIAdapter,
    AnthropicAdapter,
    GeminiAdapter,
    GrokAdapter,
    DeepSeekAdapter,
    build_adapters,
)
from database import init_db, get_picks_summary, save_picks

# ---------------------------------------------------------------------------
# The 5 models we want to use
# ---------------------------------------------------------------------------
MODELS = [
    {"name": "GPT-4o",            "adapter": OpenAIAdapter},
    {"name": "Claude Sonnet 4.5", "adapter": AnthropicAdapter},
    {"name": "Gemini 2.0 Flash",  "adapter": GeminiAdapter},
    {"name": "Grok 3",            "adapter": GrokAdapter},
    {"name": "DeepSeek V3",       "adapter": DeepSeekAdapter},
]


# ---------------------------------------------------------------------------
# collect_daily_picks — hardcoded NBA Player Props prompt
# ---------------------------------------------------------------------------
def collect_daily_picks():
    """
    Loop through each model, ask for top 3 NBA Player Props,
    collect JSON responses, and save to daily_picks.json with a timestamp.
    """
    today = date.today().isoformat()
    now = datetime.now().isoformat()
    prompt_league = "NBA Player Props"

    all_picks = {
        "timestamp": now,
        "date": today,
        "prompt": f"Top 3 {prompt_league} picks for today",
        "results": [],
    }

    successful_models = []

    for entry in MODELS:
        model_name = entry["name"]
        adapter_cls = entry["adapter"]

        try:
            adapter: SportsPredictor = adapter_cls()
        except KeyError:
            print(f"  [skip] {model_name} — API key not set")
            continue

        print(f"  Querying {model_name}...")
        picks = adapter.get_daily_picks(prompt_league, today)

        all_picks["results"].append({
            "model": model_name,
            "adapter": adapter_cls.__name__,
            "picks": picks,
        })

        if picks:
            successful_models.append(model_name)

    # Save to daily_picks.json
    output_path = os.path.join(os.path.dirname(__file__), "daily_picks.json")
    with open(output_path, "w") as f:
        json.dump(all_picks, f, indent=2)

    print(f"\n  Saved to {output_path}")
    print(f"  Successfully gathered picks from [{', '.join(successful_models)}].")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def print_banner():
    print("""
╔══════════════════════════════════════════════╗
║         SPORTS BETTING AI  —  v0.1           ║
║  5 LLMs · Async Fan-Out · SQLite Tracking   ║
╚══════════════════════════════════════════════╝
    """)


def print_status():
    """Show today's pick summary from the database."""
    today = date.today().isoformat()
    summary = get_picks_summary(today)

    if not summary:
        print(f"No picks in the database for {today}.")
        return

    print(f"\n  Picks Summary for {today}")
    print(f"  {'='*50}")
    print(f"  {'Provider':<22} {'League':<8} {'Total':>5} {'W':>4} {'L':>4}")
    print(f"  {'-'*50}")
    for row in summary:
        print(
            f"  {row['provider']:<22} {row['league']:<8} "
            f"{row['total']:>5} {row['wins']:>4} {row['losses']:>4}"
        )
    print()


def print_adapters():
    """Show which adapters are active based on available API keys."""
    adapters = build_adapters()
    print(f"  Active adapters: {len(adapters)}/5")
    for a in adapters:
        print(f"    • {type(a).__name__} ({a.model})")
    print()
    return adapters


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Sports Betting AI — Multi-LLM Pick Aggregator",
    )
    parser.add_argument("--status", action="store_true", help="Show today's pick summary")
    parser.add_argument("--league", type=str, help="Fetch picks for a single league (e.g. NBA)")
    parser.add_argument("--collect", action="store_true", help="Run collect_daily_picks() and exit")
    args = parser.parse_args()

    print_banner()
    init_db()

    # --status: quick summary, no API calls
    if args.status:
        print_status()
        return

    # --collect: the new hardcoded NBA Player Props flow
    if args.collect:
        collect_daily_picks()
        return

    # Show active adapters for any mode that hits APIs
    adapters = print_adapters()
    if not adapters and not args.status:
        print("  No API keys found in .env — nothing to do.")
        print("  Fill in your keys in .env and try again.\n")
        return

    # --league: single-league one-shot
    if args.league:
        today = date.today().isoformat()
        league = args.league.upper()
        print(f"  Fetching {league} picks for {today}...\n")

        for adapter in adapters:
            name = type(adapter).__name__
            picks = adapter.get_daily_picks(league, today)
            if picks:
                save_picks(league, name, adapter.model, picks, today)
                print(f"  {name}: {len(picks)} picks saved")
                print(f"  {json.dumps(picks, indent=4)}\n")
            else:
                print(f"  {name}: no picks returned\n")
        return

    # Default: run collect
    collect_daily_picks()


if __name__ == "__main__":
    main()
