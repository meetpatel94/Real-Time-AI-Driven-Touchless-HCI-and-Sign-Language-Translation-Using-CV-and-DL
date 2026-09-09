"""Connect: gesture event detector running inside the existing engine loop.

This detector consumes the *same* MediaPipe hands already produced by the
global GestureEngine camera loop — it never opens a second camera pipeline.
It turns raw landmark observations into sparse, lightweight gesture events:

1. Saved custom gestures (existing Custom Gesture storage + matcher) win when
   they match with enough confidence; otherwise built-in pose labels are used
   (☝️ -> "Hii", 👍 -> "Okay", ...).
2. A candidate must be stable for N consecutive frames and then held for
   ``CONNECT_GESTURE_HOLD_SECONDS`` before it is sent exactly once.
3. The same gesture is not sent again until the hand drops/changes and the
   gesture is recognized again (duplicate prevention while holding a pose).

Events are pushed to a sink (the Connect room service), never polled.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, Optional

from config import Config
from core.connect.pose_dictionary import classify_pose
from core.custom_gestures.service import custom_gesture_service


class ConnectDetector:
    """Temporal stability + hold-to-send gate for the Connect relay."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sink: Optional[Callable[[str, Dict[str, Any]], None]] = None

        self.hold_seconds = float(getattr(Config, "CONNECT_GESTURE_HOLD_SECONDS", 2.0))
        self.min_stable_frames = int(getattr(Config, "CONNECT_GESTURE_MIN_STABLE_FRAMES", 4))
        self.custom_stable_frames = int(getattr(Config, "CONNECT_CUSTOM_GESTURE_STABLE_FRAMES", 3))
        self.pose_min_quality = float(getattr(Config, "CONNECT_POSE_TRACKING_MIN_QUALITY", 0.90))
        self.progress_interval = float(getattr(Config, "CONNECT_LOCAL_PROGRESS_INTERVAL", 0.10))

        # Active candidate tracking
        self._tracked: Optional[Dict[str, Any]] = None
        self._stable_frames = 0
        self._confirmed_at: Optional[float] = None
        self._sent_key: Optional[str] = None
        self._last_status: Dict[str, Any] = {}
        self._last_progress_push = 0.0

    # ------------------------------------------------------------------
    # Sink plumbing (set once by the Connect room service)
    # ------------------------------------------------------------------
    def set_sink(self, sink: Optional[Callable[[str, Dict[str, Any]], None]]) -> None:
        with self._lock:
            self._sink = sink

    def _emit(self, event_type: str, payload: Dict[str, Any]) -> None:
        with self._lock:
            sink = self._sink
        if sink is not None:
            try:
                sink(event_type, payload)
            except Exception:
                pass  # Relay failures must never break the camera loop.

    # ------------------------------------------------------------------
    # Classification (custom first, then built-in poses)
    # ------------------------------------------------------------------
    def _classify_frame(self, left_hand: Any, right_hand: Any) -> Optional[Dict[str, Any]]:
        any_hand = left_hand is not None or right_hand is not None
        if not any_hand:
            return None

        # 1) Saved custom gestures — reuses the existing storage + matcher.
        try:
            custom_match = custom_gesture_service.match_frame_read_only(left_hand, right_hand)
        except Exception:
            custom_match = None
        if custom_match and custom_match.get("gesture_id"):
            gesture_id = str(custom_match.get("gesture_id", ""))
            threshold = float(custom_match.get("threshold", 0.85) or 0.85)
            similarity = float(custom_match.get("similarity", 0.0) or 0.0)
            if similarity >= threshold and custom_match.get("reason") != "LOW_QUALITY":
                gesture_name = str(custom_match.get("gesture_name") or gesture_id)
                return {
                    "kind": "custom",
                    "gesture_id": gesture_id,
                    "symbol": "✋",
                    "meaning": gesture_name,
                    "hint": str(custom_match.get("handedness") or ""),
                    "confidence": round(similarity, 3),
                    "has_replay": True,
                    "stable_frames_required": self.custom_stable_frames,
                }

        # 2) Built-in pose fallback (right hand first, mirroring the engine).
        pose_hand = right_hand if right_hand is not None else left_hand
        try:
            pose = classify_pose(pose_hand)
        except Exception:
            pose = None
        if pose is not None and float(pose.get("confidence", 0.0)) >= self.pose_min_quality:
            pose["has_replay"] = False
            pose["stable_frames_required"] = self.min_stable_frames
            return pose

        return None

    # ------------------------------------------------------------------
    # Frame entry point (called from the GestureEngine loop)
    # ------------------------------------------------------------------
    def reset(self) -> None:
        with self._lock:
            self._tracked = None
            self._stable_frames = 0
            self._confirmed_at = None
            self._sent_key = None
            self._last_status = {}

    def update(self, left_hand: Any, right_hand: Any) -> None:
        """Evaluate one engine frame; emits 'status' and 'gesture' events."""
        now = time.monotonic()
        candidate = self._classify_frame(left_hand, right_hand)

        if candidate is None:
            self._handle_no_candidate(now)
            return

        key = "{kind}:{gesture_id}".format(**candidate)

        if self._sent_key == key:
            # Already relayed; keep quiet while the user holds the pose.
            self._tracked = None
            self._stable_frames = 0
            self._confirmed_at = None
            self._push_status(now, {
                "state": "sent",
                "kind": candidate.get("kind"),
                "gesture_id": candidate.get("gesture_id"),
                "symbol": candidate.get("symbol", ""),
                "meaning": candidate.get("meaning", ""),
                "confidence": candidate.get("confidence", 0.0),
                "progress": 1.0,
            })
            return

        tracked_key = (
            "{kind}:{gesture_id}".format(**self._tracked)
            if self._tracked else None
        )
        if tracked_key != key:
            self._tracked = candidate
            self._stable_frames = 1
            self._confirmed_at = None
            self._push_status(now, {
                "state": "detecting",
                "kind": candidate.get("kind"),
                "gesture_id": candidate.get("gesture_id"),
                "symbol": candidate.get("symbol", ""),
                "meaning": candidate.get("meaning", ""),
                "confidence": candidate.get("confidence", 0.0),
                "progress": 0.0,
            })
            return

        # Same candidate as previous frame — accumulate stability.
        self._stable_frames += 1
        required = int(candidate.get("stable_frames_required", self.min_stable_frames))
        if self._stable_frames >= required and self._confirmed_at is None:
            self._confirmed_at = now

        if self._confirmed_at is None:
            self._push_status(now, {
                "state": "detecting",
                "kind": candidate.get("kind"),
                "gesture_id": candidate.get("gesture_id"),
                "symbol": candidate.get("symbol", ""),
                "meaning": candidate.get("meaning", ""),
                "confidence": candidate.get("confidence", 0.0),
                "progress": 0.0,
            })
            return

        elapsed = now - self._confirmed_at
        progress = min(1.0, elapsed / max(0.05, self.hold_seconds))
        if elapsed >= self.hold_seconds:
            # Hold complete -> relay exactly once.
            self._sent_key = key
            self._tracked = None
            self._stable_frames = 0
            self._confirmed_at = None
            self._emit("gesture", self._as_message(candidate))
            self._push_status(now, {
                "state": "sent",
                "kind": candidate.get("kind"),
                "gesture_id": candidate.get("gesture_id"),
                "symbol": candidate.get("symbol", ""),
                "meaning": candidate.get("meaning", ""),
                "confidence": candidate.get("confidence", 0.0),
                "progress": 1.0,
            })
            return

        self._push_status(now, {
            "state": "holding",
            "kind": candidate.get("kind"),
            "gesture_id": candidate.get("gesture_id"),
            "symbol": candidate.get("symbol", ""),
            "meaning": candidate.get("meaning", ""),
            "confidence": candidate.get("confidence", 0.0),
            "progress": round(progress, 3),
        })

    def _handle_no_candidate(self, now: float) -> None:
        """No hand / no match: cancel any pending hold, allow re-send later."""
        cancelled = False
        if self._tracked is not None or self._sent_key is not None:
            cancelled = True
        self._tracked = None
        self._stable_frames = 0
        self._confirmed_at = None
        self._sent_key = None
        self._push_status(now, {"state": "no_hand"})
        if cancelled:
            pass  # Hold progress simply resets; no message flood needed.

    def _as_message(self, candidate: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "kind": candidate.get("kind", "pose"),
            "gesture_id": candidate.get("gesture_id", ""),
            "symbol": candidate.get("symbol", ""),
            "meaning": candidate.get("meaning", ""),
            "confidence": candidate.get("confidence", 0.0),
            "has_replay": bool(candidate.get("has_replay", False)),
            "ts": time.time(),
        }

    def _same_status(self, status: Dict[str, Any]) -> bool:
        """Compare against the last pushed status (ignores progress values)."""
        last = self._last_status
        return (
            last.get("state") == status.get("state")
            and last.get("kind") == status.get("kind")
            and last.get("gesture_id") == status.get("gesture_id")
        )

    def _push_status(self, now: float, status: Dict[str, Any]) -> None:
        """Send local status only on meaningful changes / throttled progress."""
        if status.get("state") == "holding":
            last_state = self._last_status.get("state")
            if (
                last_state == "holding"
                and (now - self._last_progress_push) < self.progress_interval
            ):
                return
            self._last_progress_push = now
        elif self._same_status(status):
            return
        self._last_status = dict(status)
        self._emit("status", status)


connect_detector = ConnectDetector()
