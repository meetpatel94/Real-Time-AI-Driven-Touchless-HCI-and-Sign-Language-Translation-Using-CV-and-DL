"""Global right-hand vertical page-scroll controller.

This module deliberately consumes only the right hand's MediaPipe landmarks
that the single existing ``GestureEngine`` camera loop already produces.  It
never classifies a custom gesture, never reads an A-Z prediction, and never
starts another camera or MediaPipe pipeline.  A small, rate-limited
{direction, amount} event is published to ``global_state`` for the browser
client in ``static/js/global/hand_scroll_controller.js``, which animates it
onto the application's active scroll container.

Gesture contract
----------------
A scroll is emitted only while ALL of the following hold on the right hand:

1. a stable **open-palm posture** — every finger extended, fingers held
   together (never a single finger, never a fist, never a partial track);
2. that posture has been stable for several consecutive frames;
3. the smoothed palm centre moves with a **clear vertical velocity** above a
   dead-zone (tiny movement, jitter and stationary hands never scroll);
4. the vertical component **clearly dominates** the horizontal component, so
   sideways and diagonal movement is rejected;
5. the direction is consistent across consecutive frames and any reversal
   restarts the movement baseline (direction lock).

Amounts are rate based (``pixels/second x elapsed``) and are emitted at a
bounded interval so the browser can ease them into smooth scrolling instead of
jumping one large amount per camera frame.
"""

from __future__ import annotations

import math
import time
from collections import deque
from typing import Callable, Deque, List, Optional, Sequence, Tuple

from config import Config
from services.state_service import global_state

Point = Tuple[float, float]
Sample = Tuple[float, float, float]


