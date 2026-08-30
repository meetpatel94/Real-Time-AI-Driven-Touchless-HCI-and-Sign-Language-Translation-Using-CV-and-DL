"""MongoDB repositories for calibration and validated personalization data."""

from typing import List, Optional

from database.mongo_database import MongoDatabase, mongo_database
from models.personalization import (
    CalibrationSession,
    CorrectionRecord,
    CustomGestureMapping,
    LearnedGesture,
)


class _MongoRepository:
    def __init__(self, database: Optional[MongoDatabase] = None):
        self.database = database if database is not None else mongo_database

    @property
    def storage_available(self) -> bool:
        available = getattr(self.database, "available", True)
        return available if isinstance(available, bool) else True

    def _collection(self, name: str):
        collection_method = getattr(self.database, "collection", None)
        if callable(collection_method):
            return collection_method(name)
        try:
            return self.database[name]
        except Exception:
            return None

    def _failed(self, exc: Exception) -> None:
        mark_unavailable = getattr(self.database, "mark_unavailable", None)
        if callable(mark_unavailable):
            mark_unavailable(exc)


class CalibrationSessionRepository(_MongoRepository):
    COLLECTION = "calibration_sessions"

    def create(self, session: CalibrationSession) -> bool:
        return self.save(session)

    def save(self, session: CalibrationSession) -> bool:
        collection = self._collection(self.COLLECTION)
        if collection is None:
            return False
        try:
            collection.replace_one(
                {"_id": session.session_id}, session.to_document(), upsert=True
            )
            return True
        except Exception as exc:
            self._failed(exc)
            return False

    def get(self, user_id: str, session_id: str) -> Optional[CalibrationSession]:
        collection = self._collection(self.COLLECTION)
        if collection is None:
            return None
        try:
            document = collection.find_one({"_id": session_id, "user_id": user_id})
            return CalibrationSession.from_document(document) if document else None
        except Exception as exc:
            self._failed(exc)
            return None

    def latest_active(self, user_id: str) -> Optional[CalibrationSession]:
        collection = self._collection(self.COLLECTION)
        if collection is None:
            return None
        try:
            document = collection.find_one(
                {"user_id": user_id, "status": "ACTIVE"},
                sort=[("updated_at", -1)],
            )
            return CalibrationSession.from_document(document) if document else None
        except Exception as exc:
            self._failed(exc)
            return None

    def delete_for_user(self, user_id: str) -> bool:
        collection = self._collection(self.COLLECTION)
        if collection is None:
            return False
        try:
            collection.delete_many({"user_id": user_id})
            return True
        except Exception as exc:
            self._failed(exc)
            return False


class LearnedGestureRepository(_MongoRepository):
    COLLECTION = "learned_gestures"

    def save(self, learned: LearnedGesture) -> bool:
        collection = self._collection(self.COLLECTION)
        if collection is None:
            return False
        try:
            collection.replace_one(
                {"_id": learned.learned_id}, learned.to_document(), upsert=True
            )
            return True
        except Exception as exc:
            self._failed(exc)
            return False

    def get(self, user_id: str, learned_id: str) -> Optional[LearnedGesture]:
        collection = self._collection(self.COLLECTION)
        if collection is None:
            return None
        try:
            document = collection.find_one({"_id": learned_id, "user_id": user_id})
            return LearnedGesture.from_document(document) if document else None
        except Exception as exc:
            self._failed(exc)
            return None

    def list_for_user(self, user_id: str) -> List[LearnedGesture]:
        collection = self._collection(self.COLLECTION)
        if collection is None:
            return []
        try:
            documents = collection.find({"user_id": user_id}).sort([("updated_at", -1)])
            return [LearnedGesture.from_document(document) for document in documents]
        except Exception as exc:
            self._failed(exc)
            return []

    def delete_for_user(self, user_id: str) -> bool:
        collection = self._collection(self.COLLECTION)
        if collection is None:
            return False
        try:
            collection.delete_many({"user_id": user_id})
            return True
        except Exception as exc:
            self._failed(exc)
            return False


class CorrectionRepository(_MongoRepository):
    COLLECTION = "validated_corrections"

    def add(self, correction: CorrectionRecord) -> bool:
        collection = self._collection(self.COLLECTION)
        if collection is None:
            return False
        try:
            collection.insert_one(correction.to_document())
            return True
        except Exception as exc:
            self._failed(exc)
            return False

    def recent(self, user_id: str, limit: int = 50) -> List[CorrectionRecord]:
        # Corrections are intentionally not returned with raw landmark data by
        # the public service; this method is for bounded internal auditing.
        collection = self._collection(self.COLLECTION)
        if collection is None:
            return []
        try:
            documents = list(
                collection.find({"user_id": user_id})
                .sort([("created_at", -1)])
                .limit(max(1, min(int(limit), 100)))
            )
            return [CorrectionRecord.from_document(document) for document in documents]
        except Exception as exc:
            self._failed(exc)
            return []

    def delete_for_user(self, user_id: str) -> bool:
        collection = self._collection(self.COLLECTION)
        if collection is None:
            return False
        try:
            collection.delete_many({"user_id": user_id})
            return True
        except Exception as exc:
            self._failed(exc)
            return False


class CustomMappingRepository(_MongoRepository):
    COLLECTION = "custom_gesture_mappings"

    def save(self, mapping: CustomGestureMapping) -> bool:
        collection = self._collection(self.COLLECTION)
        if collection is None:
            return False
        try:
            collection.replace_one(
                {"_id": mapping.mapping_id}, mapping.to_document(), upsert=True
            )
            return True
        except Exception as exc:
            self._failed(exc)
            return False

    def get(self, user_id: str, mapping_id: str) -> Optional[CustomGestureMapping]:
        collection = self._collection(self.COLLECTION)
        if collection is None:
            return None
        try:
            document = collection.find_one({"_id": mapping_id, "user_id": user_id})
            return CustomGestureMapping.from_document(document) if document else None
        except Exception as exc:
            self._failed(exc)
            return None

    def get_for_learned(self, user_id: str, learned_id: str) -> Optional[CustomGestureMapping]:
        collection = self._collection(self.COLLECTION)
        if collection is None:
            return None
        try:
            document = collection.find_one(
                {"user_id": user_id, "learned_gesture_id": learned_id}
            )
            return CustomGestureMapping.from_document(document) if document else None
        except Exception as exc:
            self._failed(exc)
            return None

    def list_for_user(self, user_id: str) -> List[CustomGestureMapping]:
        collection = self._collection(self.COLLECTION)
        if collection is None:
            return []
        try:
            return [
                CustomGestureMapping.from_document(document)
                for document in collection.find({"user_id": user_id}).sort([("updated_at", -1)])
            ]
        except Exception as exc:
            self._failed(exc)
            return []

    def delete(self, user_id: str, mapping_id: str) -> bool:
        collection = self._collection(self.COLLECTION)
        if collection is None:
            return False
        try:
            result = collection.delete_one({"_id": mapping_id, "user_id": user_id})
            return bool(getattr(result, "deleted_count", 0))
        except Exception as exc:
            self._failed(exc)
            return False

    def delete_for_user(self, user_id: str) -> bool:
        collection = self._collection(self.COLLECTION)
        if collection is None:
            return False
        try:
            collection.delete_many({"user_id": user_id})
            return True
        except Exception as exc:
            self._failed(exc)
            return False
