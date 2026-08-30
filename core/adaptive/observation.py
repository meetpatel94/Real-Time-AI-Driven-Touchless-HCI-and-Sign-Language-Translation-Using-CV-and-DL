"""Framework-neutral domain objects for adaptive gesture interpretation."""

from dataclasses import dataclass, field
from typing import Any, Dict, Tuple


@dataclass
class MotionFeatures:
    """Temporal motion derived from consecutive MediaPipe palm positions."""

    delta_x: float = 0.0
    delta_y: float = 0.0
    speed: float = 0.0
    displacement: float = 0.0
    direction: str = "stationary"
    stability: float = 1.0
    sample_count: int = 0
    window_seconds: float = 0.0
    # Short normalized palm trajectory; this is derived motion, not raw frames.
    trajectory: Tuple[Tuple[float, float], ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "delta_x": round(float(self.delta_x), 4),
            "delta_y": round(float(self.delta_y), 4),
            "speed": round(float(self.speed), 4),
            "displacement": round(float(self.displacement), 4),
            "direction": self.direction,
            "stability": round(float(self.stability), 3),
            "sample_count": int(self.sample_count),
            "window_seconds": round(float(self.window_seconds), 3),
            "trajectory": [
                [round(float(point[0]), 4), round(float(point[1]), 4)]
                for point in self.trajectory[:16]
            ],
        }


@dataclass
class GestureObservation:
    """One hand observation used by unknown detection and intent interpretation."""

    timestamp: float
    handedness: str
    gesture: str
    finger_states: Dict[str, bool]
    palm_center: Tuple[float, float]
    hand_scale: float
    tracking_quality: float
    motion: MotionFeatures = field(default_factory=MotionFeatures)
    # Compact derived values used by calibration/matching. Raw landmarks are
    # intentionally not retained on the observation or sent to persistence.
    derived_features: Dict[str, Any] = field(default_factory=dict)

    @property
    def finger_count(self) -> int:
        return sum(1 for is_extended in self.finger_states.values() if is_extended)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "handedness": self.handedness,
            "gesture": self.gesture,
            "finger_states": dict(self.finger_states),
            "finger_count": self.finger_count,
            "palm_center": [round(float(self.palm_center[0]), 4), round(float(self.palm_center[1]), 4)],
            "hand_scale": round(float(self.hand_scale), 4),
            "tracking_quality": round(float(self.tracking_quality), 3),
            "motion": self.motion.to_dict(),
            "derived_features": dict(self.derived_features),
        }


@dataclass
class UnknownGestureResult:
    """Explicit state returned by the unknown gesture detector."""

    status: str = "NO_HAND"
    is_unknown: bool = False
    score: float = 0.0
    reason: str = "No hand is currently tracked."
    hand: str = "none"
    requires_feedback: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "is_unknown": bool(self.is_unknown),
            "score": round(max(0.0, min(1.0, float(self.score))), 3),
            "reason": self.reason,
            "hand": self.hand,
            "requires_feedback": bool(self.requires_feedback),
        }


@dataclass
class IntentResult:
    """Contextual interpretation, deliberately separate from command execution."""

    name: str = "IDLE"
    confidence: float = 0.0
    actionable: bool = False
    source: str = "none"
    reason: str = "No actionable hand intent."
    candidates: Tuple[str, ...] = ()
    context_used: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "confidence": round(max(0.0, min(1.0, float(self.confidence))), 3),
            "actionable": bool(self.actionable),
            "source": self.source,
            "reason": self.reason,
            "candidates": list(self.candidates),
            "context_used": dict(self.context_used),
        }
