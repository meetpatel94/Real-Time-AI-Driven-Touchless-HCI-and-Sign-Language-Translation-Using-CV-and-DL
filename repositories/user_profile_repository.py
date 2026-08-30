"""MongoDB repository for personalized user profiles."""

from datetime import datetime, timezone
from typing import Optional

from database.mongo_database import MongoDatabase, mongo_database
from models.user_profile import UserProfile


class UserProfileRepository:
    """CRUD boundary for ``user_profiles``; services own validation/policy."""

    COLLECTION = "user_profiles"

    def __init__(self, database: Optional[MongoDatabase] = None):
        self.database = database if database is not None else mongo_database

    @property
    def storage_available(self) -> bool:
        available = getattr(self.database, "available", True)
        return available if isinstance(available, bool) else True

    def _collection(self):
        collection_method = getattr(self.database, "collection", None)
        if callable(collection_method):
            return collection_method(self.COLLECTION)
        try:  # Permit a direct mocked/PyMongo Database in repository tests.
            return self.database[self.COLLECTION]
        except Exception:
            return None

    def _mark_unavailable(self, error) -> None:
        mark_unavailable = getattr(self.database, "mark_unavailable", None)
        if callable(mark_unavailable):
            mark_unavailable(error)

    def get(self, user_id: str) -> Optional[UserProfile]:
        collection = self._collection()
        if collection is None:
            return None
        try:
            document = collection.find_one({"_id": user_id})
            # The stable _id is normally the profile id, but do not trust it
            # alone: a malformed/admin-created document must not cross a user
            # boundary merely because its _id matches the requested profile.
            if document and document.get("user_id") not in (None, user_id):
                document = None
            if document is None:
                # A hand-created document may use user_id without _id; this
                # fallback keeps migration/read compatibility without storing
                # duplicate profiles.
                document = collection.find_one({"user_id": user_id})
            if not document:
                return None
            profile = UserProfile.from_document(document)
            profile.user_id = user_id
            return profile
        except Exception as exc:
            self._mark_unavailable(exc)
            return None

    def save(self, profile: UserProfile) -> bool:
        collection = self._collection()
        if collection is None:
            return False
        document = profile.to_document()
        document["_id"] = profile.user_id
        try:
            # Do not overwrite a document that reuses this stable id for a
            # different owner. Legacy hand-created profiles without user_id may
            # still be upgraded, while cross-user collisions fail safely.
            collection.replace_one(
                {
                    "_id": profile.user_id,
                    "$or": [
                        {"user_id": profile.user_id},
                        {"user_id": {"$exists": False}},
                    ],
                },
                document,
                upsert=True,
            )
            return True
        except Exception as exc:
            self._mark_unavailable(exc)
            return False

    def touch_and_increment(
        self,
        user_id: str,
        unknown: bool = False,
        confirmed: bool = False,
    ) -> bool:
        if not self.storage_available:
            return False
        collection = self._collection()
        if collection is None:
            return False
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            result = collection.update_one(
                {"_id": user_id, "user_id": user_id},
                {
                    "$inc": {
                        "interaction_count": 1,
                        "unknown_gesture_count": int(bool(unknown)),
                        "confirmed_intent_count": int(bool(confirmed)),
                    },
                    "$set": {"updated_at": now, "last_seen_at": now},
                },
            )
            return bool(getattr(result, "matched_count", 1))
        except Exception as exc:
            self._mark_unavailable(exc)
            return False

    def delete(self, user_id: str) -> bool:
        collection = self._collection()
        if collection is None:
            return False
        try:
            result = collection.delete_one({"_id": user_id, "user_id": user_id})
            return bool(getattr(result, "deleted_count", 0))
        except Exception as exc:
            self._mark_unavailable(exc)
            return False
