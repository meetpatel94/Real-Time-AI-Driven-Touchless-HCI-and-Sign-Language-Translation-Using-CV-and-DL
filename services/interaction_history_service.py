"""Service for bounded, meaningful adaptive interaction history."""

from collections import deque
from datetime import datetime, timezone
import threading
import time
from typing import Dict, List, Optional, Tuple

from config import Config
from models.user_profile import InteractionEvent
from repositories.interaction_event_repository import InteractionEventRepository
from services.logging_service import logger
from services.user_profile_service import UserProfileService, user_profile_service


class InteractionHistoryService:
    """Deduplicates frame-level observations before persisting them.

    The camera loop can run at roughly 30 FPS.  Persisting every frame would be
    noisy and harmful to latency, so only intent/unknown-state transitions are
    recorded and repeated states are rate limited.
    """

    def __init__(
        self,
        repository: Optional[InteractionEventRepository] = None,
        profile_service: Optional[UserProfileService] = None,
    ):
        self.repository = repository or InteractionEventRepository()
        self.profile_service = profile_service or user_profile_service
        self._lock = threading.RLock()
        self._last_record: Dict[str, Tuple[str, float]] = {}
        self._recent_cache: Dict[str, deque] = {}

    def _load_recent(self, user_id: str) -> deque:
        with self._lock:
            if user_id not in self._recent_cache:
                try:
                    limit = max(1, int(Config.ADAPTIVE_HISTORY_LIMIT))
                    events = self.repository.recent(user_id, limit=limit)
                    self._recent_cache[user_id] = deque(reversed(events), maxlen=limit)
                except Exception as exc:
                    logger.warning("Unable to load adaptive interaction history: %s", exc)
                    self._recent_cache[user_id] = deque(maxlen=max(1, int(Config.ADAPTIVE_HISTORY_LIMIT)))
            return self._recent_cache[user_id]

    def recent_events(self, user_id: str, limit: int = 20) -> List[InteractionEvent]:
        limit = max(1, min(int(limit), int(Config.ADAPTIVE_HISTORY_LIMIT)))
        with self._lock:
            events = list(self._load_recent(user_id))
        return list(reversed(events[-limit:]))

    def recent_intents(self, user_id: str, limit: int = 8) -> List[str]:
        events = self.recent_events(user_id, limit=limit)
        return [event.intent for event in events if event.intent and event.intent != "IDLE"]

    def record_transition(self, event: InteractionEvent) -> Optional[InteractionEvent]:
        """Persist one actual adaptive transition, returning it when written."""
        if not event.user_id:
            return None
        if event.intent in {"IDLE", "LEGACY_ROUTING"} and not event.is_unknown:
            return None

        now = time.monotonic()
        signature = "|".join(
            (
                event.intent,
                event.unknown_status,
                event.gesture,
                event.motion_direction,
            )
        )
        with self._lock:
            previous = self._last_record.get(event.user_id)
            if previous and previous[0] == signature and now - previous[1] < 0.45:
                return None
            self._last_record[event.user_id] = (signature, now)

            try:
                # Ensure the foreign-key parent exists, then prime the cache
                # before inserting so the just-written event is not loaded from
                # SQLite and then appended a second time.
                self.profile_service.get_profile(event.user_id)
                recent = self._load_recent(event.user_id)
                saved = self.repository.add(event)
                recent.append(saved)
                self.profile_service.register_interaction(
                    event.user_id,
                    unknown=event.is_unknown,
                    confirmed=event.intent in {"sign.commit", "selection.click"},
                )
                return saved
            except Exception as exc:
                # Adaptation is an enhancement; persistence failure must never
                # stop MediaPipe, cursor control, or sign inference.
                logger.warning("Adaptive interaction event was not persisted: %s", exc)
                return None

    def set_feedback(self, user_id: str, event_id: int, feedback: str) -> bool:
        normalized = str(feedback or "").strip().lower()
        if normalized not in {"accepted", "rejected", "correct", "incorrect", "dismissed"}:
            return False
        try:
            updated = self.repository.set_feedback(user_id, int(event_id), normalized)
            if updated:
                with self._lock:
                    for event in self._load_recent(user_id):
                        if event.event_id == int(event_id):
                            event.feedback = normalized
                            break
            return updated
        except Exception as exc:
            logger.warning("Adaptive feedback was not persisted: %s", exc)
            return False

    def latest_event_id(self, user_id: str) -> Optional[int]:
        events = self.recent_events(user_id, limit=1)
        return events[0].event_id if events else None


interaction_history_service = InteractionHistoryService()
