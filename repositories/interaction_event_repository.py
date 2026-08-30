"""Persistence repository for meaningful adaptive interaction events."""

from typing import List, Optional

from database.sqlite_database import SQLiteDatabase, profile_database
from models.user_profile import InteractionEvent


class InteractionEventRepository:
    def __init__(self, database: Optional[SQLiteDatabase] = None):
        self.database = database or profile_database

    def add(self, event: InteractionEvent) -> InteractionEvent:
        record = event.to_record()
        with self.database.connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO interaction_events (
                    user_id, occurred_at, module, hand, gesture, finger_count,
                    motion_direction, motion_speed, displacement, tracking_quality,
                    is_unknown, unknown_status, unknown_reason, intent,
                    intent_confidence, action_taken, temporal_features_json,
                    context_snapshot_json, feedback
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["user_id"], record["occurred_at"], record["module"],
                    record["hand"], record["gesture"], record["finger_count"],
                    record["motion_direction"], record["motion_speed"],
                    record["displacement"], record["tracking_quality"],
                    record["is_unknown"], record["unknown_status"],
                    record["unknown_reason"], record["intent"],
                    record["intent_confidence"], record["action_taken"],
                    record["temporal_features_json"], record["context_snapshot_json"],
                    record["feedback"],
                ),
            )
            event.event_id = int(cursor.lastrowid)
        return event

    def recent(self, user_id: str, limit: int = 20) -> List[InteractionEvent]:
        limit = max(1, min(int(limit), 100))
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM interaction_events
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        return [InteractionEvent.from_row(row) for row in rows]

    def set_feedback(self, user_id: str, event_id: int, feedback: str) -> bool:
        with self.database.connection() as connection:
            cursor = connection.execute(
                """
                UPDATE interaction_events
                SET feedback = ?
                WHERE id = ? AND user_id = ?
                """,
                (feedback, event_id, user_id),
            )
        return cursor.rowcount > 0
