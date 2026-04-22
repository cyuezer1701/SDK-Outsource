"""SQLite-backed ticket store for L2 escalations."""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

_DB_DIR = Path(__file__).parent
_DB_PATH = _DB_DIR / "tickets.db"


@dataclass
class Ticket:
    id: str
    created_at: str
    problem: str
    summary: str
    tried_steps: str
    system_info: str        # JSON string
    conversation: str       # JSON string
    status: str             # open | in_progress | closed
    priority: str           # low | medium | high


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tickets (
            id          TEXT PRIMARY KEY,
            created_at  TEXT NOT NULL,
            problem     TEXT NOT NULL,
            summary     TEXT NOT NULL,
            tried_steps TEXT NOT NULL,
            system_info TEXT NOT NULL DEFAULT '{}',
            conversation TEXT NOT NULL DEFAULT '[]',
            status      TEXT NOT NULL DEFAULT 'open',
            priority    TEXT NOT NULL DEFAULT 'medium'
        )
    """)
    conn.commit()
    return conn


def _next_id(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT COUNT(*) FROM tickets").fetchone()
    n = row[0] + 1
    return f"T-{n:04d}"


def create_ticket(
    problem: str,
    summary: str,
    tried_steps: str,
    system_info: dict | None = None,
    conversation: list | None = None,
    priority: str = "medium",
) -> Ticket:
    conn = _connect()
    ticket_id = _next_id(conn)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    t = Ticket(
        id=ticket_id,
        created_at=now,
        problem=problem,
        summary=summary,
        tried_steps=tried_steps,
        system_info=json.dumps(system_info or {}, ensure_ascii=False),
        conversation=json.dumps(conversation or [], ensure_ascii=False, default=str),
        status="open",
        priority=priority,
    )
    conn.execute(
        "INSERT INTO tickets VALUES (?,?,?,?,?,?,?,?,?)",
        (t.id, t.created_at, t.problem, t.summary, t.tried_steps,
         t.system_info, t.conversation, t.status, t.priority),
    )
    conn.commit()
    conn.close()
    return t


def list_tickets(status: str | None = None) -> list[Ticket]:
    conn = _connect()
    if status:
        rows = conn.execute(
            "SELECT * FROM tickets WHERE status=? ORDER BY created_at DESC", (status,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM tickets ORDER BY created_at DESC"
        ).fetchall()
    conn.close()
    return [Ticket(**dict(r)) for r in rows]


def update_status(ticket_id: str, status: str) -> bool:
    conn = _connect()
    c = conn.execute(
        "UPDATE tickets SET status=? WHERE id=?", (status, ticket_id)
    )
    conn.commit()
    conn.close()
    return c.rowcount > 0
