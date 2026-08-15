import pyautogui
from services.logging_service import logger

pyautogui.PAUSE = 0.0
pyautogui.FAILSAFE = False

class MouseController:
    """Controls OS cursor positioning and click actuation."""

    def __init__(self):
        self.screen_width, self.screen_height = pyautogui.size()
        logger.info(f"Initialized MouseController. Screen size: {self.screen_width}x{self.screen_height}")

    def move_cursor(self, x: int, y: int) -> bool:
        try:
            target_x = max(0, min(x, self.screen_width - 1))
            target_y = max(0, min(y, self.screen_height - 1))
            pyautogui.moveTo(target_x, target_y, _pause=False)
            return True
        except Exception as e:
            logger.error(f"Error executing OS mouse move: {e}")
            return False

    def click(self) -> bool:
        try:
            pyautogui.click(_pause=False)
            logger.info("Executed OS LEFT CLICK.")
            return True
        except Exception as e:
            logger.error(f"Error executing OS click: {e}")
            return False

mouse_controller = MouseController()