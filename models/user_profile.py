"""Mongo-independent personalization domain models.

These models contain no MongoDB or Flask knowledge.  They describe the
personalization documents exchanged between services and repositories.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import math
from typing import Any, Dict, Mapping, Optional


SUPPORTED_LANGUAGES = ("English", "Hindi", "Gujarati")
SUPPORTED_SCROLL_LEVELS = ("low", "medium", "high")
SUPPORTED_INTERACTION_MODES = ("adaptive", "legacy")
SUPPORTED_MODULES = (
    "overview", "drawing", "alphabet", "recognition", "studio", "sentence",
    "translation", "mouse",
)


def utc_now() -> str:
    """Return a stable, JSON/Mongo-friendly UTC timestamp."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_json_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def _safe_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _safe_derived_temporal(value: Any) -> Dict[str, Any]:
    """Serialize only the bounded motion features allowed in event history."""
    payload = _safe_json_dict(value)
    result: Dict[str, Any] = {}
    for key in ("delta_x", "delta_y", "speed", "displacement", "stability", "window_seconds"):
        if key in payload:
            result[key] = _safe_float(payload[key])
    if "direction" in payload:
        result["direction"] = str(payload["direction"] or "stationary")[:32]
    if "sample_count" in payload:
        result["sample_count"] = max(0, _safe_int(payload["sample_count"], 0))
    trajectory = payload.get("trajectory")
    if isinstance(trajectory, (list, tuple)):
        result["trajectory"] = [
            [_safe_float(point[0]), _safe_float(point[1])]
            for point in trajectory[:16]
            if isinstance(point, (list, tuple)) and len(point) >= 2
        ]
    return result


def _safe_derived_context(value: Any) -> Dict[str, Any]:
    """Serialize only explainable, non-raw context fields in event history."""
    payload = _safe_json_dict(value)
    result: Dict[str, Any] = {}
    for key in ("active_module", "sign_prediction", "last_intent", "profile_mode", "personalization_source"):
        if key in payload:
            result[key] = str(payload[key] or "")[:80]
    for key in ("gesture_enabled", "sentence_active"):
        if key in payload:
            result[key] = _safe_bool(payload[key], False)
    for key in ("sign_confidence", "personalization_confidence"):
        if key in payload:
            result[key] = _safe_float(payload[key])
    recent = payload.get("recent_intents")
    if isinstance(recent, (list, tuple)):
        result["recent_intents"] = [str(item)[:80] for item in recent[:8]]
    return result


def _safe_preferences(value: Any) -> Dict[str, Any]:
    """Keep profile preferences small and scalar rather than an arbitrary blob."""
    payload = _safe_json_dict(value)
    result: Dict[str, Any] = {}
    blocked_tokens = ("frame", "image", "video", "landmark", "recording", "dataset", "model")
    for raw_key, raw_value in list(payload.items())[:32]:
        key = str(raw_key).strip()[:64]
        if not key or any(token in key.lower() for token in blocked_tokens):
            continue
        if isinstance(raw_value, bool):
            result[key] = raw_value
        elif isinstance(raw_value, int) and not isinstance(raw_value, bool):
            result[key] = max(-1000000, min(1000000, raw_value))
        elif isinstance(raw_value, float):
            result[key] = _safe_float(raw_value)
        elif isinstance(raw_value, str):
            result[key] = raw_value[:160]
        elif isinstance(raw_value, (list, tuple)) and all(
            isinstance(item, (str, int, float, bool)) for item in raw_value[:16]
        ):
            result[key] = [
                item[:160] if isinstance(item, str) else (
                    _safe_float(item) if isinstance(item, float) else item
                )
                for item in raw_value[:16]
            ]
    return result


def _safe_feedback(value: Any) -> Optional[str]:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in {"accepted", "rejected", "correct", "incorrect", "dismissed"} else None


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


