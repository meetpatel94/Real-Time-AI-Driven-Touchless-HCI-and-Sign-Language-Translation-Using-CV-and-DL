import cv2
import threading
import time
from typing import Optional
from services.logging_service import logger
from services.state_service import global_state

class CameraManager:
    """Thread-safe camera hardware manager with annotated display buffer."""
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(CameraManager, cls).__new__(cls)
                cls._instance._initialize()
            return cls._instance

    def _initialize(self):
        self.cap: Optional[cv2.VideoCapture] = None
        self.is_running = False
        self.thread: Optional[threading.Thread] = None
        self.raw_frame: Optional[cv2.Mat] = None
        self.display_frame: Optional[cv2.Mat] = None
        self.frame_lock = threading.Lock()
        self.fps = 0
        self.frame_count = 0
        self.start_time = time.time()

    def start(self, camera_index: int = 0) -> bool:
        with self._lock:
            if self.is_running:
                return True
            logger.info(f"Initializing camera hardware at index {camera_index}...")
            self.cap = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
            if not self.cap.isOpened():
                self.cap = cv2.VideoCapture(camera_index)
            
            if not self.cap.isOpened():
                logger.error(f"Failed to open camera hardware at index {camera_index}")
                return False

            self.is_running = True
            global_state.set_camera_state(True)
            self.thread = threading.Thread(target=self._capture_loop, daemon=True)
            self.thread.start()
            logger.info("Camera stream started.")
            return True

    def stop(self) -> None:
        with self._lock:
            if not self.is_running:
                return
            logger.info("Stopping camera hardware stream...")
            self.is_running = False
            global_state.set_camera_state(False)
            if self.thread and self.thread.is_alive():
                self.thread.join(timeout=1.0)
            if self.cap:
                self.cap.release()
                self.cap = None
            self.raw_frame = None
            self.display_frame = None
            logger.info("Camera stream stopped.")

    def _capture_loop(self) -> None:
        self.start_time = time.time()
        self.frame_count = 0
        while self.is_running:
            if not self.cap or not self.cap.isOpened():
                break
            ret, frame = self.cap.read()
            if not ret:
                time.sleep(0.01)
                continue

            frame = cv2.flip(frame, 1)

            with self.frame_lock:
                self.raw_frame = frame
                if self.display_frame is None:
                    self.display_frame = frame.copy()

            self.frame_count += 1
            elapsed = time.time() - self.start_time
            if elapsed >= 1.0:
                self.fps = int(self.frame_count / elapsed)
                global_state.update_state({"fps": self.fps})
                self.frame_count = 0
                self.start_time = time.time()

            time.sleep(0.01)

    def get_raw_frame(self) -> Optional[cv2.Mat]:
        with self.frame_lock:
            return self.raw_frame.copy() if self.raw_frame is not None else None

    def set_display_frame(self, frame: cv2.Mat) -> None:
        with self.frame_lock:
            self.display_frame = frame

    def get_display_frame(self) -> Optional[cv2.Mat]:
        with self.frame_lock:
            if self.display_frame is not None:
                return self.display_frame.copy()
            return self.raw_frame.copy() if self.raw_frame is not None else None

camera_manager = CameraManager()