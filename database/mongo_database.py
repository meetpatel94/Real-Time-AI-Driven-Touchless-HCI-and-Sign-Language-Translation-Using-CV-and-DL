"""MongoDB connection and collection bootstrap for personalization data.

Only this infrastructure module knows how to create a MongoClient.  Repositories
receive collections from it and own document-level queries.  A missing or
unavailable MongoDB instance is treated as a degraded optional personalization
store; the camera and base recognition paths can continue running.
"""

import logging
import re
import threading
import time
from typing import Any, Dict, Optional

from config import Config

logger = logging.getLogger("GestureForge")

try:  # Keep imports graceful for environments that have not installed pymongo yet.
    from pymongo import MongoClient
    from pymongo.errors import CollectionInvalid
    HAS_PYMONGO = True
except ImportError:  # pragma: no cover - exercised in dependency-free smoke runs
    MongoClient = None
    CollectionInvalid = Exception
    HAS_PYMONGO = False


class MongoDatabase:
    """Lazy, bounded MongoDB client with health state and schema bootstrap."""

    COLLECTIONS = (
        "user_profiles",
        "interaction_events",
        "calibration_sessions",
        "learned_gestures",
        "validated_corrections",
        "custom_gesture_mappings",
    )

    _schema_lock = threading.Lock()

    def __init__(
        self,
        uri: Optional[str] = None,
        database_name: Optional[str] = None,
        client_factory=None,
        server_selection_timeout_ms: Optional[int] = None,
    ):
        self.uri = uri or Config.MONGODB_URI
        self.database_name = database_name or Config.MONGODB_DATABASE
        self.client_factory = client_factory or MongoClient
        configured_timeout = (
            server_selection_timeout_ms
            if server_selection_timeout_ms is not None
            else Config.MONGODB_SERVER_SELECTION_TIMEOUT_MS
        )
        try:
            self.server_selection_timeout_ms = max(1, int(configured_timeout))
        except (TypeError, ValueError):
            self.server_selection_timeout_ms = Config.MONGODB_SERVER_SELECTION_TIMEOUT_MS
        self._client = None
        self._database = None
        self._schema_ready = False
        self._available = False
        self._last_error = ""
        self._last_failure_at = 0.0
        self._last_health_check_at = 0.0
        self._health_check_interval_seconds = 5.0
        self._lock = threading.RLock()

    @property
    def available(self) -> bool:
        with self._lock:
            return bool(self._available)

    @property
    def last_error(self) -> str:
        with self._lock:
            return self._last_error

    def _connect(self):
        if self.client_factory is None:
            with self._lock:
                if self._last_failure_at and time.monotonic() - self._last_failure_at < 2.0:
                    return None
                self._mark_unavailable("pymongo is not installed")
            return None

        with self._lock:
            if self._client is not None and self._available:
                return self._client

            # Avoid reconnecting on every camera frame while MongoDB is down.
            if self._last_failure_at and (
                time.monotonic() - self._last_failure_at < 2.0
            ):
                return None

            try:
                client_options = {
                    "serverSelectionTimeoutMS": self.server_selection_timeout_ms,
                    "connectTimeoutMS": Config.MONGODB_CONNECT_TIMEOUT_MS,
                    "socketTimeoutMS": Config.MONGODB_SOCKET_TIMEOUT_MS,
                    "maxPoolSize": Config.MONGODB_MAX_POOL_SIZE,
                    "retryWrites": True,
                }
                try:
                    self._client = self.client_factory(self.uri, **client_options)
                except TypeError:
                    # Small test doubles and deployment wrappers may accept
                    # only the URI; the real PyMongo path uses all options.
                    self._client = self.client_factory(self.uri)
                self._client.admin.command("ping")
                self._database = self._client[self.database_name]
                self._ensure_schema(self._database)
                self._available = True
                self._last_error = ""
                self._last_failure_at = 0.0
                self._last_health_check_at = time.monotonic()
                return self._client
            except Exception as exc:
                self._mark_unavailable(str(exc))
                return None

    def _ensure_schema(self, database) -> None:
        """Create validators/indexes once; tolerate an already-created collection."""
        with self._schema_lock:
            if self._schema_ready:
                return

            validators: Dict[str, Dict[str, Any]] = {
                "user_profiles": {
                    "$jsonSchema": {
                        "bsonType": "object",
                        "required": ["_id", "user_id", "display_name", "updated_at"],
                        "properties": {
                            "_id": {"bsonType": "string"},
                            "user_id": {"bsonType": "string"},
                            "display_name": {"bsonType": "string"},
                            "cursor_sensitivity": {"bsonType": ["double", "int", "long"]},
                            "adaptive_enabled": {"bsonType": "bool"},
                            "learning_enabled": {"bsonType": "bool"},
                        },
                    }
                },
                "interaction_events": {
                    "$jsonSchema": {
                        "bsonType": "object",
                        "required": ["user_id", "occurred_at", "intent"],
                        "properties": {
                            "user_id": {"bsonType": "string"},
                            "intent": {"bsonType": "string"},
                            "is_unknown": {"bsonType": "bool"},
                            "temporal_features": {"bsonType": "object"},
                            "context_snapshot": {"bsonType": "object"},
                        },
                    }
                },
                "calibration_sessions": {
                    "$jsonSchema": {
                        "bsonType": "object",
                        "required": ["_id", "user_id", "target_key", "status", "samples"],
                        "properties": {
                            "_id": {"bsonType": "string"},
                            "user_id": {"bsonType": "string"},
                            "target_key": {"bsonType": "string"},
                            "status": {"enum": ["ACTIVE", "COMPLETED", "CANCELLED"]},
                            "samples": {"bsonType": "array"},
                        },
                    }
                },
                "learned_gestures": {
                    "$jsonSchema": {
                        "bsonType": "object",
                        "required": ["_id", "user_id", "gesture_key", "centroid", "validated_examples"],
                        "properties": {
                            "_id": {"bsonType": "string"},
                            "user_id": {"bsonType": "string"},
                            "gesture_key": {"bsonType": "string"},
                            "centroid": {"bsonType": "object"},
                            "validated_examples": {"bsonType": ["int", "long"]},
                            "reliability": {"bsonType": ["double", "int", "long"]},
                        },
                    }
                },
                "validated_corrections": {
                    "$jsonSchema": {
                        "bsonType": "object",
                        "required": ["_id", "user_id", "correct_label", "signature", "validated"],
                        "properties": {
                            "_id": {"bsonType": "string"},
                            "user_id": {"bsonType": "string"},
                            "correct_label": {"bsonType": "string"},
                            "signature": {"bsonType": "object"},
                            "validated": {"bsonType": "bool"},
                        },
                    }
                },
                "custom_gesture_mappings": {
                    "$jsonSchema": {
                        "bsonType": "object",
                        "required": ["_id", "user_id", "action", "learned_gesture_id"],
                        "properties": {
                            "_id": {"bsonType": "string"},
                            "user_id": {"bsonType": "string"},
                            "action": {"bsonType": "string"},
                            "learned_gesture_id": {"bsonType": "string"},
                            "enabled": {"bsonType": "bool"},
                        },
                    }
                },
            }

            for name in self.COLLECTIONS:
                validator = validators.get(name)
                try:
                    database.create_collection(
                        name,
                        validator=validator,
                        validationLevel="moderate",
                        validationAction="error",
                    )
                except CollectionInvalid:
                    # A deployment may already have the collection. Attempt to
                    # apply the validator as well; collMod is optional on some
                    # managed/test Mongo implementations.
                    try:
                        database.command(
                            "collMod",
                            name,
                            validator=validator,
                            validationLevel="moderate",
                            validationAction="error",
                        )
                    except Exception:
                        pass
                except Exception as exc:
                    # Some managed MongoDB deployments restrict collMod/create
                    # collection. Queries can still work, so log and continue.
                    logger.warning(
                        "MongoDB schema bootstrap for %s skipped: %s",
                        name,
                        self._redact_error(exc),
                    )

            indexes = {
                "user_profiles": [("user_id", 1)],
                "interaction_events": [("user_id", 1), ("occurred_at", -1)],
                "calibration_sessions": [("user_id", 1), ("status", 1)],
                "learned_gestures": [("user_id", 1), ("gesture_key", 1)],
                "validated_corrections": [("user_id", 1), ("created_at", -1)],
                "custom_gesture_mappings": [("user_id", 1), ("learned_gesture_id", 1)],
            }
            unique_indexes = {
                ("user_profiles", (("user_id", 1),)),
                ("learned_gestures", (("user_id", 1), ("gesture_key", 1))),
                ("custom_gesture_mappings", (("user_id", 1), ("learned_gesture_id", 1))),
            }
            for name, keys in indexes.items():
                try:
                    database[name].create_index(
                        keys,
                        unique=(name, tuple(keys)) in unique_indexes,
                    )
                except Exception as exc:
                    logger.warning(
                        "MongoDB index bootstrap for %s skipped: %s",
                        name,
                        self._redact_error(exc),
                    )
            self._schema_ready = True

    def collection(self, name: str):
        if name not in self.COLLECTIONS:
            raise ValueError("Unknown GestureForge MongoDB collection: %s" % name)
        client = self._connect()
        if client is None or self._database is None:
            return None
        return self._database[name]

    def mark_unavailable(self, error: Any) -> None:
        self._mark_unavailable(str(error))

    def _redact_error(self, error: Any) -> str:
        message = str(error)
        if self.uri:
            message = message.replace(str(self.uri), "mongodb://<redacted>")
        message = re.sub(
            r"(mongodb(?:\+srv)?://)([^/@\s]+)@",
            r"\1<redacted>@",
            message,
            flags=re.IGNORECASE,
        )
        return message[:500]

    def _mark_unavailable(self, error: str) -> None:
        with self._lock:
            self._available = False
            self._last_error = self._redact_error(error)
            self._last_failure_at = time.monotonic()
            if self._client is not None:
                try:
                    self._client.close()
                except Exception:
                    pass
            self._client = None
            self._database = None
            self._schema_ready = False
            self._last_health_check_at = 0.0

    def health(self) -> Dict[str, Any]:
        # ``MongoClient`` is lazy, so a cached client alone is not proof that
        # the server is still reachable. Health checks are outside the camera
        # hot path and may perform one bounded ping/reconnect attempt. Avoid
        # pinging on every adaptive-status poll while still checking regularly.
        now = time.monotonic()
        with self._lock:
            client = self._client if self._available else None
            recent_check = (
                client is not None
                and now - self._last_health_check_at < self._health_check_interval_seconds
            )
        if client is not None and recent_check:
            return {
                "available": True,
                "database": self.database_name,
                "error": self.last_error,
            }
        if client is not None:
            try:
                client.admin.command("ping")
                with self._lock:
                    self._last_health_check_at = time.monotonic()
            except Exception as exc:
                self._mark_unavailable(str(exc))
                client = None
        if client is None:
            client = self._connect()
        return {
            "available": client is not None and self.available,
            "database": self.database_name,
            "error": self.last_error,
        }

    def close(self) -> None:
        with self._lock:
            if self._client is not None:
                try:
                    self._client.close()
                except Exception:
                    pass
            self._client = None
            self._database = None
            self._available = False
            self._schema_ready = False
            # ``close`` is an explicit lifecycle boundary. Do not carry a
            # previous connection failure's backoff into a deliberate reopen.
            self._last_failure_at = 0.0
            self._last_health_check_at = 0.0
            self._last_error = ""


mongo_database = MongoDatabase()
# Short alias for integrations/tests that use the conventional mongo_db name.
mongo_db = mongo_database
