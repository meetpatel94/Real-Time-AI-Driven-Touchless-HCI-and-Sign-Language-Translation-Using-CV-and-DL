import time
from collections import deque
from config import Config
from core.mouse.mouse_controller import mouse_controller
from services.logging_service import logger

class ScrollController:
    """Detects deliberate vertical swipes on the Right Hand palm/wrist landmark."""

    def __init__(self):
        self.history = deque(maxlen=10)
        self.last_scroll_time = 0.0
        self.scroll_amount = Config.DEFAULT_SCROLL_AMOUNT
        self.threshold = Config.SCROLL_DISPLACEMENT_THRESHOLD
        self.window_sec = Config.SCROLL_WINDOW_SECONDS
        self.cooldown_sec = Config.SCROLL_COOLDOWN_SECONDS

    def set_sensitivity(self, level: str):
        """Sets scroll step amount: 'low', 'medium', or 'high'."""
        level = str(level).lower().strip()
        if level == "low":
            self.scroll_amount = 160
        elif level == "high":
            self.scroll_amount = 550
        else:
            self.scroll_amount = 320

    def process_hand(self, right_hand_landmarks, is_fist: bool) -> bool:
        """
        Evaluates right-hand wrist (landmark 0) & middle MCP (landmark 9) center.
        Executes scroll on deliberate vertical displacement.
        """
        if right_hand_landmarks is None or is_fist:
            self.reset()
            return False

        now = time.time()
        wrist = right_hand_landmarks.landmark[0]
        mcp = right_hand_landmarks.landmark[9]
        palm_y = (wrist.y + mcp.y) / 2.0
        palm_x = (wrist.x + mcp.x) / 2.0

        self.history.append((palm_x, palm_y, now))

        # Check cooldown
        if (now - self.last_scroll_time) < self.cooldown_sec:
            return False

        # Find earliest sample within sliding time window
        valid_samples = [s for s in self.history if (now - s[2]) <= self.window_sec]
        if len(valid_samples) < 3:
            return False

        start_x, start_y, _ = valid_samples[0]
        curr_x, curr_y, _ = valid_samples[-1]

        delta_y = curr_y - start_y
        delta_x = abs(curr_x - start_x)

        # Ignore diagonal/horizontal cursor motions
        if delta_x > abs(delta_y) * 1.1:
            return False

        # Hand Swiped UP (delta_y < 0) -> Webpage Scrolls DOWN (-amount)
        if delta_y < -self.threshold:
            mouse_controller.scroll(-self.scroll_amount)
            self.last_scroll_time = now
            self.history.clear()
            return True

        # Hand Swiped DOWN (delta_y > 0) -> Webpage Scrolls UP (+amount)
        elif delta_y > self.threshold:
            mouse_controller.scroll(self.scroll_amount)
            self.last_scroll_time = now
            self.history.clear()
            return True

        return False

    def reset(self):
        self.history.clear()

scroll_controller = ScrollController()