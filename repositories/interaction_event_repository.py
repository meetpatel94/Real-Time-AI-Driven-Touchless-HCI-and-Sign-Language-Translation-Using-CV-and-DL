"""MongoDB repository for meaningful adaptive interaction events."""

from typing import List, Optional
from uuid import uuid4

from database.mongo_database import MongoDatabase, mongo_database
from models.user_profile import InteractionEvent


class InteractionEventRepository:
    COLLECTION = "interaction_events"

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
        try:
            return self.database[self.COLLECTION]
        except Exception:
            return None

    def _mark_unavailable(self, error) -> None:
        mark_unavailable = getattr(self.database, "mark_unavailable", None)
        if callable(mark_unavailable):
            mark_unavailable(error)

    def add(self, event: InteractionEvent) -> Optional[InteractionEvent]:
        if not self.storage_available:
            return None
        collection = self._collection()
        if collection is None:
            return None
        if not event.event_id:
            event.event_id = str(uuid4())
        document = event.to_document()
        document["_id"] = event.event_id
        try:
            # Replacement/upsert also makes the one-time legacy import safe to
            # rerun when an event already carries a stable id.
            collection.replace_one({"_id": event.event_id}, document, upsert=True)
            return event
        except Exception as exc:
            self._mark_unavailable(exc)
            return None

    def recent(self, user_id: str, limit: int = 20) -> List[InteractionEvent]:
        collection = self._collection()
        if collection is None:
            return []
        limit = max(1, min(int(limit), 100))
        try:
            documents = list(
                collection.find({"user_id": user_id})
                .sort([("occurred_at", -1), ("_id", -1)])
                .limit(limit)
            )
            return [InteractionEvent.from_document(document) for document in documents]
        except Exception as exc:
            self._mark_unavailable(exc)
            return []

    def delete_for_user(self, user_id: str) -> bool:
        collection = self._collection()
        if collection is None:
            return False
        try:
            collection.delete_many({"user_id": user_id})
            return True
        except Exception as exc:
            self._mark_unavailable(exc)
            return False

    def set_feedback(self, user_id: str, event_id: str, feedback: str) -> bool:
        collection = self._collection()
        if collection is None:
            return False
        try:
            result = collection.update_one(
                {"_id": str(event_id), "user_id": user_id},
                {"$set": {"feedback": feedback}},
            )
            return bool(getattr(result, "matched_count", 0))
        except Exception as exc:
            self._mark_unavailable(exc)
            return False
