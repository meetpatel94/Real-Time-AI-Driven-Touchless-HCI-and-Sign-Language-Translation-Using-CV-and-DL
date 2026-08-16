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
    """Master pipeline: Single camera source -> MediaPipe -> Independent Left/Right Hand routing."""
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
        self.last_log_time = 0.0

    def start(self) -> None:
        with self._lock:
            if self.is_running:
                return
            logger.info("Starting Global Gesture Engine (Dual-Hand Mode, max_num_hands=2)...")
            self.hands = self.mp_hands.Hands(
                static_image_mode=False,
                max_num_hands=2,
                model_complexity=0,
                min_detection_confidence=0.5,
                min_tracking_confidence=0.5
            )
            self.is_running = True
            self.thread = threading.Thread(target=self._process_loop, daemon=True)
            self.thread.start()
            logger.info("Global Gesture Engine started.")

    def stop(self) -> None:
        with self._lock:
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
                recognition_state.set_hand_presence(False, False)
                time.sleep(0.05)
                continue

            frame = camera_manager.get_raw_frame()
            if frame is None:
                time.sleep(0.008)
                continue

            display_frame = frame.copy()
            frame_h, frame_w = frame.shape[:2]
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            results = self.hands.process(rgb_frame)

            cam_fps = state.get("fps", 0)
            recognition_state.set_fps(camera_fps=cam_fps, inference_fps=inference_worker.current_inf_fps)

            left_hand = None
            right_hand = None
            hand_count = 0
            debug_info = []

            if results.multi_hand_landmarks and results.multi_handedness:
                hand_count = len(results.multi_hand_landmarks)
                
                for idx, hand_handedness in enumerate(results.multi_handedness):
                    classification = hand_handedness.classification[0].label
                    score = hand_handedness.classification[0].score
                    landmarks = results.multi_hand_landmarks[idx]

                    # IMPORTANT MIRRORING FIX:
                    # Because camera_manager horizontally flips the raw frame (cv2.flip(frame, 1)):
                    # MediaPipe "Left" label == User's Physical LEFT Hand
                    # MediaPipe "Right" label == User's Physical RIGHT Hand
                    if classification == "Left":
                        left_hand = landmarks
                        hand_role = "LEFT (SIGN)"
                        box_color = (248, 189, 56)  # Cyan/Blue
                    else:
                        right_hand = landmarks
                        hand_role = "RIGHT (MOUSE)"
                        box_color = (34, 197, 94)   # Green

                    debug_info.append(f"{classification} ({score*100:.0f}%) -> {hand_role}")

                    # Draw visual landmarks and connection skeleton
                    self.mp_draw.draw_landmarks(
                        display_frame,
                        landmarks,
                        self.mp_hands.HAND_CONNECTIONS,
                        self.mp_styles.get_default_hand_landmarks_style(),
                        self.mp_styles.get_default_hand_connections_style()
                    )

                    # Draw Bounding Box and Role Tag for visual confirmation
                    x_coords = [lm.x * frame_w for lm in landmarks.landmark]
                    y_coords = [lm.y * frame_h for lm in landmarks.landmark]
                    bx1, bx2 = max(0, int(min(x_coords) - 10)), min(frame_w, int(max(x_coords) + 10))
                    by1, by2 = max(0, int(min(y_coords) - 10)), min(frame_h, int(max(y_coords) + 10))
                    
                    cv2.rectangle(display_frame, (bx1, by1), (bx2, by2), box_color, 2)
                    cv2.putText(
                        display_frame,
                        hand_role,
                        (bx1, max(20, by1 - 8)),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.55,
                        box_color,
                        2,
                        cv2.LINE_AA
                    )

            # Periodic Rate-Limited Terminal Logging (Every 1.5 seconds)
            now = time.time()
            if (now - self.last_log_time) >= 1.5 and hand_count > 0:
                self.last_log_time = now
                logger.info(
                    f"[Hand Tracker] Count: {hand_count} | Details: {' | '.join(debug_info)} | "
                    f"Left Hand Found: {left_hand is not None} | Right Hand Found: {right_hand is not None}"
                )

            # Update shared presence flags
            recognition_state.set_hand_presence(left_hand is not None, right_hand is not None)
            active_mod = state.get("active_module")

            # --------------------------------------------------
            # 1. LEFT HAND PIPELINE (SIGN LANGUAGE RECOGNITION)
            # --------------------------------------------------
            if left_hand:
                if active_mod == "alphabet":
                    dataset_collector.process_frame(frame, [left_hand])
                if active_mod in ["recognition", "studio"]:
                    inference_worker.submit_hand(frame, left_hand)
            else:
                if active_mod in ["recognition", "studio"]:
                    inference_worker.notify_no_hand()

            # --------------------------------------------------
            # 2. RIGHT HAND PIPELINE (AIR MOUSE / FIST COMMIT)
            # --------------------------------------------------
            if right_hand and state["gesture_enabled"]:
                gesture = self.classifier.classify(right_hand, frame.shape[:2])
                recognition_state.set_right_gesture(gesture.value)

                index_tip = right_hand.landmark[8]
                raw_x, raw_y = self.mapper.map_coordinates(index_tip.x, index_tip.y)

                if gesture == GestureType.CLOSED_FIST:
                    dwell_controller.reset()
                    self.smoother.reset()
                    recognition_state.commit_current_sign()
                    
                    global_state.update_state({
                        "hand_detected": True,
                        "gesture": gesture.value,
                        "dwell_active": False,
                        "interaction_state": InteractionState.COMMITTING.value
                    })
                else:
                    recognition_state.release_fist()

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
                            "interaction_state": InteractionState.IDLE.value
                        })
            else:
                recognition_state.release_fist()
                recognition_state.set_right_gesture("NONE")
                dwell_controller.reset()
                self.smoother.reset()
                global_state.update_state({
                    "hand_detected": (left_hand is not None or right_hand is not None),
                    "gesture": GestureType.NONE.value,
                    "dwell_active": False,
                    "interaction_state": InteractionState.IDLE.value
                })

            camera_manager.set_display_frame(display_frame)
            time.sleep(0.005)

gesture_engine = GestureEngine()