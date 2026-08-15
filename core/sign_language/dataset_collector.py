import os
import cv2
import time
import math
import threading
import numpy as np
from typing import Dict, Any, Optional, Tuple
from config import Config
from services.logging_service import logger

class DatasetCollector:
    """Manages clean A-Z sign language RGB image dataset collection with axis-aligned square cropping."""
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(DatasetCollector, cls).__new__(cls)
                cls._instance._initialize()
            return cls._instance

    def _initialize(self):
        self.base_dir = Config.DATASET_BASE_DIR
        self.target_per_class = Config.DATASET_TARGET_PER_CLASS
        self.image_size = Config.DATASET_IMAGE_SIZE
        self.capture_interval = Config.DATASET_CAPTURE_INTERVAL
        self.crop_padding = Config.DATASET_CROP_PADDING
        self.min_landmark_delta = Config.DATASET_MIN_LANDMARK_DELTA

        self.selected_alphabet = "A"
        self.is_collecting = False
        self.last_capture_time = 0.0
        self.prev_landmarks = None
        self.status_message = "Ready"

        self._ensure_dataset_directories()

    def _ensure_dataset_directories(self) -> None:
        """Ensures all 26 alphabet class directories exist."""
        os.makedirs(self.base_dir, exist_ok=True)
        for char_code in range(ord('A'), ord('Z') + 1):
            letter_dir = os.path.join(self.base_dir, chr(char_code))
            os.makedirs(letter_dir, exist_ok=True)

    def set_selected_alphabet(self, letter: str) -> bool:
        letter = letter.upper().strip()
        if len(letter) == 1 and 'A' <= letter <= 'Z':
            self.selected_alphabet = letter
            return True
        return False

    def set_target(self, target: int) -> None:
        if target > 0:
            self.target_per_class = int(target)

    def start_collection(self, letter: Optional[str] = None, target: Optional[int] = None) -> bool:
        if letter:
            self.set_selected_alphabet(letter)
        if target:
            self.set_target(target)

        current_count = self.get_class_count(self.selected_alphabet)
        if current_count >= self.target_per_class:
            self.status_message = f"Target already reached for {self.selected_alphabet}"
            self.is_collecting = False
            return False

        self.is_collecting = True
        self.prev_landmarks = None
        self.status_message = f"Collecting samples for {self.selected_alphabet}..."
        logger.info(f"Started dataset collection for class '{self.selected_alphabet}' (Target: {self.target_per_class})")
        return True

    def stop_collection(self) -> None:
        self.is_collecting = False
        self.status_message = "Collection stopped"
        logger.info("Dataset collection stopped.")

    def get_class_count(self, letter: str) -> int:
        letter_dir = os.path.join(self.base_dir, letter.upper())
        if not os.path.exists(letter_dir):
            return 0
        return len([f for f in os.listdir(letter_dir) if f.lower().endswith(('.jpg', '.jpeg'))])

    def get_all_counts(self) -> Dict[str, int]:
        counts = {}
        for char_code in range(ord('A'), ord('Z') + 1):
            letter = chr(char_code)
            counts[letter] = self.get_class_count(letter)
        return counts

    def clear_class(self, letter: str) -> bool:
        letter = letter.upper().strip()
        letter_dir = os.path.join(self.base_dir, letter)
        if not os.path.exists(letter_dir):
            return False

        try:
            for f in os.listdir(letter_dir):
                file_path = os.path.join(letter_dir, f)
                if os.path.isfile(file_path):
                    os.remove(file_path)
            logger.info(f"Cleared all dataset images for class '{letter}'")
            return True
        except Exception as e:
            logger.error(f"Error clearing class '{letter}': {e}")
            return False

    def _validate_hand_crop(self, crop: np.ndarray) -> Tuple[bool, str]:
        """Validates cropped hand image quality and content."""
        if crop is None or crop.size == 0:
            return False, "Empty crop"

        h, w = crop.shape[:2]
        if h < 40 or w < 40:
            return False, "Hand too small or far"

        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        mean_val = float(np.mean(gray))

        if mean_val < 20.0:
            return False, "Sample too dark"
        if mean_val > 250.0:
            return False, "Sample overexposed"

        laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()
        if laplacian_var < 10.0:
            return False, "Hand image blurry"

        return True, "Valid"

    def _is_duplicate_motion(self, landmarks) -> bool:
        """Determines if hand has moved sufficiently compared to last capture."""
        if self.prev_landmarks is None:
            return False

        total_delta = 0.0
        for i in range(21):
            curr_pt = landmarks.landmark[i]
            prev_pt = self.prev_landmarks[i]
            dist = math.hypot(curr_pt.x - prev_pt[0], curr_pt.y - prev_pt[1])
            total_delta += dist

        avg_delta = total_delta / 21.0
        return avg_delta < self.min_landmark_delta

    def _extract_axis_aligned_hand_crop(self, frame: np.ndarray, landmarks) -> Optional[np.ndarray]:
        """
        Extracts a normal upright axis-aligned bounding box around all 21 landmarks
        with padding, and letterboxes to a square target without distortion or masking.
        """
        frame_h, frame_w, _ = frame.shape
        x_coords = [lm.x * frame_w for lm in landmarks.landmark]
        y_coords = [lm.y * frame_h for lm in landmarks.landmark]

        min_x, max_x = min(x_coords), max(x_coords)
        min_y, max_y = min(y_coords), max(y_coords)

        box_w = max_x - min_x
        box_h = max_y - min_y

        if box_w < 20 or box_h < 20:
            return None

        # Add 12-15% padding around the complete hand boundary
        pad_x = box_w * self.crop_padding
        pad_y = box_h * self.crop_padding

        crop_x1 = int(math.floor(min_x - pad_x))
        crop_y1 = int(math.floor(min_y - pad_y))
        crop_x2 = int(math.ceil(max_x + pad_x))
        crop_y2 = int(math.ceil(max_y + pad_y))

        # Clamp safely to frame boundaries
        crop_x1 = max(0, crop_x1)
        crop_y1 = max(0, crop_y1)
        crop_x2 = min(frame_w, crop_x2)
        crop_y2 = min(frame_h, crop_y2)

        if crop_x2 <= crop_x1 or crop_y2 <= crop_y1:
            return None

        # Direct upright rectangular crop from original frame
        rect_crop = frame[crop_y1:crop_y2, crop_x1:crop_x2]
        h, w = rect_crop.shape[:2]

        if h < 20 or w < 20:
            return None

        # Letterbox into an exact square target without stretching
        target_w, target_h = self.image_size
        scale = min(target_w / w, target_h / h)
        new_w = int(w * scale)
        new_h = int(h * scale)

        resized = cv2.resize(rect_crop, (new_w, new_h), interpolation=cv2.INTER_LINEAR)

        # Create target canvas with neutral dark border padding
        square_canvas = np.zeros((target_h, target_w, 3), dtype=np.uint8)
        offset_x = (target_w - new_w) // 2
        offset_y = (target_h - new_h) // 2

        square_canvas[offset_y:offset_y + new_h, offset_x:offset_x + new_w] = resized

        return square_canvas

    def process_frame(self, raw_frame: np.ndarray, hand_landmarks) -> None:
        """Processes frame and saves sample if collection is active."""
        if not self.is_collecting or raw_frame is None or not hand_landmarks:
            return

        current_count = self.get_class_count(self.selected_alphabet)
        if current_count >= self.target_per_class:
            self.is_collecting = False
            self.status_message = f"Collection complete for {self.selected_alphabet}"
            logger.info(f"Target of {self.target_per_class} reached for class {self.selected_alphabet}.")
            return

        now = time.time()
        if (now - self.last_capture_time) < self.capture_interval:
            return

        primary_hand = hand_landmarks[0]

        if self._is_duplicate_motion(primary_hand):
            return

        # Extract axis-aligned hand crop
        crop = self._extract_axis_aligned_hand_crop(raw_frame, primary_hand)
        if crop is None:
            self.status_message = "Keep full hand inside camera view"
            return

        is_valid, msg = self._validate_hand_crop(crop)
        if not is_valid:
            self.status_message = f"Invalid sample: {msg}"
            return

        # Convert BGR to RGB for dataset storage
        rgb_image = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)

        # Save sequential JPEG
        letter_dir = os.path.join(self.base_dir, self.selected_alphabet)
        next_index = current_count + 1
        filename = f"{self.selected_alphabet}_{next_index:04d}.jpg"
        filepath = os.path.join(letter_dir, filename)

        try:
            cv2.imwrite(filepath, cv2.cvtColor(rgb_image, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 95])
            self.last_capture_time = now
            self.prev_landmarks = [(lm.x, lm.y) for lm in primary_hand.landmark]
            self.status_message = f"Captured {filename} ({next_index}/{self.target_per_class})"
        except Exception as e:
            logger.error(f"Failed to write image {filepath}: {e}")
            self.status_message = "Disk write failure"

    def get_status(self) -> Dict[str, Any]:
        count = self.get_class_count(self.selected_alphabet)
        target = self.target_per_class
        percentage = round((count / target * 100.0), 1) if target > 0 else 0.0
        all_counts = self.get_all_counts()
        total_images = sum(all_counts.values())
        total_target = target * 26

        return {
            "selected_alphabet": self.selected_alphabet,
            "target": target,
            "count": count,
            "percentage": min(100.0, percentage),
            "is_collecting": self.is_collecting,
            "status_message": self.status_message,
            "total_images": total_images,
            "total_target": total_target,
            "is_completed": count >= target
        }

dataset_collector = DatasetCollector()