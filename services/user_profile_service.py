"""Application service for local personalized operator profiles."""

import math
import re
import threading
from time import monotonic as monotonic_time
from typing import Any, Dict, Optional

from config import Config
from models.user_profile import (
    SUPPORTED_INTERACTION_MODES,
    SUPPORTED_LANGUAGES,
    SUPPORTED_MODULES,
    SUPPORTED_SCROLL_LEVELS,
    UserProfile,
    utc_now,
)
from repositories.user_profile_repository import UserProfileRepository


class UserProfileService:
    """Validates profile changes and hides the persistence implementation."""

    _USER_ID_RE = re.compile(r"[^a-zA-Z0-9_.-]+")
    _MODULES = set(SUPPORTED_MODULES)

    def __init__(self, repository: Optional[UserProfileRepository] = None):
        self.repository = repository if repository is not None else UserProfileRepository()
        self._lock = threading.RLock()
        self._cache: Dict[str, UserProfile] = {}
        self._cache_persisted: Dict[str, bool] = {}
        self._last_persist_attempt: Dict[str, float] = {}
        self._active_profile_id = self.normalize_user_id(Config.DEFAULT_PROFILE_ID)

    @classmethod
    def normalize_user_id(cls, user_id: Optional[str]) -> str:
        value = str(user_id or Config.DEFAULT_PROFILE_ID).strip()
        value = cls._USER_ID_RE.sub("-", value)[:64].strip("-._")
        if value:
            return value
        fallback = cls._USER_ID_RE.sub("-", str(Config.DEFAULT_PROFILE_ID).strip())[:64].strip("-._")
        return fallback or "local-user"

    def get_profile(self, user_id: Optional[str] = None) -> UserProfile:
        profile_id = self.normalize_user_id(user_id or self._active_profile_id)
        with self._lock:
            cached = self._cache.get(profile_id)
            if cached is not None:
                if not self._cache_persisted.get(profile_id, False) and self.repository.storage_available:
                    now = monotonic_time()
                    if now - self._last_persist_attempt.get(profile_id, 0.0) >= 2.0:
                        self._last_persist_attempt[profile_id] = now
                        self._cache_persisted[profile_id] = bool(self.repository.save(cached))
                return cached

            profile = self.repository.get(profile_id)
            persisted = profile is not None
            if profile is None:
                profile = UserProfile(user_id=profile_id)
                self._last_persist_attempt[profile_id] = monotonic_time()
                persisted = bool(self.repository.save(profile))
            self._cache[profile_id] = profile
            self._cache_persisted[profile_id] = persisted
            return profile

    def get_active_profile(self) -> UserProfile:
        return self.get_profile(self._active_profile_id)

    def activate(self, user_id: Optional[str]) -> UserProfile:
        profile_id = self.normalize_user_id(user_id)
        with self._lock:
            self._active_profile_id = profile_id
        return self.get_profile(profile_id)

    @property
    def active_profile_id(self) -> str:
        with self._lock:
            return self._active_profile_id

    def update_profile(self, user_id: Optional[str], changes: Dict[str, Any]) -> UserProfile:
        profile = self.activate(user_id)
        if not isinstance(changes, dict):
            return profile

        with self._lock:
            if "display_name" in changes:
                name = str(changes["display_name"] or "").strip()
                profile.display_name = (name[:80] if name else "Local user")

            if "preferred_language" in changes:
                language = str(changes["preferred_language"] or "English").strip().lower()
                language_names = {item.lower(): item for item in SUPPORTED_LANGUAGES}
                if language in language_names:
                    profile.preferred_language = language_names[language]

            if "preferred_module" in changes:
                module = str(changes["preferred_module"] or "studio").strip().lower()
                if module in self._MODULES:
                    profile.preferred_module = module

            if "cursor_sensitivity" in changes:
                try:
                    value = float(changes["cursor_sensitivity"])
                    if math.isfinite(value):
                        profile.cursor_sensitivity = max(0.10, min(1.0, value))
                except (TypeError, ValueError):
                    pass

            if "scroll_sensitivity" in changes:
                level = str(changes["scroll_sensitivity"] or "medium").strip().lower()
                if level in SUPPORTED_SCROLL_LEVELS:
                    profile.scroll_sensitivity = level

            if "interaction_mode" in changes:
                mode = str(changes["interaction_mode"] or "adaptive").strip().lower()
                if mode in SUPPORTED_INTERACTION_MODES:
                    profile.interaction_mode = mode
                    profile.adaptive_enabled = mode == "adaptive"

            if "adaptive_enabled" in changes:
                profile.adaptive_enabled = self._coerce_bool(
                    changes["adaptive_enabled"], profile.adaptive_enabled
                )
                if not profile.adaptive_enabled:
                    profile.interaction_mode = "legacy"
                elif profile.interaction_mode == "legacy" and "interaction_mode" not in changes:
                    profile.interaction_mode = "adaptive"

            if "learning_enabled" in changes:
                profile.learning_enabled = self._coerce_bool(
                    changes["learning_enabled"], profile.learning_enabled
                )

            if "unknown_gesture_threshold" in changes:
                try:
                    threshold = float(changes["unknown_gesture_threshold"])
                    if math.isfinite(threshold):
                        profile.unknown_gesture_threshold = max(0.40, min(0.90, threshold))
                except (TypeError, ValueError):
                    pass

            # Match persisted-profile normalization when callers provide both
            # views of the safety switch in one request: legacy mode must not be
            # made adaptive accidentally by a contradictory flag.
            if profile.interaction_mode == "legacy" or not profile.adaptive_enabled:
                profile.interaction_mode = "legacy"
                profile.adaptive_enabled = False
            else:
                profile.interaction_mode = "adaptive"
                profile.adaptive_enabled = True

            profile.updated_at = utc_now()
            self._last_persist_attempt[profile.user_id] = monotonic_time()
            self._cache_persisted[profile.user_id] = bool(self.repository.save(profile))
            self._cache[profile.user_id] = profile
            return profile

    def reset_profile(self, user_id: Optional[str]) -> UserProfile:
        """Reset preferences while retaining the operator's interaction history."""
        profile_id = self.normalize_user_id(user_id)
        with self._lock:
            current = self.get_profile(profile_id)
            profile = UserProfile(
                user_id=profile_id,
                created_at=current.created_at,
            )
            self._last_persist_attempt[profile_id] = monotonic_time()
            self._cache_persisted[profile_id] = bool(self.repository.save(profile))
            self._cache[profile_id] = profile
            self._active_profile_id = profile_id
            return profile

    def register_interaction(self, user_id: str, unknown: bool, confirmed: bool) -> UserProfile:
        """Update counters without writing raw camera frames or event payloads."""
        profile = self.get_profile(user_id)
        with self._lock:
            profile.interaction_count += 1
            profile.unknown_gesture_count += int(bool(unknown))
            profile.confirmed_intent_count += int(bool(confirmed))
            profile.updated_at = utc_now()
            profile.last_seen_at = profile.updated_at
            if not self.repository.touch_and_increment(user_id, unknown=unknown, confirmed=confirmed):
                self._cache_persisted[profile.user_id] = False
            return profile

    @staticmethod
    def _coerce_bool(value: Any, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "1", "yes", "on"}:
                return True
            if lowered in {"false", "0", "no", "off"}:
                return False
        return default


user_profile_service = UserProfileService()
