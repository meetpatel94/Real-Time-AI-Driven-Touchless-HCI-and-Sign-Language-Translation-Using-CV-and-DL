"""Persistence repository for :class:`models.user_profile.UserProfile`."""

from datetime import datetime, timezone
from typing import Optional

from database.sqlite_database import SQLiteDatabase, profile_database
from models.user_profile import UserProfile


class UserProfileRepository:
    """CRUD operations for profiles; no Flask or adaptive policy belongs here."""

    def __init__(self, database: Optional[SQLiteDatabase] = None):
        self.database = database or profile_database

    def get(self, user_id: str) -> Optional[UserProfile]:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM user_profiles WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return UserProfile.from_row(row) if row else None

    def save(self, profile: UserProfile) -> UserProfile:
        record = profile.to_record()
        columns = [
            "user_id", "display_name", "preferred_language", "preferred_module",
            "cursor_sensitivity", "scroll_sensitivity", "interaction_mode",
            "adaptive_enabled", "learning_enabled", "unknown_gesture_threshold",
            "interaction_count", "unknown_gesture_count", "confirmed_intent_count",
            "intent_preferences_json", "created_at", "updated_at", "last_seen_at",
        ]
        placeholders = ", ".join("?" for _ in columns)
        updates = ", ".join(
            f"{column} = excluded.{column}"
            for column in columns
            if column not in {"user_id", "created_at"}
        )
        values = tuple(record[column] for column in columns)

        with self.database.connection() as connection:
            connection.execute(
                f"""
                INSERT INTO user_profiles ({', '.join(columns)})
                VALUES ({placeholders})
                ON CONFLICT(user_id) DO UPDATE SET {updates}
                """,
                values,
            )
        return profile

    def touch_and_increment(
        self,
        user_id: str,
        unknown: bool = False,
        confirmed: bool = False,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self.database.connection() as connection:
            connection.execute(
                """
                UPDATE user_profiles
                SET interaction_count = interaction_count + 1,
                    unknown_gesture_count = unknown_gesture_count + ?,
                    confirmed_intent_count = confirmed_intent_count + ?,
                    updated_at = ?,
                    last_seen_at = ?
                WHERE user_id = ?
                """,
                (int(unknown), int(confirmed), now, now, user_id),
            )

    def delete(self, user_id: str) -> bool:
        with self.database.connection() as connection:
            cursor = connection.execute(
                "DELETE FROM user_profiles WHERE user_id = ?",
                (user_id,),
            )
        return cursor.rowcount > 0
