"""Small SQLite infrastructure layer used by adaptive persistence.

SQLite is intentional here: GestureForge is a local, single-camera application,
so a server database would add operational cost without improving the current
runtime.  Repositories are the only code allowed to issue SQL statements.
"""

from contextlib import contextmanager
import os
import sqlite3
import threading
from typing import Iterator, Optional

from config import Config


class SQLiteDatabase:
    """Thread-safe schema bootstrap with one short-lived connection per operation."""

    _schema_lock = threading.Lock()

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or Config.PROFILE_DB_PATH
        self._initialized = False
        self._instance_lock = threading.Lock()
        self._memory_connection: Optional[sqlite3.Connection] = None
        self._memory_operation_lock = threading.RLock()

    def _open(self) -> sqlite3.Connection:
        if self.db_path == ":memory:":
            if self._memory_connection is None:
                self._memory_connection = sqlite3.connect(
                    ":memory:", timeout=5.0, check_same_thread=False
                )
            connection = self._memory_connection
        else:
            parent = os.path.dirname(os.path.abspath(self.db_path))
            if parent:
                os.makedirs(parent, exist_ok=True)
            connection = sqlite3.connect(self.db_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        if self.db_path != ":memory:":
            connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def initialize(self) -> None:
        with self._instance_lock:
            if self._initialized:
                return
            with self._schema_lock:
                if self._initialized:
                    return
                connection = self._open()
                try:
                    connection.executescript(
                        """
                        CREATE TABLE IF NOT EXISTS user_profiles (
                            user_id TEXT PRIMARY KEY,
                            display_name TEXT NOT NULL,
                            preferred_language TEXT NOT NULL,
                            preferred_module TEXT NOT NULL,
                            cursor_sensitivity REAL NOT NULL,
                            scroll_sensitivity TEXT NOT NULL,
                            interaction_mode TEXT NOT NULL,
                            adaptive_enabled INTEGER NOT NULL DEFAULT 1,
                            learning_enabled INTEGER NOT NULL DEFAULT 1,
                            unknown_gesture_threshold REAL NOT NULL DEFAULT 0.60,
                            interaction_count INTEGER NOT NULL DEFAULT 0,
                            unknown_gesture_count INTEGER NOT NULL DEFAULT 0,
                            confirmed_intent_count INTEGER NOT NULL DEFAULT 0,
                            intent_preferences_json TEXT NOT NULL DEFAULT '{}',
                            created_at TEXT NOT NULL,
                            updated_at TEXT NOT NULL,
                            last_seen_at TEXT NOT NULL
                        );

                        CREATE TABLE IF NOT EXISTS interaction_events (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            user_id TEXT NOT NULL,
                            occurred_at TEXT NOT NULL,
                            module TEXT NOT NULL,
                            hand TEXT NOT NULL,
                            gesture TEXT NOT NULL,
                            finger_count INTEGER NOT NULL DEFAULT 0,
                            motion_direction TEXT NOT NULL,
                            motion_speed REAL NOT NULL DEFAULT 0,
                            displacement REAL NOT NULL DEFAULT 0,
                            tracking_quality REAL NOT NULL DEFAULT 0,
                            is_unknown INTEGER NOT NULL DEFAULT 0,
                            unknown_status TEXT NOT NULL,
                            unknown_reason TEXT NOT NULL,
                            intent TEXT NOT NULL,
                            intent_confidence REAL NOT NULL DEFAULT 0,
                            action_taken INTEGER NOT NULL DEFAULT 0,
                            temporal_features_json TEXT NOT NULL DEFAULT '{}',
                            context_snapshot_json TEXT NOT NULL DEFAULT '{}',
                            feedback TEXT,
                            FOREIGN KEY(user_id) REFERENCES user_profiles(user_id) ON DELETE CASCADE
                        );

                        CREATE INDEX IF NOT EXISTS idx_interaction_events_user_time
                            ON interaction_events(user_id, occurred_at DESC);
                        CREATE INDEX IF NOT EXISTS idx_interaction_events_user_intent
                            ON interaction_events(user_id, intent);
                        """
                    )
                    connection.commit()
                    self._initialized = True
                finally:
                    if self.db_path != ":memory:":
                        connection.close()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        self.initialize()
        operation_lock = self._memory_operation_lock if self.db_path == ":memory:" else _NullLock()
        with operation_lock:
            connection = self._open()
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                if self.db_path != ":memory:":
                    connection.close()


class _NullLock:
    """Context-manager no-op used for file-backed SQLite connections."""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False


profile_database = SQLiteDatabase()
