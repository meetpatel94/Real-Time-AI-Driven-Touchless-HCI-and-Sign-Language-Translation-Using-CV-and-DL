"""Filesystem-backed self-learning state for the Custom Gesture system.

This module owns the three conservative learning loops that live *only* inside
the custom gesture library (never the A-Z model, global recognition state, or
any other workspace):

1. Unknown gesture discovery  ->  ``_learning/unknown_candidates.json``
   Repeated, stable, high-quality unknown observations are clustered into
   "New Gesture Candidate" records.  They are never promoted to a gesture on
   their own; the user must click *Learn Gesture* (or *Ignore*).

2. Mistake memory             ->  ``_learning/corrections.json``
   User corrections of custom predictions are stored with the feature
   signature of the corrected frame.  Later similar observations may be
   relabelled by the *custom* matcher only, and only with sufficient evidence.

3. Gesture evolution          ->  ``<gesture>/variations/pending.json``
   High-confidence, stable, sufficiently novel re-observations of an existing
   gesture accumulate as pending variation clusters.  They only become
   permanent ``variation_*.json`` files after the user accepts them.

MongoDB (when available) receives mirrored *events* through the optional
``event_sink`` callback so learning history and statistics can be persisted
with the project's existing persistence architecture.  Actual gesture samples
stay in the custom gesture storage tree.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
import math
import os
import tempfile
import threading
from typing import Any, Callable, Deque, Dict, List, Optional, Sequence, Tuple
from uuid import uuid4

from config import Config
from core.custom_gestures.feature_extractor import (
    feature_extractor,
    mean_vector,
    mirrored_feature_vector,
    vector_distance,
)
from services.logging_service import logger

LearningEventSink = Callable[[Dict[str, Any]], bool]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _utc_now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: Any) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except (TypeError, ValueError):
        return None


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


def best_similarity(
    features: Sequence[float],
    references: Sequence[Sequence[float]],
    max_distance: float,
    mirror: bool = False,
) -> float:
    """Similarity (0..1) of ``features`` to the closest reference vector.

    Mirroring is applied to the candidate only, mirroring how the custom
    matcher treats either-hand gestures and hand-agnostic unknown clusters.
    """
    if not features or not references:
        return 0.0
    variants: List[Sequence[float]] = [features]
    if mirror:
        variants.append(mirrored_feature_vector(features))
    best = 0.0
    denom = max(float(max_distance), 1e-6)
    for reference in references:
        if not reference:
            continue
        closest = min(vector_distance(variant, reference) for variant in variants)
        similarity = max(0.0, min(1.0, 1.0 - (closest / denom)))
        if similarity > best:
            best = similarity
    return best


@dataclass
class LearningSummary:
    """Compact, UI-safe view of one unknown gesture candidate."""

    candidate_id: str
    observed_count: int
    cluster_similarity: float
    handedness: str
    first_seen: str
    last_seen: str
    min_observations: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "observed_count": int(self.observed_count),
            "cluster_similarity": round(self.cluster_similarity * 100.0, 1),
            "handedness": self.handedness,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "min_observations": int(self.min_observations),
            "ready": int(self.observed_count) >= int(self.min_observations),
        }


class CustomGestureLearningStore:
    """Conservative self-learning state for the isolated custom gesture library."""

    LEARNING_FOLDER = "_learning"
    CANDIDATES_FILE = "unknown_candidates.json"
    CORRECTIONS_FILE = "corrections.json"
    PENDING_LEARN_FILE = "pending_learn.json"
    PENDING_VARIATIONS_FILE = "pending.json"
    VARIATION_GLOB = "variation_*.json"
    SCHEMA_VERSION = 1

    def __init__(
        self,
        base_dir: str,
        event_sink: Optional[LearningEventSink] = None,
        max_match_distance: Optional[float] = None,
        min_tracking_quality: Optional[float] = None,
        candidate_min_observations: Optional[int] = None,
        candidate_cluster_threshold: Optional[float] = None,
        candidate_ttl_seconds: Optional[float] = None,
        candidate_stability_window: Optional[int] = None,
        candidate_stability_threshold: Optional[float] = None,
        candidate_max_observations: Optional[int] = None,
        ignored_signature_ttl_days: Optional[float] = None,
        correction_min_evidence: Optional[float] = None,
        correction_signature_threshold: Optional[float] = None,
        correction_max_age_days: Optional[float] = None,
        correction_max_stored: Optional[int] = None,
        evolution_min_confidence: Optional[float] = None,
        evolution_min_similarity: Optional[float] = None,
        evolution_max_similarity: Optional[float] = None,
        evolution_novelty_threshold: Optional[float] = None,
        evolution_min_observations: Optional[int] = None,
        evolution_max_pending_clusters: Optional[int] = None,
    ):
        self.base_dir = os.path.abspath(base_dir)
        self.learning_dir = os.path.join(self.base_dir, self.LEARNING_FOLDER)
        self.event_sink = event_sink

        self.max_match_distance = _safe_float(
            max_match_distance if max_match_distance is not None else getattr(Config, "CUSTOM_GESTURE_MAX_MATCH_DISTANCE", 0.45),
            0.45, 0.05, 2.0,
        )
        self.min_tracking_quality = _safe_float(
            min_tracking_quality if min_tracking_quality is not None else getattr(Config, "CUSTOM_GESTURE_MIN_TRACKING_QUALITY", 0.90),
            0.90, 0.10, 1.0,
        )
        self.candidate_min_observations = _safe_int(
            candidate_min_observations if candidate_min_observations is not None else getattr(Config, "CUSTOM_GESTURE_CANDIDATE_MIN_OBSERVATIONS", 8),
            8, 2, 500,
        )
        self.candidate_cluster_threshold = _safe_float(
            candidate_cluster_threshold if candidate_cluster_threshold is not None else getattr(Config, "CUSTOM_GESTURE_CANDIDATE_CLUSTER_THRESHOLD", 0.80),
            0.80, 0.10, 0.99,
        )
        self.candidate_ttl_seconds = _safe_float(
            candidate_ttl_seconds if candidate_ttl_seconds is not None else getattr(Config, "CUSTOM_GESTURE_CANDIDATE_TTL_SECONDS", 300),
            300.0, 10.0, 86400.0,
        )
        self.candidate_stability_window = _safe_int(
            candidate_stability_window if candidate_stability_window is not None else getattr(Config, "CUSTOM_GESTURE_CANDIDATE_STABILITY_WINDOW", 8),
            8, 3, 60,
        )
        self.candidate_stability_threshold = _safe_float(
            candidate_stability_threshold if candidate_stability_threshold is not None else getattr(Config, "CUSTOM_GESTURE_CANDIDATE_STABILITY_THRESHOLD", 0.05),
            0.05, 0.001, 1.0,
        )
        self.candidate_max_observations = _safe_int(
            candidate_max_observations if candidate_max_observations is not None else getattr(Config, "CUSTOM_GESTURE_CANDIDATE_MAX_OBSERVATIONS", 48),
            48, 4, 500,
        )
        self.ignored_signature_ttl_days = _safe_float(
            ignored_signature_ttl_days if ignored_signature_ttl_days is not None else getattr(Config, "CUSTOM_GESTURE_IGNORED_SIGNATURE_TTL_DAYS", 14),
            14.0, 0.0, 3650.0,
        )
        self.correction_min_evidence = _safe_float(
            correction_min_evidence if correction_min_evidence is not None else getattr(Config, "CUSTOM_GESTURE_CORRECTION_MIN_EVIDENCE", 1.0),
            1.0, 0.5, 10.0,
        )
        self.correction_signature_threshold = _safe_float(
            correction_signature_threshold if correction_signature_threshold is not None else getattr(Config, "CUSTOM_GESTURE_CORRECTION_SIGNATURE_THRESHOLD", 0.85),
            0.85, 0.10, 0.99,
        )
        self.correction_max_age_days = _safe_float(
            correction_max_age_days if correction_max_age_days is not None else getattr(Config, "CUSTOM_GESTURE_CORRECTION_MAX_AGE_DAYS", 90),
            90.0, 0.0, 3650.0,
        )
        self.correction_max_stored = _safe_int(
            correction_max_stored if correction_max_stored is not None else getattr(Config, "CUSTOM_GESTURE_CORRECTION_MAX_STORED", 500),
            500, 10, 100000,
        )
        self.evolution_min_confidence = _safe_float(
            evolution_min_confidence if evolution_min_confidence is not None else getattr(Config, "CUSTOM_GESTURE_EVOLUTION_MIN_CONFIDENCE", 0.88),
            0.88, 0.0, 1.0,
        )
        self.evolution_min_similarity = _safe_float(
            evolution_min_similarity if evolution_min_similarity is not None else getattr(Config, "CUSTOM_GESTURE_EVOLUTION_MIN_SIMILARITY", 0.75),
            0.75, 0.0, 1.0,
        )
        self.evolution_max_similarity = _safe_float(
            evolution_max_similarity if evolution_max_similarity is not None else getattr(Config, "CUSTOM_GESTURE_EVOLUTION_MAX_SIMILARITY", 0.995),
            0.995, 0.0, 1.0,
        )
        self.evolution_novelty_threshold = _safe_float(
            evolution_novelty_threshold if evolution_novelty_threshold is not None else getattr(Config, "CUSTOM_GESTURE_EVOLUTION_NOVELTY_THRESHOLD", 0.97),
            0.97, 0.0, 1.0,
        )
        self.evolution_min_observations = _safe_int(
            evolution_min_observations if evolution_min_observations is not None else getattr(Config, "CUSTOM_GESTURE_EVOLUTION_MIN_OBSERVATIONS", 3),
            3, 2, 100,
        )
        self.evolution_max_pending_clusters = _safe_int(
            evolution_max_pending_clusters if evolution_max_pending_clusters is not None else getattr(Config, "CUSTOM_GESTURE_EVOLUTION_MAX_PENDING_CLUSTERS", 4),
            4, 1, 16,
        )

        # Recent unknown frames used for the temporal stability gate.  In-memory
        # only: a process restart simply restarts discovery from a cold window.
        self._stability_buffer: Deque[Tuple[float, List[float]]] = deque(
            maxlen=max(self.candidate_stability_window * 2, 16)
        )
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # File helpers (same atomic-write discipline as the custom gesture service)
    # ------------------------------------------------------------------
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
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            return payload if isinstance(payload, dict) else {}
        except FileNotFoundError:
            return {}
        except Exception as exc:
            logger.warning(f"Could not read custom gesture learning file {path}: {exc}")
            return {}

    def _candidates_path(self) -> str:
        return os.path.join(self.learning_dir, self.CANDIDATES_FILE)

    def _corrections_path(self) -> str:
        return os.path.join(self.learning_dir, self.CORRECTIONS_FILE)

    def _pending_learn_path(self) -> str:
        return os.path.join(self.learning_dir, self.PENDING_LEARN_FILE)

    def _pending_variations_path(self, gesture_dir: str) -> str:
        return os.path.join(gesture_dir, "variations", self.PENDING_VARIATIONS_FILE)

    def _load_candidates_state(self) -> Dict[str, Any]:
        state = self._read_json(self._candidates_path())
        if not isinstance(state.get("candidates"), list):
            state["candidates"] = []
        if not isinstance(state.get("ignored"), list):
            state["ignored"] = []
        state["schema_version"] = self.SCHEMA_VERSION
        return state

    def _save_candidates_state(self, state: Dict[str, Any]) -> None:
        state["updated_at"] = _utc_now()
        self._write_json_atomic(self._candidates_path(), state)

    def _load_corrections_state(self) -> Dict[str, Any]:
        state = self._read_json(self._corrections_path())
        if not isinstance(state.get("corrections"), list):
            state["corrections"] = []
        state["schema_version"] = self.SCHEMA_VERSION
        return state

    def _save_corrections_state(self, state: Dict[str, Any]) -> None:
        state["updated_at"] = _utc_now()
        self._write_json_atomic(self._corrections_path(), state)

    # ------------------------------------------------------------------
    # Event mirroring (optional MongoDB persistence via existing architecture)
    # ------------------------------------------------------------------
    def emit_event(self, event_type: str, details: Optional[Dict[str, Any]] = None,
                   gesture_id: str = "", candidate_id: str = "") -> Dict[str, Any]:
        event = {
            "event_id": uuid4().hex,
            "event_type": str(event_type),
            "created_at": _utc_now(),
            "gesture_id": str(gesture_id or ""),
            "candidate_id": str(candidate_id or ""),
            "details": details if isinstance(details, dict) else {},
        }
        if self.event_sink is not None:
            try:
                self.event_sink(event)
            except Exception as exc:
                logger.warning(f"Custom gesture learning event sink failed: {exc}")
        return event

    # ------------------------------------------------------------------
    # FEATURE 1: Unknown gesture discovery (clustering)
    # ------------------------------------------------------------------
    def _is_stable_unknown_window(self, now_monotonic: float, features: Sequence[float]) -> bool:
        """Temporal stability gate: unknown frames must be quiet before counting.

        A single accidental frame, tracking noise, or a wildly moving hand must
        not contribute to candidate evidence.  The most recent short window of
        unknown feature vectors must have a low per-component spread.
        """
        buffer = self._stability_buffer
        buffer.append((now_monotonic, [float(v) for v in features]))
        cutoff = now_monotonic - 3.0
        while buffer and buffer[0][0] < cutoff:
            buffer.popleft()
        recent = [item for item in buffer if item[0] >= cutoff]
        if len(recent) < self.candidate_stability_window:
            return False
        window = [item[1] for item in recent][-self.candidate_stability_window:]
        size = len(window[0])
        if size <= 0:
            return False
        total = 0.0
        for index in range(size):
            values = [vec[index] if index < len(vec) else 0.0 for vec in window]
            mean_value = sum(values) / len(values)
            total += sum(abs(value - mean_value) for value in values) / len(values)
        mad = total / size
        return mad <= self.candidate_stability_threshold

    def _prune_expired_candidates(self, state: Dict[str, Any], now_dt: datetime) -> List[Dict[str, Any]]:
        expired: List[Dict[str, Any]] = []
        kept: List[Dict[str, Any]] = []
        for candidate in state.get("candidates", []):
            last_seen = _parse_iso(candidate.get("last_seen"))
            if last_seen and (now_dt - last_seen).total_seconds() > self.candidate_ttl_seconds:
                expired.append(candidate)
            else:
                kept.append(candidate)
        for candidate in expired:
            self.emit_event(
                "candidate_expired",
                {
                    "observed_count": int(candidate.get("observed_count", 0) or 0),
                    "status": str(candidate.get("status", "pending")),
                },
                candidate_id=str(candidate.get("candidate_id", "")),
            )
        state["candidates"] = kept
        return expired

    def _prune_ignored_signatures(self, state: Dict[str, Any], now_dt: datetime) -> None:
        if self.ignored_signature_ttl_days <= 0:
            state["ignored"] = []
            return
        cutoff = now_dt - timedelta(days=self.ignored_signature_ttl_days)
        state["ignored"] = [
            item for item in state.get("ignored", [])
            if (_parse_iso(item.get("ignored_at")) or now_dt) >= cutoff
        ]

    def _candidate_cluster_similarity(self, candidate: Dict[str, Any]) -> float:
        centroid = candidate.get("centroid", [])
        observations = candidate.get("observations", [])
        sims = [
            best_similarity(obs.get("features", []), [centroid], self.max_match_distance, mirror=True)
            for obs in observations
            if obs.get("features")
        ]
        if not sims:
            return 0.0
        return sum(sims) / len(sims)

    def _candidate_summary(self, candidate: Dict[str, Any]) -> LearningSummary:
        handedness_counts: Dict[str, int] = {}
        for obs in candidate.get("observations", []):
            handedness = str(obs.get("handedness", "none") or "none")
            handedness_counts[handedness] = handedness_counts.get(handedness, 0) + 1
        handedness = "either"
        if handedness_counts:
            top = max(handedness_counts.items(), key=lambda item: (item[1], item[0]))
            if top[0] in {"left", "right"}:
                handedness = top[0]
        return LearningSummary(
            candidate_id=str(candidate.get("candidate_id", "")),
            observed_count=int(candidate.get("observed_count", 0) or 0),
            cluster_similarity=self._candidate_cluster_similarity(candidate),
            handedness=handedness,
            first_seen=str(candidate.get("first_seen", "")),
            last_seen=str(candidate.get("last_seen", "")),
            min_observations=self.candidate_min_observations,
        )

    def record_unknown_observation(
        self,
        handedness: str,
        sample_representation: Dict[str, Any],
        features: Sequence[float],
        now_monotonic: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Cluster one high-quality unknown frame; returns a status dict.

        The caller must only pass observations for hands that failed to match
        any enabled custom gesture and that passed the tracking-quality gate.
        """
        with self._lock:
            if not features:
                return {"recorded": False, "reason": "no_features"}
            tracking_quality = _safe_float(sample_representation.get("tracking_quality", 0.0), 0.0, 0.0, 1.0)
            if tracking_quality < self.min_tracking_quality:
                return {"recorded": False, "reason": "low_tracking_quality"}

            now_dt = _utc_now_dt()
            now_mono = now_monotonic if now_monotonic is not None else 0.0
            state = self._load_candidates_state()
            self._prune_ignored_signatures(state, now_dt)

            # Suppressed clusters the user already ignored stay quiet.
            for ignored in state.get("ignored", []):
                signature = ignored.get("signature", [])
                if signature and best_similarity(features, [signature], self.max_match_distance, mirror=True) >= self.candidate_cluster_threshold:
                    return {"recorded": False, "reason": "suppressed"}

            if not self._is_stable_unknown_window(now_mono, features):
                return {"recorded": False, "reason": "unstable"}

            observation = {
                "at": _utc_now(),
                "handedness": str(handedness or "none"),
                "features": [float(v) for v in features],
                "sample": sample_representation,
            }

            changed = False
            matched = None
            best_sim = 0.0
            for candidate in state.get("candidates", []):
                if str(candidate.get("status", "pending")) != "pending":
                    continue
                sim = best_similarity(features, [candidate.get("centroid", [])], self.max_match_distance, mirror=True)
                if sim >= self.candidate_cluster_threshold and sim >= best_sim:
                    best_sim = sim
                    matched = candidate
            if matched is not None:
                matched["observed_count"] = int(matched.get("observed_count", 0) or 0) + 1
                matched["last_seen"] = observation["at"]
                observations = matched.setdefault("observations", [])
                observations.append(observation)
                # Keep the newest observations; the stored set feeds learning.
                if len(observations) > self.candidate_max_observations:
                    del observations[: len(observations) - self.candidate_max_observations]
                # Incremental centroid update over stored observations.
                centroid = mean_vector([obs.get("features", []) for obs in observations])
                matched["centroid"] = centroid
                changed = True
                newly_ready = int(matched.get("observed_count", 0)) == self.candidate_min_observations
                summary = self._candidate_summary(matched)
                if changed and newly_ready:
                    self.emit_event(
                        "candidate_detected",
                        summary.to_dict(),
                        candidate_id=summary.candidate_id,
                    )
                self._save_candidates_state(state)
                return {
                    "recorded": True,
                    "reason": "clustered",
                    "candidate": summary.to_dict(),
                    "new_candidate": False,
                }

            candidate = {
                "candidate_id": f"cg_{uuid4().hex[:10]}",
                "status": "pending",
                "observed_count": 1,
                "first_seen": observation["at"],
                "last_seen": observation["at"],
                "centroid": [float(v) for v in features],
                "observations": [observation],
            }
            state.setdefault("candidates", []).append(candidate)
            self._save_candidates_state(state)
            summary = self._candidate_summary(candidate)
            return {
                "recorded": True,
                "reason": "new_cluster",
                "candidate": summary.to_dict(),
                "new_candidate": True,
            }

    def learning_status(self) -> Dict[str, Any]:
        with self._lock:
            now_dt = _utc_now_dt()
            state = self._load_candidates_state()
            self._prune_expired_candidates(state, now_dt)
            self._prune_ignored_signatures(state, now_dt)
            self._save_candidates_state(state)

            surfaced = []
            for candidate in state.get("candidates", []):
                if str(candidate.get("status", "pending")) != "pending":
                    continue
                summary = self._candidate_summary(candidate)
                if summary.observed_count >= self.candidate_min_observations:
                    surfaced.append(summary.to_dict())
            surfaced.sort(key=lambda item: (item["observed_count"], item["last_seen"]), reverse=True)

            corrections_state = self._load_corrections_state()
            corrections = corrections_state.get("corrections", [])
            return {
                "candidates": surfaced,
                "pending_candidate_count": len(surfaced),
                "ignored_candidate_count": len(state.get("ignored", [])),
                "correction_count": len(corrections),
                "recent_corrections": list(reversed(corrections[-10:])),
            }

    # ------------------------------------------------------------------
    # Candidate -> gesture learning workflow
    # ------------------------------------------------------------------
    def prepare_learn(self, candidate_id: str) -> Dict[str, Any]:
        """Freeze a candidate's captured samples for the Create Gesture flow."""
        with self._lock:
            state = self._load_candidates_state()
            candidate = next(
                (
                    item for item in state.get("candidates", [])
                    if item.get("candidate_id") == candidate_id
                    and str(item.get("status", "pending")) in {"pending", "learning"}
                ),
                None,
            )
            if candidate is None:
                return {"success": False, "error": "Gesture candidate no longer available."}
            observations = candidate.get("observations", [])
            if not observations:
                return {"success": False, "error": "Candidate has no captured samples."}

            handedness_counts: Dict[str, int] = {}
            for obs in observations:
                handedness = str(obs.get("handedness", "none") or "none")
                if handedness in {"left", "right"}:
                    handedness_counts[handedness] = handedness_counts.get(handedness, 0) + 1
            dominant_hand = "either"
            if handedness_counts:
                dominant_hand = max(handedness_counts.items(), key=lambda item: (item[1], item[0]))[0]

            pending = {
                "schema_version": self.SCHEMA_VERSION,
                "candidate_id": candidate_id,
                "handedness": dominant_hand,
                "observation_count": len(observations),
                "created_at": _utc_now(),
                "observations": [
                    {"at": obs.get("at", _utc_now()), "handedness": obs.get("handedness", "none"), "sample": obs.get("sample", {})}
                    for obs in observations
                ],
            }
            self._write_json_atomic(self._pending_learn_path(), pending)
            candidate["status"] = "learning"
            self._save_candidates_state(state)
            self.emit_event(
                "candidate_learn_prepared",
                {"observation_count": len(observations), "handedness": dominant_hand},
                candidate_id=candidate_id,
            )
            return {
                "success": True,
                "candidate_id": candidate_id,
                "handedness": dominant_hand,
                "observation_count": len(observations),
            }

    def get_pending_learn(self, candidate_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            pending = self._read_json(self._pending_learn_path())
            if pending.get("candidate_id") != candidate_id:
                return None
            created = _parse_iso(pending.get("created_at"))
            if created and (_utc_now_dt() - created).total_seconds() > 3600:
                # Stale preloaded samples are safer to discard than to reuse.
                self.clear_pending_learn()
                return None
            return pending

    def set_pending_learn_target(self, candidate_id: str, gesture_id: str) -> None:
        """Bind the pending samples to the gesture capture that will use them.

        Finalization only happens for this gesture, so an unrelated capture
        completing can never consume the candidate by accident.
        """
        with self._lock:
            pending = self._read_json(self._pending_learn_path())
            if pending.get("candidate_id") == candidate_id:
                pending["target_gesture_id"] = gesture_id
                self._write_json_atomic(self._pending_learn_path(), pending)

    def clear_pending_learn(self, candidate_id: Optional[str] = None) -> None:
        with self._lock:
            pending = self._read_json(self._pending_learn_path())
            if candidate_id is None or pending.get("candidate_id") == candidate_id:
                try:
                    os.remove(self._pending_learn_path())
                except OSError:
                    pass

    def finalize_candidate_for(self, gesture_id: str) -> Optional[str]:
        """Mark the candidate learned once its gesture capture completed."""
        with self._lock:
            pending = self._read_json(self._pending_learn_path())
            if not pending or not pending.get("observations"):
                return None
            target = str(pending.get("target_gesture_id", "") or "")
            if not target or target != gesture_id:
                # Only the capture explicitly started from this candidate may
                # consume it.
                return None
            candidate_id = str(pending.get("candidate_id", ""))
            state = self._load_candidates_state()
            candidate = next(
                (item for item in state.get("candidates", []) if item.get("candidate_id") == candidate_id),
                None,
            )
            if candidate is not None:
                candidate["status"] = "learned"
                candidate["learned_as"] = gesture_id
                candidate["learned_at"] = _utc_now()
                self._save_candidates_state(state)
            self.emit_event(
                "candidate_learned",
                {"preloaded_samples": len(pending.get("observations", []))},
                gesture_id=gesture_id,
                candidate_id=candidate_id,
            )
            self.clear_pending_learn(candidate_id)
            return candidate_id

    def ignore_candidate(self, candidate_id: str) -> Dict[str, Any]:
        with self._lock:
            state = self._load_candidates_state()
            candidate = next(
                (
                    item for item in state.get("candidates", [])
                    if item.get("candidate_id") == candidate_id
                    and str(item.get("status", "pending")) in {"pending", "learning"}
                ),
                None,
            )
            if candidate is None:
                return {"success": False, "error": "Gesture candidate no longer available."}
            summary = self._candidate_summary(candidate)
            candidate["status"] = "ignored"
            candidate["ignored_at"] = _utc_now()
            state.setdefault("ignored", []).append({
                "signature": candidate.get("centroid", []),
                "ignored_at": _utc_now(),
                "source_candidate_id": candidate_id,
            })
            self._save_candidates_state(state)
            self.clear_pending_learn(candidate_id)
            self.emit_event(
                "candidate_ignored",
                summary.to_dict(),
                candidate_id=candidate_id,
            )
            return {"success": True, "candidate_id": candidate_id}

    # ------------------------------------------------------------------
    # FEATURE 2: Mistake memory (personalized corrections)
    # ------------------------------------------------------------------
    def add_correction(
        self,
        predicted_gesture_id: str,
        correct_gesture_id: str,
        signature: Optional[Sequence[float]] = None,
        handedness: str = "",
        confidence: float = 0.0,
    ) -> Dict[str, Any]:
        with self._lock:
            correction = {
                "correction_id": f"cc_{uuid4().hex[:10]}",
                "created_at": _utc_now(),
                "predicted_gesture_id": str(predicted_gesture_id),
                "correct_gesture_id": str(correct_gesture_id),
                "signature": [float(v) for v in signature] if signature else [],
                "handedness": str(handedness or ""),
                "confidence": round(_safe_float(confidence, 0.0, 0.0, 1.0), 4),
            }
            state = self._load_corrections_state()
            state.setdefault("corrections", []).append(correction)
            if len(state["corrections"]) > self.correction_max_stored:
                del state["corrections"][: len(state["corrections"]) - self.correction_max_stored]
            self._save_corrections_state(state)
            self.emit_event(
                "correction_recorded",
                {
                    "predicted": correction["predicted_gesture_id"],
                    "correct": correction["correct_gesture_id"],
                    "has_signature": bool(correction["signature"]),
                },
                gesture_id=correction["correct_gesture_id"],
            )
            return correction

    def list_corrections(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._lock:
            state = self._load_corrections_state()
            corrections = state.get("corrections", [])
            return list(reversed(corrections[-max(1, min(int(limit), 500)):]))

    def corrections_for_gesture(self, gesture_id: str, limit: int = 10) -> Dict[str, List[Dict[str, Any]]]:
        with self._lock:
            state = self._load_corrections_state()
            corrections = state.get("corrections", [])
            caused = [c for c in corrections if c.get("predicted_gesture_id") == gesture_id]
            received = [c for c in corrections if c.get("correct_gesture_id") == gesture_id]
            limit = max(1, min(int(limit), 100))
            return {
                "caused": list(reversed(caused[-limit:])),
                "received": list(reversed(received[-limit:])),
            }

    def find_correction(
        self,
        predicted_gesture_id: str,
        features: Sequence[float],
        now_dt: Optional[datetime] = None,
    ) -> Optional[Dict[str, Any]]:
        """Aggregate correction evidence for a prediction that just happened.

        Rules (conservative):
        * only corrections still within the max age count;
        * a correction with a stored feature signature contributes full
          evidence only when the current frame is close to that signature;
        * signature-less corrections count half and can never trigger an
          override alone (they need combined evidence of at least 2.0);
        * the winning target must beat ``correction_min_evidence``.
        """
        with self._lock:
            if not features:
                return None
            now_dt = now_dt or _utc_now_dt()
            if self.correction_max_age_days > 0:
                cutoff = now_dt - timedelta(days=self.correction_max_age_days)
            else:
                cutoff = None
            state = self._load_corrections_state()
            per_target: Dict[str, Dict[str, Any]] = {}
            for correction in state.get("corrections", []):
                if correction.get("predicted_gesture_id") != predicted_gesture_id:
                    continue
                if str(correction.get("correct_gesture_id", "")) == str(predicted_gesture_id):
                    continue
                created = _parse_iso(correction.get("created_at"))
                if cutoff is not None and (created is None or created < cutoff):
                    continue
                target_id = str(correction.get("correct_gesture_id", ""))
                entry = per_target.setdefault(target_id, {"evidence": 0.0, "best_signature_similarity": 0.0, "matches": 0})
                signature = correction.get("signature", [])
                if signature:
                    similarity = best_similarity(features, [signature], self.max_match_distance, mirror=True)
                    if similarity >= self.correction_signature_threshold:
                        entry["evidence"] += 1.0
                        entry["matches"] += 1
                        entry["best_signature_similarity"] = max(entry["best_signature_similarity"], similarity)
                else:
                    entry["evidence"] += 0.5

            best: Optional[Dict[str, Any]] = None
            for target_id, entry in per_target.items():
                eligible = entry["evidence"] >= self.correction_min_evidence and (
                    entry["best_signature_similarity"] >= self.correction_signature_threshold
                    or entry["evidence"] >= 2.0
                )
                if not eligible:
                    continue
                if best is None or (entry["evidence"], entry["best_signature_similarity"]) > (
                    best["evidence"], best["best_signature_similarity"]
                ):
                    best = {
                        "correct_gesture_id": target_id,
                        "evidence": round(entry["evidence"], 2),
                        "best_signature_similarity": round(entry["best_signature_similarity"], 4),
                        "matches": entry["matches"],
                    }
            return best

    # ------------------------------------------------------------------
    # FEATURE 3: Gesture evolution (personalized variations)
    # ------------------------------------------------------------------
    def pending_variations_path(self, gesture_dir: str) -> str:
        return self._pending_variations_path(gesture_dir)

    def load_pending_variations(self, gesture_dir: str) -> List[Dict[str, Any]]:
        with self._lock:
            state = self._read_json(self._pending_variations_path(gesture_dir))
            clusters = state.get("clusters", [])
            return clusters if isinstance(clusters, list) else []

    def save_pending_variations(self, gesture_dir: str, gesture_id: str, clusters: List[Dict[str, Any]]) -> None:
        with self._lock:
            self._write_json_atomic(self._pending_variations_path(gesture_dir), {
                "schema_version": self.SCHEMA_VERSION,
                "gesture_id": gesture_id,
                "updated_at": _utc_now(),
                "clusters": clusters,
            })

    def record_evolution_observation(
        self,
        gesture_dir: str,
        gesture_id: str,
        gesture_name: str,
        hand: str,
        features: Sequence[float],
        sample_representation: Dict[str, Any],
        confidence: float,
        prototype: Sequence[float],
        reference_features: Sequence[Sequence[float]],
        now_monotonic: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Accumulate one high-confidence re-observation of an existing gesture.

        ``reference_features`` must contain the gesture's original sample and
        accepted-variation vectors used for the novelty check.  The observation
        only counts when it is (a) close enough to the gesture overall, and
        (b) sufficiently different from every stored example so that identical
        repeats do not pile up.
        """
        with self._lock:
            mirror = str(hand or "either").lower() == "either"
            if not features:
                return {"recorded": False, "reason": "no_features"}
            if _safe_float(confidence, 0.0, 0.0, 1.0) < self.evolution_min_confidence:
                return {"recorded": False, "reason": "low_confidence"}

            all_references = list(reference_features) + ([prototype] if prototype else [])
            similarity_to_gesture = best_similarity(features, all_references, self.max_match_distance, mirror=mirror)
            if similarity_to_gesture < self.evolution_min_similarity:
                return {"recorded": False, "reason": "too_different"}
            if similarity_to_gesture >= self.evolution_max_similarity:
                # Essentially the stored pose itself: no new information.
                return {"recorded": False, "reason": "too_close"}
            if reference_features:
                similarity_to_nearest = best_similarity(features, list(reference_features), self.max_match_distance, mirror=mirror)
                if similarity_to_nearest >= self.evolution_novelty_threshold:
                    return {"recorded": False, "reason": "not_novel"}

            observation = {
                "at": _utc_now(),
                "confidence": round(_safe_float(confidence, 0.0, 0.0, 1.0), 4),
                "features": [float(v) for v in features],
                "sample": sample_representation,
            }
            clusters = self.load_pending_variations(gesture_dir)
            matched = None
            for cluster in clusters:
                sim = best_similarity(features, [cluster.get("centroid", [])], self.max_match_distance, mirror=mirror)
                if sim >= 0.93:
                    matched = cluster
                    break
            ready_just_now = False
            if matched is not None:
                matched["count"] = int(matched.get("count", 0) or 0) + 1
                matched["last_seen"] = observation["at"]
                observations = matched.setdefault("observations", [])
                observations.append(observation)
                if len(observations) > 6:
                    del observations[: len(observations) - 6]
                matched["centroid"] = mean_vector([obs.get("features", []) for obs in observations])
                ready_just_now = (
                    int(matched.get("count", 0)) == self.evolution_min_observations
                )
            else:
                if len(clusters) >= self.evolution_max_pending_clusters:
                    return {"recorded": False, "reason": "pending_full"}
                cluster = {
                    "cluster_id": f"pv_{uuid4().hex[:8]}",
                    "count": 1,
                    "first_seen": observation["at"],
                    "last_seen": observation["at"],
                    "centroid": [float(v) for v in features],
                    "observations": [observation],
                }
                clusters.append(cluster)
                ready_just_now = self.evolution_min_observations == 1

            self.save_pending_variations(gesture_dir, gesture_id, clusters)
            if ready_just_now:
                cluster = matched if matched is not None else clusters[-1]
                self.emit_event(
                    "variation_pending_ready",
                    {
                        "gesture_name": gesture_name,
                        "cluster_id": cluster.get("cluster_id", ""),
                        "observed_count": int(cluster.get("count", 0) or 0),
                        "similarity_to_gesture": round(similarity_to_gesture, 4),
                    },
                    gesture_id=gesture_id,
                )
            return {
                "recorded": True,
                "reason": "clustered" if matched is not None else "new_cluster",
                "similarity_to_gesture": round(similarity_to_gesture, 4),
            }

    def accept_pending_variations(
        self,
        gesture_dir: str,
        gesture_id: str,
        gesture_name: str,
        existing_variation_count: int,
    ) -> List[Dict[str, Any]]:
        """Persist ready pending clusters as permanent variation files."""
        with self._lock:
            clusters = self.load_pending_variations(gesture_dir)
            ready = [c for c in clusters if int(c.get("count", 0) or 0) >= self.evolution_min_observations]
            if not ready:
                return []
            remaining = [c for c in clusters if c not in ready]
            variations_dir = os.path.join(gesture_dir, "variations")
            os.makedirs(variations_dir, exist_ok=True)
            accepted: List[Dict[str, Any]] = []
            index = int(existing_variation_count)
            for cluster in ready:
                observations = cluster.get("observations", [])
                if not observations:
                    continue
                index += 1
                representative = observations[-1]
                variation = dict(representative.get("sample", {}) or {})
                variation.update({
                    "schema_version": self.SCHEMA_VERSION,
                    "variation_id": f"variation_{index:03d}",
                    "gesture_id": gesture_id,
                    "gesture_name": gesture_name,
                    "source": "personalized_evolution",
                    "observed_count": int(cluster.get("count", 0) or 0),
                    "accepted_at": _utc_now(),
                    "observed_at": representative.get("at", _utc_now()),
                })
                self._write_json_atomic(os.path.join(variations_dir, f"variation_{index:03d}.json"), variation)
                accepted.append({
                    "variation_id": variation["variation_id"],
                    "observed_count": variation["observed_count"],
                    "observed_at": variation["observed_at"],
                })
                self.emit_event(
                    "variation_learned",
                    {
                        "gesture_name": gesture_name,
                        "variation_id": variation["variation_id"],
                        "observed_count": variation["observed_count"],
                    },
                    gesture_id=gesture_id,
                )
            self.save_pending_variations(gesture_dir, gesture_id, remaining)
            return accepted

    def discard_pending_variations(self, gesture_dir: str, gesture_id: str) -> int:
        with self._lock:
            clusters = self.load_pending_variations(gesture_dir)
            count = len(clusters)
            if count:
                self.save_pending_variations(gesture_dir, gesture_id, [])
            return count
