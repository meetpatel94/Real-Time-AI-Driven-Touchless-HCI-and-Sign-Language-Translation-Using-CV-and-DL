"""Unknown and out-of-vocabulary gesture detection."""

import threading
import time
from typing import Dict, Optional, Tuple

from config import Config
from core.adaptive.observation import GestureObservation, UnknownGestureResult


class UnknownGestureDetector:
    """Uses confidence, hand geometry, and temporal persistence to flag novelty.

    This is intentionally a detector, not a fabricated classifier for gestures
    the model has never seen.  It exposes why a pose was rejected so a later
    phase can collect labelled examples or route it to a user-specific model.
    """

    def __init__(
        self,
        min_samples: int = Config.ADAPTIVE_UNKNOWN_MIN_SAMPLES,
        hold_seconds: float = Config.ADAPTIVE_UNKNOWN_HOLD_SECONDS,
    ):
        self.min_samples = min_samples
        self.hold_seconds = hold_seconds
        self._lock = threading.Lock()
        self._right_candidate: Optional[Tuple[int, float]] = None

    def reset(self) -> None:
        with self._lock:
            self._right_candidate = None

    def _right_unknown(self, observation: GestureObservation, threshold: float) -> UnknownGestureResult:
        now = time.monotonic()
        with self._lock:
            if self._right_candidate is None:
                self._right_candidate = (1, now)
            else:
                count, started = self._right_candidate
                self._right_candidate = (count + 1, started)
            count, started = self._right_candidate

        if observation.tracking_quality < 0.85:
            return UnknownGestureResult(
                status="LOW_TRACKING_QUALITY",
                is_unknown=False,
                score=max(0.0, 1.0 - observation.tracking_quality),
                reason="The hand landmarks are incomplete; waiting for a stable track.",
                hand=observation.handedness,
            )

        if count < self.min_samples or (now - started) < self.hold_seconds:
            return UnknownGestureResult(
                status="TRANSITIONING",
                is_unknown=False,
                score=0.0,
                reason="A hand pose is changing; waiting for temporal stability.",
                hand=observation.handedness,
            )

        motion_bonus = min(0.25, observation.motion.speed / 8.0)
        score = min(1.0, max(threshold, 0.62 + motion_bonus))
        reason = "The stable hand pose is outside the supported gesture vocabulary."
        if observation.motion.direction != "stationary":
            reason = "The hand motion and pose do not match a supported command."
        return UnknownGestureResult(
            status="UNKNOWN_GESTURE",
            is_unknown=True,
            score=score,
            reason=reason,
            hand=observation.handedness,
            requires_feedback=True,
        )

    def evaluate(
        self,
        right_observation: Optional[GestureObservation],
        left_observation: Optional[GestureObservation],
        sign_snapshot: Optional[Dict[str, object]],
        profile,
        context: Optional[Dict[str, object]] = None,
    ) -> UnknownGestureResult:
        context = context or {}
        sign_snapshot = sign_snapshot or {}
        threshold = float(getattr(profile, "unknown_gesture_threshold", 0.60))
        module = str(context.get("active_module", "overview"))

        left_present = left_observation is not None
        raw_prediction = str(sign_snapshot.get("raw_prediction", "NONE") or "NONE").upper()
        stable_prediction = str(sign_snapshot.get("prediction", "NONE") or "NONE").upper()
        raw_confidence = float(sign_snapshot.get("raw_confidence", 0.0) or 0.0)
        sign_threshold = float(Config.RECOGNITION_CONFIDENCE_THRESHOLD) * 100.0
        sign_is_unknown = left_present and (
            raw_prediction == "UNKNOWN"
            or stable_prediction == "UNKNOWN"
            or (raw_prediction not in {"NONE", "UNKNOWN"} and raw_confidence < sign_threshold)
        )

        # In sign-facing modules, a present left hand with an out-of-vocabulary
        # model result is more useful than reporting the right-hand command pose.
        if sign_is_unknown and module in {"recognition", "studio"}:
            with self._lock:
                self._right_candidate = None
            score = max(0.35, min(1.0, 1.0 - (raw_confidence / 100.0)))
            return UnknownGestureResult(
                status="UNRECOGNIZED_SIGN",
                is_unknown=True,
                score=score,
                reason="The sign model confidence is below its supported alphabet boundary.",
                hand="left",
                requires_feedback=True,
            )

        if right_observation is not None:
            if right_observation.gesture == "NONE":
                return self._right_unknown(right_observation, threshold)
            with self._lock:
                self._right_candidate = None
            return UnknownGestureResult(
                status="KNOWN_GESTURE",
                is_unknown=False,
                score=0.0,
                reason="The pose matches an existing GestureForge gesture family.",
                hand=right_observation.handedness,
            )

        with self._lock:
            self._right_candidate = None

        if left_present and not sign_is_unknown:
            return UnknownGestureResult(
                status="KNOWN_SIGN" if stable_prediction not in {"NONE", "UNKNOWN"} else "TRANSITIONING",
                is_unknown=False,
                score=0.0,
                reason="The left-hand sign is supported or still being inferred.",
                hand="left",
            )

        return UnknownGestureResult()


unknown_gesture_service = UnknownGestureDetector()
