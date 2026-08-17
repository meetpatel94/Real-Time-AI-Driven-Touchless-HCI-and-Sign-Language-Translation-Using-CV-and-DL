import pyautogui
from typing import Tuple
from config import Config

class CursorMapper:
    """Maps normalized MediaPipe coordinates to physical screen coordinates with sensitivity scaling."""
    
    def __init__(self, edge_margin: float = Config.CURSOR_EDGE_MARGIN):
        self.screen_width, self.screen_height = pyautogui.size()
        self.edge_margin = edge_margin
        self.sensitivity = Config.DEFAULT_CURSOR_SENSITIVITY
        self.center_x = self.screen_width / 2.0
        self.center_y = self.screen_height / 2.0

    def set_sensitivity(self, val: float):
        """Clamps sensitivity between 0.10 (10%) and 1.00 (100%)."""
        self.sensitivity = max(0.10, min(1.0, float(val)))

    def map_coordinates(self, norm_x: float, norm_y: float) -> Tuple[int, int]:
        norm_x = max(0.0, min(1.0, norm_x))
        norm_y = max(0.0, min(1.0, norm_y))

        margin = self.edge_margin
        usable_range = 1.0 - (2 * margin)

        if usable_range > 0:
            scaled_x = (norm_x - margin) / usable_range
            scaled_y = (norm_y - margin) / usable_range
        else:
            scaled_x = norm_x
            scaled_y = norm_y

        scaled_x = max(0.0, min(1.0, scaled_x))
        scaled_y = max(0.0, min(1.0, scaled_y))

        target_x = scaled_x * self.screen_width
        target_y = scaled_y * self.screen_height

        # Apply Sensitivity: scale delta from center based on multiplier (0.5 to 1.5x)
        multiplier = 0.4 + (self.sensitivity * 1.2)
        final_x = self.center_x + (target_x - self.center_x) * multiplier
        final_y = self.center_y + (target_y - self.center_y) * multiplier

        screen_x = int(max(0, min(self.screen_width - 1, round(final_x))))
        screen_y = int(max(0, min(self.screen_height - 1, round(final_y))))

        return screen_x, screen_y