"""One-time import of legacy GestureForge SQLite adaptive data into MongoDB.

SQLite is not an application backend anymore. This command exists only for a
previous installation that has a local profile database. It imports profiles
and meaningful adaptive events; it never imports camera frames, image files,
training data, or model artifacts.

Usage::

    GESTUREFORGE_LEGACY_SQLITE_PATH=data/gestureforge.sqlite3 \
        python -m database.migrate_sqlite_to_mongo
"""

import os
import sqlite3
from typing import Any, Dict

from config import Config
from database.mongo_database import mongo_database
from models.user_profile import (
    InteractionEvent,
    UserProfile,
    _safe_bool,
    _safe_json_dict,
)
from repositories.interaction_event_repository import InteractionEventRepository
from repositories.user_profile_repository import UserProfileRepository


def _legacy_path() -> str:
    return os.environ.get(
        "GESTUREFORGE_LEGACY_SQLITE_PATH",
        os.path.join(Config.BASE_DIR, "data", "gestureforge.sqlite3"),
    )


def _table_exists(connection, name: str) -> bool:
    row = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone()
    return row is not None


_MIGRATABLE_TEMPORAL_KEYS = frozenset({
    "delta_x", "delta_y", "speed", "displacement", "direction",
    "stability", "sample_count", "window_seconds", "trajectory",
})
_MIGRATABLE_CONTEXT_KEYS = frozenset({
    "active_module", "gesture_enabled", "sentence_active", "sign_prediction",
    "sign_confidence", "last_intent", "recent_intents", "profile_mode",
    "personalization_source", "personalization_confidence",
})


def _derived_payload(value: Any, allowed_keys) -> Dict[str, Any]:
    """Keep only bounded, known derived fields during legacy import."""
    payload = _safe_json_dict(value)
    return {key: payload[key] for key in allowed_keys if key in payload}


def _profile_document(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "user_id": row["user_id"],
        "display_name": row["display_name"],
        "preferred_language": row["preferred_language"],
        "preferred_module": row["preferred_module"],
        "cursor_sensitivity": row["cursor_sensitivity"],
        "scroll_sensitivity": row["scroll_sensitivity"],
        "interaction_mode": row["interaction_mode"],
        "adaptive_enabled": _safe_bool(row["adaptive_enabled"], True),
        "learning_enabled": _safe_bool(row["learning_enabled"], True),
        "unknown_gesture_threshold": row["unknown_gesture_threshold"],
        "interaction_count": row["interaction_count"],
        "unknown_gesture_count": row["unknown_gesture_count"],
        "confirmed_intent_count": row["confirmed_intent_count"],
        "intent_preferences": row["intent_preferences_json"] or "{}",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "last_seen_at": row["last_seen_at"],
    }


def _event_document(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "_id": str(row["id"]),
        "user_id": row["user_id"],
        "occurred_at": row["occurred_at"],
        "module": row["module"],
        "hand": row["hand"],
        "gesture": row["gesture"],
        "finger_count": row["finger_count"],
        "motion_direction": row["motion_direction"],
        "motion_speed": row["motion_speed"],
        "displacement": row["displacement"],
        "tracking_quality": row["tracking_quality"],
        "is_unknown": _safe_bool(row["is_unknown"], False),
        "unknown_status": row["unknown_status"],
        "unknown_reason": row["unknown_reason"],
        "intent": row["intent"],
        "intent_confidence": row["intent_confidence"],
        "action_taken": _safe_bool(row["action_taken"], False),
        "temporal_features": _derived_payload(
            row["temporal_features_json"], _MIGRATABLE_TEMPORAL_KEYS
        ),
        "context_snapshot": _derived_payload(
            row["context_snapshot_json"], _MIGRATABLE_CONTEXT_KEYS
        ),
        "feedback": row["feedback"],
    }


def migrate(path: str = None, database=None) -> Dict[str, Any]:
    """Import legacy rows idempotently and return a bounded migration report."""
    path = path or _legacy_path()
    database = database if database is not None else mongo_database
    report = {"source": path, "profiles": 0, "events": 0, "skipped": False, "error": ""}
    if not os.path.isfile(path):
        report["skipped"] = True
        report["error"] = "Legacy SQLite file was not found; nothing to migrate."
        return report

    profile_repository = UserProfileRepository(database)
    event_repository = InteractionEventRepository(database)
    try:
        # Establish/health-check Mongo once before the repository's real-time
        # event guard is used, including for an events-only legacy database.
        health = database.health()
        if isinstance(health, dict) and not health.get("available"):
            report["error"] = "MongoDB is unavailable; migration was not completed."
            return report
        with sqlite3.connect(path) as connection:
            connection.row_factory = sqlite3.Row
            if _table_exists(connection, "user_profiles"):
                for row in connection.execute("SELECT * FROM user_profiles"):
                    profile = UserProfile.from_document(_profile_document(row))
                    if not profile_repository.save(profile):
                        report["error"] = "MongoDB became unavailable while importing profiles."
                        return report
                    report["profiles"] += 1
            if _table_exists(connection, "interaction_events"):
                for row in connection.execute("SELECT * FROM interaction_events"):
                    event = InteractionEvent.from_document(_event_document(row))
                    # A stable legacy id makes re-running the command safe.
                    if event_repository.add(event) is None:
                        report["error"] = "MongoDB became unavailable while importing events."
                        return report
                    report["events"] += 1
    except Exception as exc:
        redactor = getattr(database, "_redact_error", None)
        report["error"] = (
            redactor(exc) if callable(redactor) else "MongoDB migration failed."
        )
    return report


if __name__ == "__main__":
    print(migrate())
