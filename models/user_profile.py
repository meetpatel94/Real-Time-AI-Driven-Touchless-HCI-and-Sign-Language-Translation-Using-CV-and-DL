"""Domain model for a local GestureForge operator profile.

The application currently controls one local webcam and OS cursor, so a profile is
identified by a client-provided opaque id rather than an account/login.  Keeping
this model independent from SQLite means the repository can be replaced by a
remote store later without changing the adaptive services.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from typing import Any, Dict, Mapping, Optional


SUPPORTED_LANGUAGES = ("English", "Hindi", "Gujarati")
SUPPORTED_SCROLL_LEVELS = ("low", "medium", "high")
SUPPORTED_INTERACTION_MODES = ("adaptive", "legacy")


def utc_now() -> str:
    """Return a stable, JSON/SQLite-friendly UTC timestamp."""
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
    def from_row(cls, row: Mapping[str, Any]) -> "UserProfile":
        """Build a profile from a sqlite row without leaking persistence details."""
        return cls(
            user_id=str(row["user_id"]),
            display_name=str(row["display_name"] or "Local user"),
            preferred_language=str(row["preferred_language"] or "English"),
            preferred_module=str(row["preferred_module"] or "studio"),
            cursor_sensitivity=float(row["cursor_sensitivity"]),
            scroll_sensitivity=str(row["scroll_sensitivity"] or "medium"),
            interaction_mode=str(row["interaction_mode"] or "adaptive"),
            adaptive_enabled=bool(row["adaptive_enabled"]),
            learning_enabled=bool(row["learning_enabled"]),
            unknown_gesture_threshold=float(row["unknown_gesture_threshold"]),
            interaction_count=int(row["interaction_count"] or 0),
            unknown_gesture_count=int(row["unknown_gesture_count"] or 0),
            confirmed_intent_count=int(row["confirmed_intent_count"] or 0),
            intent_preferences=_safe_json_dict(row["intent_preferences_json"]),
            created_at=str(row["created_at"] or utc_now()),
            updated_at=str(row["updated_at"] or utc_now()),
            last_seen_at=str(row["last_seen_at"] or utc_now()),
        )

    def to_record(self) -> Dict[str, Any]:
        """Return the persistence representation used by repositories."""
        return {
            "user_id": self.user_id,
            "display_name": self.display_name,
            "preferred_language": self.preferred_language,
            "preferred_module": self.preferred_module,
            "cursor_sensitivity": float(self.cursor_sensitivity),
            "scroll_sensitivity": self.scroll_sensitivity,
            "interaction_mode": self.interaction_mode,
            "adaptive_enabled": int(bool(self.adaptive_enabled)),
            "learning_enabled": int(bool(self.learning_enabled)),
            "unknown_gesture_threshold": float(self.unknown_gesture_threshold),
            "interaction_count": int(self.interaction_count),
            "unknown_gesture_count": int(self.unknown_gesture_count),
            "confirmed_intent_count": int(self.confirmed_intent_count),
            "intent_preferences_json": json.dumps(self.intent_preferences, sort_keys=True),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_seen_at": self.last_seen_at,
        }

    def to_dict(self) -> Dict[str, Any]:
        """Public API representation; do not expose serialized persistence fields."""
        return {
            "user_id": self.user_id,
            "display_name": self.display_name,
            "preferred_language": self.preferred_language,
            "preferred_module": self.preferred_module,
            "cursor_sensitivity": round(float(self.cursor_sensitivity), 2),
            "scroll_sensitivity": self.scroll_sensitivity,
            "interaction_mode": self.interaction_mode,
            "adaptive_enabled": bool(self.adaptive_enabled),
            "learning_enabled": bool(self.learning_enabled),
            "unknown_gesture_threshold": round(float(self.unknown_gesture_threshold), 2),
            "interaction_count": int(self.interaction_count),
            "unknown_gesture_count": int(self.unknown_gesture_count),
            "confirmed_intent_count": int(self.confirmed_intent_count),
            "intent_preferences": dict(self.intent_preferences),
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
    event_id: Optional[int] = None

    def to_record(self) -> Dict[str, Any]:
        return {
            "id": self.event_id,
            "user_id": self.user_id,
            "occurred_at": self.occurred_at,
            "module": self.module,
            "hand": self.hand,
            "gesture": self.gesture,
            "finger_count": int(self.finger_count),
            "motion_direction": self.motion_direction,
            "motion_speed": float(self.motion_speed),
            "displacement": float(self.displacement),
            "tracking_quality": float(self.tracking_quality),
            "is_unknown": int(bool(self.is_unknown)),
            "unknown_status": self.unknown_status,
            "unknown_reason": self.unknown_reason,
            "intent": self.intent,
            "intent_confidence": float(self.intent_confidence),
            "action_taken": int(bool(self.action_taken)),
            "temporal_features_json": json.dumps(self.temporal_features, sort_keys=True),
            "context_snapshot_json": json.dumps(self.context_snapshot, sort_keys=True),
            "feedback": self.feedback,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.event_id,
            "user_id": self.user_id,
            "occurred_at": self.occurred_at,
            "module": self.module,
            "hand": self.hand,
            "gesture": self.gesture,
            "finger_count": int(self.finger_count),
            "motion_direction": self.motion_direction,
            "motion_speed": round(float(self.motion_speed), 4),
            "displacement": round(float(self.displacement), 4),
            "tracking_quality": round(float(self.tracking_quality), 3),
            "is_unknown": bool(self.is_unknown),
            "unknown_status": self.unknown_status,
            "unknown_reason": self.unknown_reason,
            "intent": self.intent,
            "intent_confidence": round(float(self.intent_confidence), 3),
            "action_taken": bool(self.action_taken),
            "temporal_features": dict(self.temporal_features),
            "context_snapshot": dict(self.context_snapshot),
            "feedback": self.feedback,
        }

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "InteractionEvent":
        def decode_json(name: str) -> Dict[str, Any]:
            return _safe_json_dict(row[name])

        return cls(
            event_id=int(row["id"]),
            user_id=str(row["user_id"]),
            occurred_at=str(row["occurred_at"]),
            module=str(row["module"] or "unknown"),
            hand=str(row["hand"] or "none"),
            gesture=str(row["gesture"] or "NONE"),
            finger_count=int(row["finger_count"] or 0),
            motion_direction=str(row["motion_direction"] or "stationary"),
            motion_speed=float(row["motion_speed"] or 0.0),
            displacement=float(row["displacement"] or 0.0),
            tracking_quality=float(row["tracking_quality"] or 0.0),
            is_unknown=bool(row["is_unknown"]),
            unknown_status=str(row["unknown_status"] or "NONE"),
            unknown_reason=str(row["unknown_reason"] or ""),
            intent=str(row["intent"] or "IDLE"),
            intent_confidence=float(row["intent_confidence"] or 0.0),
            action_taken=bool(row["action_taken"]),
            temporal_features=decode_json("temporal_features_json"),
            context_snapshot=decode_json("context_snapshot_json"),
            feedback=row["feedback"],
        )
