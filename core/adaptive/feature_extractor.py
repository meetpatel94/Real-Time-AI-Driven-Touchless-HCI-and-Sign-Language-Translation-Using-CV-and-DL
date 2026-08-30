"""Privacy-preserving feature extraction for personalized gesture learning.

The extractor deliberately accepts landmarks only long enough to derive a small
numeric signature.  It never returns or persists frames, image crops, or the
original landmark object.
"""

import math
from typing import Any, List, Tuple

from core.adaptive.observation import MotionFeatures
from models.personalization import FeatureSignature


class DerivedFeatureExtractor:
    """Build translation/scale-normalized hand and motion representations."""

    _DIRECTION_ORDER = ("stationary", "up", "down", "left", "right", "diagonal")

    @staticmethod
    def _number(value: Any) -> float:
        try:
            value = float(value)
            if math.isfinite(value):
                return value
        except (TypeError, ValueError):
            pass
        return 0.0

    @classmethod
    def _points(cls, landmarks) -> List[Tuple[float, float, float]]:
        points = []
        for point in getattr(landmarks, "landmark", [])[:21]:
            points.append((cls._number(getattr(point, "x", 0.0)),
                           cls._number(getattr(point, "y", 0.0)),
                           cls._number(getattr(point, "z", 0.0))))
        while len(points) < 21:
            points.append((0.0, 0.0, 0.0))
        return points

    @staticmethod
    def _distance(first: Tuple[float, float, float], second: Tuple[float, float, float]) -> float:
        return math.sqrt(sum((first[index] - second[index]) ** 2 for index in range(3)))

    @classmethod
    def _normalization_scale(cls, points: List[Tuple[float, float, float]]) -> float:
        # Wrist-to-middle-MCP is orientation-independent enough for the
        # existing classifier and remains stable when the hand is rotated.
        scale = cls._distance(points[0], points[9])
        if scale < 1e-5:
            xs = [point[0] for point in points]
            ys = [point[1] for point in points]
            scale = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
        return max(scale, 1e-5)

    @classmethod
    def _normalized_geometry(cls, points: List[Tuple[float, float, float]], scale: float) -> List[float]:
        origin = points[0]
        result: List[float] = []
        for point in points:
            result.extend((
                round((point[0] - origin[0]) / scale, 6),
                round((point[1] - origin[1]) / scale, 6),
                round((point[2] - origin[2]) / scale, 6),
            ))
        return result

    @classmethod
    def _proportions(cls, points: List[Tuple[float, float, float]], scale: float) -> List[float]:
        # Finger lengths, palm spans, and selected relative joint distances.
        chains = ((1, 2, 3, 4), (5, 6, 7, 8), (9, 10, 11, 12),
                  (13, 14, 15, 16), (17, 18, 19, 20))
        values: List[float] = []
        for chain in chains:
            values.append(round(sum(cls._distance(points[a], points[b]) for a, b in zip(chain, chain[1:])) / scale, 6))
        values.extend((
            round(cls._distance(points[5], points[17]) / scale, 6),
            round(cls._distance(points[0], points[9]) / scale, 6),
            round(cls._distance(points[0], points[5]) / scale, 6),
            round(cls._distance(points[0], points[17]) / scale, 6),
            round(cls._distance(points[4], points[8]) / scale, 6),
            round(cls._distance(points[8], points[12]) / scale, 6),
            round(cls._distance(points[12], points[16]) / scale, 6),
            round(cls._distance(points[16], points[20]) / scale, 6),
        ))
        return values

    @classmethod
    def _trajectory(cls, motion: MotionFeatures) -> List[float]:
        values: List[float] = []
        for point_x, point_y in tuple(motion.trajectory)[:16]:
            values.extend((round(cls._number(point_x), 6), round(cls._number(point_y), 6)))
        # Keep a fixed compact shape so distances do not depend on frame rate.
        values.extend([0.0] * max(0, 32 - len(values)))
        values.extend([
            round(float(motion.delta_x), 6),
            round(float(motion.delta_y), 6),
            round(float(motion.displacement), 6),
            round(float(motion.speed), 6),
            round(float(motion.stability), 6),
            round(min(1.0, max(0.0, motion.sample_count / 16.0)), 6),
            round(min(1.0, max(0.0, motion.window_seconds / 0.45)), 6),
        ])
        return values

    @classmethod
    def _temporal_pattern(cls, motion: MotionFeatures) -> List[float]:
        direction = str(motion.direction or "stationary")
        one_hot = [1.0 if direction == item else 0.0 for item in cls._DIRECTION_ORDER]
        one_hot.extend((
            round(min(1.0, max(0.0, float(motion.speed) / 2.0)), 6),
            round(min(1.0, max(0.0, float(motion.displacement) / 0.5)), 6),
            round(min(1.0, max(0.0, float(motion.window_seconds) / 0.45)), 6),
        ))
        return one_hot

    @classmethod
    def extract(cls, landmarks, motion: MotionFeatures) -> FeatureSignature:
        points = cls._points(landmarks)
        scale = cls._normalization_scale(points)
        return FeatureSignature(
            landmark_geometry=cls._normalized_geometry(points, scale),
            proportion_features=cls._proportions(points, scale),
            trajectory_features=cls._trajectory(motion),
            motion_speed=cls._number(motion.speed),
            displacement=cls._number(motion.displacement),
            gesture_duration=max(0.0, cls._number(motion.window_seconds)),
            temporal_pattern=cls._temporal_pattern(motion),
        )

    @classmethod
    def from_observation(cls, observation) -> FeatureSignature:
        """Read the compact signature attached by ``GestureObservationBuilder``."""
        return FeatureSignature.from_document(getattr(observation, "derived_features", {}))


feature_extractor = DerivedFeatureExtractor()
