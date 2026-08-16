from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import sqlite3
from typing import Iterator


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: str):
        self.path = path
        self._anchor = sqlite3.connect(path, check_same_thread=False) if path == ":memory:" else None
        initializer = self._anchor or sqlite3.connect(path)
        try:
            self._initialize(initializer)
        finally:
            if self._anchor is None:
                initializer.close()

    @staticmethod
    def _initialize(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS orders (
              out_trade_no TEXT PRIMARY KEY,
              request_id TEXT UNIQUE NOT NULL,
              request_fingerprint TEXT NOT NULL,
              plan TEXT NOT NULL,
              amount_minor INTEGER NOT NULL,
              provider_state TEXT NOT NULL,
              payment_code TEXT NOT NULL,
              fulfilled_at TEXT,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS entitlements (
              entitlement_id TEXT PRIMARY KEY,
              out_trade_no TEXT UNIQUE NOT NULL REFERENCES orders(out_trade_no),
              plan TEXT NOT NULL,
              starts_at TEXT NOT NULL,
              expires_at TEXT NOT NULL,
              state TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS api_keys (
              key_id TEXT PRIMARY KEY,
              entitlement_id TEXT UNIQUE NOT NULL REFERENCES entitlements(entitlement_id),
              key_prefix TEXT NOT NULL,
              key_hash TEXT UNIQUE NOT NULL,
              revoked_at TEXT
            );
            CREATE TABLE IF NOT EXISTS sessions (
              session_id TEXT PRIMARY KEY,
              key_id TEXT NOT NULL REFERENCES api_keys(key_id),
              request_id TEXT NOT NULL,
              request_fingerprint TEXT NOT NULL,
              kind TEXT NOT NULL,
              exam TEXT NOT NULL,
              template_id TEXT,
              catalog_mode TEXT NOT NULL,
              questions_json TEXT NOT NULL,
              submitted_request_id TEXT,
              submitted_fingerprint TEXT,
              result_json TEXT,
              created_at TEXT NOT NULL,
              UNIQUE(key_id, request_id)
            );
            """
        )
        connection.commit()

    @contextmanager
    def connect(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        connection = self._anchor if self._anchor is not None else sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            if immediate:
                connection.execute("BEGIN IMMEDIATE")
            yield connection
            if immediate:
                connection.commit()
        except Exception:
            if immediate:
                connection.rollback()
            raise
        finally:
            if self._anchor is None:
                connection.close()
