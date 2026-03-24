"""
scheduler.py — The Heartbeat. Keeps the operation running 24/7.

Schedule:
  10:00 AM  →  collect_daily_picks() — fetch picks from all 5 AIs.
   6:00 AM  →  grade_yesterday_picks() — grade previous day's picks via nba_api.

Usage:
  python scheduler.py              # start the scheduler (blocks forever)
  python scheduler.py --once       # run morning fetch once then exit
  python scheduler.py --grade      # run grading job once then exit
"""

import os
import sys
import time
from datetime import datetime

import schedule

# Ensure parent dir (main.py) and tools/ are importable
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, TOOLS_DIR)

from main import collect_daily_picks
from grader import GradeManager, save_graded


# ---------------------------------------------------------------------------
# Job functions
# ---------------------------------------------------------------------------
def run_collect():
    """10:00 AM — Fetch AI picks for today."""
    print(f"\n[{datetime.now()}] Running collect_daily_picks()...")
    try:
        collect_daily_picks()
    except Exception as e:
        print(f"[{datetime.now()}] ERROR in collect_daily_picks: {e}")


def grade_yesterday_picks():
    """6:00 AM — Grade previous day's picks against real NBA stats."""
    print(f"\n[{datetime.now()}] Running grade_yesterday_picks()...")
    try:
        grader = GradeManager()
        graded = grader.grade_all()
        if graded:
            save_graded(graded)
            wins = sum(1 for p in graded if p.get("status") == "WIN")
            losses = sum(1 for p in graded if p.get("status") == "LOSS")
            voids = sum(1 for p in graded if p.get("status") in ("VOID", "PUSH"))
            print(f"[{datetime.now()}] Results: {wins}W - {losses}L - {voids}V")
        else:
            print(f"[{datetime.now()}] No picks to grade.")
    except Exception as e:
        print(f"[{datetime.now()}] ERROR in grade_yesterday_picks: {e}")


def heartbeat():
    """Print a heartbeat message so you know the script hasn't crashed."""
    print(f"[{datetime.now()}] Scheduler active. Waiting for next job...")


# ---------------------------------------------------------------------------
# Scheduler setup
# ---------------------------------------------------------------------------
# Schedule the two main jobs
schedule.every().day.at("10:00").do(run_collect)
schedule.every().day.at("06:00").do(grade_yesterday_picks)

# Heartbeat every 60 minutes
schedule.every(60).minutes.do(heartbeat)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Sports Betting AI Scheduler")
    parser.add_argument("--once", action="store_true", help="Run collect_daily_picks once and exit")
    parser.add_argument("--grade", action="store_true", help="Run grade_yesterday_picks once and exit")
    args = parser.parse_args()

    if args.once:
        run_collect()
    elif args.grade:
        grade_yesterday_picks()
    else:
        print(f"[{datetime.now()}] Scheduler started.")
        print("  • 10:00 AM — collect_daily_picks()")
        print("  •  6:00 AM — grade_yesterday_picks()")
        print("  • Heartbeat every 60 minutes")
        print("Press Ctrl+C to stop.\n")
        heartbeat()

        while True:
            schedule.run_pending()
            time.sleep(60)
