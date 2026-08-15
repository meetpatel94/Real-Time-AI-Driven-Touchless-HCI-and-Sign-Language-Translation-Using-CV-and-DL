import threading
from typing import Dict, Any

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
            "active_module": "overview"
        }

    def get_state(self) -> Dict[str, Any]:
        with self._state_lock:
            return self._state.copy()

    def update_state(self, updates: Dict[str, Any]) -> None:
        with self._state_lock:
            self._state.update(updates)

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

    def set_gesture_state(self, enabled: bool) -> None:
        with self._state_lock:
            if self._state["camera_enabled"]:
                self._state["gesture_enabled"] = enabled
            else:
                self._state["gesture_enabled"] = False

global_state = StateService()