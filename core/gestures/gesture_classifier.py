import math
from typing import Tuple
from core.gestures.gesture_state import GestureType

class GestureClassifier:
    """Classifies geometric MediaPipe hand landmarks for cursor control and edge-triggered fist confirmation."""

    def _is_extended(self, tip, pip, mcp, wrist) -> bool:
        dist_tip = math.hypot(tip.x - wrist.x, tip.y - wrist.y)
        dist_pip = math.hypot(pip.x - wrist.x, pip.y - wrist.y)
        dist_mcp = math.hypot(mcp.x - wrist.x, mcp.y - wrist.y)
        return dist_tip > dist_pip and dist_pip > dist_mcp and tip.y < pip.y

    def _is_folded(self, tip, pip, wrist) -> bool:
        dist_tip = math.hypot(tip.x - wrist.x, tip.y - wrist.y)
        dist_pip = math.hypot(pip.x - wrist.x, pip.y - wrist.y)
        return dist_tip < dist_pip

    def classify(self, landmarks, image_shape: Tuple[int, int]) -> GestureType:
        if not landmarks:
            return GestureType.NONE

        lm = landmarks.landmark
        wrist = lm[0]

        index_ext = self._is_extended(lm[8], lm[6], lm[5], wrist)
        middle_ext = self._is_extended(lm[12], lm[10], lm[9], wrist)
        ring_ext = self._is_extended(lm[16], lm[14], lm[13], wrist)
        pinky_ext = self._is_extended(lm[20], lm[18], lm[17], wrist)

        index_fold = self._is_folded(lm[8], lm[6], wrist)
        middle_fold = self._is_folded(lm[12], lm[10], wrist)
        ring_fold = self._is_folded(lm[16], lm[14], wrist)
        pinky_fold = self._is_folded(lm[20], lm[18], wrist)

        extended_count = sum([index_ext, middle_ext, ring_ext, pinky_ext])
        folded_count = sum([index_fold, middle_fold, ring_fold, pinky_fold])

        # Strict Geometric Fist: At least 3 fingers folded toward palm with 0 fingers extended
        if folded_count >= 3 and extended_count == 0:
            return GestureType.CLOSED_FIST

        # Air Mouse Gestures
        if extended_count == 1 and index_ext:
            return GestureType.ONE_FINGER
        elif extended_count == 2 and index_ext and middle_ext:
            return GestureType.TWO_FINGER
        elif extended_count == 3:
            return GestureType.THREE_FINGER
        elif extended_count == 4:
            return GestureType.FOUR_FINGER
        elif extended_count >= 4:
            return GestureType.FIVE_FINGER

        return GestureType.NONE