"""SQLite persistence.

* One connection per operation, foreign keys on, busy timeout set, WAL for file databases.
* Explicit ``BEGIN IMMEDIATE`` transactions, so two writers queue instead of interleaving.
* Parameterized SQL only. Money is stored as integer minor units.
* Every table that has an owner carries ``workspace_id``; every query filters by it.
* Seeding never deletes or overwrites business state. Only :func:`reset_database` does.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .rules.inventory import InventoryIndex
from .textnorm import item_key

STARTER_INVENTORY: dict[str, int] = {"WidgetA": 15, "WidgetB": 10, "GadgetX": 5, "FakeItem": 0}
LOCAL_WORKSPACE = "local"

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


def parse_iso(text: str) -> datetime:
    return datetime.fromisoformat(text)


SCHEMA = """
CREATE TABLE IF NOT EXISTS inventory (
    item  TEXT PRIMARY KEY,
    stock INTEGER NOT NULL CHECK (stock >= 0)
);
CREATE TABLE IF NOT EXISTS item_aliases (
    alias_key TEXT PRIMARY KEY,
    item      TEXT NOT NULL REFERENCES inventory(item)
);
CREATE TABLE IF NOT EXISTS workspaces (
    workspace_id TEXT PRIMARY KEY,
    kind         TEXT NOT NULL CHECK (kind IN ('local', 'session')),
    created_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
    workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id) ON DELETE CASCADE,
    document_id  TEXT NOT NULL,
    filename     TEXT NOT NULL,
    format       TEXT NOT NULL,
    size_bytes   INTEGER NOT NULL,
    byte_hash    TEXT NOT NULL,
    source_text  TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    PRIMARY KEY (workspace_id, document_id)
);
CREATE TABLE IF NOT EXISTS document_assets (
    workspace_id TEXT NOT NULL,
    document_id  TEXT NOT NULL,
    original_pdf BLOB NOT NULL,
    PRIMARY KEY (workspace_id, document_id),
    FOREIGN KEY (workspace_id, document_id) REFERENCES documents(workspace_id, document_id)
        ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS payables (
    workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id) ON DELETE CASCADE,
    payable_id   TEXT NOT NULL,
    vendor_key   TEXT NOT NULL,
    number_key   TEXT NOT NULL,
    vendor_name  TEXT NOT NULL,
    number       TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    PRIMARY KEY (workspace_id, payable_id),
    UNIQUE (workspace_id, vendor_key, number_key)
);
CREATE TABLE IF NOT EXISTS invoice_versions (
    version_id           TEXT PRIMARY KEY,
    workspace_id         TEXT NOT NULL,
    payable_id           TEXT,
    document_id          TEXT NOT NULL,
    byte_hash            TEXT NOT NULL,
    semantic_fingerprint TEXT NOT NULL,
    revision_label       TEXT,
    revision_rank        INTEGER,
    status               TEXT NOT NULL CHECK (status IN ('active', 'superseded')),
    identity_flag        TEXT,
    currency             TEXT,
    total_minor          INTEGER,
    draft_json           TEXT NOT NULL,
    created_at           TEXT NOT NULL,
    FOREIGN KEY (workspace_id, payable_id) REFERENCES payables(workspace_id, payable_id)
        ON DELETE CASCADE,
    FOREIGN KEY (workspace_id, document_id) REFERENCES documents(workspace_id, document_id)
        ON DELETE CASCADE,
    UNIQUE (workspace_id, semantic_fingerprint),
    UNIQUE (version_id, workspace_id)
);
CREATE INDEX IF NOT EXISTS idx_versions_payable ON invoice_versions(workspace_id, payable_id);
CREATE TABLE IF NOT EXISTS runs (
    run_id          TEXT PRIMARY KEY,
    workspace_id    TEXT NOT NULL REFERENCES workspaces(workspace_id) ON DELETE CASCADE,
    document_id     TEXT,
    version_id      TEXT,
    lifecycle       TEXT NOT NULL CHECK (lifecycle IN
                        ('queued', 'running', 'completed', 'failed', 'interrupted')),
    outcome         TEXT CHECK (outcome IN
                        ('paid', 'rejected', 'needs_review', 'duplicate', 'superseded')),
    terminal_reason TEXT,
    mode            TEXT,
    provider        TEXT,
    model           TEXT,
    usage_json      TEXT,
    policy_version  TEXT,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    result_json     TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_workspace ON runs(workspace_id, started_at);
CREATE TABLE IF NOT EXISTS run_links (
    workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id) ON DELETE CASCADE,
    child_run_id TEXT PRIMARY KEY REFERENCES runs(run_id) ON DELETE CASCADE,
    parent_run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('retry', 'correction'))
);
CREATE TABLE IF NOT EXISTS run_events (
    run_id       TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    seq          INTEGER NOT NULL,
    timestamp    TEXT NOT NULL,
    stage        TEXT NOT NULL,
    type         TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (run_id, seq)
);
CREATE TABLE IF NOT EXISTS findings (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id TEXT NOT NULL,
    run_id       TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    version_id   TEXT,
    code         TEXT NOT NULL,
    severity     TEXT NOT NULL,
    field        TEXT,
    item_ref     TEXT,
    observed     TEXT,
    expected     TEXT,
    evidence     TEXT,
    message      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS approval_decisions (
    approval_id        TEXT PRIMARY KEY,
    workspace_id       TEXT NOT NULL,
    version_id         TEXT NOT NULL,
    run_id             TEXT,
    stage              TEXT NOT NULL CHECK (stage IN ('approval', 'critique', 'final', 'review')),
    action             TEXT NOT NULL CHECK (action IN ('approve', 'reject', 'needs_review')),
    rationale          TEXT NOT NULL,
    actor              TEXT NOT NULL,
    policy_fingerprint TEXT NOT NULL,
    content_hash       TEXT NOT NULL,
    scrutiny_completed INTEGER NOT NULL CHECK (scrutiny_completed IN (0, 1)),
    resolved_codes     TEXT NOT NULL,
    created_at         TEXT NOT NULL,
    FOREIGN KEY (version_id, workspace_id) REFERENCES invoice_versions(version_id, workspace_id)
        ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS review_handoffs (
    handoff_id    TEXT PRIMARY KEY,
    workspace_id  TEXT NOT NULL REFERENCES workspaces(workspace_id) ON DELETE CASCADE,
    run_id        TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    version_id    TEXT,
    content_hash  TEXT,
    department    TEXT NOT NULL,
    reason_code   TEXT NOT NULL,
    note          TEXT NOT NULL,
    request_token TEXT NOT NULL,
    request_hash  TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    UNIQUE (workspace_id, request_token)
);
CREATE INDEX IF NOT EXISTS idx_handoffs_run ON review_handoffs(workspace_id, run_id, created_at);
CREATE TABLE IF NOT EXISTS payments (
    payment_id     TEXT PRIMARY KEY,
    workspace_id   TEXT NOT NULL,
    payable_id     TEXT NOT NULL,
    version_id     TEXT NOT NULL,
    approval_id    TEXT NOT NULL,
    transaction_id TEXT NOT NULL UNIQUE,
    vendor         TEXT NOT NULL,
    amount_minor   INTEGER NOT NULL CHECK (amount_minor > 0),
    currency       TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    FOREIGN KEY (workspace_id, payable_id) REFERENCES payables(workspace_id, payable_id)
        ON DELETE CASCADE,
    UNIQUE (workspace_id, payable_id)
);
CREATE TABLE IF NOT EXISTS batches (
    batch_id          TEXT PRIMARY KEY,
    workspace_id      TEXT NOT NULL REFERENCES workspaces(workspace_id) ON DELETE CASCADE,
    lifecycle         TEXT NOT NULL CHECK (lifecycle IN
                          ('queued', 'running', 'completed', 'failed', 'interrupted')),
    mode              TEXT NOT NULL,
    document_ids_json TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    finished_at       TEXT,
    result_json       TEXT,
    model             TEXT
);
CREATE TABLE IF NOT EXISTS request_tokens (
    workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id) ON DELETE CASCADE,
    token        TEXT NOT NULL,
    run_id       TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    PRIMARY KEY (workspace_id, token)
);
CREATE TABLE IF NOT EXISTS token_payloads (
    workspace_id TEXT NOT NULL,
    token        TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    PRIMARY KEY (workspace_id, token),
    FOREIGN KEY (workspace_id, token) REFERENCES request_tokens(workspace_id, token)
        ON DELETE CASCADE
);
"""


class Database:
    """A path plus helpers. Connections are opened per operation, so threads never share one."""

    def __init__(self, path: str | Path, clock: Clock = utc_now) -> None:
        self.path = Path(path)
        self.clock = clock

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 10000")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        return conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Write transaction. Rolls back on any exception."""
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            else:
                conn.execute("COMMIT")
        finally:
            conn.close()

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
        finally:
            conn.close()

    def now(self) -> str:
        return iso(self.clock())


# ---------------------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------------------


def init_database(db: Database, inventory: dict[str, int] | None = None) -> None:
    """Create tables and insert missing starter stock. Safe to call repeatedly:
    existing stock levels, payments and history are never touched."""
    seed = STARTER_INVENTORY if inventory is None else inventory
    with db.transaction() as conn:
        for statement in _statements(SCHEMA):
            conn.execute(statement)
        if "model" not in {row[1] for row in conn.execute("PRAGMA table_info(batches)")}:
            conn.execute("ALTER TABLE batches ADD COLUMN model TEXT")
        for item, stock in seed.items():
            conn.execute(
                "INSERT OR IGNORE INTO inventory (item, stock) VALUES (?, ?)", (item, stock)
            )
        conn.execute(
            "INSERT OR IGNORE INTO workspaces (workspace_id, kind, created_at)"
            " VALUES (?, 'local', ?)",
            (LOCAL_WORKSPACE, db.now()),
        )


def _statements(script: str) -> list[str]:
    return [part.strip() for part in script.split(";\n") if part.strip()]


def reset_database(path: str | Path, *, confirm_delete_all_data: bool) -> None:
    """Delete the whole database file. Never called by normal processing."""
    if confirm_delete_all_data is not True:
        raise PermissionError("reset_database needs confirm_delete_all_data=True")
    base = Path(path)
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(base) + suffix)
        if candidate.exists():
            candidate.unlink()


