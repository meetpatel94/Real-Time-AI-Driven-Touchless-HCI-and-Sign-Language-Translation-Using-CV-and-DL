"""Low-cost temporal feature extraction for the existing MediaPipe loop."""

from collections import deque
import math
import threading
import time
from typing import Deque, Dict, Optional, Tuple

from core.adaptive.observation import MotionFeatures


class MotionTracker:
    """Tracks palm trajectories independently for left and right hands.

    It stores only a short normalized trajectory.  No frames or landmark payloads
    are persisted, which keeps the real-time pipeline bounded and privacy-friendly.
    """

    def __init__(self, max_samples: int = 16, window_seconds: float = 0.45):
        self.max_samples = max_samples
        self.window_seconds = window_seconds
        self._history: Dict[str, Deque[Tuple[float, float, float]]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _palm_center(landmarks) -> Tuple[float, float]:
        points = landmarks.landmark
        wrist = points[0]
        middle_mcp = points[9]
        return ((float(wrist.x) + float(middle_mcp.x)) / 2.0,
                (float(wrist.y) + float(middle_mcp.y)) / 2.0)

    def update(self, handedness: str, landmarks, timestamp: Optional[float] = None) -> MotionFeatures:
        now = timestamp if timestamp is not None else time.time()
        center_x, center_y = self._palm_center(landmarks)
        key = str(handedness).lower()

        with self._lock:
            samples = self._history.setdefault(key, deque(maxlen=self.max_samples))
            samples.append((now, center_x, center_y))
            while samples and (now - samples[0][0]) > self.window_seconds:
                samples.popleft()

            if len(samples) < 2:
                return MotionFeatures(sample_count=len(samples))

            first_time, first_x, first_y = samples[0]
            elapsed = max(0.001, now - first_time)
            delta_x = center_x - first_x
            delta_y = center_y - first_y
            displacement = math.hypot(delta_x, delta_y)
            speed = displacement / elapsed

            # Small tracking jitter is stationary; otherwise distinguish vertical
            # swipes from cursor-like horizontal movement and diagonal motion.
            if displacement < 0.012:
                direction = "stationary"
            elif abs(delta_y) > abs(delta_x) * 1.25:
                direction = "down" if delta_y > 0 else "up"
            elif abs(delta_x) > abs(delta_y) * 1.25:
                direction = "right" if delta_x > 0 else "left"
            else:
                direction = "diagonal"

            # A normalized speed of zero is perfectly stable.  Values above 1.5
            # represent rapid movement in the camera coordinate system.
            stability = max(0.0, min(1.0, 1.0 - (speed / 1.5)))
            return MotionFeatures(
                delta_x=delta_x,
                delta_y=delta_y,
                speed=speed,
                displacement=displacement,
                direction=direction,
                stability=stability,
                sample_count=len(samples),
                window_seconds=elapsed,
            )

    def reset(self, handedness: Optional[str] = None) -> None:
        with self._lock:
            if handedness is None:
                self._history.clear()
            else:
                self._history.pop(str(handedness).lower(), None)
