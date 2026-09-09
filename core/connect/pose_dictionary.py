"""Connect: built-in pose gesture dictionary and lightweight pose labeling.

Connect deliberately reuses the existing MediaPipe pipeline and the existing
Custom Gesture library (saved samples and matcher).  This module only adds the
*communication semantics* for the built-in poses that are not part of the
Custom Gesture library — e.g. a raised index finger is relayed as "Hii" and a
thumbs-up as "Okay".

Whenever a saved Custom Gesture matches with enough confidence it always wins
over these built-in labels, so users can teach their own mapping for the exact
same hand pose and override these defaults without touching this table.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from core.custom_gestures.feature_extractor import feature_extractor

# gesture_id -> display payload for the built-in (non-custom) gestures.
# ``kind`` is always "pose" for these entries; custom gestures use the saved
# gesture name/id from the existing Custom Gesture storage.
BUILTIN_GESTURE_DICTIONARY: Dict[str, Dict[str, str]] = {
    "one": {
        "symbol": "☝️",
        "meaning": "Hii",
        "hint": "1 finger raised",
    },
    "two": {
        "symbol": "✌️",
        "meaning": "Peace",
        "hint": "2 fingers raised",
    },
    "three": {
        "symbol": "🤟",
        "meaning": "I Love You",
        "hint": "3 fingers raised",
    },
    "four": {
        "symbol": "🖖",
        "meaning": "Hi",
        "hint": "4 fingers raised",
    },
    "five": {
        "symbol": "🖐️",
        "meaning": "Hello",
        "hint": "Open palm",
    },
    "thumbs_up": {
        "symbol": "👍",
        "meaning": "Okay",
        "hint": "Thumbs up",
    },
    "fist": {
        "symbol": "✊",
        "meaning": "Stop",
        "hint": "Closed fist",
    },
}

# MediaPipe normalized coordinates put the origin at the top-left of the image
# and increase downward, so an upward pointing landmark has the smaller y.


def _is_extended(lm: Any, tip_idx: int, pip_idx: int, mcp_idx: int, wrist: Any) -> bool:
    """Same tip-vs-joint geometry used by the existing gesture classifier."""
    tip = lm.landmark[tip_idx]
    pip = lm.landmark[pip_idx]
    mcp = lm.landmark[mcp_idx]
    dist_tip = _dist(tip, wrist)
    dist_pip = _dist(pip, wrist)
    dist_mcp = _dist(mcp, wrist)
    return dist_tip > dist_pip > dist_mcp and tip.y < pip.y


def _dist(a: Any, b: Any) -> float:
    return ((a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2) ** 0.5


def classify_pose(landmarks: Any) -> Optional[Dict[str, Any]]:
    """Classify a single hand into a built-in Connect gesture.

    Returns a payload compatible with the dictionary entries above plus a
    ``confidence`` estimate derived from MediaPipe tracking quality, or None
    when the pose does not match any built-in label.
    """
    if landmarks is None:
        return None

    tracking_quality = feature_extractor.tracking_quality(landmarks)
    if tracking_quality < 0.9:
        return None

    lm = landmarks.landmark
    wrist = lm[0]
    index_ext = _is_extended(landmarks, 8, 6, 5, wrist)
    middle_ext = _is_extended(landmarks, 12, 10, 9, wrist)
    ring_ext = _is_extended(landmarks, 16, 14, 13, wrist)
    pinky_ext = _is_extended(landmarks, 20, 18, 17, wrist)
    extended_count = int(index_ext) + int(middle_ext) + int(ring_ext) + int(pinky_ext)

    # Thumb uses the same geometry but tolerates the thumb resting along the
    # palm, which is typical for a fist.
    thumb_tip = lm[4]
    thumb_pip = lm[3]
    thumb_mcp = lm[2]
    thumb_ext = (
        _dist(thumb_tip, wrist) > _dist(thumb_pip, wrist)
        and _dist(thumb_tip, wrist) > _dist(thumb_mcp, wrist)
        and thumb_tip.y < thumb_pip.y
    )

    gesture_id: Optional[str] = None

    if extended_count == 0:
        # All four fingers folded: thumbs-up, thumbs-down or fist.
        if thumb_ext:
            gesture_id = "thumbs_up"
        else:
            gesture_id = "fist"
    elif extended_count == 1 and index_ext:
        gesture_id = "one"
    elif extended_count == 2 and index_ext and middle_ext:
        gesture_id = "two"
    elif extended_count == 3:
        gesture_id = "three"
    elif extended_count == 4:
        gesture_id = "five" if thumb_ext else "four"

    if gesture_id is None:
        return None

    entry = BUILTIN_GESTURE_DICTIONARY[gesture_id]
    return {
        "kind": "pose",
        "gesture_id": gesture_id,
        "symbol": entry["symbol"],
        "meaning": entry["meaning"],
        "hint": entry["hint"],
        "confidence": round(float(tracking_quality), 3),
    }
