import threading
import time
from collections import deque
from typing import Dict, Any, Optional
from config import Config

class RecognitionState:
    """Thread-safe state container handling dual-hand live recognition, debounce confirmation, and sentence compiling."""
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
        
        # Debounce State Tracker
        self.fist_frame_counter = 0
        self.is_fist_locked = False
        self.last_commit_timestamp = 0.0
        self.last_confirmed_letter = ""
        self.confirmation_active_until = 0.0
        
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
            
            if stable_label != self.current_label and stable_label not in ["NONE", "UNKNOWN", "--"]:
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
            if not left:
                self.current_label = "NONE"
                self.confidence = 0.0

    def set_right_gesture(self, gesture: str):
        with self.lock:
            self.right_gesture = gesture

    def process_right_hand_fist(
        self,
        is_fist_detected: bool,
        confidence_threshold: float = 70.0,
        label_override: Optional[str] = None,
        confidence_override: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Debounce a confirmation pose, optionally for a validated personal sign.

        The override is only supplied by the adaptive layer after its own
        confidence/evidence checks; ordinary base-model calls retain the exact
        legacy behavior.
        """
        with self.lock:
            now = time.time()
            committed = False
            candidate_label = label_override if label_override else self.current_label
            candidate_confidence = (
                confidence_override if confidence_override is not None else self.confidence
            )

            if is_fist_detected:
                self.fist_frame_counter += 1
                
                if (
                    self.fist_frame_counter >= Config.FIST_CONSECUTIVE_FRAMES
                    and not self.is_fist_locked
                    and (now - self.last_commit_timestamp) >= Config.FIST_COOLDOWN_SECONDS
                ):
                    if (
                        self.left_hand_detected
                        and candidate_label not in ["NONE", "UNKNOWN", "--"]
                        and candidate_confidence >= confidence_threshold
                    ):
                        self.sentence += candidate_label
                        self.last_confirmed_letter = candidate_label
                        self.last_commit_timestamp = now
                        self.confirmation_active_until = now + 1.2
                        self.is_fist_locked = True
                        committed = True
            else:
                self.fist_frame_counter = 0
                self.is_fist_locked = False

            is_confirming = (self.fist_frame_counter > 0 and not self.is_fist_locked)

            return {
                "committed": committed,
                "is_confirming": is_confirming,
                "confirmed_active": now < self.confirmation_active_until,
                "last_confirmed": self.last_confirmed_letter
            }

    def set_sentence(self, new_text: str):
        with self.lock:
            self.sentence = new_text

    def clear_sentence(self):
        with self.lock:
            self.sentence = ""
            self.last_confirmed_letter = ""

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
            now = time.time()
            return {
                "left_hand_detected": self.left_hand_detected,
                "right_hand_detected": self.right_hand_detected,
                "hand_detected": self.left_hand_detected,
                "raw_prediction": self.raw_label,
                "raw_confidence": self.raw_confidence,
                "prediction": self.current_label,
                "confidence": self.confidence,
                "right_gesture": self.right_gesture,
                "is_confirming": (self.fist_frame_counter > 0 and not self.is_fist_locked),
                "last_confirmed": self.last_confirmed_letter,
                "confirmed_active": now < self.confirmation_active_until,
                "sentence": self.sentence,
                "inference_fps": self.inference_fps,
                "camera_fps": self.camera_fps,
                "fps": self.camera_fps,
                "model_status": self.model_status,
                "model_name": self.model_name,
                "recent_predictions": list(self.recent_predictions),
            }

recognition_state = RecognitionState()