"""Custom gesture library package.

This package is intentionally separate from the A-Z sign recognizer and the
legacy air-mouse gesture classifier.  It stores user-created gesture samples as
landmark-derived JSON files under data/custom_gestures/ and never updates the
existing sign-alphabet model or global prediction state.
"""

from .service import CustomGestureService, custom_gesture_service

__all__ = ["CustomGestureService", "custom_gesture_service"]
