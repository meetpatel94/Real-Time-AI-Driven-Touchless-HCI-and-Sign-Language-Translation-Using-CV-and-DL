import threading
from collections import deque
from typing import Any, Deque, Dict, List

from config import Config

class StateService:
    """Thread-safe global state service."""
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(StateService, cls).__new__(cls)
                cls._instance._initialize()
            return cls._instance

    def _initialize(self):
        self._state_lock = threading.Lock()
        self._state = {
            "camera_enabled": False,
            "gesture_enabled": False,
            "hand_detected": False,
            "gesture": "NONE",
            "cursor_x": 0,
            "cursor_y": 0,
            "dwell_active": False,
            "dwell_progress": 0,
            "selection_ready": False,
            "interaction_state": "IDLE",
            "fps": 0,
            "active_module": "overview",
            # Adaptive reasoning is additive to the legacy global state.  The
            # existing gesture/cursor keys remain unchanged for old clients.
            "adaptive_enabled": True,
            "profile_id": "local-user",
            "profile_name": "Local user",
            "unknown_gesture": False,
            "unknown_gesture_status": "NO_HAND",
            "unknown_gesture_score": 0.0,
            "unknown_gesture_reason": "No hand is currently tracked.",
            "intent": "IDLE",
            "intent_confidence": 0.0,
            "intent_actionable": False,
            "intent_source": "none",
            "personalized_action": "",
            "personalized_action_source": "",
            # Global page scrolling is enabled by default, but is executed only
            # while the existing Air Gesture control is enabled.  Events contain
            # only a direction and distance, never gesture labels or landmarks.
            "hand_scroll_enabled": True,
            "hand_scroll_sequence": 0,
            "hand_scroll_direction": "",
            "hand_scroll_amount": 0,
        }
        self._hand_scroll_events: Deque[Dict[str, Any]] = deque(
            maxlen=Config.HAND_SCROLL_EVENT_BUFFER_SIZE
        )

    def get_state(self) -> Dict[str, Any]:
        with self._state_lock:
            return self._state.copy()

    def update_state(self, updates: Dict[str, Any]) -> None:
        with self._state_lock:
            self._state.update(updates)

    def publish_hand_scroll(self, direction: str, amount: int) -> Dict[str, Any]:
        """Publish a bounded browser-scroll instruction from the shared hand loop.

        The event relay is intentionally process-local: no camera frames,
        landmarks, recognition predictions, or custom-gesture labels leave the
        MediaPipe loop.  Browser pages consume only events newer than the last
        sequence number they observed.
        """
        normalized_direction = str(direction).lower().strip()
        if normalized_direction not in {"up", "down"}:
            raise ValueError("Hand scroll direction must be 'up' or 'down'.")

        try:
            normalized_amount = max(1, min(int(amount), Config.SCROLL_MAX_AMOUNT))
        except (TypeError, ValueError):
            normalized_amount = Config.DEFAULT_SCROLL_AMOUNT

        with self._state_lock:
            sequence = int(self._state.get("hand_scroll_sequence", 0)) + 1
            event = {
                "sequence": sequence,
                "direction": normalized_direction,
                "amount": normalized_amount,
            }
            self._state.update({
                "hand_scroll_sequence": sequence,
                "hand_scroll_direction": normalized_direction,
                "hand_scroll_amount": normalized_amount,
            })
            self._hand_scroll_events.append(event)
            return event.copy()

    def hand_scroll_events_since(self, after_sequence: Any = 0) -> Dict[str, Any]:
        """Return recent events after a browser client's acknowledged sequence."""
        try:
            after = max(0, int(after_sequence))
        except (TypeError, ValueError):
            after = 0

        with self._state_lock:
            events: List[Dict[str, Any]] = [
                event.copy()
                for event in self._hand_scroll_events
                if int(event["sequence"]) > after
            ]
            latest_sequence = int(self._state.get("hand_scroll_sequence", 0))
            oldest_sequence = (
                int(self._hand_scroll_events[0]["sequence"])
                if self._hand_scroll_events
                else latest_sequence + 1
            )
            return {
                "latest_sequence": latest_sequence,
                "oldest_sequence": oldest_sequence,
                "events": events,
            }

    def set_camera_state(self, enabled: bool) -> None:
        with self._state_lock:
            self._state["camera_enabled"] = enabled
            if not enabled:
                self._state["gesture_enabled"] = False
                self._state["hand_detected"] = False
                self._state["gesture"] = "NONE"
                self._state["dwell_active"] = False
                self._state["dwell_progress"] = 0
                self._state["selection_ready"] = False
                self._state["interaction_state"] = "IDLE"
                self._state["unknown_gesture"] = False
                self._state["unknown_gesture_status"] = "NO_HAND"
                self._state["unknown_gesture_score"] = 0.0
                self._state["unknown_gesture_reason"] = "No hand is currently tracked."
                self._state["intent"] = "IDLE"
                self._state["intent_confidence"] = 0.0
                self._state["intent_actionable"] = False
                self._state["intent_source"] = "none"
                self._state["personalized_action"] = ""
                self._state["personalized_action_source"] = ""
                self._state["hand_scroll_direction"] = ""
                self._state["hand_scroll_amount"] = 0

    def set_gesture_state(self, enabled: bool) -> None:
        with self._state_lock:
            if self._state["camera_enabled"]:
                self._state["gesture_enabled"] = enabled
            else:
                self._state["gesture_enabled"] = False

global_state = StateService()