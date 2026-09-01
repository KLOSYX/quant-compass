"""Small versioned SQLite audit store for local household use."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from core.domain import _canonicalize, canonical_hash


SCHEMA_VERSION = 1


def migrate(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS schema_meta (version INTEGER NOT NULL)"
    )
    row = connection.execute("SELECT version FROM schema_meta LIMIT 1").fetchone()
    current = int(row[0]) if row else 0
    if current < 1:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS market_snapshots (
                data_hash TEXT PRIMARY KEY, payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS decisions (
                decision_hash TEXT PRIMARY KEY, strategy_policy_id TEXT NOT NULL,
                input_json TEXT NOT NULL, result_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS actual_fills (
                id INTEGER PRIMARY KEY AUTOINCREMENT, decision_hash TEXT NOT NULL,
                payload_json TEXT NOT NULL, created_at TEXT NOT NULL,
                FOREIGN KEY(decision_hash) REFERENCES decisions(decision_hash)
            );
            """
        )
        if row:
            connection.execute("UPDATE schema_meta SET version = 1")
        else:
            connection.execute("INSERT INTO schema_meta(version) VALUES (1)")
    connection.commit()


class AuditStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.execute("PRAGMA foreign_keys = ON")
        migrate(self.connection)

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(_canonicalize(value), ensure_ascii=False, sort_keys=True)

    def save_snapshot(self, snapshot: Any, created_at: Any) -> str:
        snapshot_hash = getattr(snapshot, "data_hash", canonical_hash(snapshot))
        self.connection.execute(
            "INSERT OR IGNORE INTO market_snapshots VALUES (?, ?, ?)",
            (snapshot_hash, self._json(snapshot), str(created_at)),
        )
        self.connection.commit()
        return snapshot_hash

    def save_decision(
        self, decision_input: Any, decision_result: Any, created_at: Any
    ) -> str:
        decision_hash = canonical_hash(decision_result)
        self.connection.execute(
            "INSERT OR REPLACE INTO decisions VALUES (?, ?, ?, ?, ?)",
            (
                decision_hash,
                decision_result.strategy_policy_id,
                self._json(decision_input),
                self._json(decision_result),
                str(created_at),
            ),
        )
        self.connection.commit()
        return decision_hash

    def save_actual_fill(self, decision_hash: str, fill: Any, created_at: Any) -> None:
        self.connection.execute(
            "INSERT INTO actual_fills(decision_hash, payload_json, created_at) VALUES (?, ?, ?)",
            (decision_hash, self._json(fill), str(created_at)),
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


def default_audit_store() -> AuditStore:
    configured = os.environ.get("QUANT_COMPASS_AUDIT_DB")
    path = (
        Path(configured)
        if configured
        else Path(__file__).resolve().parents[1] / "data" / "audit.sqlite3"
    )
    return AuditStore(path)