@dataclass
class UserProfile:
    """Persisted preferences and lightweight usage counters for one operator."""

    user_id: str
    display_name: str = "Local user"
    preferred_language: str = "English"
    preferred_module: str = "studio"
    cursor_sensitivity: float = 0.50
    scroll_sensitivity: str = "medium"
    interaction_mode: str = "adaptive"
    adaptive_enabled: bool = True
    learning_enabled: bool = True
    unknown_gesture_threshold: float = 0.60
    interaction_count: int = 0
    unknown_gesture_count: int = 0
    confirmed_intent_count: int = 0
    intent_preferences: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    last_seen_at: str = field(default_factory=utc_now)

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> "UserProfile":
        """Build a bounded, internally consistent profile from a document."""
        document = document if isinstance(document, Mapping) else {}
        language = str(document.get("preferred_language") or "English").strip().lower()
        language_names = {item.lower(): item for item in SUPPORTED_LANGUAGES}
        language = language_names.get(language, "English")
        module = str(document.get("preferred_module") or "studio").strip().lower()
        module = module if module in SUPPORTED_MODULES else "studio"
        scroll_level = str(document.get("scroll_sensitivity") or "medium").strip().lower()
        scroll_level = scroll_level if scroll_level in SUPPORTED_SCROLL_LEVELS else "medium"
        mode = str(document.get("interaction_mode") or "adaptive").strip().lower()
        mode = mode if mode in SUPPORTED_INTERACTION_MODES else "adaptive"
        adaptive_enabled = _safe_bool(document.get("adaptive_enabled", True), True)
        # Mode and flag are two persisted views of the same safety switch. Make
        # malformed or contradictory documents safe before they reach runtime.
        if mode == "legacy" or not adaptive_enabled:
            mode = "legacy"
            adaptive_enabled = False
        else:
            mode = "adaptive"
            adaptive_enabled = True
        return cls(
            user_id=str(document.get("user_id") or document.get("_id") or "local-user"),
            display_name=str(document.get("display_name") or "Local user"),
            preferred_language=language,
            preferred_module=module,
            cursor_sensitivity=max(0.10, min(1.0, _safe_float(document.get("cursor_sensitivity", 0.50), 0.50))),
            scroll_sensitivity=scroll_level,
            interaction_mode=mode,
            adaptive_enabled=adaptive_enabled,
            learning_enabled=_safe_bool(document.get("learning_enabled", True), True),
            unknown_gesture_threshold=max(0.40, min(0.90, _safe_float(document.get("unknown_gesture_threshold", 0.60), 0.60))),
            interaction_count=max(0, _safe_int(document.get("interaction_count", 0), 0)),
            unknown_gesture_count=max(0, _safe_int(document.get("unknown_gesture_count", 0), 0)),
            confirmed_intent_count=max(0, _safe_int(document.get("confirmed_intent_count", 0), 0)),
            intent_preferences=_safe_preferences(document.get("intent_preferences", {})),
            created_at=str(document.get("created_at") or utc_now()),
            updated_at=str(document.get("updated_at") or utc_now()),
            last_seen_at=str(document.get("last_seen_at") or utc_now()),
        )

    def to_document(self) -> Dict[str, Any]:
        """Return the validated profile document (without a Mongo _id)."""
        return {
            "user_id": self.user_id,
            "display_name": self.display_name,
            "preferred_language": self.preferred_language,
            "preferred_module": self.preferred_module,
            "cursor_sensitivity": _safe_float(self.cursor_sensitivity, 0.50),
            "scroll_sensitivity": self.scroll_sensitivity,
            "interaction_mode": self.interaction_mode,
            "adaptive_enabled": _safe_bool(self.adaptive_enabled, True),
            "learning_enabled": _safe_bool(self.learning_enabled, True),
            "unknown_gesture_threshold": _safe_float(self.unknown_gesture_threshold, 0.60),
            "interaction_count": max(0, _safe_int(self.interaction_count, 0)),
            "unknown_gesture_count": max(0, _safe_int(self.unknown_gesture_count, 0)),
            "confirmed_intent_count": max(0, _safe_int(self.confirmed_intent_count, 0)),
            "intent_preferences": _safe_preferences(self.intent_preferences),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_seen_at": self.last_seen_at,
        }

    def to_dict(self) -> Dict[str, Any]:
        """Public API representation."""
        return {
            "user_id": self.user_id,
            "display_name": self.display_name,
            "preferred_language": self.preferred_language,
            "preferred_module": self.preferred_module,
            "cursor_sensitivity": round(_safe_float(self.cursor_sensitivity, 0.50), 2),
            "scroll_sensitivity": self.scroll_sensitivity,
            "interaction_mode": self.interaction_mode,
            "adaptive_enabled": _safe_bool(self.adaptive_enabled, True),
            "learning_enabled": _safe_bool(self.learning_enabled, True),
            "unknown_gesture_threshold": round(_safe_float(self.unknown_gesture_threshold, 0.60), 2),
            "interaction_count": max(0, _safe_int(self.interaction_count, 0)),
            "unknown_gesture_count": max(0, _safe_int(self.unknown_gesture_count, 0)),
            "confirmed_intent_count": max(0, _safe_int(self.confirmed_intent_count, 0)),
            "intent_preferences": _safe_preferences(self.intent_preferences),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_seen_at": self.last_seen_at,
        }