def ensure_workspace(conn: sqlite3.Connection, workspace_id: str, kind: str, now: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO workspaces (workspace_id, kind, created_at) VALUES (?, ?, ?)",
        (workspace_id, kind, now),
    )


def reset_workspace(db: Database, workspace_id: str) -> None:
    """Delete one workspace's records. Refuses while a run or batch is active.
    Shared inventory is untouched. This is the only reset a browser session may trigger."""
    with db.transaction() as conn:
        active = conn.execute(
            "SELECT (SELECT count(*) FROM runs WHERE workspace_id=? "
            "AND lifecycle IN ('queued','running')) + "
            "(SELECT count(*) FROM batches WHERE workspace_id=? "
            "AND lifecycle IN ('queued','running'))",
            (workspace_id, workspace_id),
        ).fetchone()[0]
        if active:
            raise RuntimeError("A run or batch is still active in this workspace.")
        conn.execute("DELETE FROM workspaces WHERE workspace_id = ?", (workspace_id,))
        kind = "local" if workspace_id == LOCAL_WORKSPACE else "session"
        ensure_workspace(conn, workspace_id, kind, db.now())


# ---------------------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------------------


def load_inventory_index(conn: sqlite3.Connection) -> InventoryIndex:
    stock = {row["item"]: row["stock"] for row in conn.execute("SELECT item, stock FROM inventory")}
    aliases = {
        row["alias_key"]: row["item"]
        for row in conn.execute("SELECT alias_key, item FROM item_aliases")
    }
    return InventoryIndex.build(stock, aliases)


