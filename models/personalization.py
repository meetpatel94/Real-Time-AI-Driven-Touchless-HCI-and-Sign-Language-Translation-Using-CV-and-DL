"""Domain documents and value objects for user-specific gesture learning."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple
from uuid import uuid4

from models.user_profile import utc_now


KNOWN_GESTURE_TARGETS = frozenset({
    "ONE_FINGER",
    "TWO_FINGER",
    "THREE_FINGER",
    "FOUR_FINGER",
    "FIVE_FINGER",
    "CLOSED_FIST",
})
SUPPORTED_CUSTOM_ACTIONS = frozenset({"back", "scroll_up", "scroll_down", "click"})
SUPPORTED_SIGN_TARGETS = frozenset(chr(code) for code in range(ord("A"), ord("Z") + 1))


def _finite_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if result == result and abs(result) != float("inf") else default
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


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


def _vector(value: Any, maximum: int = 128) -> List[float]:
    if not isinstance(value, (list, tuple)):
        return []
    return [_finite_float(item) for item in value[:maximum]]


def normalize_calibration_target(value: Any) -> Optional[Tuple[str, str]]:
    """Return ``(target_type, target_value)`` for an allowed target.

    ``custom:NAME`` is intentionally a label, not a model class: it lets an
    operator calibrate a new personal pose and map it to one of the supported
    actions without changing the global A-Z/gesture model.
    """
    raw = str(value or "").strip()
    target = raw.upper()
    if target in KNOWN_GESTURE_TARGETS:
        return "gesture", target
    if target in SUPPORTED_SIGN_TARGETS:
        return "sign", target
    if target.startswith("ACTION:"):
        action = target.split(":", 1)[1].strip().lower()
        if action in SUPPORTED_CUSTOM_ACTIONS:
            return "action", action
    if raw.lower() in SUPPORTED_CUSTOM_ACTIONS:
        return "action", raw.lower()
    if raw.lower().startswith("custom:"):
        custom_name = raw.split(":", 1)[1].strip().lower()
        if 1 <= len(custom_name) <= 48 and all(
            character.isalnum() or character in {"-", "_", " "}
            for character in custom_name
        ):
            return "custom", custom_name
    return None


@dataclass
class FeatureSignature:
    """Compact, derived representation of one stable landmark sequence."""

    landmark_geometry: List[float] = field(default_factory=list)
    proportion_features: List[float] = field(default_factory=list)
    trajectory_features: List[float] = field(default_factory=list)
    motion_speed: float = 0.0
    displacement: float = 0.0
    gesture_duration: float = 0.0
    temporal_pattern: List[float] = field(default_factory=list)
    feature_version: int = 1

    @classmethod
    def from_document(cls, document: Optional[Mapping[str, Any]]) -> "FeatureSignature":
        document = document if isinstance(document, Mapping) else {}
        return cls(
            landmark_geometry=_vector(document.get("landmark_geometry")),
            proportion_features=_vector(document.get("proportion_features")),
            trajectory_features=_vector(document.get("trajectory_features")),
            motion_speed=_finite_float(document.get("motion_speed")),
            displacement=_finite_float(document.get("displacement")),
            gesture_duration=_finite_float(document.get("gesture_duration")),
            temporal_pattern=_vector(document.get("temporal_pattern")),
            feature_version=max(1, _safe_int(document.get("feature_version", 1), 1)),
        )

    def to_document(self) -> Dict[str, Any]:
        return {
            "feature_version": int(self.feature_version),
            "landmark_geometry": _vector(self.landmark_geometry),
            "proportion_features": _vector(self.proportion_features),
            "trajectory_features": _vector(self.trajectory_features),
            "motion_speed": _finite_float(self.motion_speed),
            "displacement": _finite_float(self.displacement),
            "gesture_duration": _finite_float(self.gesture_duration),
            "temporal_pattern": _vector(self.temporal_pattern),
        }


@dataclass
class CalibrationSample:
    signature: FeatureSignature
    captured_at: str
    base_label: str
    base_confidence: float
    validation_reason: str = ""
    validated: bool = True

    def to_document(self) -> Dict[str, Any]:
        return {
            "signature": self.signature.to_document(),
            "captured_at": self.captured_at,
            "base_label": self.base_label,
            "base_confidence": _finite_float(self.base_confidence),
            "validated": _safe_bool(self.validated, True),
            "validation_reason": self.validation_reason,
        }

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> "CalibrationSample":
        return cls(
            signature=FeatureSignature.from_document(document.get("signature")),
            captured_at=str(document.get("captured_at") or utc_now()),
            base_label=str(document.get("base_label") or "NONE"),
            base_confidence=_finite_float(document.get("base_confidence")),
            validated=_safe_bool(document.get("validated", False), False),
            validation_reason=str(document.get("validation_reason") or ""),
        )


@dataclass
class CalibrationSession:
    user_id: str
    target_key: str
    target_type: str
    target_value: str
    required_samples: int
    session_id: str = field(default_factory=lambda: str(uuid4()))
    status: str = "ACTIVE"
    accepted_samples: List[CalibrationSample] = field(default_factory=list)
    rejected_samples: int = 0
    rejection_reasons: Dict[str, int] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    @property
    def accepted_count(self) -> int:
        return sum(
            1
            for sample in self.accepted_samples
            if sample.validated and len(sample.signature.landmark_geometry) >= 63
        )

    @property
    def progress(self) -> float:
        return min(100.0, (self.accepted_count / self.required_samples) * 100.0) if self.required_samples else 0.0

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> "CalibrationSession":
        raw_samples = document.get("samples", [])
        samples = [
            CalibrationSample.from_document(item)
            for item in raw_samples
            if isinstance(item, Mapping)
        ] if isinstance(raw_samples, (list, tuple)) else []
        rejection_reasons = document.get("rejection_reasons")
        return cls(
            user_id=str(document.get("user_id")),
            target_key=str(document.get("target_key")),
            target_type=str(document.get("target_type") or "gesture").strip().lower(),
            target_value=str(document.get("target_value") or document.get("target_key")),
            required_samples=max(3, _safe_int(document.get("required_samples", 5), 5)),
            session_id=str(document.get("_id") or document.get("session_id")),
            status=str(document.get("status") or "CANCELLED").strip().upper(),
            accepted_samples=samples,
            rejected_samples=max(0, _safe_int(document.get("rejected_samples", 0), 0)),
            rejection_reasons=dict(rejection_reasons) if isinstance(rejection_reasons, Mapping) else {},
            created_at=str(document.get("created_at") or utc_now()),
            updated_at=str(document.get("updated_at") or utc_now()),
        )

    def to_document(self) -> Dict[str, Any]:
        return {
            "_id": self.session_id,
            "user_id": self.user_id,
            "target_key": self.target_key,
            "target_type": self.target_type,
            "target_value": self.target_value,
            "required_samples": int(self.required_samples),
            "status": self.status,
            "samples": [sample.to_document() for sample in self.accepted_samples],
            "rejected_samples": int(self.rejected_samples),
            "rejection_reasons": dict(self.rejection_reasons),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def to_dict(self, storage_available: bool = True) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "target_key": self.target_key,
            "target_type": self.target_type,
            "target_value": self.target_value,
            "required_samples": self.required_samples,
            "accepted_samples": self.accepted_count,
            "rejected_samples": self.rejected_samples,
            "rejection_reasons": dict(self.rejection_reasons),
            "progress_percent": round(self.progress, 1),
            "status": self.status,
            "storage_available": storage_available,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class LearnedGesture:
    user_id: str
    gesture_key: str
    display_name: str
    target_type: str
    target_value: str
    centroid: FeatureSignature
    validated_examples: int
    reliability: float
    match_distance_threshold: float
    source: str = "calibration"
    correction_count: int = 0
    learned_id: str = field(default_factory=lambda: str(uuid4()))
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    last_validated_at: str = field(default_factory=utc_now)

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> "LearnedGesture":
        return cls(
            learned_id=str(document.get("_id") or document.get("learned_id")),
            user_id=str(document.get("user_id")),
            gesture_key=str(document.get("gesture_key")),
            display_name=str(document.get("display_name") or document.get("gesture_key")),
            target_type=str(document.get("target_type") or "gesture").strip().lower(),
            target_value=str(document.get("target_value") or document.get("gesture_key")),
            centroid=FeatureSignature.from_document(document.get("centroid")),
            validated_examples=max(0, _safe_int(document.get("validated_examples", 0), 0)),
            reliability=max(0.0, min(1.0, _finite_float(document.get("reliability")))),
            match_distance_threshold=max(
                0.0001,
                min(0.60, _finite_float(document.get("match_distance_threshold"), 0.2)),
            ),
            source=str(document.get("source") or "calibration"),
            correction_count=max(0, _safe_int(document.get("correction_count", 0), 0)),
            created_at=str(document.get("created_at") or utc_now()),
            updated_at=str(document.get("updated_at") or utc_now()),
            last_validated_at=str(document.get("last_validated_at") or utc_now()),
        )

    def to_document(self) -> Dict[str, Any]:
        return {
            "_id": self.learned_id,
            "user_id": self.user_id,
            "gesture_key": self.gesture_key,
            "display_name": self.display_name,
            "target_type": self.target_type,
            "target_value": self.target_value,
            "centroid": self.centroid.to_document(),
            "validated_examples": int(self.validated_examples),
            "reliability": max(0.0, min(1.0, _finite_float(self.reliability))),
            "match_distance_threshold": max(
                0.0001,
                min(0.60, _finite_float(self.match_distance_threshold, 0.2)),
            ),
            "source": self.source,
            "correction_count": int(self.correction_count),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_validated_at": self.last_validated_at,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.learned_id,
            "gesture_key": self.gesture_key,
            "display_name": self.display_name,
            "target_type": self.target_type,
            "target_value": self.target_value,
            "validated_examples": self.validated_examples,
            "reliability": round(max(0.0, min(1.0, _finite_float(self.reliability))), 3),
            "match_distance_threshold": round(
                max(0.0001, min(0.60, _finite_float(self.match_distance_threshold, 0.2))),
                4,
            ),
            "source": self.source,
            "correction_count": self.correction_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_validated_at": self.last_validated_at,
        }


@dataclass
class CorrectionRecord:
    user_id: str
    correct_label: str
    correct_intent: str
    base_label: str
    base_confidence: float
    signature: FeatureSignature
    correction_id: str = field(default_factory=lambda: str(uuid4()))
    created_at: str = field(default_factory=utc_now)
    validated: bool = True

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> "CorrectionRecord":
        return cls(
            correction_id=str(document.get("_id") or document.get("correction_id")),
            user_id=str(document.get("user_id")),
            correct_label=str(document.get("correct_label") or ""),
            correct_intent=str(document.get("correct_intent") or ""),
            base_label=str(document.get("base_label") or "NONE"),
            base_confidence=_finite_float(document.get("base_confidence")),
            signature=FeatureSignature.from_document(document.get("signature")),
            validated=_safe_bool(document.get("validated", False), False),
            created_at=str(document.get("created_at") or utc_now()),
        )

    def to_document(self) -> Dict[str, Any]:
        return {
            "_id": self.correction_id,
            "user_id": self.user_id,
            "correct_label": self.correct_label,
            "correct_intent": self.correct_intent,
            "base_label": self.base_label,
            "base_confidence": _finite_float(self.base_confidence),
            "signature": self.signature.to_document(),
            "validated": _safe_bool(self.validated, True),
            "created_at": self.created_at,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.correction_id,
            "correct_label": self.correct_label,
            "correct_intent": self.correct_intent,
            "base_label": self.base_label,
            "base_confidence": round(_finite_float(self.base_confidence), 3),
            "validated": _safe_bool(self.validated, True),
            "created_at": self.created_at,
        }


@dataclass
class CustomGestureMapping:
    user_id: str
    learned_gesture_id: str
    name: str
    action: str
    mapping_id: str = field(default_factory=lambda: str(uuid4()))
    enabled: bool = True
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> "CustomGestureMapping":
        return cls(
            mapping_id=str(document.get("_id") or document.get("mapping_id")),
            user_id=str(document.get("user_id")),
            learned_gesture_id=str(document.get("learned_gesture_id")),
            name=str(document.get("name") or "Custom gesture"),
            action=str(document.get("action") or "click"),
            enabled=_safe_bool(document.get("enabled", False), False),
            created_at=str(document.get("created_at") or utc_now()),
            updated_at=str(document.get("updated_at") or utc_now()),
        )

    def to_document(self) -> Dict[str, Any]:
        return {
            "_id": self.mapping_id,
            "user_id": self.user_id,
            "learned_gesture_id": self.learned_gesture_id,
            "name": self.name,
            "action": self.action,
            "enabled": _safe_bool(self.enabled, True),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.mapping_id,
            "learned_gesture_id": self.learned_gesture_id,
            "name": self.name,
            "action": self.action,
            "enabled": _safe_bool(self.enabled, True),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class PersonalizationDecision:
    """Safe decision returned by the matcher for runtime/UI use."""

    base_label: str = "NONE"
    base_confidence: float = 0.0
    personalized_label: Optional[str] = None
    mapping_action: Optional[str] = None
    confidence: float = 0.0
    used: bool = False
    source: str = "BASE_MODEL"
    reason: str = "No sufficiently validated user-specific representation matched."
    learned_gesture_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "base_label": self.base_label,
            "base_confidence": round(_finite_float(self.base_confidence), 3),
            "personalized_label": self.personalized_label,
            "mapping_action": self.mapping_action,
            "confidence": round(_finite_float(self.confidence), 3),
            "used": bool(self.used),
            "source": self.source,
            "reason": self.reason,
            "learned_gesture_id": self.learned_gesture_id,
        }
