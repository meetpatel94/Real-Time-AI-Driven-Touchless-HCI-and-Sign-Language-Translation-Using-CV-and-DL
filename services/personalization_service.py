"""Application service for explicit, confidence-gated personalized learning.

This service is the only layer allowed to turn a stable runtime observation into
learning data. Ordinary predictions merely update an in-memory latest-observation
slot so an operator can explicitly request a calibration sample or correction.
"""

from datetime import datetime, timezone
import math
import threading
import time
from uuid import uuid4
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from config import Config
from core.adaptive.observation import GestureObservation
from models.personalization import (
    CalibrationSample,
    CalibrationSession,
    CorrectionRecord,
    CustomGestureMapping,
    FeatureSignature,
    LearnedGesture,
    SUPPORTED_CUSTOM_ACTIONS,
    normalize_calibration_target,
)
from repositories.interaction_event_repository import InteractionEventRepository
from repositories.personalization_repository import (
    CalibrationSessionRepository,
    CorrectionRepository,
    CustomMappingRepository,
    LearnedGestureRepository,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _confidence(value: Any) -> float:
    try:
        result = float(value)
        if result > 1.0:
            result /= 100.0
        return max(0.0, min(1.0, result)) if math.isfinite(result) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _mean_vectors(vectors: Iterable[Sequence[float]]) -> List[float]:
    values = [list(vector) for vector in vectors]
    if not values:
        return []
    size = max(len(vector) for vector in values)
    return [
        _mean([float(vector[index]) if index < len(vector) else 0.0 for vector in values])
        for index in range(size)
    ]


def _weighted_vectors(first: Sequence[float], first_weight: int, second: Sequence[float], second_weight: int) -> List[float]:
    size = max(len(first), len(second))
    total = max(1, first_weight + second_weight)
    return [
        (
            (float(first[index]) if index < len(first) else 0.0) * first_weight
            + (float(second[index]) if index < len(second) else 0.0) * second_weight
        ) / total
        for index in range(size)
    ]


def signature_distance(first: FeatureSignature, second: FeatureSignature) -> float:
    """Bounded weighted distance across geometry, proportions, and motion."""
    def component(left: Sequence[float], right: Sequence[float], divisor: float = 1.0) -> float:
        size = max(len(left), len(right))
        if not size:
            return 0.0
        differences = [
            abs((float(left[index]) if index < len(left) else 0.0)
                - (float(right[index]) if index < len(right) else 0.0))
            for index in range(size)
        ]
        return min(1.0, _mean(differences) / max(0.0001, divisor))

    geometry = component(first.landmark_geometry, second.landmark_geometry, 1.0)
    proportions = component(first.proportion_features, second.proportion_features, 2.0)
    trajectory = component(first.trajectory_features, second.trajectory_features, 1.0)
    temporal = component(first.temporal_pattern, second.temporal_pattern, 1.0)
    scalar = min(1.0, _mean((
        abs(first.motion_speed - second.motion_speed) / 2.0,
        abs(first.displacement - second.displacement) / 0.5,
        abs(first.gesture_duration - second.gesture_duration) / 0.45,
    )))
    return min(1.0, 0.55 * geometry + 0.18 * proportions + 0.15 * trajectory + 0.07 * temporal + 0.05 * scalar)


class PersonalizationService:
    """Coordinates Mongo repositories and safety policy for one active profile."""

    def __init__(
        self,
        calibration_repository: Optional[CalibrationSessionRepository] = None,
        learned_repository: Optional[LearnedGestureRepository] = None,
        correction_repository: Optional[CorrectionRepository] = None,
        mapping_repository: Optional[CustomMappingRepository] = None,
        history_repository: Optional[InteractionEventRepository] = None,
    ):
        self.calibration_repository = calibration_repository if calibration_repository is not None else CalibrationSessionRepository()
        self.learned_repository = learned_repository if learned_repository is not None else LearnedGestureRepository()
        self.correction_repository = correction_repository if correction_repository is not None else CorrectionRepository()
        self.mapping_repository = mapping_repository if mapping_repository is not None else CustomMappingRepository()
        self.history_repository = history_repository if history_repository is not None else InteractionEventRepository(self.mapping_repository.database)
        self._lock = threading.RLock()
        self._sessions: Dict[Tuple[str, str], CalibrationSession] = {}
        # Keep the newest derived sample per hand so a sign calibration can use
        # the left hand even while the right hand supplies a confirmation pose.
        self._latest: Dict[str, Dict[str, Tuple[float, GestureObservation, str, float]]] = {}
        self._pending_samples: Dict[str, str] = {}
        self._last_actions: Dict[str, Tuple[str, str, float]] = {}
        # Matching runs on every camera frame. Keep bounded in-process snapshots
        # so MongoDB is not queried from the high-frequency inference loop.
        self._learned_cache: Dict[str, List[LearnedGesture]] = {}
        self._mapping_cache: Dict[str, List[CustomGestureMapping]] = {}
        self._cache_versions: Dict[str, int] = {}

    @property
    def database(self):
        return self.learned_repository.database

    def _database_available(self) -> bool:
        available = getattr(self.database, "available", True)
        return available if isinstance(available, bool) else True

    def storage_status(self) -> Dict[str, Any]:
        try:
            health = getattr(self.database, "health", None)
            if callable(health):
                return health()
            return {
                "available": self._database_available(),
                "database": str(getattr(self.database, "name", "mongodb")),
                "error": "",
            }
        except Exception as exc:  # Defensive: status must never break the UI.
            redactor = getattr(self.database, "_redact_error", None)
            safe_error = redactor(exc) if callable(redactor) else "MongoDB status check failed."
            return {"available": False, "database": "unknown", "error": safe_error}

    @property
    def storage_available(self) -> bool:
        return bool(self.storage_status().get("available"))

    def _invalidate_learning_cache(self, user_id: str) -> None:
        with self._lock:
            self._learned_cache.pop(user_id, None)
            self._mapping_cache.pop(user_id, None)
            self._cache_versions[user_id] = self._cache_versions.get(user_id, 0) + 1

    def _learned_for_user(self, user_id: str, refresh: bool = False) -> List[LearnedGesture]:
        with self._lock:
            cached = self._learned_cache.get(user_id)
            version = self._cache_versions.get(user_id, 0)
            if cached is not None and not refresh:
                return list(cached)
        values = list(self.learned_repository.list_for_user(user_id))
        with self._lock:
            if version == self._cache_versions.get(user_id, 0):
                self._learned_cache[user_id] = values
        return list(values)

    def _mappings_for_user(self, user_id: str, refresh: bool = False) -> List[CustomGestureMapping]:
        with self._lock:
            cached = self._mapping_cache.get(user_id)
            version = self._cache_versions.get(user_id, 0)
            if cached is not None and not refresh:
                return list(cached)
        values = list(self.mapping_repository.list_for_user(user_id))
        with self._lock:
            if version == self._cache_versions.get(user_id, 0):
                self._mapping_cache[user_id] = values
        return list(values)

    def preload_learning_data(self, user_id: str) -> None:
        """Load user-owned matching data before the camera loop starts.

        This is deliberately best-effort. A missing or unavailable MongoDB must
        not delay the legacy recognition path or make startup fail.
        """
        if not self._database_available():
            return
        with self._lock:
            if user_id in self._learned_cache and user_id in self._mapping_cache:
                return
        try:
            self._learned_for_user(user_id, refresh=True)
            self._mappings_for_user(user_id, refresh=True)
        except Exception:
            # Repositories normally fail open; this guard also protects custom
            # test doubles and keeps preloading outside the runtime hot path.
            return

    def active_calibration_status(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Return only the in-process active session for low-cost frame status."""
        with self._lock:
            session = self._sessions.get((user_id, "active"))
            return session.to_dict(
                storage_available=self._database_available()
            ) if session else None

    def clear_latest_observations(self, user_id: Optional[str] = None) -> None:
        """Invalidate camera observations when a runtime stream is interrupted.

        Calibration and correction APIs may consume the newest observation only
        within a short TTL. A camera-off transition must not allow a previously
        seen pose to be accepted as a new live sample after that transition.
        """
        with self._lock:
            if user_id is None:
                self._latest.clear()
            else:
                self._latest.pop(user_id, None)

    def register_observation(
        self,
        user_id: str,
        observation: Optional[GestureObservation],
        base_label: str = "NONE",
        base_confidence: float = 0.0,
    ) -> None:
        """Publish one newest derived observation to the in-process service."""
        self.register_observations(
            user_id,
            [(observation, base_label, base_confidence)],
        )

    def register_observations(
        self,
        user_id: str,
        observations: Iterable[Tuple[Optional[GestureObservation], str, float]],
    ) -> None:
        """Publish both hands atomically and consume one pending sample.

        The tuples are bounded and never written to MongoDB. Atomic publication
        prevents a pending sign sample from being consumed by the right-hand
        control pose before the current left-hand observation is available.
        """
        with self._lock:
            latest_by_hand = self._latest.setdefault(user_id, {})
            for observation, base_label, base_confidence in observations:
                if observation is None:
                    continue
                hand = str(getattr(observation, "handedness", "unknown") or "unknown").lower()
                latest_by_hand[hand] = (
                    time.monotonic(), observation, str(base_label or "NONE"), _confidence(base_confidence)
                )
            session_id = self._pending_samples.get(user_id)
            if session_id:
                _, reason = self._capture_latest_locked(user_id, session_id)
                # Keep a pending request alive until the expected hand is
                # actually observed; a wrong-hand frame must not silently
                # consume the user's explicit capture request.
                if reason != "No live observation is available yet.":
                    self._pending_samples.pop(user_id, None)

    def start_calibration(
        self,
        user_id: str,
        target: str,
        required_samples: Optional[int] = None,
        display_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        normalized = normalize_calibration_target(target)
        if normalized is None:
            return {"success": False, "error": "Unsupported calibration target."}
        target_type, target_value = normalized
        try:
            required = int(required_samples or 5)
        except (TypeError, ValueError):
            required = 5
        required = max(3, min(required, int(Config.PERSONALIZATION_MAX_CALIBRATION_SAMPLES)))
        with self._lock:
            # A pending capture belongs to the previous active session. Never
            # let the next camera frame append it to a cancelled calibration.
            self._pending_samples.pop(user_id, None)
            previous = self._sessions.get((user_id, "active"))
            if previous is not None:
                previous.status = "CANCELLED"
                previous.updated_at = _now()
                self.calibration_repository.save(previous)
            session = CalibrationSession(
                user_id=user_id,
                target_key="%s:%s" % (target_type, target_value),
                target_type=target_type,
                target_value=target_value,
                required_samples=required,
            )
            self._sessions[(user_id, session.session_id)] = session
            self._sessions[(user_id, "active")] = session
            saved = self.calibration_repository.create(session)
            result = session.to_dict(storage_available=self.storage_available)
            result["display_name"] = (str(display_name).strip()[:80] if display_name else target_value)
            result["persisted"] = bool(saved)
            if not saved:
                result["warning"] = "MongoDB is unavailable; calibration is temporary until storage recovers."
            return {"success": True, "calibration": result}

    def get_calibration(self, user_id: str, session_id: Optional[str] = None) -> Optional[CalibrationSession]:
        with self._lock:
            if session_id:
                session = self._sessions.get((user_id, session_id))
                if session is not None:
                    return session
                session = self.calibration_repository.get(user_id, session_id)
                if session is not None:
                    self._sessions[(user_id, session_id)] = session
                    if session.status == "ACTIVE":
                        self._sessions[(user_id, "active")] = session
                return session
            session = self._sessions.get((user_id, "active"))
            if session is not None:
                return session
            session = self.calibration_repository.latest_active(user_id)
            if session is not None:
                self._sessions[(user_id, session.session_id)] = session
                self._sessions[(user_id, "active")] = session
            return session

    def _latest_for_session_locked(
        self,
        user_id: str,
        session: CalibrationSession,
    ) -> Optional[Tuple[float, GestureObservation, str, float]]:
        latest_by_hand = self._latest.get(user_id, {})
        preferred_hand = "left" if session.target_type == "sign" else "right"
        preferred = latest_by_hand.get(preferred_hand)
        if preferred is not None:
            return preferred
        # Do not silently learn a sign from the right-hand control stream (or a
        # control mapping from the left-hand sign stream): the matcher enforces
        # the same hand boundary at runtime.
        return None

    def request_sample(self, user_id: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        session = self.get_calibration(user_id, session_id)
        if session is None or session.status != "ACTIVE":
            return {"success": False, "error": "No active calibration session."}
        with self._lock:
            latest = self._latest_for_session_locked(user_id, session)
            if latest and time.monotonic() - latest[0] <= float(Config.PERSONALIZATION_LATEST_OBSERVATION_TTL_SECONDS):
                accepted, reason = self._capture_latest_locked(user_id, session.session_id)
                return self._sample_response(
                    session, accepted, reason, pending=False,
                    storage_available=self.storage_available,
                )
            self._pending_samples[user_id] = session.session_id
            return self._sample_response(
                session,
                False,
                "Waiting for the next live, stable camera observation.",
                pending=True,
                storage_available=self.storage_available,
            )

    def capture_sample(
        self,
        user_id: str,
        session_id: str,
        observation: Optional[GestureObservation],
        base_label: str = "NONE",
        base_confidence: float = 0.0,
    ) -> Dict[str, Any]:
        session = self.get_calibration(user_id, session_id)
        if session is None or session.status != "ACTIVE":
            return {"success": False, "error": "No active calibration session."}
        with self._lock:
            accepted, reason = self._append_sample(
                session, observation, base_label, base_confidence
            )
            return self._sample_response(
                session, accepted, reason, pending=False,
                storage_available=self.storage_available,
            )

    def _capture_latest_locked(self, user_id: str, session_id: str) -> Tuple[bool, str]:
        session = self._sessions.get((user_id, session_id))
        if session is None or session.status != "ACTIVE":
            return False, "No active calibration session."
        latest = self._latest_for_session_locked(user_id, session)
        if latest is None:
            return False, "No live observation is available yet."
        if time.monotonic() - latest[0] > float(Config.PERSONALIZATION_LATEST_OBSERVATION_TTL_SECONDS):
            return False, "The latest camera observation is stale; hold the pose and try again."
        _, observation, base_label, base_confidence = latest
        return self._append_sample(session, observation, base_label, base_confidence)

    def _append_sample(
        self,
        session: CalibrationSession,
        observation: Optional[GestureObservation],
        base_label: str,
        base_confidence: float,
    ) -> Tuple[bool, str]:
        if session.accepted_count >= session.required_samples:
            return False, "The required number of accepted samples has already been reached."
        expected_hand = "left" if session.target_type == "sign" else "right"
        reason = self._sample_rejection_reason(observation, expected_hand=expected_hand)
        if reason:
            session.rejected_samples += 1
            session.rejection_reasons[reason] = session.rejection_reasons.get(reason, 0) + 1
            session.updated_at = _now()
            self.calibration_repository.save(session)
            return False, reason

        signature = FeatureSignature.from_document(observation.derived_features)
        session.accepted_samples.append(CalibrationSample(
            signature=signature,
            captured_at=_now(),
            base_label=str(base_label or "NONE"),
            base_confidence=_confidence(base_confidence),
            validated=True,
            validation_reason="stable-valid-explicit-capture",
        ))
        session.updated_at = _now()
        self.calibration_repository.save(session)
        return True, "Accepted stable derived sample."

    @staticmethod
    def _sample_rejection_reason(
        observation: Optional[GestureObservation],
        expected_hand: Optional[str] = None,
    ) -> str:
        if observation is None:
            return "no_live_observation"
        if expected_hand and str(getattr(observation, "handedness", "") or "").lower() != expected_hand:
            return "wrong_hand"
        try:
            tracking_quality = float(getattr(observation, "tracking_quality", 0.0))
        except (TypeError, ValueError):
            tracking_quality = 0.0
        if not math.isfinite(tracking_quality) or tracking_quality < 0.85:
            return "low_tracking_quality"
        motion = getattr(observation, "motion", None)
        try:
            sample_count = int(getattr(motion, "sample_count", 0)) if motion is not None else 0
        except (TypeError, ValueError):
            sample_count = 0
        if motion is None or sample_count < 2:
            return "insufficient_temporal_samples"
        try:
            stability = float(getattr(motion, "stability", 0.0))
        except (TypeError, ValueError):
            stability = 0.0
        if not math.isfinite(stability) or stability < 0.25:
            return "unstable_motion"
        signature = FeatureSignature.from_document(getattr(observation, "derived_features", {}))
        if len(signature.landmark_geometry) < 63:
            return "missing_derived_geometry"
        return ""

    @staticmethod
    def _sample_response(
        session: CalibrationSession,
        accepted: bool,
        reason: str,
        pending: bool,
        storage_available: bool = True,
    ) -> Dict[str, Any]:
        return {
            "success": True,
            "accepted": bool(accepted),
            "pending": bool(pending),
            "reason": reason,
            "calibration": session.to_dict(storage_available=storage_available),
        }

    def complete_calibration(
        self,
        user_id: str,
        session_id: Optional[str] = None,
        display_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        session = self.get_calibration(user_id, session_id)
        if session is None:
            return {"success": False, "error": "No calibration session was found."}
        if session.status != "ACTIVE":
            return {"success": False, "error": "Calibration session is not active."}
        if session.accepted_count < session.required_samples:
            return {
                "success": False,
                "error": "Calibration needs more stable accepted samples.",
                "calibration": session.to_dict(storage_available=self.storage_available),
            }

        with self._lock:
            self._pending_samples.pop(user_id, None)
            learned = self._learn_from_signatures(
                user_id=user_id,
                target_type=session.target_type,
                target_value=session.target_value,
                signatures=[sample.signature for sample in session.accepted_samples if sample.validated],
                display_name=display_name or session.target_value,
                source="calibration",
            )
            if learned is None:
                return {
                    "success": False,
                    "error": "MongoDB is unavailable; no learned gesture was persisted.",
                    "calibration": session.to_dict(storage_available=False),
                    "storage": self.storage_status(),
                }
            session.status = "COMPLETED"
            session.updated_at = _now()
            self.calibration_repository.save(session)
            self._sessions[(user_id, session.session_id)] = session
            if self._sessions.get((user_id, "active")) is session:
                self._sessions.pop((user_id, "active"), None)
            return {
                "success": True,
                "calibration": session.to_dict(storage_available=self.storage_available),
                "learned_gesture": learned.to_dict(),
                "storage": self.storage_status(),
            }

    def _learn_from_signatures(
        self,
        user_id: str,
        target_type: str,
        target_value: str,
        signatures: Sequence[FeatureSignature],
        display_name: str,
        source: str,
    ) -> Optional[LearnedGesture]:
        signatures = [
            signature
            for signature in signatures
            if isinstance(signature, FeatureSignature)
            and len(signature.landmark_geometry) >= 63
        ]
        if not signatures:
            return None
        gesture_key = "%s:%s" % (target_type, target_value)
        existing = next(
            (item for item in self._learned_for_user(user_id) if item.gesture_key == gesture_key),
            None,
        )
        new_centroid = FeatureSignature(
            landmark_geometry=_mean_vectors([item.landmark_geometry for item in signatures]),
            proportion_features=_mean_vectors([item.proportion_features for item in signatures]),
            trajectory_features=_mean_vectors([item.trajectory_features for item in signatures]),
            motion_speed=_mean([item.motion_speed for item in signatures]),
            displacement=_mean([item.displacement for item in signatures]),
            gesture_duration=_mean([item.gesture_duration for item in signatures]),
            temporal_pattern=_mean_vectors([item.temporal_pattern for item in signatures]),
        )
        if existing is not None:
            old_count = max(0, existing.validated_examples)
            centroid = FeatureSignature(
                landmark_geometry=_weighted_vectors(existing.centroid.landmark_geometry, old_count, new_centroid.landmark_geometry, len(signatures)),
                proportion_features=_weighted_vectors(existing.centroid.proportion_features, old_count, new_centroid.proportion_features, len(signatures)),
                trajectory_features=_weighted_vectors(existing.centroid.trajectory_features, old_count, new_centroid.trajectory_features, len(signatures)),
                motion_speed=(existing.centroid.motion_speed * old_count + new_centroid.motion_speed * len(signatures)) / max(1, old_count + len(signatures)),
                displacement=(existing.centroid.displacement * old_count + new_centroid.displacement * len(signatures)) / max(1, old_count + len(signatures)),
                gesture_duration=(existing.centroid.gesture_duration * old_count + new_centroid.gesture_duration * len(signatures)) / max(1, old_count + len(signatures)),
                temporal_pattern=_weighted_vectors(existing.centroid.temporal_pattern, old_count, new_centroid.temporal_pattern, len(signatures)),
            )
            total_count = old_count + len(signatures)
            learned_id = existing.learned_id
            created_at = existing.created_at
            correction_count = existing.correction_count + int(source == "validated_correction")
            prior_reliability = existing.reliability
        else:
            centroid = new_centroid
            total_count = len(signatures)
            learned_id = None
            created_at = _now()
            correction_count = int(source == "validated_correction")
            prior_reliability = 0.0

        distances = [signature_distance(item, centroid) for item in signatures]
        consistency = 1.0 / (1.0 + (_mean(distances) * 5.0))
        evidence = min(1.0, total_count / 5.0)
        reliability = min(0.99, max(prior_reliability * (0.5 if existing else 0.0), 0.45 + 0.55 * consistency * evidence))
        threshold = max(0.06, min(0.60, (_mean(distances) * 2.5) + 0.06))
        if existing is not None:
            threshold = max(threshold, existing.match_distance_threshold)
        learned = LearnedGesture(
            learned_id=learned_id or str(uuid4()),
            user_id=user_id,
            gesture_key=gesture_key,
            display_name=str(display_name or target_value)[:80],
            target_type=target_type,
            target_value=target_value,
            centroid=centroid,
            validated_examples=total_count,
            reliability=reliability,
            match_distance_threshold=threshold,
            source=source,
            correction_count=correction_count,
            created_at=created_at,
            updated_at=_now(),
            last_validated_at=_now(),
        )
        if not self.learned_repository.save(learned):
            return None
        self._invalidate_learning_cache(user_id)
        return learned

    def record_correction(
        self,
        user_id: str,
        correct_label: str,
        observation: Optional[GestureObservation] = None,
        base_label: str = "NONE",
        base_confidence: float = 0.0,
        correct_intent: str = "",
        validated: bool = False,
    ) -> Dict[str, Any]:
        if validated is not True:
            return {"success": False, "error": "A correction must be explicitly validated by the user."}
        target = normalize_calibration_target(correct_label)
        if target is None:
            return {"success": False, "error": "Unsupported correction label."}
        with self._lock:
            if observation is None:
                latest_by_hand = self._latest.get(user_id, {})
                preferred_hand = "left" if target[0] == "sign" else "right"
                preferred = latest_by_hand.get(preferred_hand)
                latest = preferred
                if latest and time.monotonic() - latest[0] <= float(Config.PERSONALIZATION_LATEST_OBSERVATION_TTL_SECONDS):
                    _, observation, latest_label, latest_confidence = latest
                    if base_label == "NONE":
                        base_label = latest_label
                    if not base_confidence:
                        base_confidence = latest_confidence
            expected_hand = "left" if target[0] == "sign" else "right"
            reason = self._sample_rejection_reason(observation, expected_hand=expected_hand)
            if reason:
                return {"success": False, "error": "Correction needs a current stable observation: %s." % reason}
            signature = FeatureSignature.from_document(observation.derived_features)
            correction = CorrectionRecord(
                user_id=user_id,
                correct_label=target[1],
                correct_intent=str(correct_intent or "")[:80],
                base_label=str(base_label or "NONE"),
                base_confidence=_confidence(base_confidence),
                signature=signature,
            )
            if not self.correction_repository.add(correction):
                return {"success": False, "error": "MongoDB is unavailable; correction was not persisted."}
            learned = self._learn_from_signatures(
                user_id=user_id,
                target_type=target[0],
                target_value=target[1],
                signatures=[signature],
                display_name=target[1],
                source="validated_correction",
            )
            return {
                "success": learned is not None,
                "correction_id": correction.correction_id,
                "learned_gesture": learned.to_dict() if learned else None,
                "storage": self.storage_status(),
                "error": None if learned else "Correction stored but derived gesture update is unavailable.",
            }

    def create_mapping(
        self,
        user_id: str,
        learned_gesture_id: str,
        action: str,
        name: Optional[str] = None,
    ) -> Dict[str, Any]:
        normalized_action = str(action or "").strip().lower()
        if normalized_action not in SUPPORTED_CUSTOM_ACTIONS:
            return {"success": False, "error": "Unsupported custom action."}
        if not self._database_available():
            return {
                "success": False,
                "error": "MongoDB is unavailable; mapping was not persisted.",
                "storage": self.storage_status(),
            }
        learned = self.learned_repository.get(user_id, str(learned_gesture_id))
        if learned is None:
            return {"success": False, "error": "Learned gesture was not found for this profile."}
        if learned.target_type == "sign":
            return {"success": False, "error": "Control mappings require a right-hand gesture calibration."}
        mapping = CustomGestureMapping(
            user_id=user_id,
            learned_gesture_id=learned.learned_id,
            name=str(name or learned.display_name)[:80],
            action=normalized_action,
        )
        # Reuse an existing mapping id for the learned gesture when possible.
        current = self.mapping_repository.get_for_learned(user_id, learned.learned_id)
        if current is not None:
            mapping.mapping_id = current.mapping_id
            mapping.created_at = current.created_at
        if not self.mapping_repository.save(mapping):
            return {"success": False, "error": "MongoDB is unavailable; mapping was not persisted."}
        self._invalidate_learning_cache(user_id)
        return {"success": True, "mapping": mapping.to_dict(), "storage": self.storage_status()}

    def delete_mapping(self, user_id: str, mapping_id: str) -> bool:
        deleted = self.mapping_repository.delete(user_id, str(mapping_id))
        if deleted:
            self._invalidate_learning_cache(user_id)
        return deleted

    def list_corrections(self, user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        return [item.to_dict() for item in self.correction_repository.recent(user_id, limit=limit)]

    def get_data(self, user_id: str) -> Dict[str, Any]:
        session = self.get_calibration(user_id)
        # This is an explicit API read, so refresh the runtime snapshot here;
        # the camera loop itself only consumes the in-process snapshot.
        learned = self._learned_for_user(user_id, refresh=True)
        mappings = self._mappings_for_user(user_id, refresh=True)
        return {
            "storage": self.storage_status(),
            "profile_id": user_id,
            "active_calibration": session.to_dict(self.storage_available) if session else None,
            "learned_gestures": [item.to_dict() for item in learned],
            "mappings": [item.to_dict() for item in mappings],
            "corrections": self.list_corrections(user_id),
            "privacy": {
                "derived_features_only": True,
                "webcam_video_stored": False,
                "continuous_recordings_stored": False,
                "base_model_or_dataset_stored": False,
            },
        }

    def match(
        self,
        user_id: str,
        observation: Optional[GestureObservation],
        base_label: str = "NONE",
        base_confidence: float = 0.0,
        profile=None,
    ):
        from models.personalization import PersonalizationDecision

        normalized_base = str(base_label or "NONE").upper()
        normalized_confidence = _confidence(base_confidence)
        decision = PersonalizationDecision(
            base_label=normalized_base,
            base_confidence=normalized_confidence,
        )
        if profile is not None and (
            not bool(getattr(profile, "adaptive_enabled", True))
            or not bool(getattr(profile, "learning_enabled", True))
            or str(getattr(profile, "interaction_mode", "adaptive")).lower() != "adaptive"
        ):
            decision.reason = "Adaptive or personalized learning mode is disabled."
            return decision
        if normalized_base not in {"NONE", "UNKNOWN", "--"} and normalized_confidence >= float(Config.PERSONALIZATION_BASE_RELIABLE_THRESHOLD):
            decision.reason = "Reliable base prediction retained; personalization cannot override it."
            decision.confidence = normalized_confidence
            return decision
        if observation is None:
            decision.reason = "No current observation is available for personalized matching."
            return decision
        try:
            tracking_quality = float(getattr(observation, "tracking_quality", 0.0))
        except (TypeError, ValueError):
            tracking_quality = 0.0
        if not math.isfinite(tracking_quality) or tracking_quality < 0.85:
            decision.reason = "Current tracking quality is too low; the base pipeline remains authoritative."
            return decision
        motion = getattr(observation, "motion", None)
        try:
            sample_count = int(getattr(motion, "sample_count", 0)) if motion is not None else 0
        except (TypeError, ValueError, OverflowError):
            sample_count = 0
        try:
            stability = float(getattr(motion, "stability", 0.0)) if motion is not None else 0.0
        except (TypeError, ValueError):
            stability = 0.0
        if motion is None or sample_count < 2 or not math.isfinite(stability) or stability < 0.25:
            decision.reason = "Current movement is unstable or too short for a personalized match."
            return decision
        signature = FeatureSignature.from_document(observation.derived_features)
        if len(signature.landmark_geometry) < 63:
            decision.reason = "No compact derived geometry is available for matching."
            return decision
        with self._lock:
            match_generation = self._cache_versions.get(user_id, 0)
        if not self._database_available():
            decision.reason = "Personalization storage is unavailable; the base pipeline remains authoritative."
            return decision
        observation_hand = str(getattr(observation, "handedness", "") or "").lower()
        learned_items = [
            item for item in self._learned_for_user(user_id)
            if item.validated_examples >= int(Config.PERSONALIZATION_MIN_VALIDATED_SAMPLES)
            and item.reliability >= float(Config.PERSONALIZATION_MIN_RELIABILITY)
            # Sign calibration belongs to the left-hand sign stream; control
            # gestures and action mappings belong to the right-hand stream.
            # Keeping this boundary in the matcher prevents a left-hand sign
            # from triggering a user action mapping (or vice versa).
            and (
                (item.target_type == "sign" and observation_hand == "left")
                or (item.target_type != "sign" and observation_hand == "right")
            )
        ]
        if not learned_items:
            decision.reason = "No learned gesture has sufficient validated evidence for this hand."
            return decision
        mappings = {
            item.learned_gesture_id: item
            for item in self._mappings_for_user(user_id)
            if item.enabled
            and str(item.action or "").strip().lower() in SUPPORTED_CUSTOM_ACTIONS
        }
        with self._lock:
            if match_generation != self._cache_versions.get(user_id, 0):
                decision.reason = "Personalization data changed during matching; the base pipeline remains authoritative."
                return decision
        candidates = []
        for learned in learned_items:
            distance = signature_distance(signature, learned.centroid)
            if distance > learned.match_distance_threshold:
                continue
            similarity = max(0.0, 1.0 - (distance / max(learned.match_distance_threshold, 0.0001)))
            confidence = similarity * (0.65 + (0.35 * learned.reliability))
            if confidence >= float(Config.PERSONALIZATION_MATCH_MIN_CONFIDENCE):
                mapping = None if learned.target_type == "sign" else mappings.get(learned.learned_id)
                candidates.append((confidence, learned, mapping))
        if not candidates:
            decision.reason = "The current derived movement is not close enough to validated personal gestures."
            return decision
        confidence, learned, mapping = max(candidates, key=lambda item: item[0])
        decision.personalized_label = learned.target_value
        decision.confidence = confidence
        decision.learned_gesture_id = learned.learned_id
        decision.used = True
        if mapping is not None:
            decision.mapping_action = mapping.action
            decision.source = "USER_LEARNED_MAPPING"
            decision.reason = "A sufficiently validated personal gesture matched a user-owned action mapping."
        else:
            decision.source = "PERSONALIZED_MODEL"
            decision.reason = "A sufficiently validated personal gesture matched without overriding a reliable base prediction."
        return decision

    def should_execute_mapping(self, user_id: str, decision) -> bool:
        """Debounce a matched mapped action before it reaches an OS controller."""
        action = str(getattr(decision, "mapping_action", "") or "").lower()
        learned_id = str(getattr(decision, "learned_gesture_id", "") or "")
        if not getattr(decision, "used", False) or not action or not learned_id:
            return False
        now = time.monotonic()
        with self._lock:
            learned_cache = self._learned_cache.get(user_id)
            if learned_cache is None or not any(item.learned_id == learned_id for item in learned_cache):
                # A reset or cache invalidation may race the frame that produced
                # this decision. Never execute a mapping that is no longer in the
                # current user-scoped learning snapshot.
                return False
            previous = self._last_actions.get(user_id)
            if previous and previous[0] == action and previous[1] == learned_id and (
                now - previous[2] < float(Config.PERSONALIZATION_ACTION_COOLDOWN_SECONDS)
            ):
                return False
            self._last_actions[user_id] = (action, learned_id, now)
            return True

    def reset(self, user_id: str) -> Dict[str, Any]:
        with self._lock:
            self._pending_samples.pop(user_id, None)
            self._latest.pop(user_id, None)
            self._last_actions.pop(user_id, None)
            self._invalidate_learning_cache(user_id)
            for key in list(self._sessions):
                if key[0] == user_id:
                    self._sessions.pop(key, None)
        outcomes = [
            self.calibration_repository.delete_for_user(user_id),
            self.learned_repository.delete_for_user(user_id),
            self.correction_repository.delete_for_user(user_id),
            self.mapping_repository.delete_for_user(user_id),
            self.history_repository.delete_for_user(user_id),
        ]
        # A concurrent pre-reset read must not repopulate a stale snapshot after
        # the deletion sequence completes.
        self._invalidate_learning_cache(user_id)
        return {
            "success": all(outcomes),
            "storage": self.storage_status(),
            "message": "Personalized gestures, corrections, mappings, calibration sessions, and adaptive history were reset." if all(outcomes) else "Personalization memory was cleared; MongoDB reset is incomplete while storage is unavailable.",
        }


personalization_service = PersonalizationService()
