import pyautogui
from typing import Tuple
from config import Config

class CursorMapper:
    """Maps normalized MediaPipe coordinates (0.0-1.0) to actual OS monitor resolution."""
    
    def __init__(self, edge_margin: float = Config.CURSOR_EDGE_MARGIN):
        # Fetch actual physical screen resolution dynamically
        self.screen_width, self.screen_height = pyautogui.size()
        self.edge_margin = edge_margin

    def map_coordinates(self, norm_x: float, norm_y: float) -> Tuple[int, int]:
        """
        Maps normalized coordinates to physical screen coordinates.
        Uses edge margin clamping so screen edges/corners are easily reachable.
        """
        # Clamp inputs to valid ranges
        norm_x = max(0.0, min(1.0, norm_x))
        norm_y = max(0.0, min(1.0, norm_y))

        # Scale coordinate range [margin, 1 - margin] to [0.0, 1.0]
        margin = self.edge_margin
        usable_range = 1.0 - (2 * margin)

        if usable_range > 0:
            scaled_x = (norm_x - margin) / usable_range
            scaled_y = (norm_y - margin) / usable_range
        else:
            scaled_x = norm_x
            scaled_y = norm_y

        # Clamp to [0.0, 1.0] bounded range
        scaled_x = max(0.0, min(1.0, scaled_x))
        scaled_y = max(0.0, min(1.0, scaled_y))

        # Calculate final physical screen coordinates
        screen_x = int(scaled_x * self.screen_width)
        screen_y = int(scaled_y * self.screen_height)

        # Ensure absolute upper boundary is within screen pixel bounds
        screen_x = min(screen_x, self.screen_width - 1)
        screen_y = min(screen_y, self.screen_height - 1)

        return screen_x, screen_y