@dataclass
class InteractionEvent:
    """A meaningful adaptive observation, not a raw per-camera-frame dump."""

    user_id: str
    occurred_at: str
    module: str
    hand: str
    gesture: str
    finger_count: int
    motion_direction: str
    motion_speed: float
    displacement: float
    tracking_quality: float
    is_unknown: bool
    unknown_status: str
    unknown_reason: str
    intent: str
    intent_confidence: float
    action_taken: bool
    temporal_features: Dict[str, Any] = field(default_factory=dict)
    context_snapshot: Dict[str, Any] = field(default_factory=dict)
    feedback: Optional[str] = None
    event_id: Optional[str] = None

    def to_document(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "occurred_at": self.occurred_at,
            "module": self.module,
            "hand": self.hand,
            "gesture": self.gesture,
            "finger_count": max(0, _safe_int(self.finger_count, 0)),
            "motion_direction": self.motion_direction,
            "motion_speed": _safe_float(self.motion_speed),
            "displacement": _safe_float(self.displacement),
            "tracking_quality": _safe_float(self.tracking_quality),
            "is_unknown": _safe_bool(self.is_unknown, False),
            "unknown_status": self.unknown_status,
            "unknown_reason": self.unknown_reason,
            "intent": self.intent,
            "intent_confidence": _safe_float(self.intent_confidence),
            "action_taken": _safe_bool(self.action_taken, False),
            "temporal_features": _safe_derived_temporal(self.temporal_features),
            "context_snapshot": _safe_derived_context(self.context_snapshot),
            "feedback": _safe_feedback(self.feedback),
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.event_id,
            **self.to_document(),
            "finger_count": max(0, _safe_int(self.finger_count, 0)),
            "motion_speed": round(_safe_float(self.motion_speed), 4),
            "displacement": round(_safe_float(self.displacement), 4),
            "tracking_quality": round(_safe_float(self.tracking_quality), 3),
            "intent_confidence": round(_safe_float(self.intent_confidence), 3),
        }

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> "InteractionEvent":
        return cls(
            event_id=str(document.get("_id")) if document.get("_id") is not None else None,
            user_id=str(document.get("user_id")),
            occurred_at=str(document.get("occurred_at")),
            module=str(document.get("module") or "unknown"),
            hand=str(document.get("hand") or "none"),
            gesture=str(document.get("gesture") or "NONE"),
            finger_count=max(0, _safe_int(document.get("finger_count", 0), 0)),
            motion_direction=str(document.get("motion_direction") or "stationary"),
            motion_speed=_safe_float(document.get("motion_speed", 0.0)),
            displacement=_safe_float(document.get("displacement", 0.0)),
            tracking_quality=_safe_float(document.get("tracking_quality", 0.0)),
            is_unknown=_safe_bool(document.get("is_unknown", False), False),
            unknown_status=str(document.get("unknown_status") or "NONE"),
            unknown_reason=str(document.get("unknown_reason") or ""),
            intent=str(document.get("intent") or "IDLE"),
            intent_confidence=_safe_float(document.get("intent_confidence", 0.0)),
            action_taken=_safe_bool(document.get("action_taken", False), False),
            temporal_features=_safe_derived_temporal(document.get("temporal_features", {})),
            context_snapshot=_safe_derived_context(document.get("context_snapshot", {})),
            feedback=_safe_feedback(document.get("feedback")),
        )
