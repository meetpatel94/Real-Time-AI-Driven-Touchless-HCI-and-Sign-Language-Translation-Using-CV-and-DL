"""Landmark normalization and matching features for user-defined gestures.

The custom gesture library deliberately works from MediaPipe hand landmarks and
keeps its representation independent from the existing A-Z image classifier.  A
sample stores raw normalized landmark coordinates plus a compact feature vector:

* translation normalization: wrist is the origin
* scale normalization: palm/finger distances are divided by a stable hand scale
* minor rotation normalization: the wrist -> middle-MCP palm axis is aligned to
  a canonical vertical direction in the image plane
* shape features: selected pairwise distances and joint angles are added to make
  matching less sensitive to camera position and small rotations
"""

from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Sequence, Tuple

Point3D = Tuple[float, float, float]


class CustomGestureFeatureExtractor:
    """Build normalized, fixed-length vectors from MediaPipe landmarks."""

    LANDMARK_COUNT = 21
    FEATURE_SCHEMA_VERSION = 1

    # Distances are rotation/translation invariant after scaling and help keep
    # the matcher robust when the absolute landmark coordinates jitter.
    DISTANCE_PAIRS: Tuple[Tuple[int, int], ...] = (
        (0, 4), (0, 8), (0, 12), (0, 16), (0, 20),
        (4, 8), (8, 12), (12, 16), (16, 20), (4, 20),
        (5, 9), (9, 13), (13, 17), (5, 17),
        (2, 5), (5, 8), (9, 12), (13, 16), (17, 20),
    )

    # Finger joint angles as cosine values.  Cosines are compact and stable
    # under scale/rotation; they improve separation between folded/extended
    # poses without hardcoding any semantic gesture meaning.
    ANGLE_TRIPLES: Tuple[Tuple[int, int, int], ...] = (
        (1, 2, 3), (2, 3, 4),
        (5, 6, 7), (6, 7, 8),
        (9, 10, 11), (10, 11, 12),
        (13, 14, 15), (14, 15, 16),
        (17, 18, 19), (18, 19, 20),
        (0, 5, 8), (0, 9, 12), (0, 13, 16), (0, 17, 20),
    )

    @staticmethod
    def _number(value: Any) -> float:
        try:
            result = float(value)
            return result if math.isfinite(result) else 0.0
        except (TypeError, ValueError):
            return 0.0

    @classmethod
    def _read_point(cls, point: Any) -> Point3D:
        if isinstance(point, dict):
            return (
                cls._number(point.get("x", 0.0)),
                cls._number(point.get("y", 0.0)),
                cls._number(point.get("z", 0.0)),
            )
        return (
            cls._number(getattr(point, "x", 0.0)),
            cls._number(getattr(point, "y", 0.0)),
            cls._number(getattr(point, "z", 0.0)),
        )

    @classmethod
    def points_from_landmarks(cls, landmarks: Any) -> List[Point3D]:
        """Return exactly 21 numeric points from a MediaPipe-like object."""
        source = getattr(landmarks, "landmark", landmarks)
        points = [cls._read_point(point) for point in list(source or [])[:cls.LANDMARK_COUNT]]
        while len(points) < cls.LANDMARK_COUNT:
            points.append((0.0, 0.0, 0.0))
        return points

    @classmethod
    def raw_landmark_dicts(cls, landmarks: Any) -> List[Dict[str, float]]:
        """Serialize the current landmark payload for reproducible samples."""
        source = getattr(landmarks, "landmark", landmarks)
        result: List[Dict[str, float]] = []
        for point in list(source or [])[:cls.LANDMARK_COUNT]:
            if isinstance(point, dict):
                result.append({
                    "x": round(cls._number(point.get("x", 0.0)), 6),
                    "y": round(cls._number(point.get("y", 0.0)), 6),
                    "z": round(cls._number(point.get("z", 0.0)), 6),
                    "visibility": round(cls._number(point.get("visibility", 0.0)), 6),
                })
            else:
                result.append({
                    "x": round(cls._number(getattr(point, "x", 0.0)), 6),
                    "y": round(cls._number(getattr(point, "y", 0.0)), 6),
                    "z": round(cls._number(getattr(point, "z", 0.0)), 6),
                    "visibility": round(cls._number(getattr(point, "visibility", 0.0)), 6),
                })
        while len(result) < cls.LANDMARK_COUNT:
            result.append({"x": 0.0, "y": 0.0, "z": 0.0, "visibility": 0.0})
        return result

    @staticmethod
    def _distance(first: Point3D, second: Point3D) -> float:
        return math.sqrt(sum((first[index] - second[index]) ** 2 for index in range(3)))

    @classmethod
    def _normalization_scale(cls, points: Sequence[Point3D]) -> float:
        # Combine multiple stable spans instead of relying on one segment only.
        candidate_pairs = ((0, 9), (5, 17), (0, 5), (0, 17), (0, 12))
        spans = [cls._distance(points[a], points[b]) for a, b in candidate_pairs]
        positive_spans = [span for span in spans if span > 1e-5]
        if positive_spans:
            return max(sum(positive_spans) / len(positive_spans), 1e-5)

        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        fallback = math.hypot(max(xs) - min(xs), max(ys) - min(ys)) if points else 0.0
        return max(fallback, 1e-5)

    @classmethod
    def _rotation_to_canonical(cls, points: Sequence[Point3D]) -> float:
        """Angle that rotates the palm axis to a canonical upward vector."""
        wrist = points[0]
        middle_mcp = points[9]
        vx = middle_mcp[0] - wrist[0]
        vy = middle_mcp[1] - wrist[1]
        if math.hypot(vx, vy) < 1e-6:
            return 0.0
        current_angle = math.atan2(vy, vx)
        canonical_up = -math.pi / 2.0
        return canonical_up - current_angle

    @classmethod
    def normalized_points(cls, landmarks: Any) -> List[Point3D]:
        """Return wrist-origin, scale-normalized, rotation-normalized points."""
        points = cls.points_from_landmarks(landmarks)
        origin = points[0]
        scale = cls._normalization_scale(points)
        theta = cls._rotation_to_canonical(points)
        cos_t = math.cos(theta)
        sin_t = math.sin(theta)

        normalized: List[Point3D] = []
        for point in points:
            dx = (point[0] - origin[0]) / scale
            dy = (point[1] - origin[1]) / scale
            dz = (point[2] - origin[2]) / scale
            rx = dx * cos_t - dy * sin_t
            ry = dx * sin_t + dy * cos_t
            normalized.append((round(rx, 6), round(ry, 6), round(dz, 6)))
        return normalized

    @classmethod
    def normalized_landmark_dicts(cls, landmarks: Any) -> List[Dict[str, float]]:
        return [
            {"x": point[0], "y": point[1], "z": point[2]}
            for point in cls.normalized_points(landmarks)
        ]

    @staticmethod
    def _angle_cosine(a: Point3D, b: Point3D, c: Point3D) -> float:
        ba = (a[0] - b[0], a[1] - b[1], a[2] - b[2])
        bc = (c[0] - b[0], c[1] - b[1], c[2] - b[2])
        norm_ba = math.sqrt(sum(value * value for value in ba))
        norm_bc = math.sqrt(sum(value * value for value in bc))
        if norm_ba < 1e-6 or norm_bc < 1e-6:
            return 0.0
        dot = sum(ba[index] * bc[index] for index in range(3))
        return max(-1.0, min(1.0, dot / (norm_ba * norm_bc)))

    @classmethod
    def feature_vector(cls, landmarks: Any) -> List[float]:
        points = cls.normalized_points(landmarks)
        vector: List[float] = []

        for x_coord, y_coord, z_coord in points:
            vector.extend((x_coord, y_coord, z_coord))

        for first_idx, second_idx in cls.DISTANCE_PAIRS:
            vector.append(round(cls._distance(points[first_idx], points[second_idx]), 6))

        for a_idx, b_idx, c_idx in cls.ANGLE_TRIPLES:
            vector.append(round(cls._angle_cosine(points[a_idx], points[b_idx], points[c_idx]), 6))

        return vector

    @classmethod
    def tracking_quality(cls, landmarks: Any) -> float:
        source = getattr(landmarks, "landmark", landmarks)
        valid = 0
        for point in list(source or [])[:cls.LANDMARK_COUNT]:
            try:
                x_value = float(point.get("x", 0.0)) if isinstance(point, dict) else float(getattr(point, "x", 0.0))
                y_value = float(point.get("y", 0.0)) if isinstance(point, dict) else float(getattr(point, "y", 0.0))
                if math.isfinite(x_value) and math.isfinite(y_value):
                    valid += 1
            except (TypeError, ValueError):
                continue
        return valid / float(cls.LANDMARK_COUNT)

    @classmethod
    def sample_representation(cls, landmarks: Any) -> Dict[str, Any]:
        """Build the serializable per-sample representation."""
        return {
            "feature_schema_version": cls.FEATURE_SCHEMA_VERSION,
            "raw_landmarks": cls.raw_landmark_dicts(landmarks),
            "normalized_landmarks": cls.normalized_landmark_dicts(landmarks),
            "features": cls.feature_vector(landmarks),
            "tracking_quality": round(cls.tracking_quality(landmarks), 3),
        }


