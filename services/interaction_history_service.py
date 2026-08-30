"""Service for bounded, meaningful adaptive interaction history."""

from collections import deque
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
        self.repository = repository if repository is not None else InteractionEventRepository()
        self.profile_service = profile_service if profile_service is not None else user_profile_service
        self._lock = threading.RLock()
        self._last_record: Dict[str, Tuple[str, float]] = {}
        self._last_persisted_signature: Dict[str, str] = {}
        self._last_failure_storage_unavailable: Dict[str, bool] = {}
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

    def preload_user(self, user_id: str) -> None:
        """Best-effort history load for callers outside the camera hot path."""
        try:
            self._load_recent(user_id)
        except Exception as exc:
            logger.warning("Unable to preload adaptive interaction history: %s", exc)

    def _storage_available(self) -> bool:
        available = getattr(self.repository, "storage_available", True)
        return available if isinstance(available, bool) else True

    def recent_events(self, user_id: str, limit: int = 20) -> List[InteractionEvent]:
        limit = max(1, min(int(limit), int(Config.ADAPTIVE_HISTORY_LIMIT)))
        with self._lock:
            events = list(self._load_recent(user_id))
        return list(reversed(events[-limit:]))

    def recent_intents(self, user_id: str, limit: int = 8) -> List[str]:
        events = self.recent_events(user_id, limit=limit)
        return [event.intent for event in events if event.intent and event.intent != "IDLE"]

    def record_transition(self, event: InteractionEvent) -> Optional[InteractionEvent]:
        """Persist one actual adaptive transition, returning it when written.

        The signature intentionally excludes per-frame geometry and motion
        direction. Those values can fluctuate while one intent is held and
        must not turn the camera loop into a MongoDB write loop.
        """
        if not event.user_id:
            return None

        now = time.monotonic()
        signature = "|".join(
            (
                event.intent,
                event.unknown_status,
                "unknown" if event.is_unknown else "known",
            )
        )
        with self._lock:
            previous = self._last_record.get(event.user_id)
            if event.intent in {"IDLE", "LEGACY_ROUTING"} and not event.is_unknown:
                # Remember the non-persisted state so returning to the same
                # intent later is a real transition and is recorded once.
                self._last_record[event.user_id] = (signature, now)
                return None
            if previous and previous[0] == signature:
                if self._last_persisted_signature.get(event.user_id) == signature:
                    return None
                recovered_after_storage_failure = (
                    self._last_failure_storage_unavailable.get(event.user_id, False)
                    and self._storage_available()
                )
                if now - previous[1] < 0.45 and not recovered_after_storage_failure:
                    # Keep the first-seen time so a stable transition that was
                    # initially rate-limited can be persisted later.
                    return None
            elif previous and now - previous[1] < 0.45:
                # Suppress classifier jitter without losing the latest state.
                self._last_record[event.user_id] = (signature, now)
                self._last_failure_storage_unavailable.pop(event.user_id, None)
                return None
            self._last_record[event.user_id] = (signature, now)

            try:
                # Ensure the profile parent exists, then prime the cache before
                # inserting so the just-written event is not loaded and appended
                # a second time. Only meaningful derived events are persisted.
                self.profile_service.get_profile(event.user_id)
                recent = self._load_recent(event.user_id)
                saved = self.repository.add(event)
                if saved is None:
                    # Keep a bounded retry marker instead of attempting a write
                    # on every camera frame while storage is unavailable.
                    self._last_record[event.user_id] = (signature, now)
                    self._last_failure_storage_unavailable[event.user_id] = not self._storage_available()
                    return None
                recent.append(saved)
                self._last_persisted_signature[event.user_id] = signature
                self._last_failure_storage_unavailable.pop(event.user_id, None)
                self.profile_service.register_interaction(
                    event.user_id,
                    unknown=event.is_unknown,
                    confirmed=event.intent in {"sign.commit", "selection.click"},
                )
                return saved
            except Exception as exc:
                # Adaptation is an enhancement; persistence failure must never
                # stop MediaPipe, cursor control, or sign inference. Keep a
                # bounded retry marker so recovery does not create a write loop.
                self._last_record[event.user_id] = (signature, now)
                self._last_failure_storage_unavailable[event.user_id] = not self._storage_available()
                logger.warning("Adaptive interaction event was not persisted: %s", exc)
                return None

    def set_feedback(self, user_id: str, event_id: str, feedback: str) -> bool:
        normalized = str(feedback or "").strip().lower()
        if normalized not in {"accepted", "rejected", "correct", "incorrect", "dismissed"}:
            return False
        event_id = str(event_id)
        try:
            updated = self.repository.set_feedback(user_id, event_id, normalized)
            if updated:
                with self._lock:
                    for event in self._load_recent(user_id):
                        if event.event_id == event_id:
                            event.feedback = normalized
                            break
            return updated
        except Exception as exc:
            logger.warning("Adaptive feedback was not persisted: %s", exc)
            return False

    def latest_event_id(self, user_id: str) -> Optional[str]:
        events = self.recent_events(user_id, limit=1)
        return events[0].event_id if events else None

    def clear_user(self, user_id: str) -> None:
        """Clear only this user's in-memory cache after a personalization reset."""
        with self._lock:
            self._recent_cache.pop(user_id, None)
            self._last_record.pop(user_id, None)
            self._last_persisted_signature.pop(user_id, None)
            self._last_failure_storage_unavailable.pop(user_id, None)


interaction_history_service = InteractionHistoryService()
