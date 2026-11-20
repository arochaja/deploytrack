"""SQLite storage: deployments, activity log and email outbox."""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS deployments (
    id TEXT PRIMARY KEY,
    phase TEXT DEFAULT '',
    req_number TEXT DEFAULT '',
    tech TEXT DEFAULT '',
    status TEXT DEFAULT 'Verify',
    shared_device TEXT DEFAULT '',
    deployment_type TEXT DEFAULT '',
    new_device_sn TEXT DEFAULT '',
    dubsupport_tag TEXT DEFAULT '',
    accessories TEXT DEFAULT '',
    location TEXT DEFAULT '',
    user_name TEXT DEFAULT '',
    netid TEXT DEFAULT '',
    user_email TEXT DEFAULT '',
    dept_contact TEXT DEFAULT '',
    dept_contact_email TEXT DEFAULT '',
    department TEXT DEFAULT '',
    old_device_sn TEXT DEFAULT '',
    last_login TEXT DEFAULT '',
    old_device_returned TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    scheduled_start TEXT DEFAULT '',
    scheduled_end TEXT DEFAULT '',
    scheduling_requested_at TEXT DEFAULT '',
    last_contacted_at TEXT DEFAULT '',
    reminder_count INTEGER DEFAULT 0,
    escalated_at TEXT DEFAULT '',
    ticket_pending INTEGER DEFAULT 0,
    close_ticket_pending INTEGER DEFAULT 0,
    created_at TEXT DEFAULT '',
    updated_at TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deployment_id TEXT,
    ts TEXT,
    kind TEXT,
    actor TEXT,
    message TEXT
);
CREATE INDEX IF NOT EXISTS idx_activity_dep ON activity(deployment_id);
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT,
    deployment_id TEXT,
    to_addr TEXT,
    cc TEXT,
    subject TEXT,
    body TEXT,
    delivered INTEGER DEFAULT 0,
    error TEXT DEFAULT ''
);
"""

EDITABLE_FIELDS = [
    "phase", "req_number", "tech", "shared_device", "deployment_type", "new_device_sn",
    "dubsupport_tag", "accessories", "location", "user_name", "netid", "user_email",
    "dept_contact", "dept_contact_email", "department", "old_device_sn", "last_login",
    "old_device_returned", "notes", "scheduled_start", "scheduled_end",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect() -> sqlite3.Connection:
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(config.DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@contextmanager
def session():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init() -> None:
    with session() as conn:
        conn.executescript(SCHEMA)


def log(conn, deployment_id, kind, message, actor="system"):
    conn.execute(
        "INSERT INTO activity (deployment_id, ts, kind, actor, message) VALUES (?,?,?,?,?)",
        (deployment_id, now_iso(), kind, actor, message),
    )


def get_deployment(conn, dep_id):
    row = conn.execute("SELECT * FROM deployments WHERE id = ?", (dep_id,)).fetchone()
    return dict(row) if row else None


def update_fields(conn, dep_id, fields: dict):
    if not fields:
        return
    fields = dict(fields, updated_at=now_iso())
    cols = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE deployments SET {cols} WHERE id = ?", (*fields.values(), dep_id))
