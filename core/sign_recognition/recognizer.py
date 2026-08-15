import os
import cv2
import time
import math
import pickle
import json
import threading
from collections import deque, Counter
import numpy as np
import tensorflow as tf
from typing import Dict, Any, List, Optional
from config import Config
from services.logging_service import logger

class SignRecognizer:
    """Asynchronous, thread-isolated sign alphabet inference service with zero frame backlog."""
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(SignRecognizer, cls).__new__(cls)
                cls._instance._initialize()
            return cls._instance

    def _initialize(self):
        self.model: Optional[tf.keras.Model] = None
        self.classes: List[str] = [chr(i) for i in range(ord('A'), ord('Z') + 1)]
        self.model_status = "INITIALIZING"
        self.model_name = "None"
        
        self.confidence_threshold = getattr(Config, "RECOGNITION_CONFIDENCE_THRESHOLD", 0.70)
        self.image_size = getattr(Config, "DATASET_IMAGE_SIZE", (160, 160))
        self.crop_padding = getattr(Config, "DATASET_CROP_PADDING", 0.12)
        
        # Producer-Consumer atomic buffer (holds ONLY the single latest ROI)
        self._latest_roi: Optional[np.ndarray] = None
        self._roi_lock = threading.Lock()
        self._new_frame_event = threading.Event()
        self.is_running = False
        self.inference_thread: Optional[threading.Thread] = None

        # Metrics
        self.raw_prediction = "NONE"
        self.raw_confidence = 0.0
        self.stable_prediction = "NONE"
        self.stable_confidence = 0.0
        self.hand_detected = False
        self.inference_fps = 0
        self._inf_count = 0
        self._inf_timer = time.time()
        
        # Snappy smoothing buffer (3 samples for low latency transitions)
        self.buffer = deque(maxlen=3)
        self.recent_history = deque(maxlen=8)
        self.history_lock = threading.Lock()
        
        self.test_stats = {
            "total": 0,
            "correct": 0,
            "incorrect": 0,
            "accuracy": 0.0
        }
        
        self._load_model_and_labels()
        self._start_worker()

    def _load_model_and_labels(self):
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        model_paths = [
            getattr(Config, "RECOGNITION_MODEL_PATH", os.path.join(base_dir, "models", "gesture_model.h5")),
            getattr(Config, "RECOGNITION_ALT_MODEL_PATH", os.path.join(base_dir, "models", "sign_alphabet", "sign_alphabet_model.keras"))
        ]
        
        loaded_model_path = None
        for p in model_paths:
            if os.path.isfile(p):
                loaded_model_path = p
                break

        if not loaded_model_path:
            self.model_status = "MODEL NOT FOUND"
            logger.warning(f"Sign Recognition: No trained model found at {model_paths}")
            return

        try:
            logger.info(f"Loading trained sign recognition model from {loaded_model_path}...")
            self.model = tf.keras.models.load_model(loaded_model_path, compile=False)
            self.model_name = os.path.basename(loaded_model_path)
            self.model_status = "READY"
            
            # Warm-up pass
            dummy = np.zeros((1, self.image_size[0], self.image_size[1], 3), dtype=np.float32)
            _ = self.model(dummy, training=False)
            logger.info("Model loaded and warmed up successfully.")
        except Exception as e:
            self.model_status = "MODEL ERROR"
            logger.error(f"Failed to load sign recognition model: {e}")

        # Load label mapping
        label_encoder_path = getattr(Config, "LABEL_ENCODER_PATH", os.path.join(base_dir, "models", "label_encoder.pkl"))
        class_names_path = getattr(Config, "CLASS_NAMES_JSON_PATH", os.path.join(base_dir, "models", "sign_alphabet", "class_names.json"))

        if os.path.isfile(label_encoder_path):
            try:
                with open(label_encoder_path, "rb") as f:
                    self.classes = pickle.load(f)
            except Exception as e:
                logger.error(f"Error loading label encoder pkl: {e}")
        elif os.path.isfile(class_names_path):
            try:
                with open(class_names_path, "r") as f:
                    self.classes = json.load(f)
            except Exception as e:
                logger.error(f"Error loading class names json: {e}")

    def _start_worker(self):
        self.is_running = True
        self.inference_thread = threading.Thread(target=self._inference_loop, daemon=True)
        self.inference_thread.start()

    def submit_frame(self, raw_frame: np.ndarray, primary_hand) -> None:
        """Non-blocking producer method called by camera thread."""
        if self.model is None or raw_frame is None or not primary_hand:
            self.hand_detected = False
            return

        self.hand_detected = True
        
        # Fast ROI crop calculation
        frame_h, frame_w, _ = raw_frame.shape
        x_coords = [lm.x * frame_w for lm in primary_hand.landmark]
        y_coords = [lm.y * frame_h for lm in primary_hand.landmark]

        min_x, max_x = min(x_coords), max(x_coords)
        min_y, max_y = min(y_coords), max(y_coords)

        box_w = max_x - min_x
        box_h = max_y - min_y

        if box_w < 20 or box_h < 20:
            return

        pad_x = box_w * self.crop_padding
        pad_y = box_h * self.crop_padding

        crop_x1 = max(0, int(math.floor(min_x - pad_x)))
        crop_y1 = max(0, int(math.floor(min_y - pad_y)))
        crop_x2 = min(frame_w, int(math.ceil(max_x + pad_x)))
        crop_y2 = min(frame_h, int(math.ceil(max_y + pad_y)))

        if crop_x2 <= crop_x1 or crop_y2 <= crop_y1:
            return

        rect_crop = raw_frame[crop_y1:crop_y2, crop_x1:crop_x2]
        h, w = rect_crop.shape[:2]
        if h < 20 or w < 20:
            return

        target_w, target_h = self.image_size
        scale = min(target_w / w, target_h / h)
        new_w = int(w * scale)
        new_h = int(h * scale)

        resized = cv2.resize(rect_crop, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
        square_canvas = np.zeros((target_h, target_w, 3), dtype=np.uint8)
        offset_x = (target_w - new_w) // 2
        offset_y = (target_h - new_h) // 2
        square_canvas[offset_y:offset_y + new_h, offset_x:offset_x + new_w] = resized

        # Overwrite single latest ROI pointer (drops any stale unprocessed frames)
        with self._roi_lock:
            self._latest_roi = square_canvas
        self._new_frame_event.set()

    def _inference_loop(self):
        """Dedicated consumer thread running decoupled inference."""
        while self.is_running:
            # Wait for a new frame or timeout at 50ms
            if not self._new_frame_event.wait(timeout=0.05):
                continue

            self._new_frame_event.clear()

            with self._roi_lock:
                crop = self._latest_roi
                self._latest_roi = None

            if crop is None or self.model is None:
                continue

            try:
                rgb_img = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
                input_tensor = np.expand_dims(rgb_img.astype(np.float32), axis=0)

                preds = self.model(input_tensor, training=False).numpy()[0]
                top_idx = int(np.argmax(preds))
                confidence = float(preds[top_idx])

                if confidence >= self.confidence_threshold and top_idx < len(self.classes):
                    predicted_label = self.classes[top_idx]
                else:
                    predicted_label = "UNKNOWN"

                self.raw_prediction = predicted_label
                self.raw_confidence = round(confidence * 100.0, 1)

                self.buffer.append((predicted_label, self.raw_confidence))
                self._update_stable_prediction()

                # Calculate Inference FPS
                self._inf_count += 1
                now = time.time()
                elapsed = now - self._inf_timer
                if elapsed >= 1.0:
                    self.inference_fps = int(self._inf_count / elapsed)
                    self._inf_count = 0
                    self._inf_timer = now

            except Exception as e:
                logger.error(f"Inference error: {e}")

            time.sleep(0.01)

    def _update_stable_prediction(self):
        if not self.buffer:
            self.stable_prediction = "NONE"
            self.stable_confidence = 0.0
            return

        valid_preds = [item for item in self.buffer if item[0] not in ["NONE", "UNKNOWN"]]
        if not valid_preds:
            self.stable_prediction = "UNKNOWN"
            self.stable_confidence = self.raw_confidence
            return

        labels = [item[0] for item in valid_preds]
        counts = Counter(labels)
        most_common_label, count = counts.most_common(1)[0]

        # Requires 2/3 agreement for low latency and high stability
        if count >= 2:
            matching_confidences = [item[1] for item in valid_preds if item[0] == most_common_label]
            avg_conf = round(sum(matching_confidences) / len(matching_confidences), 1)

            if most_common_label != self.stable_prediction and most_common_label != "UNKNOWN":
                with self.history_lock:
                    self.recent_history.appendleft({
                        "label": most_common_label,
                        "confidence": avg_conf,
                        "time": time.strftime("%H:%M:%S")
                    })

            self.stable_prediction = most_common_label
            self.stable_confidence = avg_conf

    def mark_test(self, expected_letter: str) -> Dict[str, Any]:
        expected = expected_letter.upper().strip()
        predicted = self.stable_prediction.upper().strip()
        is_correct = (expected == predicted) and (self.stable_confidence >= (self.confidence_threshold * 100.0))

        self.test_stats["total"] += 1
        if is_correct:
            self.test_stats["correct"] += 1
        else:
            self.test_stats["incorrect"] += 1

        self.test_stats["accuracy"] = round(
            (self.test_stats["correct"] / self.test_stats["total"]) * 100.0, 1
        )

        return {
            "expected": expected,
            "predicted": predicted,
            "confidence": self.stable_confidence,
            "is_correct": is_correct,
            "stats": self.test_stats.copy()
        }

    def reset_tests(self) -> Dict[str, Any]:
        self.test_stats = {"total": 0, "correct": 0, "incorrect": 0, "accuracy": 0.0}
        return self.test_stats.copy()

    def get_state(self) -> Dict[str, Any]:
        with self.history_lock:
            recent = list(self.recent_history)

        return {
            "model_status": self.model_status,
            "model_name": self.model_name,
            "hand_detected": self.hand_detected,
            "raw_prediction": self.raw_prediction,
            "raw_confidence": self.raw_confidence,
            "prediction": self.stable_prediction,
            "confidence": self.stable_confidence,
            "inference_fps": self.inference_fps,
            "recent_predictions": recent,
            "test_stats": self.test_stats
        }

sign_recognizer = SignRecognizer()