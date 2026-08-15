import cv2
import numpy as np
from typing import Optional

class FrameProcessor:
    """Frame processing and encoding utilities."""
    
    @staticmethod
    def encode_to_jpeg(frame: np.ndarray, quality: int = 80) -> Optional[bytes]:
        if frame is None:
            return None
        encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), quality]
        success, encoded_img = cv2.imencode('.jpg', frame, encode_param)
        if success:
            return encoded_img.tobytes()
        return None

    @staticmethod
    def create_placeholder_frame(width: int = 640, height: int = 480, message: str = "CAMERA OFF") -> np.ndarray:
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        frame[:] = (20, 24, 33)
        
        cv2.putText(
            frame,
            message,
            (width // 2 - 120, height // 2),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.9,
            (100, 116, 139),
            2,
            cv2.LINE_AA
        )
        return frame