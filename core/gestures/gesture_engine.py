import threading
import time
import cv2
import mediapipe as mp
from core.camera.camera_manager import camera_manager
from core.gestures.gesture_classifier import GestureClassifier
from core.gestures.gesture_state import GestureType, InteractionState
from core.mouse.cursor_mapper import CursorMapper
from core.mouse.cursor_smoother import CursorSmoother
from core.mouse.mouse_controller import mouse_controller
from core.mouse.dwell_controller import dwell_controller
from core.sign_language.dataset_collector import dataset_collector
from core.recognition.inference_worker import inference_worker
from core.recognition.recognition_state import recognition_state
from services.logging_service import logger
from services.state_service import global_state

class GestureEngine:
    """Master pipeline: camera -> MediaPipe -> gestures -> cursor/dwell/recognition -> UI feed."""
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(GestureEngine, cls).__new__(cls)
                cls._instance._initialize()
            return cls._instance

    def _initialize(self):
        self.is_running = False
        self.thread = None
        self.classifier = GestureClassifier()
        self.mapper = CursorMapper()
        self.smoother = CursorSmoother()
        self.mp_hands = mp.solutions.hands
        self.mp_draw = mp.solutions.drawing_utils
        self.mp_styles = mp.solutions.drawing_styles
        self.hands = None

    def start(self) -> None:
        with self._lock:
            if self.is_running:
                return
            logger.info("Starting Global Gesture Engine (model_complexity=0)...")
            self.hands = self.mp_hands.Hands(
                static_image_mode=False,
                max_num_hands=2,
                model_complexity=0,  # Fast CPU tracking
                min_detection_confidence=0.6,
                min_tracking_confidence=0.6
            )
            self.is_running = True
            self.thread = threading.Thread(target=self._process_loop, daemon=True)
            self.thread.start()
            logger.info("Global Gesture Engine started.")

    def stop(self) -> None:
        with self._lock:
            if not self.is_running:
                return
            self.is_running = False
            if self.thread and self.thread.is_alive():
                self.thread.join(timeout=1.0)
            if self.hands:
                self.hands.close()
                self.hands = None

    def _process_loop(self) -> None:
        while self.is_running:
            state = global_state.get_state()
            if not state["camera_enabled"]:
                self.smoother.reset()
                dwell_controller.reset()
                inference_worker.notify_no_hand()
                time.sleep(0.05)
                continue

            frame = camera_manager.get_raw_frame()
            if frame is None:
                time.sleep(0.01)
                continue

            display_frame = frame.copy()
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            results = self.hands.process(rgb_frame)

            # Update shared camera FPS
            cam_fps = state.get("fps", 0)
            recognition_state.set_fps(camera_fps=cam_fps, inference_fps=inference_worker.current_inf_fps)

            if results.multi_hand_landmarks:
                primary_hand = results.multi_hand_landmarks[0]

                for hand_landmarks in results.multi_hand_landmarks:
                    self.mp_draw.draw_landmarks(
                        display_frame,
                        hand_landmarks,
                        self.mp_hands.HAND_CONNECTIONS,
                        self.mp_styles.get_default_hand_landmarks_style(),
                        self.mp_styles.get_default_hand_connections_style()
                    )

                # Process dataset collection if active
                dataset_collector.process_frame(frame, results.multi_hand_landmarks)

                # Submit hand to decoupled inference worker for BOTH recognition and studio modules
                active_mod = state.get("active_module")
                if active_mod in ["recognition", "studio"]:
                    inference_worker.submit_hand(frame, primary_hand)

                gesture = self.classifier.classify(primary_hand, frame.shape[:2])
                index_tip = primary_hand.landmark[8]
                raw_x, raw_y = self.mapper.map_coordinates(index_tip.x, index_tip.y)

                if state["gesture_enabled"]:
                    if gesture == GestureType.ONE_FINGER:
                        dwell_controller.reset()
                        smooth_x, smooth_y = self.smoother.smooth(raw_x, raw_y)
                        mouse_controller.move_cursor(smooth_x, smooth_y)

                        global_state.update_state({
                            "hand_detected": True,
                            "gesture": gesture.value,
                            "cursor_x": smooth_x,
                            "cursor_y": smooth_y,
                            "dwell_active": False,
                            "dwell_progress": 0,
                            "selection_ready": False,
                            "interaction_state": InteractionState.ONE_FINGER.value
                        })
                    elif gesture == GestureType.TWO_FINGER:
                        progress, ready, i_state = dwell_controller.update(is_two_finger=True)
                        global_state.update_state({
                            "hand_detected": True,
                            "gesture": gesture.value,
                            "dwell_active": True,
                            "dwell_progress": progress,
                            "selection_ready": ready,
                            "interaction_state": i_state.value
                        })
                    else:
                        dwell_controller.reset()
                        self.smoother.reset()
                        global_state.update_state({
                            "hand_detected": True,
                            "gesture": gesture.value,
                            "dwell_active": False,
                            "dwell_progress": 0,
                            "selection_ready": False,
                            "interaction_state": InteractionState.IDLE.value
                        })
                else:
                    dwell_controller.reset()
                    self.smoother.reset()
                    global_state.update_state({
                        "hand_detected": True,
                        "gesture": gesture.value,
                        "dwell_active": False,
                        "dwell_progress": 0,
                        "selection_ready": False,
                        "interaction_state": InteractionState.IDLE.value
                    })
            else:
                dwell_controller.reset()
                self.smoother.reset()
                active_mod = state.get("active_module")
                if active_mod in ["recognition", "studio"]:
                    inference_worker.notify_no_hand()

                global_state.update_state({
                    "hand_detected": False,
                    "gesture": GestureType.NONE.value,
                    "dwell_active": False,
                    "dwell_progress": 0,
                    "selection_ready": False,
                    "interaction_state": InteractionState.IDLE.value
                })

            camera_manager.set_display_frame(display_frame)
            time.sleep(0.01)

gesture_engine = GestureEngine()