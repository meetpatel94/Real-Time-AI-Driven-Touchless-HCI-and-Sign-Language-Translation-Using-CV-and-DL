import cv2
import math
import numpy as np
from collections import deque, Counter
from typing import Optional, Tuple
from config import Config
from core.recognition.model_manager import model_manager

class Predictor:
    """Handles ROI cropping, letterboxing, normalization, and rolling temporal smoothing."""
    
    def __init__(self):
        self.image_size = getattr(Config, "DATASET_IMAGE_SIZE", (160, 160))
        self.crop_padding = getattr(Config, "DATASET_CROP_PADDING", 0.12)
        self.confidence_threshold = getattr(Config, "RECOGNITION_CONFIDENCE_THRESHOLD", 0.70)
        self.buffer = deque(maxlen=3)

    def extract_hand_roi(self, frame: np.ndarray, landmarks) -> Optional[np.ndarray]:
        frame_h, frame_w, _ = frame.shape
        x_coords = [lm.x * frame_w for lm in landmarks.landmark]
        y_coords = [lm.y * frame_h for lm in landmarks.landmark]

        min_x, max_x = min(x_coords), max(x_coords)
        min_y, max_y = min(y_coords), max(y_coords)

        box_w = max_x - min_x
        box_h = max_y - min_y

        if box_w < 20 or box_h < 20:
            return None

        pad_x = box_w * self.crop_padding
        pad_y = box_h * self.crop_padding

        crop_x1 = max(0, int(math.floor(min_x - pad_x)))
        crop_y1 = max(0, int(math.floor(min_y - pad_y)))
        crop_x2 = min(frame_w, int(math.ceil(max_x + pad_x)))
        crop_y2 = min(frame_h, int(math.ceil(max_y + pad_y)))

        if crop_x2 <= crop_x1 or crop_y2 <= crop_y1:
            return None

        rect_crop = frame[crop_y1:crop_y2, crop_x1:crop_x2]
        h, w = rect_crop.shape[:2]
        if h < 20 or w < 20:
            return None

        target_w, target_h = self.image_size
        scale = min(target_w / w, target_h / h)
        new_w = int(w * scale)
        new_h = int(h * scale)

        resized = cv2.resize(rect_crop, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
        square_canvas = np.zeros((target_h, target_w, 3), dtype=np.uint8)
        offset_x = (target_w - new_w) // 2
        offset_y = (target_h - new_h) // 2
        square_canvas[offset_y:offset_y + new_h, offset_x:offset_x + new_w] = resized

        return square_canvas

    def process_inference(self, roi_bgr: np.ndarray) -> Tuple[str, float, str, float]:
        rgb_img = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2RGB)
        input_tensor = np.expand_dims(rgb_img.astype(np.float32), axis=0)

        preds = model_manager.predict(input_tensor)
        top_idx = int(np.argmax(preds))
        confidence = float(preds[top_idx])

        if confidence >= self.confidence_threshold and top_idx < len(model_manager.classes):
            raw_label = model_manager.classes[top_idx]
        else:
            raw_label = "UNKNOWN"

        raw_conf = round(confidence * 100.0, 1)
        self.buffer.append((raw_label, raw_conf))

        # Temporal smoothing over the 3-sample rolling window
        valid_preds = [item for item in self.buffer if item[0] not in ["NONE", "UNKNOWN"]]
        if not valid_preds:
            return raw_label, raw_conf, "UNKNOWN", raw_conf

        labels = [item[0] for item in valid_preds]
        counts = Counter(labels)
        most_common_label, count = counts.most_common(1)[0]

        if count >= 2:
            matching_confidences = [item[1] for item in valid_preds if item[0] == most_common_label]
            avg_conf = round(sum(matching_confidences) / len(matching_confidences), 1)
            return raw_label, raw_conf, most_common_label, avg_conf

        return raw_label, raw_conf, raw_label, raw_conf

    def reset(self):
        self.buffer.clear()

predictor = Predictor()