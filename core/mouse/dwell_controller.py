import time
from typing import Tuple
from config import Config
from core.gestures.gesture_state import InteractionState
from core.mouse.mouse_controller import mouse_controller
from services.logging_service import logger

class DwellController:
    """Centralized dwell-to-click state tracker."""

    def __init__(self, duration: float = Config.DWELL_DURATION_SECONDS):
        self.duration = duration
        self.dwell_start_time = None
        self.completed = False
        self.last_state = InteractionState.IDLE

    def update(self, is_two_finger: bool) -> Tuple[int, bool, InteractionState]:
        """
        Updates dwell progression.
        Returns: (dwell_progress, selection_ready, interaction_state)
        """
        if not is_two_finger:
            was_dwelling = self.dwell_start_time is not None and not self.completed
            self.dwell_start_time = None
            self.completed = False
            state = InteractionState.CANCELLED if was_dwelling else InteractionState.IDLE
            return 0, False, state

        # If already completed this cycle, lock until released
        if self.completed:
            return 100, False, InteractionState.SELECTED

        # Initialize dwell start
        now = time.time()
        if self.dwell_start_time is None:
            self.dwell_start_time = now

        elapsed = now - self.dwell_start_time
        progress = int(min(100.0, (elapsed / self.duration) * 100.0))

        if progress >= 100:
            self.completed = True
            mouse_controller.click()
            return 100, True, InteractionState.SELECTED

        return progress, False, InteractionState.DWELLING

    def reset(self):
        self.dwell_start_time = None
        self.completed = False

dwell_controller = DwellController()