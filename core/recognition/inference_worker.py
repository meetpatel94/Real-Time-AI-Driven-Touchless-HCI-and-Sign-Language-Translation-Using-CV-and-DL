import time
import threading
import numpy as np
from typing import Optional
from config import Config
from core.recognition.predictor import predictor
from core.recognition.recognition_state import recognition_state

class InferenceWorker:
    """Producer-consumer background worker with single-slot atomic buffer."""
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(InferenceWorker, cls).__new__(cls)
                cls._instance._initialize()
            return cls._instance

    def _initialize(self):
        self.latest_roi: Optional[np.ndarray] = None
        self.roi_lock = threading.Lock()
        self.new_frame_event = threading.Event()
        self.is_running = False
        self.worker_thread: Optional[threading.Thread] = None

        self.target_interval = 1.0 / 10.0  # Target ~10 FPS inference
        self.inf_count = 0
        self.inf_timer = time.time()
        self.current_inf_fps = 0

        self.start()

    def start(self):
        if not self.is_running:
            self.is_running = True
            self.worker_thread = threading.Thread(target=self._run, daemon=True)
            self.worker_thread.start()

    def submit_hand(self, raw_frame: np.ndarray, primary_hand):
        """Non-blocking producer method called from the camera tracking thread."""
        roi = predictor.extract_hand_roi(raw_frame, primary_hand)
        if roi is None:
            return

        with self.roi_lock:
            self.latest_roi = roi
        self.new_frame_event.set()

    def notify_no_hand(self):
        predictor.reset()
        recognition_state.update_prediction("NONE", 0.0, "NONE", 0.0, hand_detected=False)

    def _run(self):
        while self.is_running:
            # Wait for newest frame event or 50ms timeout
            if not self.new_frame_event.wait(timeout=0.05):
                continue

            self.new_frame_event.clear()

            with self.roi_lock:
                crop = self.latest_roi
                self.latest_roi = None

            if crop is None:
                continue

            start_t = time.time()
            raw_label, raw_conf, stable_label, stable_conf = predictor.process_inference(crop)
            recognition_state.update_prediction(raw_label, raw_conf, stable_label, stable_conf, hand_detected=True)

            # Update actual completed Inference FPS
            self.inf_count += 1
            now = time.time()
            elapsed = now - self.inf_timer
            if elapsed >= 1.0:
                self.current_inf_fps = int(self.inf_count / elapsed)
                self.inf_count = 0
                self.inf_timer = now
                cam_fps = getattr(recognition_state, "camera_fps", 0)
                recognition_state.set_fps(camera_fps=cam_fps, inference_fps=self.current_inf_fps)

            # Throttle loop to maintain 8-12 FPS budget on CPU
            infer_dur = time.time() - start_t
            sleep_time = max(0.005, self.target_interval - infer_dur)
            time.sleep(sleep_time)

inference_worker = InferenceWorker()