def add_alias(db: Database, alias: str, item: str) -> None:
    """Operator action: map an alternate spelling to one inventory item."""
    with db.transaction() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO item_aliases (alias_key, item) VALUES (?, ?)",
            (item_key(alias), item),
        )


# ---------------------------------------------------------------------------------------
# Runs, events, findings, request tokens
# ---------------------------------------------------------------------------------------


def create_run(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    now: str,
    document_id: str | None = None,
    mode: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    policy_version: str | None = None,
    lifecycle: str = "running",
) -> str:
    run_id = new_id("run")
    conn.execute(
        "INSERT INTO runs (run_id, workspace_id, document_id, lifecycle, mode, provider, model,"
        " policy_version, started_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (run_id, workspace_id, document_id, lifecycle, mode, provider, model, policy_version, now),
    )
    return run_id


def append_event(
    conn: sqlite3.Connection, run_id: str, stage: str, type_: str, payload: dict, now: str
) -> int:
    """Append with a gapless, monotonic ``seq`` (the surrounding transaction serializes writers)."""
    seq = conn.execute(
        "SELECT COALESCE(MAX(seq), 0) + 1 FROM run_events WHERE run_id = ?", (run_id,)
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO run_events (run_id, seq, timestamp, stage, type, payload_json)"
        " VALUES (?,?,?,?,?,?)",
        (run_id, seq, now, stage, type_, json.dumps(payload, default=str)),
    )
    return seq


