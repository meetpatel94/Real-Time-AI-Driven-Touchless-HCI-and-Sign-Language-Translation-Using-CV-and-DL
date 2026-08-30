"""Thread-safe runtime snapshot for adaptive UI clients."""

from collections import deque
from datetime import datetime, timezone
import threading
from typing import Any, Dict, Optional

from core.adaptive.observation import GestureObservation, IntentResult, UnknownGestureResult
from services.state_service import global_state


class AdaptiveRuntimeService:
    """Publishes current adaptive reasoning without exposing internal objects."""

    def __init__(self):
        self._lock = threading.RLock()
        self._snapshots: Dict[str, Dict[str, Any]] = {}
        self._intent_history: Dict[str, deque] = {}

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def publish(
        self,
        profile,
        right_observation: Optional[GestureObservation],
        left_observation: Optional[GestureObservation],
        unknown: UnknownGestureResult,
        intent: IntentResult,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        observation = right_observation or left_observation
        with self._lock:
            history = self._intent_history.setdefault(profile.user_id, deque(maxlen=12))
            if not history or history[-1]["name"] != intent.name:
                history.append({
                    "name": intent.name,
                    "confidence": round(float(intent.confidence), 3),
                    "time": self._timestamp(),
                })

            motion = observation.motion.to_dict() if observation else {
                "delta_x": 0.0,
                "delta_y": 0.0,
                "speed": 0.0,
                "displacement": 0.0,
                "direction": "stationary",
                "stability": 1.0,
                "sample_count": 0,
                "window_seconds": 0.0,
            }
            personalization = dict(context.get("personalization", {}) or {})
            snapshot = {
                "profile_id": profile.user_id,
                "profile_name": profile.display_name,
                "adaptive_enabled": bool(profile.adaptive_enabled),
                "interaction_mode": profile.interaction_mode,
                "gesture": observation.gesture if observation else "NONE",
                "personalization": personalization,
                "prediction_source": personalization.get("source", "BASE_MODEL"),
                "base_model_label": personalization.get("base_label", "NONE"),
                "personalized_prediction": personalization.get("personalized_label"),
                "user_learned_mapping": personalization.get("mapping_action"),
                "calibration": context.get("personalization_calibration"),
                "hand": observation.handedness if observation else "none",
                "finger_count": observation.finger_count if observation else 0,
                "tracking_quality": round(observation.tracking_quality, 3) if observation else 0.0,
                "motion": motion,
                "unknown": unknown.to_dict(),
                "intent": intent.to_dict(),
                "context": {
                    "module": context.get("active_module", "overview"),
                    "gesture_enabled": bool(context.get("gesture_enabled", False)),
                    "left_hand_detected": bool(context.get("left_hand_detected", False)),
                    "right_hand_detected": bool(context.get("right_hand_detected", False)),
                    "sign_prediction": context.get("sign_prediction", "NONE"),
                    "sign_confidence": round(float(context.get("sign_confidence", 0.0)), 1),
                    "sentence_active": bool(context.get("sentence_active", False)),
                    "last_intent": history[-2]["name"] if len(history) > 1 else "IDLE",
                    "recent_intents": list(context.get("recent_intents", []))[:8],
                },
                "recent_intents": list(history),
                "last_updated": self._timestamp(),
            }
            self._snapshots[profile.user_id] = snapshot

        global_state.update_state({
            "adaptive_enabled": bool(profile.adaptive_enabled),
            "profile_id": profile.user_id,
            "profile_name": profile.display_name,
            "unknown_gesture": bool(unknown.is_unknown),
            "unknown_gesture_status": unknown.status,
            "unknown_gesture_score": unknown.score,
            "unknown_gesture_reason": unknown.reason,
            "intent": intent.name,
            "intent_confidence": intent.confidence,
            "intent_actionable": bool(intent.actionable),
            "intent_source": intent.source,
            "personalized_action": personalization.get("mapping_action", ""),
            "personalized_action_source": personalization.get("source", ""),
        })
        return snapshot

    def get_snapshot(self, profile_id: str) -> Dict[str, Any]:
        with self._lock:
            snapshot = self._snapshots.get(profile_id)
            if snapshot is None:
                return {
                    "profile_id": profile_id,
                    "profile_name": "Local user",
                    "adaptive_enabled": True,
                    "interaction_mode": "adaptive",
                    "gesture": "NONE",
                    "prediction_source": "BASE_MODEL",
                    "base_model_label": "NONE",
                    "personalized_prediction": None,
                    "user_learned_mapping": None,
                    "calibration": None,
                    "personalization": {},
                    "hand": "none",
                    "finger_count": 0,
                    "tracking_quality": 0.0,
                    "motion": {"direction": "stationary", "speed": 0.0, "displacement": 0.0, "stability": 1.0, "sample_count": 0},
                    "unknown": UnknownGestureResult().to_dict(),
                    "intent": IntentResult().to_dict(),
                    "context": {},
                    "recent_intents": [],
                    "last_updated": self._timestamp(),
                }
            return dict(snapshot)

    def clear(self, profile) -> Dict[str, Any]:
        with self._lock:
            self._intent_history.pop(profile.user_id, None)
            self._snapshots.pop(profile.user_id, None)
        unknown = UnknownGestureResult()
        intent = IntentResult()
        return self.publish(profile, None, None, unknown, intent, {"active_module": "overview"})


adaptive_runtime_service = AdaptiveRuntimeService()
