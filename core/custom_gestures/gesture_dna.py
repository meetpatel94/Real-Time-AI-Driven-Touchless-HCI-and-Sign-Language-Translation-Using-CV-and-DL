"""Gesture DNA: measurable characteristics of a custom gesture.

The DNA is derived from actual MediaPipe landmarks already stored in sample
files.  Each dimension is a number in [0, 100] representing how strongly
the gesture exhibits that property relative to stored samples.

Dimensions
----------
hand_shape          – openness / closure from wrist-to-fingertip distances
finger_extension    – average joint cosine angles (more open = higher)
palm_orientation    – wrist→middle-MCP axis angle relative to canonical
stability           – inverse of feature-vector standard deviation
spatial_consistency – inverse variance of normalized landmark positions

The ``compute_dna`` function accepts a list of raw landmark arrays (each
entry is a list of 21 dicts with x/y/z) or pre-extracted feature vectors.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional, Sequence

from core.custom_gestures.feature_extractor import (
    CustomGestureFeatureExtractor,
    feature_extractor,
    mean_vector,
    vector_distance,
)


_F = CustomGestureFeatureExtractor


def _clamp_pct(value: float) -> float:
    return max(0.0, min(100.0, round(value, 1)))


# ---------------------------------------------------------------------------
# Low-level per-landmark characteristics
# ---------------------------------------------------------------------------
def _finger_extension_score(points: Sequence) -> float:
    """Average cosine of joint angles; higher = more extended fingers.

    Maps cosine ∈ [-1, 1] → [0, 1] then scales to [0, 100].
    """
    cosines: List[float] = []
    for triple in _F.ANGLE_TRIPLES:
        a, b, c = triple
        cos_val = _F._angle_cosine(points[a], points[b], points[c])
        cosines.append((cos_val + 1.0) / 2.0)
    if not cosines:
        return 50.0
    return (sum(cosines) / len(cosines)) * 100.0


def _hand_shape_score(points: Sequence) -> float:
    """Normalized distances from wrist to each fingertip, averaged.

    Higher values mean the fingertips are farther from the wrist (open hand).
    Scaled to [0, 100] using typical MediaPipe ranges.
    """
    wrist = points[0]
    tips = [points[4], points[8], points[12], points[16], points[20]]
    spans = [_F._distance(wrist, tip) for tip in tips]
    avg_span = sum(spans) / max(1, len(spans))
    # Typical range for wrist→fingertip spans in normalized coords is ~0.8-2.5
    score = _clamp_pct(((avg_span - 0.5) / 2.0) * 100.0)
    return score


def _palm_orientation_score(points: Sequence) -> float:
    """Palm axis rotation mapped to [0, 100] where 50 = canonical up.

    The raw angle is in [-π, π]; we map it so that canonical-up = 50.
    """
    wrist = points[0]
    middle_mcp = points[9]
    vx = middle_mcp[0] - wrist[0]
    vy = middle_mcp[1] - wrist[1]
    if math.hypot(vx, vy) < 1e-6:
        return 50.0
    angle = math.atan2(vy, vx)
    # Map [-π, π] → [0, 100] where angle = -π/2 (canonical up) → 50
    score = ((angle + math.pi) / (2.0 * math.pi)) * 100.0
    return _clamp_pct(score)


def _finger_spread_score(points: Sequence) -> float:
    """Average distance between adjacent fingertips (MCP level).

    Higher = more spread fingers.
    """
    mcp_pairs = [(5, 9), (9, 13), (13, 17), (5, 17)]
    spans = [_F._distance(points[a], points[b]) for a, b in mcp_pairs]
    avg = sum(spans) / max(1, len(spans))
    # Typical range ~0.3-1.2 in normalized coords
    return _clamp_pct(((avg - 0.2) / 1.0) * 100.0)


def _relative_finger_distances(points: Sequence) -> Dict[str, float]:
    """Distances between adjacent fingertips for spatial distribution."""
    tip_pairs = [(4, 8), (8, 12), (12, 16), (16, 20)]
    result = {}
    for (a, b) in tip_pairs:
        result[f"{a}_{b}"] = round(_F._distance(points[a], points[b]), 4)
    return result


# ---------------------------------------------------------------------------
# Per-sample DNA extraction
# ---------------------------------------------------------------------------
def extract_sample_dna(raw_landmarks: Any) -> Dict[str, Any]:
    """Compute DNA characteristics from a single landmark observation."""
    points = _F.points_from_landmarks(raw_landmarks)
    norm_points = _F.normalized_points(raw_landmarks)
    features = _F.feature_vector(raw_landmarks)
    quality = _F.tracking_quality(raw_landmarks)

    return {
        "hand_shape": _hand_shape_score(norm_points),
        "finger_extension": _finger_extension_score(norm_points),
        "palm_orientation": _palm_orientation_score(norm_points),
        "finger_spread": _finger_spread_score(norm_points),
        "tracking_quality": _clamp_pct(quality * 100.0),
        "features": features,
        "raw_landmarks": raw_landmarks,
        "normalized_points": norm_points,
    }


# ---------------------------------------------------------------------------
# Aggregate DNA profile over multiple samples
# ---------------------------------------------------------------------------
def aggregate_dna(dna_list: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Build an aggregate DNA profile from multiple per-sample DNA records.

    Each dimension is averaged; stability / consistency are derived from the
    standard deviation of the underlying feature vectors.
    """
    if not dna_list:
        return {
            "hand_shape": 0.0,
            "finger_extension": 0.0,
            "palm_orientation": 0.0,
            "finger_spread": 0.0,
            "stability": 0.0,
            "spatial_consistency": 0.0,
            "sample_count": 0,
        }

    n = len(dna_list)
    avg_hand_shape = sum(d.get("hand_shape", 0.0) for d in dna_list) / n
    avg_finger_ext = sum(d.get("finger_extension", 0.0) for d in dna_list) / n
    avg_palm_orient = sum(d.get("palm_orientation", 0.0) for d in dna_list) / n
    avg_finger_spread = sum(d.get("finger_spread", 0.0) for d in dna_list) / n

    # Stability = inverse of feature-vector variance
    feature_vectors = [d.get("features", []) for d in dna_list if d.get("features")]
    stability = 0.0
    spatial_consistency = 0.0
    if len(feature_vectors) >= 2:
        mean_fv = mean_vector(feature_vectors)
        distances = [vector_distance(fv, mean_fv) for fv in feature_vectors]
        avg_dist = sum(distances) / len(distances)
        # max_match_distance ~0.45 corresponds to "very different"
        # similarity = 1 - dist/0.45; stability maps this to [0, 100]
        stability = _clamp_pct((1.0 - min(1.0, avg_dist / 0.45)) * 100.0)
        spatial_consistency = _clamp_pct((1.0 - min(1.0, avg_dist / 0.50)) * 100.0)
    elif len(feature_vectors) == 1:
        stability = 100.0
        spatial_consistency = 100.0

    # Compute standard deviations for dimension-level variability
    def _std(values: Sequence[float]) -> float:
        if len(values) < 2:
            return 0.0
        avg = sum(values) / len(values)
        var = sum((v - avg) ** 2 for v in values) / len(values)
        return math.sqrt(var)

    return {
        "hand_shape": _clamp_pct(avg_hand_shape),
        "finger_extension": _clamp_pct(avg_finger_ext),
        "palm_orientation": _clamp_pct(avg_palm_orient),
        "finger_spread": _clamp_pct(avg_finger_spread),
        "stability": _clamp_pct(stability),
        "spatial_consistency": _clamp_pct(spatial_consistency),
        "sample_count": n,
        "dimension_std": {
            "hand_shape": _clamp_pct(_std([d.get("hand_shape", 0.0) for d in dna_list])),
            "finger_extension": _clamp_pct(_std([d.get("finger_extension", 0.0) for d in dna_list])),
            "palm_orientation": _clamp_pct(_std([d.get("palm_orientation", 0.0) for d in dna_list])),
            "finger_spread": _clamp_pct(_std([d.get("finger_spread", 0.0) for d in dna_list])),
        },
    }