class ScrollController:
    """Convert deliberate vertical right-palm travel into smooth scroll events."""

    # MediaPipe hand landmark groups (same indices used by GestureClassifier).
    _PALM_POINTS = (0, 5, 9, 17)          # wrist + index/middle/pinky MCP
    _FINGERS = (                          # (mcp, pip, tip)
        (5, 6, 8),                        # index
        (9, 10, 12),                      # middle
        (13, 14, 16),                     # ring
        (17, 18, 20),                     # pinky
    )
    _THUMB_MCP = 2
    _THUMB_TIP = 4
    _INDEX_MCP = 5

    def __init__(
        self,
        event_publisher: Optional[Callable[[str, int], dict]] = None,
        max_history: int = Config.SCROLL_MAX_HISTORY,
    ) -> None:
        self.history: Deque[Sample] = deque(maxlen=max_history)
        self.scroll_amount = Config.DEFAULT_SCROLL_AMOUNT
        self.scroll_speed_factor = Config.SCROLL_SPEED_FACTOR_MEDIUM

        # Posture / temporal stability
        self.pose_min_frames = Config.SCROLL_POSE_MIN_FRAMES
        self.pose_tolerance = Config.SCROLL_POSE_STABILITY_TOLERANCE
        self.extension_margin = Config.SCROLL_POSE_EXTENSION_MARGIN
        self.min_extension_span = Config.SCROLL_POSE_MIN_EXTENSION_SPAN
        self.straightness_ratio = Config.SCROLL_POSE_STRAIGHTNESS_RATIO
        self.min_finger_length = Config.SCROLL_POSE_MIN_FINGER_LENGTH
        self.max_spread_ratio = Config.SCROLL_POSE_MAX_SPREAD_RATIO

        # Motion gating
        self.smoothing = Config.SCROLL_SMOOTHING
        self.velocity_window = Config.SCROLL_VELOCITY_WINDOW_SECONDS
        self.min_samples = Config.SCROLL_MIN_SAMPLES
        self.min_speed = Config.SCROLL_MIN_SPEED
        self.dominance = Config.SCROLL_VERTICAL_DOMINANCE
        self.trigger_distance = Config.SCROLL_TRIGGER_DISTANCE
        self.direction_frames = Config.SCROLL_DIRECTION_FRAMES
        self.direction_jitter = Config.SCROLL_DIRECTION_JITTER
        self.max_frame_travel = Config.SCROLL_MAX_FRAME_TRAVEL

        # Emission / smoothing control
        self.min_event_interval = Config.SCROLL_MIN_EVENT_INTERVAL
        self.max_event_interval = Config.SCROLL_MAX_EVENT_INTERVAL
        self.max_amount = Config.SCROLL_MAX_AMOUNT
        self.max_speed = Config.SCROLL_MAX_SPEED_PX_PER_SEC
        self.min_speed_px = Config.SCROLL_SPEED_MIN_PX_PER_SEC
        self.speed_gain = Config.SCROLL_SPEED_GAIN

        self._smoothed_position: Optional[Point] = None
        self._last_raw_position: Optional[Point] = None
        self._pose_signature: Optional[Tuple[float, ...]] = None
        self._pose_stable_frames = 0
        self._stroke_direction: Optional[str] = None
        self._stroke_armed = False
        self._stroke_anchor: Point = (0.0, 0.0)
        self._last_event_time: Optional[float] = None
        self._publish_event = event_publisher or global_state.publish_hand_scroll

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------
    def set_sensitivity(self, level: str) -> None:
        """Set the OS scroll distance and the browser scroll speed together.

        ``scroll_amount`` keeps its legacy meaning (a single pyautogui scroll
        step used by learned personalised mappings).  ``scroll_speed_factor``
        scales the continuous pixels/second budget used for the global browser
        relay.
        """
        level = str(level).lower().strip()
        if level == "low":
            self.scroll_amount = 160
            self.scroll_speed_factor = Config.SCROLL_SPEED_FACTOR_LOW
        elif level == "high":
            self.scroll_amount = 440
            self.scroll_speed_factor = Config.SCROLL_SPEED_FACTOR_HIGH
        else:
            self.scroll_amount = Config.DEFAULT_SCROLL_AMOUNT
            self.scroll_speed_factor = Config.SCROLL_SPEED_FACTOR_MEDIUM

    # ------------------------------------------------------------------
    # Landmark helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _point(landmark) -> Point:
        return (float(landmark.x), float(landmark.y))

    @staticmethod
    def _distance(first: Point, second: Point) -> float:
        return math.hypot(first[0] - second[0], first[1] - second[1])

    @classmethod
    def _landmark_points(cls, landmarks) -> Optional[List[Point]]:
        points = [cls._point(landmark) for landmark in landmarks.landmark]
        if len(points) < 21:
            return None
        return points

    def palm_center(self, landmarks) -> Point:
        """Return a stable palm centre from wrist/index/middle/pinky MCP.

        Only palm landmarks are used: fingertip-only movement therefore cannot
        move this point, which keeps finger poses from scrolling the page.
        """
        points = self._landmark_points(landmarks)
        if points is None:
            raise ValueError("A complete 21-landmark hand track is required.")
        total_x = 0.0
        total_y = 0.0
        for index in self._PALM_POINTS:
            total_x += points[index][0]
            total_y += points[index][1]
        count = float(len(self._PALM_POINTS))
        return (total_x / count, total_y / count)

    def evaluate_open_palm(self, landmarks) -> Tuple[bool, Tuple[float, ...]]:
        """Return ``(is_stable_open_palm, pose_signature)`` for one hand track.

        The signature is a scale-invariant fingerprint of the finger geometry so
        the caller can require the same posture across consecutive frames.  It
        is built from distances only, which keeps it independent of the hand's
        distance to the camera and of where the hand sits in the frame.
        """
        points = self._landmark_points(landmarks)
        if points is None:
            return False, ()

        wrist = points[0]
        index_mcp = points[self._INDEX_MCP]
        pinky_mcp = points[17]
        middle_mcp = points[9]

        # Hand scale: palm length, with the MCP span as a fallback for poses
        # where the wrist is partially occluded.
        scale = max(
            self._distance(wrist, middle_mcp),
            self._distance(index_mcp, pinky_mcp) * 0.9,
            1e-4,
        )

        signature: List[float] = []
        finger_lengths: List[float] = []
        extended_fingers = 0

        for mcp_index, pip_index, tip_index in self._FINGERS:
            mcp = points[mcp_index]
            pip = points[pip_index]
            tip = points[tip_index]

            wrist_to_tip = self._distance(wrist, tip) / scale
            wrist_to_pip = self._distance(wrist, pip) / scale
            mcp_to_tip = self._distance(mcp, tip) / scale
            mcp_to_pip = self._distance(mcp, pip) / scale

            finger_lengths.append(mcp_to_tip)
            signature.append(round(mcp_to_tip, 4))

            # A finger counts as extended only when every gate agrees:
            #   * the tip sits clearly further from the wrist than the PIP
            #   * the tip is far enough from the wrist in absolute terms
            #   * the finger is straight, not curled back towards the palm
            # Thresholds are calibrated on real MediaPipe tracks measured on
            # open-palm photos (wrist->tip 1.38-1.97, MCP->tip 0.66-1.40) and
            # on fist photos (wrist->tip 0.59-1.23, MCP->tip 0.15-0.34).
            straight = mcp_to_tip > max(
                mcp_to_pip * self.straightness_ratio,
                self.min_finger_length,
            )
            extended = (
                wrist_to_tip > wrist_to_pip * self.extension_margin
                and wrist_to_tip > self.min_extension_span
                and straight
            )
            if extended:
                extended_fingers += 1

        # The thumb is tracked for posture stability but is NOT a validity
        # gate: a raised palm often keeps the thumb beside the fingers, so a
        # strict abduction test would reject perfectly valid scroll poses.
        # Four extended fingers already separate an open palm from a fist.
        thumb_tip = points[self._THUMB_TIP]
        thumb_tip_span = self._distance(thumb_tip, index_mcp) / scale
        signature.append(round(thumb_tip_span, 4))

        # Fingers together: adjacent fingertips must not be splayed apart.
        tips = [points[8], points[12], points[16], points[20]]
        mean_length = (sum(finger_lengths) / len(finger_lengths)) if finger_lengths else 0.0
        if mean_length <= 1e-4:
            return False, ()
        spread = max(
            self._distance(tips[index], tips[index + 1]) / scale for index in range(len(tips) - 1)
        ) / mean_length
        signature.append(round(spread, 4))

        # Every finger must be extended: a single finger, a peace sign or a
        # partial track can never arm the scroll gesture.  The thumb is not
        # required (see above) and the fingertips must stay together.
        is_open_palm = (
            extended_fingers == len(self._FINGERS)
            and spread <= self.max_spread_ratio
        )
        return is_open_palm, tuple(signature)

    def _signature_stable(self, previous: Sequence[float], current: Sequence[float]) -> bool:
        if not previous or len(previous) != len(current):
            return False
        return all(abs(first - second) <= self.pose_tolerance for first, second in zip(previous, current))

    # ------------------------------------------------------------------
    # Motion helpers
    # ------------------------------------------------------------------
    def _smooth_position(self, raw_x: float, raw_y: float) -> Point:
        if self._smoothed_position is None:
            self._smoothed_position = (raw_x, raw_y)
            return self._smoothed_position

        previous_x, previous_y = self._smoothed_position
        alpha = self.smoothing
        self._smoothed_position = (
            previous_x + alpha * (raw_x - previous_x),
            previous_y + alpha * (raw_y - previous_y),
        )
        return self._smoothed_position

    def _reset_motion(self, x: float, y: float) -> None:
        """Re-baseline movement tracking without dropping the posture state."""
        self.history.clear()
        self._smoothed_position = (x, y)
        self._stroke_direction = None
        self._stroke_armed = False
        self._stroke_anchor = (x, y)
        # Restart the pacing clock as well: the time spent re-baselining must
        # never be converted into one large catch-up scroll.
        self._last_event_time = None

    def _direction_consistent(self, direction: str) -> bool:
        """Require the same vertical direction across consecutive frames."""
        samples = list(self.history)[-(self.direction_frames + 1):]
        if len(samples) < 3:
            return False
        sign = 1.0 if direction == "down" else -1.0
        for previous, current in zip(samples, samples[1:]):
            delta = (current[1] - previous[1]) * sign
            if delta < -self.direction_jitter:
                return False
        return True

    def _pixels_per_second(self, speed: float) -> float:
        """Map an intentional vertical speed onto a bounded scroll speed."""
        extra = max(0.0, speed - self.min_speed)
        pixels = (self.min_speed_px + extra * self.speed_gain) * self.scroll_speed_factor
        return max(60.0, min(self.max_speed, pixels))

    # ------------------------------------------------------------------
    # Frame processing
    # ------------------------------------------------------------------
    def process_hand(self, right_hand_landmarks, timestamp: Optional[float] = None) -> bool:
        """Publish a smoothed ``up``/``down`` event for intentional hand travel.

        ``timestamp`` is optional solely to make the temporal gates deterministic
        in unit tests.  Production uses ``monotonic`` so system clock changes
        cannot affect rate limiting.
        """
        if right_hand_landmarks is None:
            self.reset()
            return False

        now = time.monotonic() if timestamp is None else float(timestamp)

        try:
            open_palm, signature = self.evaluate_open_palm(right_hand_landmarks)
            raw_x, raw_y = self.palm_center(right_hand_landmarks)
        except (AttributeError, IndexError, TypeError, ValueError):
            # A partial or malformed tracker result is never a scroll command.
            self.reset()
            return False

        if not open_palm:
            # Any other pose (fist, one finger, peace sign, partial track)
            # cancels the gesture and clears the movement baseline.
            self.reset()
            return False

        # A landmark jump this large is a tracking glitch or a different hand
        # entering the frame, never a real hand movement: re-baseline instead
        # of turning it into a large scroll.
        previous_raw = self._last_raw_position
        self._last_raw_position = (raw_x, raw_y)
        if previous_raw is not None and self._distance(previous_raw, (raw_x, raw_y)) > self.max_frame_travel:
            self._reset_motion(raw_x, raw_y)
            return False

        palm_x, palm_y = self._smooth_position(raw_x, raw_y)
        self.history.append((palm_x, palm_y, now))

        # --- posture stability -------------------------------------------------
        if self._signature_stable(self._pose_signature or (), signature):
            self._pose_stable_frames += 1
        else:
            self._pose_stable_frames = 1
        self._pose_signature = signature

        if self._pose_stable_frames < self.pose_min_frames:
            # Keep the movement baseline fresh until the posture has proved it
            # is stable, so motion before arming is never credited.
            self._stroke_direction = None
            self._stroke_armed = False
            self._stroke_anchor = (palm_x, palm_y)
            return False

        # --- vertical velocity -------------------------------------------------
        window = [sample for sample in self.history if (now - sample[2]) <= self.velocity_window]
        if len(window) < self.min_samples:
            return False

        start_x, start_y, _ = window[0]
        current_x, current_y, _ = window[-1]
        elapsed_window = window[-1][2] - window[0][2]
        if elapsed_window <= 1e-4:
            return False

        velocity_y = (current_y - start_y) / elapsed_window
        velocity_x = (current_x - start_x) / elapsed_window

        # Dead-zone: small or slow movement never scrolls.
        if abs(velocity_y) < self.min_speed:
            return False

        # Vertical dominance: sideways and diagonal movement never scrolls.
        if abs(velocity_y) < abs(velocity_x) * self.dominance:
            return False

        direction = "down" if velocity_y > 0 else "up"

        # Temporal stability: the last frames must agree with that direction.
        if not self._direction_consistent(direction):
            return False

        # --- direction lock and dead-zone travel -------------------------------
        if self._stroke_direction is None or direction != self._stroke_direction:
            # A reversal restarts the baseline before the opposite direction is
            # allowed to scroll, which removes up/down jitter.
            self._stroke_direction = direction
            self._stroke_armed = False
            self._stroke_anchor = (palm_x, palm_y)
            return False

        travel = abs(palm_y - self._stroke_anchor[1])
        if not self._stroke_armed:
            if travel < self.trigger_distance:
                return False
            self._stroke_armed = True

        # --- bounded, rate-limited emission ------------------------------------
        elapsed = self.min_event_interval
        if self._last_event_time is not None:
            # Clamp the elapsed window so a paused or irregular frame clock can
            # never turn into one large jump.
            elapsed = max(0.0, min(now - self._last_event_time, self.max_event_interval))
        if elapsed < self.min_event_interval:
            return False

        pixels_per_second = self._pixels_per_second(abs(velocity_y))
        amount = int(round(pixels_per_second * elapsed))
        amount = max(1, min(self.max_amount, amount))

        self._publish_event(direction, amount)
        self._last_event_time = now
        return True

    def reset(self) -> None:
        """Forget a hand track when the pose, camera or gesture input changes."""
        self.history.clear()
        self._smoothed_position = None
        self._last_raw_position = None
        self._pose_signature = None
        self._pose_stable_frames = 0
        self._stroke_direction = None
        self._stroke_armed = False
        self._stroke_anchor = (0.0, 0.0)
        self._last_event_time = None


scroll_controller = ScrollController()
