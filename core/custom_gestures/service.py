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
from core.custom_gestures.learning import CustomGestureLearningStore, best_similarity
from core.custom_gestures.gesture_dna import (
    compute_current_dna,
    compute_dna_from_samples,
    dna_match_level,
    dna_similarity,
    aggregate_dna,
)
from core.custom_gestures.gesture_coach import (
    coach_capture_feedback,
    coach_live_feedback,
    coach_test_feedback,
)
from core.custom_gestures.gesture_analytics import (
    compute_analytics,
    log_recognition_event,
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


def _default_event_sink(event: Dict[str, Any]) -> bool:
    """Mirror learning events into the existing MongoDB persistence layer.

    The repository degrades to a no-op when MongoDB is unavailable, so the
    custom gesture library keeps working purely from local storage.
    """
    try:
        from repositories.custom_gesture_learning_repository import (
            custom_gesture_learning_repository,
        )
    except Exception:
        return False
    try:
        return bool(custom_gesture_learning_repository.add_event(event))
    except Exception as exc:
        logger.warning(f"Could not persist custom gesture learning event: {exc}")
        return False


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
        event_sink: Optional[Any] = None,
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
        # Self-learning state stays inside the custom gesture storage tree and
        # is only reachable through this isolated service.
        self._learning = CustomGestureLearningStore(
            base_dir=self.base_dir,
            event_sink=event_sink if event_sink is not None else _default_event_sink,
            max_match_distance=self.max_match_distance,
            min_tracking_quality=self.min_tracking_quality,
        )
        self._last_stable_observation: Optional[Dict[str, Any]] = None
        self._last_detected_id = ""

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

    def _variation_paths(self, gesture_id: Any) -> List[str]:
        folder = self._gesture_dir(gesture_id)
        return sorted(glob.glob(os.path.join(folder, "variations", self._learning.VARIATION_GLOB)))

    def _variation_count(self, gesture_id: Any) -> int:
        return len(self._variation_paths(gesture_id))

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

    def _load_variation_features(self, gesture_id: Any) -> List[List[float]]:
        features: List[List[float]] = []
        for path in self._variation_paths(gesture_id):
            try:
                payload = self._read_json(path)
                sample_features = payload.get("features", [])
                if isinstance(sample_features, list) and sample_features:
                    features.append([float(value) for value in sample_features])
            except Exception as exc:
                logger.warning(f"Skipping unreadable custom gesture variation {path}: {exc}")
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
            "variation_count": _safe_int(metadata.get("variation_count"), 0, 0, 1000),
            "learning_stats": (
                metadata.get("learning_stats")
                if isinstance(metadata.get("learning_stats"), dict)
                else {}
            ),
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
                if entry.startswith(("_", ".")):
                    # Internal folders (e.g. "_learning") are never gestures.
                    continue
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
                metadata["variation_features"] = self._load_variation_features(gesture_id)
                metadata["sample_count"] = len(features) if features else self._sample_count(gesture_id)
                metadata["variation_count"] = len(metadata["variation_features"])
                try:
                    pending_clusters = self._learning.load_pending_variations(folder)
                    metadata["pending_variation_count"] = sum(
                        1 for cluster in pending_clusters
                        if int(cluster.get("count", 0) or 0) >= self._learning.evolution_min_observations
                    )
                except Exception:
                    metadata["pending_variation_count"] = 0
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
        stats = metadata.get("learning_stats") if isinstance(metadata.get("learning_stats"), dict) else {}
        detections = int(stats.get("detections", 0) or 0)
        corrections_caused = int(stats.get("corrections_caused", 0) or 0)
        recognition_accuracy = (
            round(detections / max(1, detections + corrections_caused) * 100.0, 1)
            if (detections + corrections_caused) > 0
            else None
        )
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
            "variation_count": int(metadata.get("variation_count", 0) or 0),
            "pending_variation_count": int(metadata.get("pending_variation_count", 0) or 0),
            "detection_count": detections,
            "correction_count": corrections_caused,
            "recognition_accuracy": recognition_accuracy,
        }

    def start_capture(
        self,
        gesture_name: Any,
        description: Any = "",
        hand: Any = "either",
        target_samples: Any = 30,
        replace: bool = False,
        candidate_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        safe_id = sanitize_gesture_name(gesture_name)
        friendly_name = str(gesture_name or "").strip()
        target = _safe_int(target_samples, 30, 1, 300)
        normalized_hand = self._normalize_hand(hand)
        folder = self._gesture_dir(safe_id)

        pending_learn = None
        if candidate_id:
            pending_learn = self._learning.get_pending_learn(str(candidate_id))
            if pending_learn is None:
                return {
                    "success": False,
                    "error": "The candidate samples are no longer available. Use a regular capture instead.",
                }

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

            if pending_learn is not None:
                self._learning.set_pending_learn_target(str(candidate_id), safe_id)
                preloaded = self._preload_candidate_samples_locked(self._capture_session, pending_learn)
                if preloaded:
                    self._runtime["message"] = (
                        f"Preloaded {preloaded} captured sample(s) from the gesture candidate."
                    )
                if self._sample_count(safe_id) >= target:
                    self._complete_capture_locked(self._capture_session)
                    return {"success": True, "gesture": self._public_record(self._load_metadata(safe_id) or metadata), "runtime": dict(self._runtime)}

            return {"success": True, "gesture": self._public_record(metadata), "runtime": dict(self._runtime)}

    def _preload_candidate_samples_locked(self, session: CaptureSession, pending_learn: Dict[str, Any]) -> int:
        """Write the candidate's captured samples into the new gesture folder."""
        written = 0
        target = session.target_samples
        for observation in pending_learn.get("observations", []):
            if written >= target:
                break
            sample = dict(observation.get("sample", {}) or {})
            if not sample.get("features"):
                continue
            written += 1
            sample.update({
                "schema_version": self.SCHEMA_VERSION,
                "gesture_id": session.gesture_id,
                "gesture_name": session.gesture_name,
                "description": session.description,
                "hand": session.hand,
                "captured_handedness": observation.get("handedness", ""),
                "captured_at": observation.get("at", _utc_now()),
                "sample_index": written,
                "source": "gesture_candidate",
            })
            sample_path = os.path.join(self._gesture_dir(session.gesture_id), f"sample_{written:03d}.json")
            self._write_json_atomic(sample_path, sample)
        if written:
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
        return written

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
            if self._last_detected_id == safe_id:
                self._last_detected_id = ""
            if self._last_stable_observation and self._last_stable_observation.get("gesture_id") == safe_id:
                self._last_stable_observation = None
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
            "correction": None,
        }

    def _reset_learning_runtime(self) -> None:
        self._prediction_buffer.clear()
        self._last_stable_observation = None
        self._last_detected_id = ""

    def start_live_recognition(self) -> Dict[str, Any]:
        with self._lock:
            if self._capture_session and self._capture_session.active:
                return {"success": False, "error": "Finish or stop capture before starting live recognition."}
            self._reset_learning_runtime()
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

            self._reset_learning_runtime()
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
            self._reset_learning_runtime()
            return {"success": True, "runtime": dict(self._runtime)}

    def notify_camera_off(self) -> None:
        with self._lock:
            self._last_stable_observation = None
            self._last_detected_id = ""
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
                    "correction": None,
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
            self._reset_learning_runtime()

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

    def match_frame_read_only(self, left_hand: Any, right_hand: Any) -> Dict[str, Any]:
        """Best enabled-custom-gesture match without mutating library state.

        Connect uses this read-only matcher so it can relay saved custom
        gestures while the Custom Gestures page capture/test/live runtime and
        its self-learning buffers stay completely untouched.
        """
        with self._lock:
            return self._best_match(left_hand, right_hand)

    def get_replay_sample(self, gesture_id: Any) -> Optional[Dict[str, Any]]:
        """Return an existing saved sample for gesture replay/preview.

        Reuses only data already stored by the Custom Gesture capture system
        (mean of the normalized landmark tracks across saved samples).  Returns
        None when the gesture does not exist or has no samples yet.
        """
        safe_id = sanitize_gesture_name(gesture_id)
        metadata = self._load_metadata(safe_id)
        if metadata is None:
            return None
        landmarks_sum: Dict[int, List[float]] = {}
        count = 0
        for path in self._sample_paths(safe_id):
            try:
                payload = self._read_json(path)
            except Exception:
                continue
            raw = payload.get("normalized_landmarks")
            if not isinstance(raw, list) or len(raw) < 21:
                raw = payload.get("raw_landmarks")
            if not isinstance(raw, list) or len(raw) < 21:
                continue
            for index, point in enumerate(raw[:21]):
                if not isinstance(point, dict):
                    continue
                try:
                    x = float(point.get("x", 0.0) or 0.0)
                    y = float(point.get("y", 0.0) or 0.0)
                    z = float(point.get("z", 0.0) or 0.0)
                except (TypeError, ValueError):
                    continue
                bucket = landmarks_sum.setdefault(index, [0.0, 0.0, 0.0])
                bucket[0] += x
                bucket[1] += y
                bucket[2] += z
            count += 1
        if not count:
            return {
                "gesture_id": safe_id,
                "gesture_name": metadata.get("gesture_name", safe_id),
                "description": metadata.get("description", ""),
                "sample_count": 0,
                "points": None,
            }
        points = []
        for index in range(21):
            bucket = landmarks_sum[index]
            points.append({
                "x": round(bucket[0] / count, 6),
                "y": round(bucket[1] / count, 6),
                "z": round(bucket[2] / count, 6),
            })
        return {
            "gesture_id": safe_id,
            "gesture_name": metadata.get("gesture_name", safe_id),
            "description": metadata.get("description", ""),
            "sample_count": count,
            "points": points,
        }

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
            # Update last observation for coach feedback during capture
            try:
                self._last_stable_observation = {
                    "gesture_id": session.gesture_id,
                    "features": feature_extractor.feature_vector(selected_hand),
                    "handedness": handedness,
                    "confidence": 0.0,
                    "at": time.monotonic(),
                    "landmarks": selected_hand,
                }
            except Exception:
                pass
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

            # Store current observation for coach feedback during capture
            self._last_stable_observation = {
                "gesture_id": session.gesture_id,
                "features": sample.get("features", []),
                "handedness": handedness,
                "confidence": 0.0,
                "at": time.monotonic(),
                "landmarks": selected_hand,
            }

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
        self._finalize_candidate_learning(session.gesture_id)

    def _finalize_candidate_learning(self, gesture_id: str) -> None:
        """Mark the source candidate learned once the gesture capture finished."""
        try:
            candidate_id = self._learning.finalize_candidate_for(gesture_id)
            if candidate_id:
                metadata = self._load_metadata(gesture_id)
                if metadata is not None:
                    stats = metadata.setdefault("learning_stats", {})
                    stats["candidates_learned"] = int(stats.get("candidates_learned", 0) or 0) + 1
                    stats["learned_from_candidate"] = candidate_id
                    self._save_metadata(metadata)
        except Exception as exc:
            logger.warning(f"Could not finalize learned candidate for {gesture_id}: {exc}")

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
        # Accepted personalized variations extend the gesture's representation.
        for variation_features in gesture.get("variation_features", [])[:40]:
            distances.append(min(vector_distance(candidate, variation_features) for candidate in candidate_variants))
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
                "features": candidate_features,
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
        # Per-hand feature vectors feed the self-learning hooks (correction
        # signatures, unknown clustering) without changing match results.
        hand_features: Dict[str, List[float]] = {}
        for candidate in candidates:
            handedness = str(candidate.get("handedness", ""))
            features = candidate.get("features")
            if handedness and features and handedness not in hand_features:
                hand_features[handedness] = features
        if not candidates:
            return {
                "gesture_id": "",
                "gesture_name": "Unknown",
                "similarity": 0.0,
                "distance": 1.0,
                "threshold": self.similarity_threshold,
                "handedness": "none",
                "reason": "NO_HAND" if left_hand is None and right_hand is None else "NO_ENABLED_GESTURES",
                "hand_features": hand_features,
            }
        result = max(candidates, key=lambda item: float(item.get("similarity", 0.0)))
        result = dict(result)
        result.pop("features", None)
        result["hand_features"] = hand_features
        return result

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
            "handedness": str(raw_match.get("handedness") or ""),
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
        stable_handedness = next((item.get("handedness", "") for item in reversed(matching_items) if item.get("handedness")), "")
        if avg_similarity < self.similarity_threshold:
            return {
                "gesture_id": "",
                "gesture_name": "Unknown",
                "confidence": 0.0,
                "stable_frames": stable_count,
                "handedness": stable_handedness,
            }
        return {
            "gesture_id": stable_id,
            "gesture_name": gesture_name,
            "confidence": avg_similarity,
            "stable_frames": stable_count,
            "handedness": stable_handedness,
        }

    def _process_recognition_frame(self, left_hand: Any, right_hand: Any) -> Dict[str, Any]:
        with self._lock:
            any_hand = left_hand is not None or right_hand is not None
            if not any_hand:
                self._prediction_buffer.clear()
                self._last_stable_observation = None
                self._last_detected_id = ""
                self._runtime.update({
                    "hand_detected": False,
                    "handedness": "none",
                    "correction": None,
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

            # ----------------------------------------------------------
            # Self-learning hooks (Custom Gestures scope only)
            #   * detection episodes + personalized correction memory
            #   * gesture evolution (valid variations)
            #   * unknown gesture clustering (live mode only)
            # These never modify the A-Z model or global state.
            # ----------------------------------------------------------
            hand_features = raw_match.get("hand_features") or {}
            obs_handedness = str(stable.get("handedness") or "")
            if not obs_handedness and hand_features:
                obs_handedness = next(iter(hand_features))
            obs_features = hand_features.get(obs_handedness)

            correction_info = None
            # Determine current landmarks for coach feedback
            obs_landmarks = None
            if obs_handedness == "right" and right_hand is not None:
                obs_landmarks = right_hand
            elif obs_handedness == "left" and left_hand is not None:
                obs_landmarks = left_hand
            elif right_hand is not None:
                obs_landmarks = right_hand
            elif left_hand is not None:
                obs_landmarks = left_hand

            if detected_id:
                if mode == "live":
                    self._record_detection_episode(detected_id)
                self._last_stable_observation = {
                    "gesture_id": detected_id,
                    "features": obs_features,
                    "handedness": obs_handedness,
                    "confidence": confidence,
                    "at": time.monotonic(),
                    "landmarks": obs_landmarks,
                }
                # Log recognition event for analytics (on-demand, not per-frame expensive)
                self._log_recognition_for_analytics(
                    detected_id, confidence, confidence, matched=True, source=mode
                )
                if mode == "live":
                    self._record_evolution_observation(
                        detected_id, obs_handedness, confidence, left_hand, right_hand
                    )
                    correction_info = self._maybe_apply_correction(
                        detected_id, detected_name, obs_features
                    )
                    if correction_info:
                        detected_id = correction_info["corrected_gesture_id"]
                        detected_name = correction_info["corrected_name"]
            else:
                self._last_stable_observation = None
                self._last_detected_id = ""
                # Log unknown recognition event for analytics
                if mode == "test" and expected_id:
                    self._log_recognition_for_analytics(
                        expected_id, confidence, confidence, matched=False, source="test"
                    )
                if mode == "live":
                    self._record_unknown_observation(left_hand, right_hand)

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
                "correction": correction_info or None,
            })
            return dict(self._runtime)

    # ------------------------------------------------------------------
    # Self-learning hooks (conservative; Custom Gestures scope only)
    # ------------------------------------------------------------------
    def _record_detection_episode(self, gesture_id: str) -> None:
        if gesture_id == self._last_detected_id:
            return
        self._last_detected_id = gesture_id
        self._update_gesture_stats(gesture_id, detections=1, last_detected_at=_utc_now())

    def _log_recognition_for_analytics(
        self, gesture_id: str, confidence: float, similarity: float,
        matched: bool = True, source: str = "live",
    ) -> None:
        """Log a recognition event to the gesture's analytics history.

        Only fires when the gesture_id changes or at most every 2 seconds
        to avoid excessive disk I/O on every camera frame.
        """
        now = time.monotonic()
        last_log = getattr(self, "_last_analytics_log_time", {}).get(gesture_id, 0.0)
        if now - last_log < 2.0 and matched:
            return
        try:
            metadata = self._load_metadata(gesture_id)
            if metadata is None:
                return
            log_recognition_event(metadata, confidence, similarity, matched, source)
            self._save_metadata(metadata)
            if not hasattr(self, "_last_analytics_log_time"):
                self._last_analytics_log_time = {}
            self._last_analytics_log_time[gesture_id] = now
        except Exception as exc:
            logger.warning(f"Analytics log skipped for {gesture_id}: {exc}")

    def _update_gesture_stats(self, gesture_id: str, **increments: Any) -> None:
        try:
            metadata = self._load_metadata(gesture_id)
            if metadata is None:
                return
            stats = metadata.get("learning_stats") if isinstance(metadata.get("learning_stats"), dict) else {}
            for key, value in increments.items():
                if key == "last_detected_at":
                    stats["last_detected_at"] = str(value)
                else:
                    stats[key] = int(stats.get(key, 0) or 0) + int(value)
            metadata["learning_stats"] = stats
            self._save_metadata(metadata)
        except Exception as exc:
            logger.warning(f"Could not update custom gesture stats for {gesture_id}: {exc}")

    def _record_unknown_observation(self, left_hand: Any, right_hand: Any) -> None:
        best = None
        for handedness, landmarks in (("right", right_hand), ("left", left_hand)):
            if landmarks is None:
                continue
            quality = feature_extractor.tracking_quality(landmarks)
            if best is None or quality > best[0]:
                best = (quality, handedness, landmarks)
        if best is None:
            return
        quality, handedness, landmarks = best
        if quality < self.min_tracking_quality:
            return
        try:
            features = feature_extractor.feature_vector(landmarks)
            self._learning.record_unknown_observation(
                handedness,
                feature_extractor.sample_representation(landmarks),
                features,
            )
        except Exception as exc:
            logger.warning(f"Unknown gesture clustering skipped: {exc}")

    def _record_evolution_observation(
        self,
        gesture_id: str,
        handedness: str,
        confidence: float,
        left_hand: Any,
        right_hand: Any,
    ) -> None:
        if confidence < self._learning.evolution_min_confidence:
            return
        if handedness == "right":
            landmarks = right_hand
        elif handedness == "left":
            landmarks = left_hand
        else:
            landmarks = right_hand if right_hand is not None else left_hand
        if landmarks is None:
            return
        try:
            gesture = self._load_cache().get(gesture_id)
            if not gesture:
                return
            references = list(gesture.get("sample_features", [])) + list(gesture.get("variation_features", []))
            if not references:
                return
            features = feature_extractor.feature_vector(landmarks)
            self._learning.record_evolution_observation(
                gesture_dir=self._gesture_dir(gesture_id),
                gesture_id=gesture_id,
                gesture_name=str(gesture.get("gesture_name", gesture_id)),
                hand=str(gesture.get("hand", "either")),
                features=features,
                sample_representation=feature_extractor.sample_representation(landmarks),
                confidence=confidence,
                prototype=gesture.get("prototype", []),
                reference_features=references,
            )
        except Exception as exc:
            logger.warning(f"Gesture evolution observation skipped: {exc}")

    def _maybe_apply_correction(
        self,
        detected_id: str,
        detected_name: str,
        features: Optional[List[float]],
    ) -> Optional[Dict[str, Any]]:
        if not features:
            return None
        try:
            found = self._learning.find_correction(predicted_gesture_id=detected_id, features=features)
        except Exception as exc:
            logger.warning(f"Correction memory lookup skipped: {exc}")
            return None
        if not found:
            return None
        target = self._load_cache().get(found["correct_gesture_id"])
        if not target or not target.get("enabled", True):
            return None
        if int(target.get("sample_count", 0) or 0) < int(target.get("target_samples", 1) or 1):
            return None
        info = {
            "predicted_gesture_id": detected_id,
            "corrected_gesture_id": found["correct_gesture_id"],
            "original_name": detected_name,
            "corrected_name": target.get("gesture_name", found["correct_gesture_id"]),
            "evidence": found["evidence"],
            "signature_similarity": round(float(found.get("best_signature_similarity", 0.0)) * 100.0, 1),
        }
        self._update_gesture_stats(found["correct_gesture_id"], corrections_applied=1)
        self._learning.emit_event("correction_applied", info, gesture_id=found["correct_gesture_id"])
        return info

    # ------------------------------------------------------------------
    # Public self-learning API (routed only from the Custom Gestures page)
    # ------------------------------------------------------------------
    def learning_status(self) -> Dict[str, Any]:
        with self._lock:
            return {"success": True, "learning": self._learning.learning_status()}

    def prepare_candidate_learning(self, candidate_id: Any) -> Dict[str, Any]:
        with self._lock:
            result = self._learning.prepare_learn(str(candidate_id))
            if result.get("success"):
                self._prediction_buffer.clear()
            return result

    def ignore_candidate(self, candidate_id: Any) -> Dict[str, Any]:
        with self._lock:
            return self._learning.ignore_candidate(str(candidate_id))

    def record_correction(
        self,
        predicted_gesture_id: Any,
        correct_gesture_id: Any,
    ) -> Dict[str, Any]:
        with self._lock:
            try:
                safe_predicted = sanitize_gesture_name(predicted_gesture_id)
                safe_correct = sanitize_gesture_name(correct_gesture_id)
            except ValueError as exc:
                return {"success": False, "error": str(exc)}
            if safe_predicted == safe_correct:
                return {"success": False, "error": "Choose a different gesture for the correction."}
            if self._load_metadata(safe_predicted) is None:
                return {"success": False, "error": "The predicted gesture is not a saved custom gesture."}
            correct = self._load_metadata(safe_correct)
            if correct is None:
                return {"success": False, "error": "Correction target gesture not found."}
            if not correct.get("enabled", True):
                return {"success": False, "error": "The correction target must be an enabled gesture."}
            if int(correct.get("sample_count", 0) or 0) < int(correct.get("target_samples", 1) or 1):
                return {"success": False, "error": "The correction target must have captured all requested samples."}

            signature: Optional[List[float]] = None
            handedness = ""
            confidence = 0.0
            observation = self._last_stable_observation
            if observation and observation.get("gesture_id") == safe_predicted:
                age = time.monotonic() - float(observation.get("at", 0.0) or 0.0)
                if age <= 20.0 and observation.get("features"):
                    signature = list(observation["features"])
                    handedness = str(observation.get("handedness", ""))
                    confidence = float(observation.get("confidence", 0.0) or 0.0)

            correction = self._learning.add_correction(
                predicted_gesture_id=safe_predicted,
                correct_gesture_id=safe_correct,
                signature=signature,
                handedness=handedness,
                confidence=confidence,
            )
            self._update_gesture_stats(safe_predicted, corrections_caused=1)
            self._update_gesture_stats(safe_correct, corrections_received=1)
            return {
                "success": True,
                "correction": {
                    "correction_id": correction["correction_id"],
                    "predicted_gesture_id": safe_predicted,
                    "correct_gesture_id": safe_correct,
                    "stored_signature": bool(signature),
                },
                "message": "Correction saved to your personalized Custom Gesture memory.",
            }

    def list_corrections(self, limit: int = 20) -> Dict[str, Any]:
        with self._lock:
            corrections = self._learning.list_corrections(limit)
            for item in corrections:
                item.pop("signature", None)
            return {"success": True, "corrections": corrections}

    def accept_pending_variations(self, gesture_id: Any) -> Dict[str, Any]:
        with self._lock:
            safe_id = sanitize_gesture_name(gesture_id)
            metadata = self._load_metadata(safe_id)
            if metadata is None:
                return {"success": False, "error": "Custom gesture not found."}
            accepted = self._learning.accept_pending_variations(
                gesture_dir=self._gesture_dir(safe_id),
                gesture_id=safe_id,
                gesture_name=str(metadata.get("gesture_name", safe_id)),
                existing_variation_count=self._variation_count(safe_id),
            )
            if not accepted:
                return {
                    "success": False,
                    "error": "No pending variations are ready yet. Keep using the gesture; repeated stable variations will be proposed.",
                }
            combined = self._load_sample_features(safe_id) + self._load_variation_features(safe_id)
            prototype = mean_vector(combined)
            metadata.update({
                "prototype": prototype,
                "feature_count": len(prototype),
                "variation_count": self._variation_count(safe_id),
            })
            saved = self._save_metadata(metadata)
            self._update_gesture_stats(safe_id, variations_accepted=len(accepted))
            self._prediction_buffer.clear()
            return {
                "success": True,
                "accepted": accepted,
                "gesture": self._public_record(saved),
            }

    def discard_pending_variations(self, gesture_id: Any) -> Dict[str, Any]:
        with self._lock:
            safe_id = sanitize_gesture_name(gesture_id)
            if self._load_metadata(safe_id) is None:
                return {"success": False, "error": "Custom gesture not found."}
            discarded = self._learning.discard_pending_variations(self._gesture_dir(safe_id), safe_id)
            if discarded:
                self._update_gesture_stats(safe_id, variations_ignored=1)
                self._invalidate_cache()
            return {"success": True, "discarded": discarded}

    def evolution_details(self, gesture_id: Any) -> Dict[str, Any]:
        with self._lock:
            safe_id = sanitize_gesture_name(gesture_id)
            metadata = self._load_metadata(safe_id)
            if metadata is None:
                return {"success": False, "error": "Custom gesture not found."}
            gesture = self._load_cache().get(safe_id) or {}
            prototype = gesture.get("prototype") or metadata.get("prototype", [])
            mirror = self._normalize_hand(metadata.get("hand", "either")) == "either"

            pending = []
            for cluster in self._learning.load_pending_variations(self._gesture_dir(safe_id)):
                similarity = (
                    best_similarity(cluster.get("centroid", []), [prototype], self.max_match_distance, mirror=mirror)
                    if prototype
                    else 0.0
                )
                pending.append({
                    "cluster_id": cluster.get("cluster_id", ""),
                    "observed_count": int(cluster.get("count", 0) or 0),
                    "similarity_to_gesture": round(similarity * 100.0, 1),
                    "ready": int(cluster.get("count", 0) or 0) >= self._learning.evolution_min_observations,
                    "first_seen": cluster.get("first_seen", ""),
                    "last_seen": cluster.get("last_seen", ""),
                })

            variations = []
            for path in self._variation_paths(safe_id):
                try:
                    payload = self._read_json(path)
                    similarity = (
                        best_similarity(payload.get("features", []), [prototype], self.max_match_distance, mirror=mirror)
                        if prototype
                        else 0.0
                    )
                    variations.append({
                        "variation_id": payload.get("variation_id", os.path.basename(path)),
                        "accepted_at": payload.get("accepted_at", ""),
                        "observed_count": int(payload.get("observed_count", 0) or 0),
                        "similarity_to_gesture": round(similarity * 100.0, 1),
                    })
                except Exception:
                    continue

            stats = metadata.get("learning_stats") if isinstance(metadata.get("learning_stats"), dict) else {}
            detections = int(stats.get("detections", 0) or 0)
            caused = int(stats.get("corrections_caused", 0) or 0)
            accuracy = (
                round(detections / max(1, detections + caused) * 100.0, 1)
                if (detections + caused) > 0
                else None
            )
            corrections = self._learning.corrections_for_gesture(safe_id)
            for group in corrections.values():
                for item in group:
                    item.pop("signature", None)
            return {
                "success": True,
                "evolution": {
                    "gesture_id": safe_id,
                    "gesture_name": metadata.get("gesture_name", safe_id),
                    "original_samples": int(metadata.get("sample_count", 0) or 0),
                    "target_samples": int(metadata.get("target_samples", 0) or 0),
                    "variations": variations,
                    "pending": pending,
                    "stats": {
                        "detections": detections,
                        "corrections_caused": caused,
                        "corrections_received": int(stats.get("corrections_received", 0) or 0),
                        "corrections_applied": int(stats.get("corrections_applied", 0) or 0),
                        "variations_accepted": int(stats.get("variations_accepted", 0) or 0),
                        "variations_ignored": int(stats.get("variations_ignored", 0) or 0),
                        "candidates_learned": int(stats.get("candidates_learned", 0) or 0),
                        "recognition_accuracy": accuracy,
                        "last_detected_at": stats.get("last_detected_at", ""),
                    },
                    "corrections": corrections,
                },
            }

    # ------------------------------------------------------------------
    # Gesture DNA (Feature 1)
    # ------------------------------------------------------------------
    def gesture_dna(self, gesture_id: Any) -> Dict[str, Any]:
        """Compute the stored Gesture DNA profile from actual samples."""
        safe_id = sanitize_gesture_name(gesture_id)
        metadata = self._load_metadata(safe_id)
        if metadata is None:
            return {"success": False, "error": "Custom gesture not found."}
        sample_paths = self._sample_paths(safe_id)
        variation_paths = self._variation_paths(safe_id)
        dna = compute_dna_from_samples(sample_paths, self._read_json)
        if not dna or dna.get("sample_count", 0) == 0:
            return {"success": False, "error": "No samples available to compute DNA."}
        return {
            "success": True,
            "dna": dna,
            "gesture_id": safe_id,
            "gesture_name": metadata.get("gesture_name", safe_id),
            "sample_count": dna.get("sample_count", 0),
        }

    def current_dna_and_comparison(self, gesture_id: Any) -> Dict[str, Any]:
        """Compare the current observation's DNA against the stored DNA.

        Uses the last stable observation if available, otherwise returns
        the stored DNA only.
        """
        safe_id = sanitize_gesture_name(gesture_id)
        metadata = self._load_metadata(safe_id)
        if metadata is None:
            return {"success": False, "error": "Custom gesture not found."}
        sample_paths = self._sample_paths(safe_id)
        stored_dna = compute_dna_from_samples(sample_paths, self._read_json)
        if not stored_dna or stored_dna.get("sample_count", 0) == 0:
            return {"success": False, "error": "No samples to compute stored DNA."}

        observation = self._last_stable_observation
        if not observation or not observation.get("features"):
            return {
                "success": True,
                "stored_dna": stored_dna,
                "current_dna": None,
                "similarity": None,
                "match_level": None,
                "gesture_id": safe_id,
            }

        # Reconstruct landmarks from the stored observation features
        # Since we store the full raw_landmarks in samples, but the observation
        # only has features, we compare feature vectors via DNA dimensions
        # We need actual landmarks; use them from the current stable observation
        current_landmarks = observation.get("landmarks")
        if not current_landmarks:
            # Fallback: compute similarity from stored features directly
            obs_features = observation.get("features", [])
            prototype = metadata.get("prototype", [])
            if prototype and obs_features:
                dist = vector_distance(obs_features, prototype)
                sim = max(0.0, min(1.0, 1.0 - (dist / max(self.max_match_distance, 1e-6))))
                return {
                    "success": True,
                    "stored_dna": stored_dna,
                    "current_dna": None,
                    "feature_similarity": round(sim * 100.0, 1),
                    "similarity": round(sim * 100.0, 1),
                    "match_level": dna_match_level(sim * 100.0),
                    "gesture_id": safe_id,
                }
            return {
                "success": True,
                "stored_dna": stored_dna,
                "current_dna": None,
                "similarity": None,
                "match_level": None,
                "gesture_id": safe_id,
            }

        current_dna = compute_current_dna(current_landmarks)
        sim = dna_similarity(current_dna, stored_dna)
        return {
            "success": True,
            "stored_dna": stored_dna,
            "current_dna": current_dna,
            "similarity": round(sim, 1),
            "match_level": dna_match_level(sim),
            "gesture_id": safe_id,
        }

    # ------------------------------------------------------------------
    # AI Gesture Coach (Feature 2)
    # ------------------------------------------------------------------
    def coach_feedback(self, gesture_id: Any) -> Dict[str, Any]:
        """Return AI coach feedback for the given gesture.

        During test mode, compares current vs stored DNA.
        During capture mode, evaluates sample quality.
        During live/idle, provides general tips.
        """
        safe_id = sanitize_gesture_name(gesture_id)
        metadata = self._load_metadata(safe_id)
        if metadata is None:
            return {"success": False, "error": "Custom gesture not found."}

        mode = self._runtime.get("mode", "idle")
        sample_paths = self._sample_paths(safe_id)
        stored_dna = compute_dna_from_samples(sample_paths, self._read_json)

        if mode == "capture":
            # Capture coach: evaluate current hand for sample quality
            observation = self._last_stable_observation
            if observation and observation.get("landmarks"):
                fb = coach_capture_feedback(observation["landmarks"], stored_dna)
                return {"success": True, "coach": fb, "mode": "capture"}
            # Fallback: no observation yet
            return {
                "success": True,
                "coach": {
                    "sample_quality": 0.0,
                    "issues": ["Waiting for hand detection..."],
                    "guidance": ["Show your hand to the camera to start capture."],
                    "dimensions": {},
                },
                "mode": "capture",
            }

        if mode in ("test", "live"):
            # Test/Live coach: compare current vs stored
            runtime = self._runtime
            confidence = float(runtime.get("confidence", 0.0) or 0.0)
            detected_name = runtime.get("prediction", "Unknown")
            expected_name = metadata.get("gesture_name", safe_id)

            observation = self._last_stable_observation
            if observation and observation.get("landmarks") and stored_dna and stored_dna.get("sample_count", 0) > 0:
                fb = coach_test_feedback(
                    current_landmarks=observation["landmarks"],
                    stored_dna=stored_dna,
                    expected_gesture_name=expected_name,
                    detected_gesture_name=detected_name,
                    confidence=confidence,
                    similarity_pct=confidence,  # Use confidence as proxy
                )
                return {"success": True, "coach": fb, "mode": mode}

            # Feature-level comparison without landmarks
            obs_features = observation.get("features") if observation else None
            prototype = metadata.get("prototype", [])
            if obs_features and prototype:
                dist = vector_distance(obs_features, prototype)
                sim = max(0.0, min(1.0, 1.0 - (dist / max(self.max_match_distance, 1e-6))))
                sim_pct = round(sim * 100.0, 1)
                tips = []
                if sim_pct >= 90.0:
                    feedback = "Excellent match."
                elif sim_pct >= 75.0:
                    feedback = "Good match."
                elif sim_pct >= 60.0:
                    feedback = "Partial match. Adjust your gesture."
                    tips.append("Try to match the saved gesture shape more closely.")
                else:
                    feedback = "Low match. The gesture differs significantly from the stored profile."
                    tips.append("Consider recapturing samples with more variation.")
                return {
                    "success": True,
                    "coach": {
                        "similarity": sim_pct,
                        "match_level": dna_match_level(sim_pct),
                        "feedback": feedback,
                        "tips": tips,
                    },
                    "mode": mode,
                }

            return {
                "success": True,
                "coach": {
                    "feedback": "Show the gesture to get coach feedback.",
                    "tips": ["Hold the gesture steady in front of the camera."],
                },
                "mode": mode,
            }

        # Idle mode
        return {
            "success": True,
            "coach": {
                "feedback": "Start a test or live recognition to get coach feedback.",
                "tips": [],
            },
            "mode": "idle",
        }

    def capture_coach_feedback(self) -> Dict[str, Any]:
        """Real-time coach feedback during capture (no gesture_id needed).

        Uses the current runtime observation and the gesture being captured.
        """
        session = self._capture_session
        if not session:
            return {"success": False, "error": "No capture session active."}

        safe_id = session.gesture_id
        metadata = self._load_metadata(safe_id)
        sample_paths = self._sample_paths(safe_id)
        stored_dna = compute_dna_from_samples(sample_paths, self._read_json) if sample_paths else None

        # Try to get current landmarks from the observation
        observation = self._last_stable_observation
        if observation and observation.get("landmarks"):
            fb = coach_capture_feedback(observation["landmarks"], stored_dna)
            return {"success": True, "coach": fb, "gesture_id": safe_id}

        # No observation yet
        return {
            "success": True,
            "coach": {
                "sample_quality": 0.0,
                "issues": ["No hand detected yet."],
                "guidance": ["Show your hand to the camera to begin."],
                "dimensions": {},
            },
            "gesture_id": safe_id,
        }

    # ------------------------------------------------------------------
    # Gesture Quality & Analytics (Feature 3)
    # ------------------------------------------------------------------
    def gesture_analytics(self, gesture_id: Any) -> Dict[str, Any]:
        """Compute analytics and quality metrics for a gesture."""
        safe_id = sanitize_gesture_name(gesture_id)
        metadata = self._load_metadata(safe_id)
        if metadata is None:
            return {"success": False, "error": "Custom gesture not found."}
        sample_paths = self._sample_paths(safe_id)
        variation_paths = self._variation_paths(safe_id)
        return compute_analytics(metadata, sample_paths, variation_paths, self._read_json)

    # ------------------------------------------------------------------
    # Combined gesture details (DNA + Coach + Analytics in one call)
    # ------------------------------------------------------------------
    def gesture_full_details(self, gesture_id: Any) -> Dict[str, Any]:
        """Return a comprehensive gesture detail view with DNA, coach, and analytics.

        This is the main endpoint for the enhanced Custom Gesture details UI.
        Analytics are computed on demand (not per-frame).
        """
        safe_id = sanitize_gesture_name(gesture_id)
        metadata = self._load_metadata(safe_id)
        if metadata is None:
            return {"success": False, "error": "Custom gesture not found."}

        sample_paths = self._sample_paths(safe_id)
        variation_paths = self._variation_paths(safe_id)

        # DNA
        dna = compute_dna_from_samples(sample_paths, self._read_json)

        # Analytics
        analytics_result = compute_analytics(metadata, sample_paths, variation_paths, self._read_json)
        analytics = analytics_result.get("analytics", {})

        # Coach feedback
        coach = self.coach_feedback(safe_id)
        coach_data = coach.get("coach", {}) if coach.get("success") else {}

        # DNA comparison with current observation
        dna_comparison = self.current_dna_and_comparison(safe_id)

        gesture = self._public_record(metadata)
        gesture.update({
            "sample_features_count": len(self._load_sample_features(safe_id)),
            "variation_features_count": len(self._load_variation_features(safe_id)),
        })

        return {
            "success": True,
            "gesture": gesture,
            "dna": dna,
            "analytics": analytics,
            "coach": coach_data,
            "dna_comparison": {
                "stored_dna": dna_comparison.get("stored_dna"),
                "current_dna": dna_comparison.get("current_dna"),
                "similarity": dna_comparison.get("similarity"),
                "match_level": dna_comparison.get("match_level"),
                "feature_similarity": dna_comparison.get("feature_similarity"),
            },
        }

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