def compute_dna_from_samples(sample_paths: Sequence[str], read_json_fn) -> Dict[str, Any]:
    """Read sample JSON files and compute the aggregate DNA profile.

    ``read_json_fn`` should be a callable that takes a path and returns a dict.
    """
    dna_records: List[Dict[str, Any]] = []
    for path in sample_paths:
        try:
            payload = read_json_fn(path)
            raw = payload.get("raw_landmarks", [])
            if raw:
                dna_records.append(extract_sample_dna(raw))
        except Exception:
            continue
    return aggregate_dna(dna_records)


def dna_similarity(current_dna: Dict[str, Any], stored_dna: Dict[str, Any]) -> float:
    """Compute overall similarity percentage between two DNA profiles.

    Returns a value in [0, 100].  Each dimension contributes equally.
    """
    if not current_dna or not stored_dna:
        return 0.0
    dimensions = ["hand_shape", "finger_extension", "palm_orientation", "finger_spread", "stability", "spatial_consistency"]
    total_sim = 0.0
    count = 0
    for dim in dimensions:
        c = current_dna.get(dim)
        s = stored_dna.get(dim)
        if c is not None and s is not None:
            diff = abs(float(c) - float(s))
            # 30 percentage points difference = 0% similarity for that dim
            sim = max(0.0, 100.0 - (diff / 30.0) * 100.0)
            total_sim += sim
            count += 1
    if count == 0:
        return 0.0
    return _clamp_pct(total_sim / count)


def dna_match_level(similarity_pct: float) -> str:
    """Convert a DNA similarity percentage to a human-readable level."""
    if similarity_pct >= 85.0:
        return "Excellent"
    if similarity_pct >= 70.0:
        return "High"
    if similarity_pct >= 50.0:
        return "Moderate"
    if similarity_pct >= 30.0:
        return "Low"
    return "Very Low"


def compute_current_dna(landmarks: Any) -> Dict[str, Any]:
    """Compute DNA for a single current observation (real-time)."""
    if landmarks is None:
        return {}
    dna = extract_sample_dna(landmarks)
    # For a single observation, stability and spatial_consistency are undefined
    # but we report the other dimensions directly.
    return {
        "hand_shape": _clamp_pct(dna["hand_shape"]),
        "finger_extension": _clamp_pct(dna["finger_extension"]),
        "palm_orientation": _clamp_pct(dna["palm_orientation"]),
        "finger_spread": _clamp_pct(dna["finger_spread"]),
        "stability": 0.0,  # Requires multiple observations
        "spatial_consistency": 0.0,
        "tracking_quality": _clamp_pct(dna["tracking_quality"]),
    }
