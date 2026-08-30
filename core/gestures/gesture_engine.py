import threading
import time
import cv2
import mediapipe as mp
from core.camera.camera_manager import camera_manager
from core.adaptive.observation_builder import GestureObservationBuilder
from core.gestures.gesture_classifier import GestureClassifier
from core.gestures.gesture_state import GestureType, InteractionState
from core.mouse.cursor_mapper import CursorMapper
from core.mouse.cursor_smoother import CursorSmoother
from core.mouse.mouse_controller import mouse_controller
from core.mouse.dwell_controller import dwell_controller
from core.mouse.scroll_controller import scroll_controller
from core.sign_language.dataset_collector import dataset_collector
from core.recognition.inference_worker import inference_worker
from core.recognition.recognition_state import recognition_state
from services.adaptive_intent_service import adaptive_intent_service
from services.logging_service import logger
from services.state_service import global_state
from services.user_profile_service import user_profile_service

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
        # Adaptive observation is an additive read-only layer. The existing
        # controllers below remain responsible for executing OS actions.
        self.observation_builder = GestureObservationBuilder(self.classifier)
        self.mapper = CursorMapper()
        self.smoother = CursorSmoother()
        self.mp_hands = mp.solutions.hands
        self.mp_draw = mp.solutions.drawing_utils
        self.mp_styles = mp.solutions.drawing_styles
        self.hands = None
        self._adaptive_camera_active = False
        self._adaptive_error_logged = False

    def start(self) -> None:
        with self._lock:
            if self.is_running:
                return
            logger.info("Starting Global Gesture Engine (Dual-Hand + Scroll Mode)...")
            self.hands = self.mp_hands.Hands(
                static_image_mode=False,
                max_num_hands=2,
                model_complexity=0,
                min_detection_confidence=0.55,
                min_tracking_confidence=0.55
            )
            try:
                profile = user_profile_service.get_active_profile()
                self.mapper.set_sensitivity(profile.cursor_sensitivity)
                scroll_controller.set_sensitivity(profile.scroll_sensitivity)
            except Exception as exc:
                # Profile persistence is optional at runtime; never prevent the
                # established camera/gesture engine from starting.
                logger.warning(f"Adaptive profile could not be loaded at startup: {exc}")
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
                scroll_controller.reset()
                inference_worker.notify_no_hand()
                recognition_state.set_hand_presence(False, False)
                recognition_state.process_right_hand_fist(False)
                if self._adaptive_camera_active:
                    self.observation_builder.reset()
                    try:
                        adaptive_intent_service.reset()
                    except Exception as exc:
                        if not self._adaptive_error_logged:
                            logger.warning(f"Adaptive runtime reset skipped: {exc}")
                            self._adaptive_error_logged = True
                    self._adaptive_camera_active = False
                time.sleep(0.05)
                continue

            if not self._adaptive_camera_active:
                self.observation_builder.reset()
                self._adaptive_camera_active = True

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

            if results.multi_hand_landmarks and results.multi_handedness:
                for idx, hand_handedness in enumerate(results.multi_handedness):
                    classification = hand_handedness.classification[0].label
                    landmarks = results.multi_hand_landmarks[idx]

                    if classification == "Left":
                        left_hand = landmarks
                        box_color = (248, 189, 56)  # Cyan
                        tag = "LEFT (SIGN)"
                    else:
                        right_hand = landmarks
                        box_color = (34, 197, 94)   # Green
                        tag = "RIGHT (MOUSE)"

                    self.mp_draw.draw_landmarks(
                        display_frame,
                        landmarks,
                        self.mp_hands.HAND_CONNECTIONS,
                        self.mp_styles.get_default_hand_landmarks_style(),
                        self.mp_styles.get_default_hand_connections_style()
                    )

                    x_coords = [lm.x * frame_w for lm in landmarks.landmark]
                    y_coords = [lm.y * frame_h for lm in landmarks.landmark]
                    bx1, bx2 = max(0, int(min(x_coords) - 10)), min(frame_w, int(max(x_coords) + 10))
                    by1, by2 = max(0, int(min(y_coords) - 10)), min(frame_h, int(max(y_coords) + 10))
                    
                    cv2.rectangle(display_frame, (bx1, by1), (bx2, by2), box_color, 2)
                    cv2.putText(display_frame, tag, (bx1, max(20, by1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, box_color, 2, cv2.LINE_AA)

            recognition_state.set_hand_presence(left_hand is not None, right_hand is not None)
            active_mod = state.get("active_module")

            # --------------------------------------------------
            # ADAPTIVE OBSERVATION LAYER
            # --------------------------------------------------
            # Classify for observation even when Air Gesture execution is off;
            # no controller is called until the legacy gesture_enabled gate below.
            right_gesture = (
                self.classifier.classify(right_hand, frame.shape[:2])
                if right_hand else GestureType.NONE
            )
            if right_hand:
                right_observation = self.observation_builder.build(
                    "right", right_hand, right_gesture
                )
            else:
                self.observation_builder.reset_hand("right")
                right_observation = None

            if left_hand:
                left_observation = self.observation_builder.build(
                    "left", left_hand, GestureType.NONE
                )
            else:
                self.observation_builder.reset_hand("left")
                left_observation = None

            sign_snapshot = recognition_state.get_snapshot()
            try:
                profile = user_profile_service.get_active_profile()
                adaptive_intent_service.process_frame(
                    right_observation=right_observation,
                    left_observation=left_observation,
                    sign_snapshot=sign_snapshot,
                    profile=profile,
                    context={
                        "active_module": active_mod,
                        "camera_enabled": True,
                        "gesture_enabled": state["gesture_enabled"],
                        "interaction_state": state.get("interaction_state", "IDLE"),
                        "left_hand_detected": left_hand is not None,
                        "right_hand_detected": right_hand is not None,
                        "sentence_active": bool(str(sign_snapshot.get("sentence", "")).strip()),
                        "legacy_pipeline_active": True,
                    },
                )
                self._adaptive_error_logged = False
            except Exception as exc:
                # The adaptive layer must fail open: existing MediaPipe,
                # inference, cursor, click and scroll paths continue untouched.
                if not self._adaptive_error_logged:
                    logger.warning(f"Adaptive interpretation skipped: {exc}")
                    self._adaptive_error_logged = True

            # --------------------------------------------------
            # 1. LEFT HAND PIPELINE (SIGN RECOGNITION ONLY)
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
            # 2. RIGHT HAND PIPELINE (AIR MOUSE + FIST + SCROLL)
            # --------------------------------------------------
            if right_hand and state["gesture_enabled"]:
                gesture = right_gesture
                recognition_state.set_right_gesture(gesture.value)

                index_tip = right_hand.landmark[8]
                raw_x, raw_y = self.mapper.map_coordinates(index_tip.x, index_tip.y)

                is_fist = (gesture == GestureType.CLOSED_FIST)
                fist_status = recognition_state.process_right_hand_fist(is_fist)
                
                # Check deliberate vertical swipe scrolling
                scroll_controller.process_hand(right_hand, is_fist)

                if fist_status["committed"]:
                    logger.info(f"Fist Confirmation -> Appended letter: '{recognition_state.last_confirmed_letter}'")

                if is_fist:
                    dwell_controller.reset()
                    self.smoother.reset()
                    global_state.update_state({
                        "hand_detected": True,
                        "gesture": gesture.value,
                        "dwell_active": False,
                        "interaction_state": InteractionState.COMMITTING.value
                    })
                else:
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
                recognition_state.process_right_hand_fist(False)
                recognition_state.set_right_gesture("NONE")
                dwell_controller.reset()
                self.smoother.reset()
                scroll_controller.reset()
                global_state.update_state({
                    "hand_detected": (left_hand is not None or right_hand is not None),
                    "gesture": GestureType.NONE.value,
                    "dwell_active": False,
                    "interaction_state": InteractionState.IDLE.value
                })

            camera_manager.set_display_frame(display_frame)
            time.sleep(0.005)

gesture_engine = GestureEngine()