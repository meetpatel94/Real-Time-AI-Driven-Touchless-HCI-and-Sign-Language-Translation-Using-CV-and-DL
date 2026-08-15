import threading
import time
from collections import deque
from typing import Dict, Any, List

class RecognitionState:
    """Thread-safe state container for live recognition and validation metrics."""
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(RecognitionState, cls).__new__(cls)
                cls._instance._initialize()
            return cls._instance

    def _initialize(self):
        self.lock = threading.Lock()
        self.hand_detected = False
        self.raw_label = "NONE"
        self.raw_confidence = 0.0
        self.current_label = "NONE"
        self.confidence = 0.0
        self.inference_fps = 0
        self.camera_fps = 0
        self.model_status = "INITIALIZING"
        self.model_name = "None"
        self.recent_predictions = deque(maxlen=8)
        self.test_stats = {
            "total": 0,
            "correct": 0,
            "incorrect": 0,
            "accuracy": 0.0
        }

    def update_prediction(self, raw_label: str, raw_conf: float, stable_label: str, stable_conf: float, hand_detected: bool):
        with self.lock:
            self.hand_detected = hand_detected
            self.raw_label = raw_label
            self.raw_confidence = raw_conf
            
            if stable_label != self.current_label and stable_label not in ["NONE", "UNKNOWN"]:
                self.recent_predictions.appendleft({
                    "label": stable_label,
                    "confidence": stable_conf,
                    "time": time.strftime("%H:%M:%S")
                })
                
            self.current_label = stable_label
            self.confidence = stable_conf

    def set_fps(self, camera_fps: int, inference_fps: int):
        with self.lock:
            self.camera_fps = camera_fps
            self.inference_fps = inference_fps

    def set_model_status(self, status: str, name: str = "None"):
        with self.lock:
            self.model_status = status
            self.model_name = name

    def mark_test(self, expected_letter: str, confidence_threshold: float = 70.0) -> Dict[str, Any]:
        with self.lock:
            expected = expected_letter.upper().strip()
            predicted = self.current_label.upper().strip()
            is_correct = (expected == predicted) and (self.confidence >= confidence_threshold)

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
                "confidence": self.confidence,
                "is_correct": is_correct,
                "stats": self.test_stats.copy()
            }

    def reset_tests(self) -> Dict[str, Any]:
        with self.lock:
            self.test_stats = {
                "total": 0,
                "correct": 0,
                "incorrect": 0,
                "accuracy": 0.0
            }
            return self.test_stats.copy()

    def get_snapshot(self) -> Dict[str, Any]:
        with self.lock:
            return {
                "hand_detected": self.hand_detected,
                "raw_prediction": self.raw_label,
                "raw_confidence": self.raw_confidence,
                "prediction": self.current_label,
                "label": self.current_label,
                "confidence": self.confidence,
                "inference_fps": self.inference_fps,
                "camera_fps": self.camera_fps,
                "fps": self.camera_fps,
                "model_status": self.model_status,
                "model_name": self.model_name,
                "recent_predictions": list(self.recent_predictions),
                "test_stats": self.test_stats.copy()
            }

recognition_state = RecognitionState()