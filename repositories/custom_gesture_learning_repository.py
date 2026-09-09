"""MongoDB repository for Custom Gesture self-learning events.

Mirrors the learning *history* of the isolated Custom Gesture system
(candidate discovery, corrections, evolution) into the project's existing
persistence architecture.  The actual gesture samples remain local under
``data/custom_gestures/``; when MongoDB is unavailable every call degrades to
a no-op so the custom gesture library keeps working offline.
"""

from typing import Any, Dict, List, Optional
from uuid import uuid4

from database.mongo_database import MongoDatabase, mongo_database


class CustomGestureLearningRepository:
    COLLECTION = "custom_gesture_learning_events"

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

    def add_event(self, event: Dict[str, Any]) -> bool:
        """Persist one learning event.  Never raises; returns success flag."""
        if not self.storage_available:
            return False
        collection = self._collection()
        if collection is None:
            return False
        if not isinstance(event, dict) or not event.get("event_type"):
            return False
        document = {
            "event_type": str(event.get("event_type")),
            "created_at": str(event.get("created_at") or ""),
            "gesture_id": str(event.get("gesture_id") or ""),
            "candidate_id": str(event.get("candidate_id") or ""),
            "details": event.get("details") if isinstance(event.get("details"), dict) else {},
        }
        document["_id"] = str(event.get("event_id") or uuid4().hex)
        try:
            collection.replace_one({"_id": document["_id"]}, document, upsert=True)
            return True
        except Exception as exc:
            self._mark_unavailable(exc)
            return False

    def recent_events(self, limit: int = 50, gesture_id: str = "") -> List[Dict[str, Any]]:
        collection = self._collection()
        if collection is None:
            return []
        limit = max(1, min(int(limit), 200))
        query: Dict[str, Any] = {}
        if gesture_id:
            query["gesture_id"] = gesture_id
        try:
            documents = list(
                collection.find(query).sort([("created_at", -1), ("_id", -1)]).limit(limit)
            )
            return [
                {
                    "event_id": str(doc.get("_id", "")),
                    "event_type": doc.get("event_type", ""),
                    "created_at": doc.get("created_at", ""),
                    "gesture_id": doc.get("gesture_id", ""),
                    "candidate_id": doc.get("candidate_id", ""),
                    "details": doc.get("details", {}),
                }
                for doc in documents
            ]
        except Exception as exc:
            self._mark_unavailable(exc)
            return []


custom_gesture_learning_repository = CustomGestureLearningRepository()
