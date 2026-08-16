import threading
import time
from collections import deque
from typing import Dict, Any

class RecognitionState:
    """Thread-safe state container handling dual-hand live recognition, stats, and sentence compilation."""
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
        
        self.left_hand_detected = False
        self.right_hand_detected = False
        
        self.raw_label = "NONE"
        self.raw_confidence = 0.0
        self.current_label = "NONE"
        self.confidence = 0.0
        
        self.right_gesture = "NONE"
        self.fist_active = False
        self.fist_triggered = False
        self.sentence = ""
        
        self.inference_fps = 0
        self.camera_fps = 0
        self.model_status = "INITIALIZING"
        self.model_name = "None"
        
        self.recent_predictions = deque(maxlen=8)
        self.test_stats = {
            "total": 0, "correct": 0, "incorrect": 0, "accuracy": 0.0
        }

    def update_prediction(self, raw_label: str, raw_conf: float, stable_label: str, stable_conf: float, hand_detected: bool = True):
        with self.lock:
            self.left_hand_detected = hand_detected
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

    def set_hand_presence(self, left: bool, right: bool):
        with self.lock:
            self.left_hand_detected = left
            self.right_hand_detected = right

    def set_right_gesture(self, gesture: str):
        with self.lock:
            self.right_gesture = gesture

    def commit_current_sign(self) -> bool:
        """Edge-triggered sentence appending (Fist OPEN -> CLOSED)."""
        with self.lock:
            if self.fist_triggered:
                return False  # Already committed in this fist cycle
                
            self.fist_active = True
            self.fist_triggered = True
            
            # Commit only stable, valid predictions
            if self.current_label not in ["NONE", "UNKNOWN"]:
                self.sentence += self.current_label
                return True
        return False

    def release_fist(self):
        """Resets the commit trigger (CLOSED -> OPEN)."""
        with self.lock:
            self.fist_active = False
            self.fist_triggered = False

    def clear_sentence(self):
        with self.lock:
            self.sentence = ""

    def backspace_sentence(self):
        with self.lock:
            if len(self.sentence) > 0:
                self.sentence = self.sentence[:-1]

    def set_fps(self, camera_fps: int, inference_fps: int):
        with self.lock:
            self.camera_fps = camera_fps
            self.inference_fps = inference_fps

    def set_model_status(self, status: str, name: str = "None"):
        with self.lock:
            self.model_status = status
            self.model_name = name

    def get_snapshot(self) -> Dict[str, Any]:
        with self.lock:
            return {
                "left_hand_detected": self.left_hand_detected,
                "right_hand_detected": self.right_hand_detected,
                "hand_detected": self.left_hand_detected,  # Preserves legacy single-hand frontend checks
                "raw_prediction": self.raw_label,
                "raw_confidence": self.raw_confidence,
                "prediction": self.current_label,
                "confidence": self.confidence,
                "right_gesture": self.right_gesture,
                "sentence": self.sentence,
                "inference_fps": self.inference_fps,
                "camera_fps": self.camera_fps,
                "fps": self.camera_fps,
                "model_status": self.model_status,
                "model_name": self.model_name,
                "recent_predictions": list(self.recent_predictions),
            }

recognition_state = RecognitionState()