"""Converts MediaPipe output into the adaptive domain representation."""

import math
import time
from typing import Optional

from core.adaptive.feature_extractor import feature_extractor
from core.adaptive.motion_tracker import MotionTracker
from core.adaptive.observation import GestureObservation


class GestureObservationBuilder:
    def __init__(self, classifier, motion_tracker: Optional[MotionTracker] = None):
        self.classifier = classifier
        self.motion_tracker = motion_tracker or MotionTracker()

    @staticmethod
    def _tracking_quality(landmarks) -> float:
        valid = 0
        for point in getattr(landmarks, "landmark", []):
            try:
                if math.isfinite(float(point.x)) and math.isfinite(float(point.y)):
                    valid += 1
            except (AttributeError, TypeError, ValueError):
                continue
        return valid / 21.0 if valid else 0.0

    @staticmethod
    def _hand_scale(landmarks) -> float:
        points = landmarks.landmark
        xs = [float(point.x) for point in points]
        ys = [float(point.y) for point in points]
        return math.hypot(max(xs) - min(xs), max(ys) - min(ys))

    def build(self, handedness: str, landmarks, gesture, timestamp: Optional[float] = None) -> GestureObservation:
        now = timestamp if timestamp is not None else time.time()
        gesture_name = getattr(gesture, "value", str(gesture))
        finger_states = self.classifier.get_finger_states(landmarks)
        points = landmarks.landmark
        palm_center = (
            (float(points[0].x) + float(points[9].x)) / 2.0,
            (float(points[0].y) + float(points[9].y)) / 2.0,
        )
        motion = self.motion_tracker.update(handedness, landmarks, now)
        derived_features = feature_extractor.extract(landmarks, motion).to_document()
        return GestureObservation(
            timestamp=now,
            handedness=str(handedness).lower(),
            gesture=gesture_name,
            finger_states=finger_states,
            palm_center=palm_center,
            hand_scale=self._hand_scale(landmarks),
            tracking_quality=self._tracking_quality(landmarks),
            motion=motion,
            derived_features=derived_features,
        )

    def reset_hand(self, handedness: str) -> None:
        self.motion_tracker.reset(handedness)

    def reset(self) -> None:
        self.motion_tracker.reset()
