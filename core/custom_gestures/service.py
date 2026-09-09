"""Filesystem-backed custom gesture library and runtime matcher.

This service is intentionally isolated from the existing A-Z sign recognition
model, word prediction, translation, air-mouse and personalization pipelines.
It owns only data under ``data/custom_gestures/<safe_gesture_name>/`` and exposes
separate capture/test/live state for the Custom Gestures page.
"""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime, timezone
import glob
import json
import math
import os
import re
import shutil
import tempfile
import threading
import time
import unicodedata
from typing import Any, Deque, Dict, List, Optional, Sequence, Tuple

from config import Config
from core.custom_gestures.feature_extractor import (
    feature_extractor,
    mean_vector,
    mirrored_feature_vector,
    vector_distance,
)
from services.logging_service import logger


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_bool(value: Any, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on", "enabled"}:
            return True
        if normalized in {"false", "0", "no", "off", "disabled"}:
            return False
    return default


def _safe_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        result = float(value)
        if not math.isfinite(result):
            raise ValueError
    except (TypeError, ValueError):
        result = default
    return max(minimum, min(maximum, result))


def _safe_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = default
    return max(minimum, min(maximum, result))


def sanitize_gesture_name(name: Any) -> str:
    """Convert a user label to a folder-safe gesture id.

    The returned value never contains path separators, leading dots, or traversal
    segments.  Friendly names are retained in metadata; the safe id is only the
    storage folder name and API identifier.
    """
    raw = str(name or "").strip()
    if not raw:
        raise ValueError("Gesture name is required.")
    ascii_name = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode("ascii")
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", ascii_name).strip("._-").lower()
    safe = re.sub(r"_+", "_", safe)
    safe = safe[:64].strip("._-")
    if not safe or safe in {".", ".."} or safe.startswith("."):
        raise ValueError("Gesture name must contain letters or numbers.")
    if safe.upper() in {"CON", "PRN", "AUX", "NUL", "COM1", "COM2", "LPT1", "LPT2"}:
        raise ValueError("Gesture name is reserved; choose another name.")
    return safe


@dataclass
class CaptureSession:
    gesture_id: str
    gesture_name: str
    description: str
    hand: str
    target_samples: int
    started_at: str
    active: bool = True


class CustomGestureService:
    """Create, persist, and recognize user-defined hand gestures.

    Matching is lightweight nearest-neighbor/prototype matching over normalized
    MediaPipe landmark features.  It never trains or mutates the existing A-Z
    model and never writes to global recognition state.
    """

    METADATA_FILE = "metadata.json"
    SAMPLE_GLOB = "sample_*.json"
    SCHEMA_VERSION = 1

    def __init__(
        self,
        base_dir: Optional[str] = None,
        similarity_threshold: Optional[float] = None,
        max_match_distance: Optional[float] = None,
        capture_interval: Optional[float] = None,
        stability_frames: Optional[int] = None,
        smoothing_window: Optional[int] = None,
        min_ready_samples: Optional[int] = None,
        min_tracking_quality: Optional[float] = None,
    ):
        self.base_dir = os.path.abspath(base_dir or getattr(
            Config,
            "CUSTOM_GESTURE_BASE_DIR",
            os.path.join(Config.BASE_DIR, "data", "custom_gestures"),
        ))
        self.similarity_threshold = _safe_float(
            similarity_threshold if similarity_threshold is not None else getattr(Config, "CUSTOM_GESTURE_SIMILARITY_THRESHOLD", 0.85),
            0.85,
            0.10,
            0.99,
        )
        self.max_match_distance = _safe_float(
            max_match_distance if max_match_distance is not None else getattr(Config, "CUSTOM_GESTURE_MAX_MATCH_DISTANCE", 0.45),
            0.45,
            0.05,
            2.0,
        )
        self.capture_interval = _safe_float(
            capture_interval if capture_interval is not None else getattr(Config, "CUSTOM_GESTURE_CAPTURE_INTERVAL", 0.12),
            0.12,
            0.0,
            5.0,
        )
        self.stability_frames = _safe_int(
            stability_frames if stability_frames is not None else getattr(Config, "CUSTOM_GESTURE_STABILITY_FRAMES", 3),
            3,
            1,
            10,
        )
        self.smoothing_window = _safe_int(
            smoothing_window if smoothing_window is not None else getattr(Config, "CUSTOM_GESTURE_SMOOTHING_WINDOW", 6),
            6,
            self.stability_frames,
            30,
        )
        self.min_ready_samples = _safe_int(
            min_ready_samples if min_ready_samples is not None else getattr(Config, "CUSTOM_GESTURE_MIN_READY_SAMPLES", 3),
            3,
            1,
            300,
        )
        self.min_tracking_quality = _safe_float(
            min_tracking_quality if min_tracking_quality is not None else getattr(Config, "CUSTOM_GESTURE_MIN_TRACKING_QUALITY", 0.90),
            0.90,
            0.10,
            1.0,
        )

        self._lock = threading.RLock()
        self._cache: Optional[Dict[str, Dict[str, Any]]] = None
        self._capture_session: Optional[CaptureSession] = None
        self._last_capture_time = 0.0
        self._prediction_buffer: Deque[Dict[str, Any]] = deque(maxlen=self.smoothing_window)
        self._recent_predictions: Deque[Dict[str, Any]] = deque(maxlen=8)
        self._runtime: Dict[str, Any] = self._default_runtime()
        self._ensure_base_dir()

    # ------------------------------------------------------------------
    # Filesystem safety and metadata
    # ------------------------------------------------------------------
    def _ensure_base_dir(self) -> None:
        os.makedirs(self.base_dir, exist_ok=True)

    def _gesture_dir(self, gesture_id: Any) -> str:
        safe_id = sanitize_gesture_name(gesture_id)
        candidate = os.path.abspath(os.path.join(self.base_dir, safe_id))
        base = os.path.abspath(self.base_dir)
        if os.path.commonpath([base, candidate]) != base:
            raise ValueError("Invalid gesture path.")
        return candidate

    def _metadata_path(self, gesture_id: Any) -> str:
        return os.path.join(self._gesture_dir(gesture_id), self.METADATA_FILE)

    @staticmethod
    def _write_json_atomic(path: str, payload: Dict[str, Any]) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(prefix=".tmp_", suffix=".json", dir=os.path.dirname(path))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
            os.replace(tmp_path, path)
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    @staticmethod
    def _read_json(path: str) -> Dict[str, Any]:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, dict) else {}

    def _sample_paths(self, gesture_id: Any) -> List[str]:
        folder = self._gesture_dir(gesture_id)
        return sorted(glob.glob(os.path.join(folder, self.SAMPLE_GLOB)))

    def _sample_count(self, gesture_id: Any) -> int:
        return len(self._sample_paths(gesture_id))

    def _load_sample_features(self, gesture_id: Any) -> List[List[float]]:
        features: List[List[float]] = []
        for path in self._sample_paths(gesture_id):
            try:
                payload = self._read_json(path)
                sample_features = payload.get("features", [])
                if isinstance(sample_features, list) and sample_features:
                    features.append([float(value) for value in sample_features])
            except Exception as exc:
                logger.warning(f"Skipping unreadable custom gesture sample {path}: {exc}")
        return features

    def _status_for(self, metadata: Dict[str, Any], sample_count: int) -> str:
        target = _safe_int(metadata.get("target_samples"), 30, 1, 300)
        prototype = metadata.get("prototype", [])
        if sample_count >= target and prototype:
            return "Ready"
        if sample_count > 0:
            return "Incomplete"
        return "No samples"

    def _normalize_metadata(self, metadata: Dict[str, Any], gesture_id: str) -> Dict[str, Any]:
        sample_count = self._sample_count(gesture_id)
        normalized = {
            "schema_version": self.SCHEMA_VERSION,
            "gesture_id": gesture_id,
            "gesture_name": str(metadata.get("gesture_name") or gesture_id).strip() or gesture_id,
            "description": str(metadata.get("description") or ""),
            "hand": self._normalize_hand(metadata.get("hand", "either")),
            "enabled": _safe_bool(metadata.get("enabled", True), True),
            "target_samples": _safe_int(metadata.get("target_samples"), 30, 1, 300),
            "sample_count": sample_count,
            "status": str(metadata.get("status") or ""),
            "created_at": str(metadata.get("created_at") or _utc_now()),
            "updated_at": str(metadata.get("updated_at") or _utc_now()),
            "similarity_threshold": _safe_float(
                metadata.get("similarity_threshold", self.similarity_threshold),
                self.similarity_threshold,
                0.10,
                0.99,
            ),
            "prototype": metadata.get("prototype", []) if isinstance(metadata.get("prototype", []), list) else [],
            "feature_count": _safe_int(metadata.get("feature_count"), 0, 0, 1000),
        }
        normalized["status"] = self._status_for(normalized, sample_count)
        return normalized

    def _load_metadata(self, gesture_id: Any) -> Optional[Dict[str, Any]]:
        safe_id = sanitize_gesture_name(gesture_id)
        path = self._metadata_path(safe_id)
        if not os.path.isfile(path):
            return None
        try:
            payload = self._read_json(path)
        except Exception as exc:
            logger.warning(f"Could not read custom gesture metadata {path}: {exc}")
            return None
        return self._normalize_metadata(payload, safe_id)

    def _save_metadata(self, metadata: Dict[str, Any]) -> Dict[str, Any]:
        gesture_id = sanitize_gesture_name(metadata.get("gesture_id"))
        normalized = self._normalize_metadata(metadata, gesture_id)
        normalized["updated_at"] = _utc_now()
        self._write_json_atomic(self._metadata_path(gesture_id), normalized)
        self._invalidate_cache()
        return normalized

    def _invalidate_cache(self) -> None:
        self._cache = None

    def _load_cache(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            if self._cache is not None:
                return dict(self._cache)
            self._ensure_base_dir()
            cache: Dict[str, Dict[str, Any]] = {}
            for entry in sorted(os.listdir(self.base_dir)):
                folder = os.path.join(self.base_dir, entry)
                if not os.path.isdir(folder):
                    continue
                try:
                    gesture_id = sanitize_gesture_name(entry)
                except ValueError:
                    continue
                metadata = self._load_metadata(gesture_id)
                if metadata is None:
                    continue
                features = self._load_sample_features(gesture_id)
                if features and not metadata.get("prototype"):
                    metadata["prototype"] = mean_vector(features)
                metadata["sample_features"] = features
                metadata["sample_count"] = len(features) if features else self._sample_count(gesture_id)
                metadata["status"] = self._status_for(metadata, metadata["sample_count"])
                cache[gesture_id] = metadata
            self._cache = cache
            return dict(cache)

    # ------------------------------------------------------------------
    # Public library CRUD
    # ------------------------------------------------------------------
    @staticmethod
    def _normalize_hand(hand: Any) -> str:
        normalized = str(hand or "either").strip().lower()
        if normalized in {"left", "l"}:
            return "left"
        if normalized in {"right", "r"}:
            return "right"
        return "either"

    def list_gestures(self) -> List[Dict[str, Any]]:
        cache = self._load_cache()
        public = []
        active_capture_id = ""
        with self._lock:
            if self._capture_session and self._capture_session.active:
                active_capture_id = self._capture_session.gesture_id
        for gesture in cache.values():
            record = self._public_record(gesture)
            if record.get("gesture_id") == active_capture_id:
                record["status"] = "Capturing"
            public.append(record)
        return sorted(public, key=lambda item: item.get("created_at", ""), reverse=True)

    def get_gesture(self, gesture_id: Any, include_samples: bool = False) -> Optional[Dict[str, Any]]:
        safe_id = sanitize_gesture_name(gesture_id)
        cache = self._load_cache()
        item = cache.get(safe_id)
        if item is None:
            return None
        record = self._public_record(item)
        if include_samples:
            record["samples"] = [os.path.basename(path) for path in self._sample_paths(safe_id)]
        return record

    @staticmethod
    def _public_record(metadata: Dict[str, Any]) -> Dict[str, Any]:
        sample_count = int(metadata.get("sample_count", 0) or 0)
        target_samples = int(metadata.get("target_samples", 30) or 30)
        return {
            "gesture_id": metadata.get("gesture_id", ""),
            "gesture_name": metadata.get("gesture_name", ""),
            "description": metadata.get("description", ""),
            "hand": metadata.get("hand", "either"),
            "enabled": bool(metadata.get("enabled", True)),
            "sample_count": sample_count,
            "target_samples": target_samples,
            "status": metadata.get("status", "No samples"),
            "created_at": metadata.get("created_at", ""),
            "updated_at": metadata.get("updated_at", ""),
            "similarity_threshold": float(metadata.get("similarity_threshold", 0.85) or 0.85),
            "storage_folder": metadata.get("gesture_id", ""),
        }

    def start_capture(
        self,
        gesture_name: Any,
        description: Any = "",
        hand: Any = "either",
        target_samples: Any = 30,
        replace: bool = False,
    ) -> Dict[str, Any]:
        safe_id = sanitize_gesture_name(gesture_name)
        friendly_name = str(gesture_name or "").strip()
        target = _safe_int(target_samples, 30, 1, 300)
        normalized_hand = self._normalize_hand(hand)
        folder = self._gesture_dir(safe_id)

        with self._lock:
            if self._capture_session and self._capture_session.active:
                return {
                    "success": False,
                    "error": f"Capture already in progress for {self._capture_session.gesture_name}.",
                }

            if os.path.exists(folder):
                existing_count = self._sample_count(safe_id)
                if existing_count > 0 and not replace:
                    return {
                        "success": False,
                        "error": "A custom gesture with this folder already exists. Choose a different name or delete it first.",
                        "gesture_id": safe_id,
                    }
                if replace:
                    for path in self._sample_paths(safe_id):
                        os.remove(path)
            os.makedirs(folder, exist_ok=True)

            now = _utc_now()
            metadata = {
                "schema_version": self.SCHEMA_VERSION,
                "gesture_id": safe_id,
                "gesture_name": friendly_name,
                "description": str(description or "").strip(),
                "hand": normalized_hand,
                "enabled": True,
                "target_samples": target,
                "sample_count": 0,
                "status": "Capturing",
                "created_at": now,
                "updated_at": now,
                "similarity_threshold": self.similarity_threshold,
                "prototype": [],
                "feature_count": 0,
            }
            self._write_json_atomic(self._metadata_path(safe_id), metadata)
            self._invalidate_cache()

            self._capture_session = CaptureSession(
                gesture_id=safe_id,
                gesture_name=friendly_name,
                description=str(description or "").strip(),
                hand=normalized_hand,
                target_samples=target,
                started_at=now,
            )
            self._last_capture_time = 0.0
            self._prediction_buffer.clear()
            self._runtime = self._default_runtime(mode="capture")
            self._runtime.update({
                "capture_active": True,
                "capture_completed": False,
                "gesture_id": safe_id,
                "gesture_name": friendly_name,
                "expected_gesture_id": safe_id,
                "expected_gesture": friendly_name,
                "target_samples": target,
                "sample_count": 0,
                "capture_progress": 0.0,
                "detection_status": "Waiting for a hand in the camera frame.",
                "message": "Capture started. Keep the gesture visible until all samples are collected.",
            })

            return {"success": True, "gesture": self._public_record(metadata), "runtime": dict(self._runtime)}

    def stop_capture(self) -> Dict[str, Any]:
        with self._lock:
            if self._capture_session:
                self._capture_session.active = False
            self._capture_session = None
            if self._runtime.get("mode") == "capture":
                self._runtime["capture_active"] = False
                self._runtime["detection_status"] = "Capture stopped."
                self._runtime["message"] = "Capture stopped."
            return {"success": True, "runtime": dict(self._runtime)}

    def update_gesture(self, gesture_id: Any, changes: Dict[str, Any]) -> Dict[str, Any]:
        safe_id = sanitize_gesture_name(gesture_id)
        if not isinstance(changes, dict):
            return {"success": False, "error": "Update payload must be a JSON object."}
        with self._lock:
            metadata = self._load_metadata(safe_id)
            if metadata is None:
                return {"success": False, "error": "Custom gesture not found."}

            target_id = safe_id
            if "gesture_name" in changes or "name" in changes:
                new_name = str(changes.get("gesture_name", changes.get("name")) or "").strip()
                if not new_name:
                    return {"success": False, "error": "Gesture name is required."}
                new_id = sanitize_gesture_name(new_name)
                if new_id != safe_id:
                    new_folder = self._gesture_dir(new_id)
                    if os.path.exists(new_folder):
                        return {"success": False, "error": "Another gesture already uses that folder name."}
                    os.replace(self._gesture_dir(safe_id), new_folder)
                    target_id = new_id
                metadata["gesture_id"] = target_id
                metadata["gesture_name"] = new_name

            if "description" in changes:
                metadata["description"] = str(changes.get("description") or "").strip()
            if "hand" in changes:
                metadata["hand"] = self._normalize_hand(changes.get("hand"))
            if "enabled" in changes:
                metadata["enabled"] = _safe_bool(changes.get("enabled"), bool(metadata.get("enabled", True)))
            if "target_samples" in changes:
                metadata["target_samples"] = _safe_int(changes.get("target_samples"), int(metadata.get("target_samples", 30)), 1, 300)
            if "similarity_threshold" in changes:
                metadata["similarity_threshold"] = _safe_float(
                    changes.get("similarity_threshold"),
                    float(metadata.get("similarity_threshold", self.similarity_threshold) or self.similarity_threshold),
                    0.10,
                    0.99,
                )

            saved = self._save_metadata(metadata)
            if safe_id != target_id:
                self._rewrite_sample_gesture_ids(target_id)
            self._prediction_buffer.clear()
            return {"success": True, "gesture": self._public_record(saved)}

    def _rewrite_sample_gesture_ids(self, gesture_id: str) -> None:
        metadata = self._load_metadata(gesture_id)
        friendly_name = metadata.get("gesture_name", gesture_id) if metadata else gesture_id
        for path in self._sample_paths(gesture_id):
            try:
                sample = self._read_json(path)
                sample["gesture_id"] = gesture_id
                sample["gesture_name"] = friendly_name
                self._write_json_atomic(path, sample)
            except Exception as exc:
                logger.warning(f"Could not update sample metadata {path}: {exc}")
        self._invalidate_cache()

    def toggle_gesture(self, gesture_id: Any, enabled: Optional[bool] = None) -> Dict[str, Any]:
        safe_id = sanitize_gesture_name(gesture_id)
        with self._lock:
            metadata = self._load_metadata(safe_id)
            if metadata is None:
                return {"success": False, "error": "Custom gesture not found."}
            next_enabled = (
                not bool(metadata.get("enabled", True))
                if enabled is None
                else _safe_bool(enabled, bool(metadata.get("enabled", True)))
            )
            metadata["enabled"] = next_enabled
            saved = self._save_metadata(metadata)
            self._prediction_buffer.clear()
            if not next_enabled and self._runtime.get("expected_gesture_id") == safe_id:
                self._runtime["prediction"] = "Unknown"
                self._runtime["confidence"] = 0.0
                self._runtime["status"] = "DISABLED"
                self._runtime["detection_status"] = "This gesture is disabled and is excluded from recognition."
            return {"success": True, "gesture": self._public_record(saved)}

    def delete_gesture(self, gesture_id: Any) -> Dict[str, Any]:
        safe_id = sanitize_gesture_name(gesture_id)
        folder = self._gesture_dir(safe_id)
        base = os.path.abspath(self.base_dir)
        if os.path.commonpath([base, folder]) != base:
            return {"success": False, "error": "Invalid gesture path."}
        with self._lock:
            if not os.path.isdir(folder):
                return {"success": False, "error": "Custom gesture not found."}
            if self._capture_session and self._capture_session.gesture_id == safe_id:
                self._capture_session.active = False
                self._capture_session = None
            if self._runtime.get("expected_gesture_id") == safe_id:
                self._runtime = self._default_runtime()
            shutil.rmtree(folder)
            self._invalidate_cache()
            self._prediction_buffer.clear()
            return {"success": True, "gesture_id": safe_id}

    # ------------------------------------------------------------------
    # Runtime modes
    # ------------------------------------------------------------------
    def _default_runtime(self, mode: str = "idle") -> Dict[str, Any]:
        return {
            "mode": mode,
            "active": mode in {"capture", "live", "test"},
            "capture_active": False,
            "capture_completed": False,
            "hand_detected": False,
            "handedness": "none",
            "gesture_id": "",
            "gesture_name": "",
            "expected_gesture_id": "",
            "expected_gesture": "",
            "raw_prediction": "Unknown",
            "raw_gesture_id": "",
            "raw_similarity": 0.0,
            "prediction": "Unknown",
            "detected_gesture_id": "",
            "confidence": 0.0,
            "similarity": 0.0,
            "similarity_threshold": round(self.similarity_threshold * 100.0, 1),
            "stable_frames": 0,
            "required_stable_frames": self.stability_frames,
            "sample_count": 0,
            "target_samples": 0,
            "capture_progress": 0.0,
            "status": "IDLE",
            "detection_status": "Idle",
            "message": "Custom gesture recognition is idle.",
            "updated_at": _utc_now(),
            "recent_predictions": [],
        }

    def start_live_recognition(self) -> Dict[str, Any]:
        with self._lock:
            if self._capture_session and self._capture_session.active:
                return {"success": False, "error": "Finish or stop capture before starting live recognition."}
            self._prediction_buffer.clear()
            self._runtime = self._default_runtime(mode="live")
            self._runtime.update({
                "active": True,
                "status": "WAITING",
                "detection_status": "Live custom recognition is waiting for a hand.",
                "message": "Live Custom Gesture Recognition is active only on this page.",
            })
            return {"success": True, "runtime": dict(self._runtime)}

    def start_test(self, gesture_id: Any) -> Dict[str, Any]:
        safe_id = sanitize_gesture_name(gesture_id)
        with self._lock:
            if self._capture_session and self._capture_session.active:
                return {"success": False, "error": "Finish or stop capture before testing."}
            gesture = self.get_gesture(safe_id)
            if gesture is None:
                return {"success": False, "error": "Custom gesture not found."}
            if not gesture.get("enabled", True):
                return {"success": False, "error": "Disabled gestures do not participate in custom recognition. Enable it before testing."}
            if int(gesture.get("sample_count", 0)) < int(gesture.get("target_samples", 1) or 1):
                return {"success": False, "error": "Capture all requested samples before testing this gesture."}

            self._prediction_buffer.clear()
            self._runtime = self._default_runtime(mode="test")
            self._runtime.update({
                "active": True,
                "expected_gesture_id": safe_id,
                "expected_gesture": gesture.get("gesture_name", safe_id),
                "status": "WAITING",
                "detection_status": "Show the expected gesture to test it.",
                "message": "Test mode compares the live custom prediction with the selected gesture.",
            })
            return {"success": True, "runtime": dict(self._runtime)}

    def stop_recognition(self) -> Dict[str, Any]:
        with self._lock:
            if self._runtime.get("mode") in {"live", "test"}:
                self._runtime = self._default_runtime()
            self._prediction_buffer.clear()
            return {"success": True, "runtime": dict(self._runtime)}

    def notify_camera_off(self) -> None:
        with self._lock:
            if self._runtime.get("mode") in {"live", "test", "capture"}:
                self._runtime.update({
                    "hand_detected": False,
                    "handedness": "none",
                    "prediction": "Unknown",
                    "detected_gesture_id": "",
                    "confidence": 0.0,
                    "similarity": 0.0,
                    "raw_prediction": "Unknown",
                    "raw_gesture_id": "",
                    "raw_similarity": 0.0,
                    "stable_frames": 0,
                    "status": "NO_HAND",
                    "detection_status": "Camera is off. Enable the camera to capture or test custom gestures.",
                    "updated_at": _utc_now(),
                })
            self._prediction_buffer.clear()

    def mark_inactive(self) -> None:
        """Stop live/test recognition when the user leaves the section."""
        with self._lock:
            if self._runtime.get("mode") in {"live", "test"}:
                self._runtime = self._default_runtime()
            elif self._runtime.get("mode") == "capture":
                self._runtime.update({
                    "capture_active": bool(self._capture_session and self._capture_session.active),
                    "detection_status": "Capture is paused outside the Custom Gestures section.",
                    "message": "Return to Custom Gestures to continue capturing samples.",
                    "updated_at": _utc_now(),
                })
            self._prediction_buffer.clear()

    def get_runtime_status(self) -> Dict[str, Any]:
        with self._lock:
            status = dict(self._runtime)
            status["active"] = status.get("mode") in {"capture", "live", "test"}
            status["recent_predictions"] = list(self._recent_predictions)
            if self._capture_session:
                status["capture_active"] = bool(self._capture_session.active)
                status["gesture_id"] = self._capture_session.gesture_id
                status["gesture_name"] = self._capture_session.gesture_name
                status["target_samples"] = self._capture_session.target_samples
                metadata = self._load_metadata(self._capture_session.gesture_id)
                if metadata:
                    status["sample_count"] = int(metadata.get("sample_count", 0))
                    target = int(metadata.get("target_samples", self._capture_session.target_samples) or self._capture_session.target_samples)
                    status["capture_progress"] = round(min(100.0, (status["sample_count"] / max(1, target)) * 100.0), 1)
            return status

    # ------------------------------------------------------------------
    # Frame processing
    # ------------------------------------------------------------------
    def process_frame(self, left_hand: Any, right_hand: Any, active_module: Any = "") -> Dict[str, Any]:
        """Consume current MediaPipe landmarks only for the Custom Gestures page."""
        if str(active_module or "").lower() != "custom_gestures":
            self.mark_inactive()
            return self.get_runtime_status()

        with self._lock:
            mode = self._runtime.get("mode", "idle")
            session = self._capture_session

        if mode == "capture" and session and session.active:
            return self._process_capture_frame(left_hand, right_hand)
        if mode in {"live", "test"}:
            return self._process_recognition_frame(left_hand, right_hand)

        with self._lock:
            any_hand = left_hand is not None or right_hand is not None
            self._runtime.update({
                "hand_detected": any_hand,
                "handedness": "right" if right_hand is not None else ("left" if left_hand is not None else "none"),
                "status": "IDLE",
                "detection_status": "Ready. Create a gesture or start live recognition.",
                "updated_at": _utc_now(),
            })
            return dict(self._runtime)

    def _select_hand_for_capture(self, left_hand: Any, right_hand: Any, required_hand: str) -> Tuple[Optional[Any], str, str]:
        if required_hand == "left":
            return left_hand, "left" if left_hand is not None else "none", "Show your left hand."
        if required_hand == "right":
            return right_hand, "right" if right_hand is not None else "none", "Show your right hand."
        if right_hand is not None:
            return right_hand, "right", "Right hand selected."
        if left_hand is not None:
            return left_hand, "left", "Left hand selected."
        return None, "none", "Show either hand."

    def _process_capture_frame(self, left_hand: Any, right_hand: Any) -> Dict[str, Any]:
        with self._lock:
            session = self._capture_session
            if not session:
                return dict(self._runtime)
            current_count = self._sample_count(session.gesture_id)
            target = session.target_samples
            selected_hand, handedness, hint = self._select_hand_for_capture(left_hand, right_hand, session.hand)
            self._runtime.update({
                "hand_detected": selected_hand is not None,
                "handedness": handedness,
                "sample_count": current_count,
                "target_samples": target,
                "capture_progress": round(min(100.0, (current_count / max(1, target)) * 100.0), 1),
                "updated_at": _utc_now(),
            })

            if current_count >= target:
                self._complete_capture_locked(session)
                return dict(self._runtime)

            if selected_hand is None:
                self._runtime.update({
                    "status": "NO_HAND",
                    "detection_status": hint,
                    "message": f"Capturing {session.gesture_name}: waiting for hand.",
                })
                return dict(self._runtime)

            quality = feature_extractor.tracking_quality(selected_hand)
            if quality < self.min_tracking_quality:
                self._runtime.update({
                    "status": "LOW_QUALITY",
                    "detection_status": "Hand landmarks are unstable. Keep the full hand visible.",
                    "message": f"Tracking quality {quality * 100:.0f}% is below the capture requirement.",
                })
                return dict(self._runtime)

            now = time.monotonic()
            if (now - self._last_capture_time) < self.capture_interval:
                self._runtime.update({
                    "status": "CAPTURING",
                    "detection_status": f"Capturing... {current_count} / {target} samples",
                    "message": "Hold the gesture steady while samples are collected.",
                })
                return dict(self._runtime)

            next_index = current_count + 1
            sample = feature_extractor.sample_representation(selected_hand)
            sample.update({
                "schema_version": self.SCHEMA_VERSION,
                "gesture_id": session.gesture_id,
                "gesture_name": session.gesture_name,
                "description": session.description,
                "hand": session.hand,
                "captured_handedness": handedness,
                "captured_at": _utc_now(),
                "sample_index": next_index,
            })
            sample_path = os.path.join(self._gesture_dir(session.gesture_id), f"sample_{next_index:03d}.json")
            self._write_json_atomic(sample_path, sample)
            self._last_capture_time = now

            features = self._load_sample_features(session.gesture_id)
            prototype = mean_vector(features)
            metadata = self._load_metadata(session.gesture_id) or {}
            metadata.update({
                "gesture_id": session.gesture_id,
                "gesture_name": session.gesture_name,
                "description": session.description,
                "hand": session.hand,
                "sample_count": len(features),
                "target_samples": target,
                "status": "Capturing" if len(features) < target else "Ready",
                "prototype": prototype,
                "feature_count": len(prototype),
            })
            self._save_metadata(metadata)

            self._runtime.update({
                "status": "CAPTURING" if len(features) < target else "READY",
                "sample_count": len(features),
                "capture_progress": round(min(100.0, (len(features) / max(1, target)) * 100.0), 1),
                "detection_status": f"Capturing... {len(features)} / {target} samples",
                "message": f"Captured sample {len(features)} of {target}.",
            })
            if len(features) >= target:
                self._complete_capture_locked(session)
            return dict(self._runtime)

    def _complete_capture_locked(self, session: CaptureSession) -> None:
        metadata = self._load_metadata(session.gesture_id)
        if metadata is not None:
            features = self._load_sample_features(session.gesture_id)
            metadata.update({
                "sample_count": len(features),
                "status": "Ready",
                "prototype": mean_vector(features),
                "feature_count": len(mean_vector(features)),
            })
            self._save_metadata(metadata)
        self._capture_session = None
        self._runtime.update({
            "mode": "capture",
            "active": True,
            "capture_active": False,
            "capture_completed": True,
            "status": "READY",
            "detection_status": f"Capture complete for {session.gesture_name}.",
            "message": "Gesture is ready for Custom Gesture testing.",
            "capture_progress": 100.0,
            "updated_at": _utc_now(),
        })

    def _compatible_hand(self, gesture_hand: str, handedness: str) -> bool:
        return gesture_hand == "either" or gesture_hand == handedness

    def _feature_similarity(self, candidate_features: Sequence[float], gesture: Dict[str, Any]) -> Tuple[float, float]:
        candidate_variants: List[Sequence[float]] = [candidate_features]
        if self._normalize_hand(gesture.get("hand", "either")) == "either":
            # Either-hand gestures should be tolerant of left/right mirroring.
            candidate_variants.append(mirrored_feature_vector(candidate_features))

        distances: List[float] = []
        prototype = gesture.get("prototype", [])
        if isinstance(prototype, list) and prototype:
            distances.append(min(vector_distance(candidate, prototype) for candidate in candidate_variants))
        for sample_features in gesture.get("sample_features", [])[:120]:
            distances.append(min(vector_distance(candidate, sample_features) for candidate in candidate_variants))
        if not distances:
            return 0.0, 1.0
        best_distance = min(distances)
        similarity = max(0.0, min(1.0, 1.0 - (best_distance / max(self.max_match_distance, 1e-6))))
        return similarity, best_distance

    def _enabled_ready_gestures(self) -> List[Dict[str, Any]]:
        return [
            gesture for gesture in self._load_cache().values()
            if bool(gesture.get("enabled", True))
            and int(gesture.get("sample_count", 0) or 0) >= int(gesture.get("target_samples", 1) or 1)
            and (gesture.get("sample_features") or gesture.get("prototype"))
        ]

    def _best_match_for_hand(self, landmarks: Any, handedness: str) -> Optional[Dict[str, Any]]:
        if landmarks is None:
            return None
        if feature_extractor.tracking_quality(landmarks) < self.min_tracking_quality:
            return {
                "gesture_id": "",
                "gesture_name": "Unknown",
                "similarity": 0.0,
                "distance": 1.0,
                "threshold": self.similarity_threshold,
                "handedness": handedness,
                "reason": "LOW_QUALITY",
            }
        candidate_features = feature_extractor.feature_vector(landmarks)
        best: Optional[Dict[str, Any]] = None
        for gesture in self._enabled_ready_gestures():
            gesture_hand = self._normalize_hand(gesture.get("hand", "either"))
            if not self._compatible_hand(gesture_hand, handedness):
                continue
            similarity, distance = self._feature_similarity(candidate_features, gesture)
            threshold = float(gesture.get("similarity_threshold", self.similarity_threshold) or self.similarity_threshold)
            item = {
                "gesture_id": gesture.get("gesture_id", ""),
                "gesture_name": gesture.get("gesture_name", ""),
                "similarity": similarity,
                "distance": distance,
                "threshold": threshold,
                "handedness": handedness,
                "reason": "MATCH_CANDIDATE",
            }
            if best is None or similarity > float(best.get("similarity", 0.0)):
                best = item
        if best is None:
            return None
        return best

    def _best_match(self, left_hand: Any, right_hand: Any) -> Dict[str, Any]:
        candidates = []
        if right_hand is not None:
            candidate = self._best_match_for_hand(right_hand, "right")
            if candidate:
                candidates.append(candidate)
        if left_hand is not None:
            candidate = self._best_match_for_hand(left_hand, "left")
            if candidate:
                candidates.append(candidate)
        if not candidates:
            return {
                "gesture_id": "",
                "gesture_name": "Unknown",
                "similarity": 0.0,
                "distance": 1.0,
                "threshold": self.similarity_threshold,
                "handedness": "none",
                "reason": "NO_HAND" if left_hand is None and right_hand is None else "NO_ENABLED_GESTURES",
            }
        return max(candidates, key=lambda item: float(item.get("similarity", 0.0)))

    def _stable_prediction(self, raw_match: Dict[str, Any]) -> Dict[str, Any]:
        raw_similarity = float(raw_match.get("similarity", 0.0) or 0.0)
        raw_threshold = float(raw_match.get("threshold", self.similarity_threshold) or self.similarity_threshold)
        raw_label = str(raw_match.get("gesture_name") or "Unknown")
        raw_id = str(raw_match.get("gesture_id") or "")
        accepted = bool(raw_id and raw_similarity >= raw_threshold)
        buffer_item = {
            "gesture_id": raw_id if accepted else "",
            "gesture_name": raw_label if accepted else "Unknown",
            "similarity": raw_similarity if accepted else 0.0,
            "threshold": raw_threshold,
        }
        self._prediction_buffer.append(buffer_item)

        accepted_items = [item for item in self._prediction_buffer if item.get("gesture_id")]
        if not accepted_items:
            return {
                "gesture_id": "",
                "gesture_name": "Unknown",
                "confidence": 0.0,
                "stable_frames": 0,
            }

        counts = Counter(item["gesture_id"] for item in accepted_items)
        stable_id, stable_count = counts.most_common(1)[0]
        if stable_count < self.stability_frames:
            return {
                "gesture_id": "",
                "gesture_name": "Unknown",
                "confidence": 0.0,
                "stable_frames": stable_count,
            }

        matching_items = [item for item in accepted_items if item.get("gesture_id") == stable_id]
        avg_similarity = sum(float(item.get("similarity", 0.0)) for item in matching_items) / max(1, len(matching_items))
        gesture_name = next((item.get("gesture_name", "Unknown") for item in reversed(matching_items) if item.get("gesture_name")), "Unknown")
        if avg_similarity < self.similarity_threshold:
            return {
                "gesture_id": "",
                "gesture_name": "Unknown",
                "confidence": 0.0,
                "stable_frames": stable_count,
            }
        return {
            "gesture_id": stable_id,
            "gesture_name": gesture_name,
            "confidence": avg_similarity,
            "stable_frames": stable_count,
        }

    def _process_recognition_frame(self, left_hand: Any, right_hand: Any) -> Dict[str, Any]:
        with self._lock:
            any_hand = left_hand is not None or right_hand is not None
            if not any_hand:
                self._prediction_buffer.clear()
                self._runtime.update({
                    "hand_detected": False,
                    "handedness": "none",
                    "raw_prediction": "Unknown",
                    "raw_gesture_id": "",
                    "raw_similarity": 0.0,
                    "prediction": "Unknown",
                    "detected_gesture_id": "",
                    "confidence": 0.0,
                    "similarity": 0.0,
                    "stable_frames": 0,
                    "status": "NO_HAND",
                    "detection_status": "No hand detected.",
                    "updated_at": _utc_now(),
                })
                return dict(self._runtime)

            raw_match = self._best_match(left_hand, right_hand)
            raw_similarity = float(raw_match.get("similarity", 0.0) or 0.0)
            raw_threshold = float(raw_match.get("threshold", self.similarity_threshold) or self.similarity_threshold)
            raw_accepted = bool(raw_match.get("gesture_id") and raw_similarity >= raw_threshold)
            stable = self._stable_prediction(raw_match)
            detected_id = stable.get("gesture_id", "")
            detected_name = stable.get("gesture_name", "Unknown") if detected_id else "Unknown"
            confidence = float(stable.get("confidence", 0.0) or 0.0)
            mode = self._runtime.get("mode", "live")
            expected_id = str(self._runtime.get("expected_gesture_id") or "")

            if mode == "test" and expected_id:
                if detected_id and detected_id == expected_id:
                    status = "MATCH"
                    message = "Expected custom gesture matched."
                elif detected_id:
                    status = "NO_MATCH"
                    message = f"Detected {detected_name}, not the expected gesture."
                elif raw_accepted:
                    status = "STABILIZING"
                    message = "Gesture candidate found; waiting for stable frames."
                else:
                    status = "NO_MATCH"
                    message = "Current pose is below the custom gesture threshold."
            else:
                if detected_id:
                    status = "MATCH"
                    message = "Custom gesture recognized."
                elif raw_accepted:
                    status = "STABILIZING"
                    message = "Custom gesture candidate is stabilizing."
                else:
                    status = "UNKNOWN"
                    message = "No enabled custom gesture is close enough."

            if detected_id:
                recent = {
                    "gesture_id": detected_id,
                    "label": detected_name,
                    "confidence": round(confidence * 100.0, 1),
                    "time": time.strftime("%H:%M:%S"),
                }
                if not self._recent_predictions or self._recent_predictions[0].get("gesture_id") != detected_id:
                    self._recent_predictions.appendleft(recent)

            self._runtime.update({
                "hand_detected": True,
                "handedness": raw_match.get("handedness", "none"),
                "raw_prediction": raw_match.get("gesture_name", "Unknown") if raw_accepted else "Unknown",
                "raw_gesture_id": raw_match.get("gesture_id", "") if raw_accepted else "",
                "raw_similarity": round((raw_similarity if raw_accepted else 0.0) * 100.0, 1),
                "prediction": detected_name,
                "detected_gesture_id": detected_id,
                "confidence": round(confidence * 100.0, 1),
                "similarity": round(confidence * 100.0, 1),
                "similarity_threshold": round(raw_threshold * 100.0, 1),
                "stable_frames": int(stable.get("stable_frames", 0) or 0),
                "required_stable_frames": self.stability_frames,
                "status": status,
                "detection_status": message,
                "message": message,
                "updated_at": _utc_now(),
                "recent_predictions": list(self._recent_predictions),
            })
            return dict(self._runtime)

    def overlay_lines(self) -> List[str]:
        """Small status summary for the camera frame overlay."""
        status = self.get_runtime_status()
        mode = str(status.get("mode", "idle")).upper()
        if status.get("mode") == "capture":
            return [
                "CUSTOM GESTURE CAPTURE",
                f"Gesture: {status.get('gesture_name') or '--'}",
                f"Samples: {status.get('sample_count', 0)} / {status.get('target_samples', 0)}",
                str(status.get("detection_status") or ""),
            ]
        if status.get("mode") in {"live", "test"}:
            expected = status.get("expected_gesture")
            lines = [
                f"CUSTOM {mode} MODE",
                f"Detected: {status.get('prediction', 'Unknown')}",
                f"Confidence: {status.get('confidence', 0.0)}%",
                f"Status: {status.get('status', 'IDLE')}",
            ]
            if expected:
                lines.insert(1, f"Expected: {expected}")
            return lines
        return ["CUSTOM GESTURES", str(status.get("detection_status") or "Ready")]


custom_gesture_service = CustomGestureService()
