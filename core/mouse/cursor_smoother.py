import math
from typing import Tuple, Optional
from config import Config

class CursorSmoother:
    """Applies Exponential Moving Average (EMA) and deadzone filters to cursor coordinates."""
    
    def __init__(self, alpha: float = Config.CURSOR_SMOOTHING, deadzone: int = Config.CURSOR_DEADZONE):
        self.alpha = alpha
        self.deadzone = deadzone
        self.prev_x: Optional[float] = None
        self.prev_y: Optional[float] = None

    def smooth(self, target_x: int, target_y: int) -> Tuple[int, int]:
        if self.prev_x is None or self.prev_y is None:
            self.prev_x = float(target_x)
            self.prev_y = float(target_y)
            return target_x, target_y

        # Exponential Moving Average filter
        curr_x = (self.alpha * target_x) + ((1.0 - self.alpha) * self.prev_x)
        curr_y = (self.alpha * target_y) + ((1.0 - self.alpha) * self.prev_y)

        # Calculate distance moved from previous frame
        dx = curr_x - self.prev_x
        dy = curr_y - self.prev_y
        distance = math.hypot(dx, dy)

        # Apply deadzone filter to remove stationary hand jitter
        if distance < self.deadzone:
            return int(self.prev_x), int(self.prev_y)

        self.prev_x = curr_x
        self.prev_y = curr_y

        return int(curr_x), int(curr_y)

    def reset(self):
        """Reset state when hand tracking is lost or gesture stops."""
        self.prev_x = None
        self.prev_y = None