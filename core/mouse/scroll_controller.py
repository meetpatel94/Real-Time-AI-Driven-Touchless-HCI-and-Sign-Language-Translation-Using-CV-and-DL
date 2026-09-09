"""Global right-hand movement-to-page-scroll controller.

This module deliberately consumes only the right hand's MediaPipe landmarks. It
never classifies a hand pose, reads custom-gesture matches, or starts another
camera/MediaPipe pipeline.  A small scroll event is published to ``global_state``
for the browser client in ``static/js/global/hand_scroll_controller.js`` to apply
to the application's active scroll container.
"""

from __future__ import annotations

import time
from collections import deque
from typing import Callable, Deque, Optional, Tuple

from config import Config
from services.state_service import global_state


class ScrollController:
    """Turn deliberate vertical right-palm motion into bounded scroll events.

    The controller uses an exponentially-smoothed center between MediaPipe's
    wrist (0) and middle-finger MCP (9).  It requires a minimum vertical travel,
    rejects predominantly horizontal movement, and clears its motion baseline
    after each event.  Together with the cooldown this prevents stationary hands
    and normal landmark jitter from generating a continuous scroll.
    """

    def __init__(
        self,
        event_publisher: Optional[Callable[[str, int], dict]] = None,
        max_history: int = 18,
    ) -> None:
        self.history: Deque[Tuple[float, float, float]] = deque(maxlen=max_history)
        self.last_scroll_time = float("-inf")
        self.scroll_amount = Config.DEFAULT_SCROLL_AMOUNT
        self.threshold = Config.SCROLL_DISPLACEMENT_THRESHOLD
        self.window_sec = Config.SCROLL_WINDOW_SECONDS
        self.cooldown_sec = Config.SCROLL_COOLDOWN_SECONDS
        self.smoothing = Config.SCROLL_SMOOTHING
        self.max_amount = Config.SCROLL_MAX_AMOUNT
        self._smoothed_position: Optional[Tuple[float, float]] = None
        self._publish_event = event_publisher or global_state.publish_hand_scroll

    def set_sensitivity(self, level: str) -> None:
        """Set the base browser distance for low, medium, or high scrolling."""
        level = str(level).lower().strip()
        if level == "low":
            self.scroll_amount = 160
        elif level == "high":
            self.scroll_amount = 440
        else:
            self.scroll_amount = Config.DEFAULT_SCROLL_AMOUNT

    @staticmethod
    def _palm_center(right_hand_landmarks) -> Tuple[float, float]:
        """Return a stable palm position from the shared MediaPipe hand track."""
        points = right_hand_landmarks.landmark
        wrist = points[0]
        middle_mcp = points[9]
        return (
            (float(wrist.x) + float(middle_mcp.x)) / 2.0,
            (float(wrist.y) + float(middle_mcp.y)) / 2.0,
        )

    def _smooth_position(self, raw_x: float, raw_y: float) -> Tuple[float, float]:
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

    def _amount_for(self, vertical_displacement: float) -> int:
        """Scale intentional travel gently while capping each browser update."""
        magnitude = abs(vertical_displacement)
        # The dead-zone has already been crossed.  Additional travel can add up
        # to 55% of the base distance, which feels responsive without runaway
        # movement when a hand is moved rapidly.
        extra_ratio = min(1.0, max(0.0, (magnitude - self.threshold) / (self.threshold * 2.0)))
        amount = self.scroll_amount * (1.0 + 0.55 * extra_ratio)
        return max(1, min(self.max_amount, int(round(amount))))

    def process_hand(self, right_hand_landmarks, timestamp: Optional[float] = None) -> bool:
        """Publish one ``up`` or ``down`` event for intentional right-hand travel.

        ``timestamp`` is optional solely to make the temporal dead-zone and
        cooldown deterministic in unit tests.  Production uses ``monotonic`` so
        system clock changes cannot affect rate limiting.
        """
        if right_hand_landmarks is None:
            self.reset()
            return False

        now = time.monotonic() if timestamp is None else float(timestamp)
        try:
            raw_x, raw_y = self._palm_center(right_hand_landmarks)
        except (AttributeError, IndexError, TypeError, ValueError):
            # A partial tracker result is never a scroll instruction.
            self.reset()
            return False

        palm_x, palm_y = self._smooth_position(raw_x, raw_y)
        self.history.append((palm_x, palm_y, now))

        if (now - self.last_scroll_time) < self.cooldown_sec:
            return False

        recent_samples = [sample for sample in self.history if (now - sample[2]) <= self.window_sec]
        if len(recent_samples) < 3:
            return False

        start_x, start_y, _ = recent_samples[0]
        current_x, current_y, _ = recent_samples[-1]
        delta_y = current_y - start_y
        delta_x = abs(current_x - start_x)

        # Ignore horizontal/diagonal hand positioning.  The gesture is based on
        # vertical displacement only; no finger-count or custom-gesture state is
        # considered here.
        if abs(delta_y) < self.threshold or delta_x > abs(delta_y) * 1.15:
            return False

        direction = "down" if delta_y > 0 else "up"
        amount = self._amount_for(delta_y)
        self._publish_event(direction, amount)
        self.last_scroll_time = now

        # Begin a fresh baseline after an accepted movement.  This prevents one
        # held end-position from firing repeat events while stationary.
        self.history.clear()
        self._smoothed_position = (palm_x, palm_y)
        return True

    def reset(self) -> None:
        """Forget a hand track when camera/gesture input is unavailable."""
        self.history.clear()
        self._smoothed_position = None


scroll_controller = ScrollController()
