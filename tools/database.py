"""
database.py — SQLite persistence layer for AI picks and game results.
"""

import sqlite3
import json
import os
from datetime import datetime
from typing import Dict, List, Optional

DB_PATH = os.environ.get("PICKS_DB_PATH", "picks.db")


def get_connection(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(db_path: str = DB_PATH):
    """Create tables if they don't exist."""
    conn = get_connection(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS picks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            league      TEXT    NOT NULL,
            provider    TEXT    NOT NULL,
            model       TEXT    NOT NULL,
            game        TEXT    NOT NULL,
            pick        TEXT    NOT NULL,
            odds        REAL,
            confidence  INTEGER,
            raw_json    TEXT    NOT NULL,
            pick_date   TEXT    NOT NULL,
            created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
            status      TEXT    NOT NULL DEFAULT 'PENDING'
        );

        CREATE INDEX IF NOT EXISTS idx_picks_date   ON picks(pick_date);
        CREATE INDEX IF NOT EXISTS idx_picks_status  ON picks(status);
        CREATE INDEX IF NOT EXISTS idx_picks_league  ON picks(league);
    """)
    conn.close()


def save_picks(
    league: str,
    provider: str,
    model: str,
    picks: List[Dict],
    pick_date: str,
    db_path: str = DB_PATH,
):
    """Insert a batch of picks from one provider into the database."""
    if not picks:
        return

    conn = get_connection(db_path)
    rows = [
        (
            league,
            provider,
            model,
            p.get("game", ""),
            p.get("pick", ""),
            p.get("odds"),
            p.get("confidence"),
            json.dumps(p),
            pick_date,
        )
        for p in picks
    ]
    conn.executemany(
        """INSERT INTO picks
           (league, provider, model, game, pick, odds, confidence, raw_json, pick_date)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    conn.commit()
    conn.close()


def get_pending_picks(pick_date: str, db_path: str = DB_PATH) -> List[dict]:
    """Return all PENDING picks for a given date."""
    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT * FROM picks WHERE pick_date = ? AND status = 'PENDING'",
        (pick_date,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_pick_status(pick_id: int, status: str, db_path: str = DB_PATH):
    """Mark a pick as WON or LOST."""
    conn = get_connection(db_path)
    conn.execute(
        "UPDATE picks SET status = ? WHERE id = ?",
        (status, pick_id),
    )
    conn.commit()
    conn.close()


def get_picks_summary(pick_date: str, db_path: str = DB_PATH) -> List[dict]:
    """Summary grouped by provider for a given date."""
    conn = get_connection(db_path)
    rows = conn.execute(
        """SELECT provider, model, league,
                  COUNT(*) as total,
                  SUM(CASE WHEN status='WON' THEN 1 ELSE 0 END) as wins,
                  SUM(CASE WHEN status='LOST' THEN 1 ELSE 0 END) as losses
           FROM picks WHERE pick_date = ?
           GROUP BY provider, league""",
        (pick_date,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
