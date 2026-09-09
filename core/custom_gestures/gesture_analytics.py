"""Gesture Quality & Analytics for the Custom Gesture Library.

This module computes performance metrics, quality scores, and evolution
tracking for each custom gesture.  All values are derived from actual data
stored in the gesture metadata (learning_stats, sample files, recognition
events).  Nothing is random or fabricated.

Quality Score Formula (documented)
----------------------------------
The overall quality score is a weighted combination of measurable factors:

    quality = (
        0.25 * recognition_success_rate   # detections / (detections + corrections_caused)
      + 0.25 * avg_confidence_pct         # average recognition confidence
      + 0.20 * avg_similarity_pct         # average DNA similarity
      + 0.15 * stability_score            # DNA stability dimension
      + 0.15 * sample_diversity_score     # number of samples / target samples (capped at 100)
    )

Each factor is in [0, 100].  If a factor is unavailable (e.g. no recognition
events yet), it is excluded from the weighted average.

Performance tracking appends timestamped events to
``learning_stats.recognition_log`` in the gesture metadata.  Events are small
dicts: {"at": iso_timestamp, "confidence": float, "similarity": float,
"matched": bool, "source": "test"|"live"}
"""

from __future__ import annotations

import math
import os
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from core.custom_gestures.gesture_dna import (
    aggregate_dna,
    compute_dna_from_samples,
    extract_sample_dna,
    _clamp_pct,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Recognition event logging
# ---------------------------------------------------------------------------
MAX_LOG_SIZE = 500  # cap per gesture to avoid unbounded growth


def log_recognition_event(
    metadata: Dict[str, Any],
    confidence: float,
    similarity: float,
    matched: bool,
    source: str = "live",
) -> Dict[str, Any]:
    """Append a recognition event to the gesture's learning_stats log.

    Returns the updated stats dict.  ``metadata`` is mutated in place.
    """
    stats = metadata.setdefault("learning_stats", {})
    log = stats.setdefault("recognition_log", [])
    event = {
        "at": _utc_now(),
        "confidence": round(float(confidence), 2),
        "similarity": round(float(similarity), 2),
        "matched": bool(matched),
        "source": str(source),
    }
    log.append(event)
    # Trim to keep storage bounded
    if len(log) > MAX_LOG_SIZE:
        stats["recognition_log"] = log[-MAX_LOG_SIZE:]
    return stats


def compute_analytics(
    metadata: Dict[str, Any],
    sample_paths: Sequence[str],
    variation_paths: Sequence[str],
    read_json_fn,
) -> Dict[str, Any]:
    """Compute all analytics for a gesture from its metadata and files.

    ``read_json_fn`` is a callable that reads a JSON file and returns a dict.
    """
    stats = metadata.get("learning_stats", {})
    if not isinstance(stats, dict):
        stats = {}

    recognition_log = stats.get("recognition_log", [])
    if not isinstance(recognition_log, list):
        recognition_log = []

    # --- Basic counts ---
    total_samples = int(metadata.get("sample_count", 0) or len(sample_paths))
    original_samples = total_samples
    variation_count = int(metadata.get("variation_count", 0) or len(variation_paths))

    # Recognition events
    recognition_count = len(recognition_log)
    successful_count = sum(1 for e in recognition_log if e.get("matched"))
    unknown_count = sum(1 for e in recognition_log if not e.get("matched"))

    # Confidence & similarity statistics
    confidences = [float(e.get("confidence", 0.0)) for e in recognition_log if e.get("confidence") is not None]
    similarities = [float(e.get("similarity", 0.0)) for e in recognition_log if e.get("similarity") is not None]

    avg_confidence = round(sum(confidences) / max(1, len(confidences)), 1) if confidences else 0.0
    avg_similarity = round(sum(similarities) / max(1, len(similarities)), 1) if similarities else 0.0

    # Correction counts
    corrections_caused = int(stats.get("corrections_caused", 0) or 0)
    corrections_received = int(stats.get("corrections_received", 0) or 0)
    corrections_applied = int(stats.get("corrections_applied", 0) or 0)

    # Recognition success rate
    detections = int(stats.get("detections", 0) or 0)
    if (detections + corrections_caused) > 0:
        recognition_success_rate = round(detections / (detections + corrections_caused) * 100.0, 1)
    elif recognition_count > 0:
        recognition_success_rate = round(successful_count / recognition_count * 100.0, 1)
    else:
        recognition_success_rate = 0.0

    # --- DNA-based stability score ---
    dna = compute_dna_from_samples(sample_paths, read_json_fn)
    stability_score = dna.get("stability", 0.0)

    # --- Quality score (documented formula) ---
    quality_factors: List[float] = []
    quality_weights: List[float] = []

    # recognition_success_rate (weight 0.25)
    if recognition_count > 0 or (detections + corrections_caused) > 0:
        quality_factors.append(recognition_success_rate)
        quality_weights.append(0.25)

    # avg_confidence_pct (weight 0.25)
    if confidences:
        quality_factors.append(avg_confidence)
        quality_weights.append(0.25)

    # avg_similarity_pct (weight 0.20)
    if similarities:
        quality_factors.append(avg_similarity)
        quality_weights.append(0.20)

    # stability_score (weight 0.15)
    if dna.get("sample_count", 0) >= 2:
        quality_factors.append(stability_score)
        quality_weights.append(0.15)

    # sample_diversity_score (weight 0.15)
    target = int(metadata.get("target_samples", 30) or 30)
    diversity = min(100.0, (total_samples / max(1, target)) * 100.0)
    quality_factors.append(diversity)
    quality_weights.append(0.15)

    if quality_weights:
        total_weight = sum(quality_weights)
        quality_score = round(
            sum(f * w for f, w in zip(quality_factors, quality_weights)) / total_weight, 1
        )
    else:
        quality_score = 0.0

    # --- Low quality warning ---
    low_quality_warning = False
    low_quality_reasons: List[str] = []
    if recognition_count >= 5:  # Only warn after enough data
        if recognition_success_rate < 65.0:
            low_quality_warning = True
            low_quality_reasons.append("recognition success rate is below 65%")
        if avg_confidence < 60.0 and confidences:
            low_quality_reasons.append("average confidence is below 60%")
        if stability_score < 50.0 and dna.get("sample_count", 0) >= 2:
            low_quality_reasons.append("gesture stability is low – hand position varies a lot")
        if total_samples < max(5, target * 0.3):
            low_quality_reasons.append("insufficient sample diversity")

    # --- Environmental metrics (if data available) ---
    environmental = _compute_environmental_metrics(recognition_log, sample_paths, read_json_fn)

    # --- Time-based evolution ---
    evolution_timeline = _compute_evolution_timeline(recognition_log)

    return {
        "success": True,
        "analytics": {
            "gesture_id": metadata.get("gesture_id", ""),
            "gesture_name": metadata.get("gesture_name", ""),
            "total_samples": total_samples,
            "original_samples": original_samples,
            "variation_count": variation_count,
            "recognition_count": recognition_count,
            "successful_count": successful_count,
            "unknown_count": unknown_count,
            "corrections_caused": corrections_caused,
            "corrections_received": corrections_received,
            "corrections_applied": corrections_applied,
            "avg_confidence": avg_confidence,
            "avg_similarity": avg_similarity,
            "recognition_success_rate": recognition_success_rate,
            "stability_score": round(stability_score, 1),
            "quality_score": quality_score,
            "low_quality_warning": low_quality_warning,
            "low_quality_reasons": low_quality_reasons,
            "dna": dna,
            "environmental": environmental,
            "evolution_timeline": evolution_timeline,
            "event_count": recognition_count,
        },
    }


def _compute_environmental_metrics(
    recognition_log: Sequence[Dict[str, Any]],
    sample_paths: Sequence[str],
    read_json_fn,
) -> Dict[str, Any]:
    """Estimate environmental conditions from available data.

    Only uses metrics that can actually be measured from landmarks:
    - hand size in frame (proxy for camera distance)
    - stability (landmark variance)
    - left/right hand (from sample handedness)
    """
    hand_sizes: List[float] = []
    handedness_counts: Dict[str, int] = {}

    for path in sample_paths:
        try:
            payload = read_json_fn(path)
            h = payload.get("captured_handedness", "")
            if h:
                handedness_counts[h] = handedness_counts.get(h, 0) + 1

            raw = payload.get("raw_landmarks", [])
            if raw:
                # Hand size in frame: distance from wrist (0) to middle_mcp (9)
                if len(raw) >= 10:
                    w = raw[0]
                    m = raw[9]
                    wx = float(w.get("x", 0.0) if isinstance(w, dict) else 0.0)
                    wy = float(w.get("y", 0.0) if isinstance(w, dict) else 0.0)
                    mx = float(m.get("x", 0.0) if isinstance(m, dict) else 0.0)
                    my = float(m.get("y", 0.0) if isinstance(m, dict) else 0.0)
                    size = math.sqrt((mx - wx) ** 2 + (my - wy) ** 2)
                    if size > 0.01:
                        hand_sizes.append(size)
        except Exception:
            continue

    avg_hand_size = round(sum(hand_sizes) / max(1, len(hand_sizes)), 4) if hand_sizes else 0.0
    dominant_hand = max(handedness_counts, key=handedness_counts.get) if handedness_counts else "unknown"

    return {
        "avg_hand_size_in_frame": avg_hand_size,
        "camera_distance_proxy": "close" if avg_hand_size > 0.2 else ("medium" if avg_hand_size > 0.1 else "far") if avg_hand_size > 0 else "unknown",
        "dominant_hand": dominant_hand,
        "handedness_distribution": handedness_counts,
        "hand_size_samples": len(hand_sizes),
    }


def _compute_evolution_timeline(
    recognition_log: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Group recognition events by date to show performance over time.

    Returns a list of {date, success_rate, avg_confidence, event_count}.
    """
    if not recognition_log:
        return []

    daily: Dict[str, Dict[str, Any]] = {}
    for event in recognition_log:
        at = str(event.get("at", ""))
        if not at:
            continue
        date_key = at[:10]  # "YYYY-MM-DD"
        if date_key not in daily:
            daily[date_key] = {"events": 0, "matched": 0, "confidence_sum": 0.0}
        daily[date_key]["events"] += 1
        if event.get("matched"):
            daily[date_key]["matched"] += 1
        daily[date_key]["confidence_sum"] += float(event.get("confidence", 0.0) or 0.0)

    timeline = []
    for date_key in sorted(daily.keys()):
        entry = daily[date_key]
        events = entry["events"]
        matched = entry["matched"]
        rate = round(matched / max(1, events) * 100.0, 1)
        avg_conf = round(entry["confidence_sum"] / max(1, events), 1)
        timeline.append({
            "date": date_key,
            "success_rate": rate,
            "avg_confidence": avg_conf,
            "event_count": events,
        })
    return timeline
