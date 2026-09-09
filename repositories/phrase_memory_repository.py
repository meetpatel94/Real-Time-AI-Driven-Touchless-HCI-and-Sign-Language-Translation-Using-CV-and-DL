"""MongoDB repository for the personalized phrase memory.

Follows the project's personalization storage rules: MongoDB is an *optional*
store, so every method degrades to "no data" instead of raising when the
database (or pymongo) is unavailable.
"""

from typing import List, Optional

from database.mongo_database import MongoDatabase, mongo_database
from models.phrase_memory import PhraseMemoryEntry


class PhraseMemoryRepository:
    """CRUD boundary for ``phrase_memory``; the service owns policy."""

    COLLECTION = "phrase_memory"

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

    def save(self, entry: PhraseMemoryEntry) -> bool:
        if not entry.is_valid or not self.storage_available:
            return False
        collection = self._collection()
        if collection is None:
            return False
        try:
            collection.replace_one({"_id": entry.memory_id}, entry.to_document(), upsert=True)
            return True
        except Exception as exc:  # pragma: no cover - storage outage path
            self._mark_unavailable(exc)
            return False

    def list_for_user(self, user_id: str, limit: int = 200) -> List[PhraseMemoryEntry]:
        collection = self._collection()
        if collection is None:
            return []
        limit = max(1, min(int(limit), 500))
        try:
            documents = list(
                collection.find({"user_id": user_id}).sort([("updated_at", -1)]).limit(limit)
            )
        except Exception as exc:  # pragma: no cover - storage outage path
            self._mark_unavailable(exc)
            return []
        entries = [PhraseMemoryEntry.from_document(document) for document in documents]
        return [entry for entry in entries if entry is not None]

    def delete_for_user(self, user_id: str) -> bool:
        collection = self._collection()
        if collection is None:
            return False
        try:
            result = collection.delete_many({"user_id": user_id})
            return bool(getattr(result, "deleted_count", 0))
        except Exception as exc:  # pragma: no cover - storage outage path
            self._mark_unavailable(exc)
            return False


phrase_memory_repository = PhraseMemoryRepository()