def list_events(
    conn: sqlite3.Connection, workspace_id: str, run_id: str, after_seq: int = 0, limit: int = 200
) -> list[dict]:
    """Events for a run the workspace owns. Another workspace's run ID returns nothing."""
    limit = max(1, min(limit, 500))
    rows = conn.execute(
        "SELECT e.seq, e.timestamp, e.stage, e.type, e.payload_json FROM run_events e"
        " JOIN runs r ON r.run_id = e.run_id"
        " WHERE r.workspace_id = ? AND e.run_id = ? AND e.seq > ? ORDER BY e.seq LIMIT ?",
        (workspace_id, run_id, after_seq, limit),
    ).fetchall()
    return [
        {
            "seq": r["seq"],
            "timestamp": r["timestamp"],
            "stage": r["stage"],
            "type": r["type"],
            "payload": json.loads(r["payload_json"]),
        }
        for r in rows
    ]


def save_findings(
    conn: sqlite3.Connection, workspace_id: str, run_id: str, version_id: str | None, findings: list
) -> None:
    conn.execute("DELETE FROM findings WHERE run_id = ?", (run_id,))
    conn.executemany(
        "INSERT INTO findings (workspace_id, run_id, version_id, code, severity, field, item_ref,"
        " observed, expected, evidence, message) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [
            (
                workspace_id,
                run_id,
                version_id,
                f.code.value,
                f.severity.value,
                f.field,
                f.item_ref,
                f.observed,
                f.expected,
                f.evidence,
                f.message,
            )
            for f in findings
        ],
    )


def update_run(db: Database, run_id: str, **fields: object) -> None:
    """Update columns of one run row. Column names are fixed here, never taken from input."""
    allowed = {
        "lifecycle",
        "outcome",
        "terminal_reason",
        "document_id",
        "version_id",
        "mode",
        "provider",
        "model",
        "usage_json",
        "policy_version",
        "finished_at",
        "result_json",
    }
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"cannot update {sorted(unknown)}")
    if not fields:
        return
    sets = ", ".join(f"{name} = ?" for name in fields)
    with db.transaction() as conn:
        conn.execute(f"UPDATE runs SET {sets} WHERE run_id = ?", (*fields.values(), run_id))  # noqa: S608


def find_request_token(conn: sqlite3.Connection, workspace_id: str, token: str) -> str | None:
    """The run (or batch) that already owns this token in this workspace, if any."""
    row = conn.execute(
        "SELECT run_id FROM request_tokens WHERE workspace_id = ? AND token = ?",
        (workspace_id, token),
    ).fetchone()
    return row["run_id"] if row else None


def claim_request_token(
    conn: sqlite3.Connection, workspace_id: str, token: str, run_id: str, now: str
) -> str:
    """Return ``run_id`` if the token is new, else the run that already owns it."""
    conn.execute(
        "INSERT OR IGNORE INTO request_tokens (workspace_id, token, run_id, created_at)"
        " VALUES (?,?,?,?)",
        (workspace_id, token, run_id, now),
    )
    return conn.execute(
        "SELECT run_id FROM request_tokens WHERE workspace_id = ? AND token = ?",
        (workspace_id, token),
    ).fetchone()["run_id"]