def vector_distance(first: Sequence[float], second: Sequence[float]) -> float:
    """Root-mean-square distance for two fixed/near-fixed feature vectors."""
    size = max(len(first), len(second))
    if size <= 0:
        return 1.0
    total = 0.0
    for index in range(size):
        left = float(first[index]) if index < len(first) else 0.0
        right = float(second[index]) if index < len(second) else 0.0
        total += (left - right) ** 2
    return math.sqrt(total / size)


def mean_vector(vectors: Iterable[Sequence[float]]) -> List[float]:
    values = [list(vector) for vector in vectors if vector]
    if not values:
        return []
    size = max(len(vector) for vector in values)
    means: List[float] = []
    for index in range(size):
        component_values = [float(vector[index]) if index < len(vector) else 0.0 for vector in values]
        means.append(round(sum(component_values) / len(component_values), 6))
    return means


def mirrored_feature_vector(vector: Sequence[float]) -> List[float]:
    """Mirror normalized x coordinates while keeping invariant features intact.

    This is used only for gestures configured as ``Either`` hand.  The feature
    vector layout starts with 21 normalized x/y/z triplets; later distance and
    angle components are already mirror invariant.
    """
    mirrored = [float(value) for value in vector]
    coordinate_values = CustomGestureFeatureExtractor.LANDMARK_COUNT * 3
    for index in range(0, min(len(mirrored), coordinate_values), 3):
        mirrored[index] = round(-mirrored[index], 6)
    return mirrored


feature_extractor = CustomGestureFeatureExtractor()
