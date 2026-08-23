"""
SQLite-backed audit log -- the production-appropriate alternative to
ConcurrentAuditLog's JSONL file. Fixes two real problems, not just
swaps technology for its own sake:

1. ConcurrentAuditLog's threading.Lock only serializes writes within a
   single Python process. A real deployment running multiple worker
   processes (uvicorn --workers N, or multiple container replicas)
   would still race on the same file, because a lock held in one
   process's memory means nothing to another process. SQLite's own
   file locking (used here via an explicit BEGIN IMMEDIATE transaction,
   not the default deferred one) is enforced by the OS/SQLite engine
   itself -- correct across processes, not just threads.
2. Every write no longer needs to re-read the entire history to find
   the last hash: a single indexed query does it, so throughput
   doesn't degrade as the log grows the way LedgerStore's O(n) re-read
   does.

Still produces the exact same DecisionRecord shape and hash chain as
LedgerStore, so ledger.verify_chain() works unchanged against records
read back from here.
"""

import sqlite3
import threading

from ledger import GENESIS_HASH, DecisionRecord, data_snapshot, model_version, prompt_version

SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    timestamp TEXT NOT NULL,
    model_version TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    data_snapshot TEXT NOT NULL,
    input TEXT NOT NULL,
    output TEXT NOT NULL,
    previous_hash TEXT NOT NULL,
    actor TEXT NOT NULL DEFAULT ''
)
"""

_COLUMNS = "id, timestamp, model_version, prompt_version, data_snapshot, input, output, previous_hash, actor"


class SQLAuditLog:
    def __init__(self, db_path):
        self.db_path = str(db_path)
        self._local = threading.local()
        self._init_schema()

    def _init_schema(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(SCHEMA)
        conn.commit()
        conn.close()

    def _conn(self):
        # One connection per thread: sqlite3 connections aren't safe to
        # share across threads. WAL mode plus BEGIN IMMEDIATE (below) is
        # what makes multiple connections -- same process or different
        # processes -- safe to write concurrently, which a single
        # Python-level lock could never give us across processes anyway.
        if not hasattr(self._local, "conn"):
            conn = sqlite3.connect(self.db_path, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn = conn
        return self._local.conn

    def _last_hash(self, conn):
        row = conn.execute(f"SELECT {_COLUMNS} FROM decisions ORDER BY seq DESC LIMIT 1").fetchone()
        if row is None:
            return GENESIS_HASH
        return DecisionRecord(*row).record_hash()

    def record(self, decision_id, timestamp, actor, action, resource, decision):
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")  # takes SQLite's write lock now, not at the first write --
        try:                             # closes the exact read-then-write race a deferred BEGIN would leave open
            previous_hash = self._last_hash(conn)
            record = DecisionRecord(
                id=decision_id,
                timestamp=timestamp,
                model_version=model_version("warrant-policy-engine-v1"),
                prompt_version=prompt_version(f"authorize:{action}"),
                data_snapshot=data_snapshot({"resource": resource}),
                input=f"{actor} -> {action} -> {resource}",
                output=f"{'ALLOWED' if decision.allowed else 'DENIED'}: {decision.reason}",
                previous_hash=previous_hash,
                actor=actor,
            )
            conn.execute(
                f"INSERT INTO decisions ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record.id, record.timestamp, record.model_version, record.prompt_version,
                    record.data_snapshot, record.input, record.output, record.previous_hash, record.actor,
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return record

    def read_all(self):
        conn = self._conn()
        rows = conn.execute(f"SELECT {_COLUMNS} FROM decisions ORDER BY seq ASC").fetchall()
        return [DecisionRecord(*row) for row in rows]
