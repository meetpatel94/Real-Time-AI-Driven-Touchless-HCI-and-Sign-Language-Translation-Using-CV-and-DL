"""AI Gesture Coach: actionable feedback for custom gestures.

The coach compares the *current* observation's DNA against the stored gesture
DNA profile and generates specific, measurable advice.  Feedback is never
random – every tip is derived from an actual dimension difference.

Two contexts:
  * Capture Coach – quality guidance while capturing samples.
  * Test Coach – comparison feedback during test/live recognition.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from core.custom_gestures.gesture_dna import (
    dna_match_level,
    dna_similarity,
    compute_current_dna,
    _clamp_pct,
)


# Thresholds for when coach advice is generated
_DIMENSION_SIGNIFICANCE = 15.0  # percentage-point difference to mention
_CAPTURE_QUALITY_THRESHOLD = 70.0
_TEST_CONFIDENCE_EXCELLENT = 90.0
_TEST_CONFIDENCE_GOOD = 75.0
_TEST_CONFIDENCE_LOW = 60.0


def _dimension_diff(current: Dict[str, float], stored: Dict[str, float], dim: str) -> float:
    """Positive means current > stored, negative means current < stored."""
    return float(current.get(dim, 0.0)) - float(stored.get(dim, 0.0))


# ---------------------------------------------------------------------------
# Capture coach – quality of a single sample being captured
# ---------------------------------------------------------------------------
def coach_capture_feedback(
    current_landmarks: Any,
    stored_dna: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Evaluate the quality of a sample being captured.

    Returns sample_quality (0-100), issues list, and guidance tips.
    """
    current_dna = compute_current_dna(current_landmarks)
    if not current_dna:
        return {
            "sample_quality": 0.0,
            "issues": ["No hand detected."],
            "guidance": ["Make sure your hand is fully visible to the camera."],
            "dimensions": {},
        }

    quality_components: List[float] = []
    issues: List[str] = []
    guidance: List[str] = []

    # Tracking quality
    tq = current_dna.get("tracking_quality", 0.0)
    quality_components.append(tq)
    if tq < 90.0:
        issues.append("Hand landmarks are unstable.")
        guidance.append("Keep your full hand visible and steady.")

    # Hand shape – check if hand is fully open/closed relative to context
    hs = current_dna.get("hand_shape", 50.0)
    quality_components.append(max(0.0, 100.0 - abs(hs - 50.0)))  # Mid-range is neutral
    if hs < 25.0:
        issues.append("Hand appears very closed.")
        guidance.append("Consider extending your fingers more clearly.")
    elif hs > 90.0:
        issues.append("Hand appears fully extended.")
        guidance.append("Ensure all fingers are distinctly visible.")

    # Palm orientation
    po = current_dna.get("palm_orientation", 50.0)
    quality_components.append(100.0 - abs(po - 50.0))  # Near-canonical is more stable

    # If stored DNA available, compare
    if stored_dna:
        for dim, label, threshold in [
            ("hand_shape", "hand shape", _DIMENSION_SIGNIFICANCE),
            ("finger_extension", "finger extension", _DIMENSION_SIGNIFICANCE),
            ("palm_orientation", "palm orientation", _DIMENSION_SIGNIFICANCE + 5.0),
        ]:
            diff = abs(float(current_dna.get(dim, 0.0)) - float(stored_dna.get(dim, 0.0)))
            if diff > threshold:
                current_val = float(current_dna.get(dim, 0.0))
                stored_val = float(stored_dna.get(dim, 0.0))
                if dim == "hand_shape":
                    if current_val < stored_val:
                        issues.append("Hand is more closed than your saved examples.")
                        guidance.append("Open your hand a bit more to match previous samples.")
                    else:
                        issues.append("Hand is more open than your saved examples.")
                        guidance.append("Close your fingers slightly to match previous samples.")
                elif dim == "finger_extension":
                    if current_val < stored_val:
                        issues.append("Fingers are more curled than in saved examples.")
                        guidance.append("Extend your fingers more.")
                    else:
                        issues.append("Fingers are more extended than in saved examples.")
                        guidance.append("Curl your fingers slightly to match saved examples.")
                elif dim == "palm_orientation":
                    issues.append("Hand orientation differs from saved examples.")
                    guidance.append("Rotate your palm to face the camera more directly.")

    # Distance estimation from tracking quality and hand size
    if tq < 80.0:
        issues.append("Hand may be too far from the camera.")
        guidance.append("Move slightly closer to the camera.")

    # Final quality score
    quality = _clamp_pct(sum(quality_components) / max(1, len(quality_components)))

    if not issues:
        guidance.append("Sample looks good! Hold steady.")

    return {
        "sample_quality": quality,
        "issues": issues[:4],
        "guidance": guidance[:3],
        "dimensions": {
            "hand_shape": current_dna.get("hand_shape", 0.0),
            "finger_extension": current_dna.get("finger_extension", 0.0),
            "palm_orientation": current_dna.get("palm_orientation", 0.0),
            "finger_spread": current_dna.get("finger_spread", 0.0),
            "tracking_quality": current_dna.get("tracking_quality", 0.0),
        },
    }


# ---------------------------------------------------------------------------
# Test coach – comparison of current vs stored during test/live
# ---------------------------------------------------------------------------
def coach_test_feedback(
    current_landmarks: Any,
    stored_dna: Dict[str, Any],
    expected_gesture_name: str,
    detected_gesture_name: str,
    confidence: float,
    similarity_pct: float,
) -> Dict[str, Any]:
    """Generate coaching feedback during test/live recognition.

    ``stored_dna`` is the aggregate DNA profile of the expected gesture.
    ``confidence`` is 0-100, ``similarity_pct`` is 0-100 DNA similarity.
    """
    current_dna = compute_current_dna(current_landmarks)
    if not current_dna or not stored_dna:
        return {
            "similarity": 0.0,
            "match_level": "None",
            "feedback": "No hand detected to compare." if not current_dna else "No stored DNA to compare against.",
            "dimensions": current_dna,
            "tips": [],
        }

    sim = dna_similarity(current_dna, stored_dna)
    match_level = dna_match_level(sim)
    tips: List[str] = []
    feedback = ""

    # Determine overall feedback
    if confidence >= _TEST_CONFIDENCE_EXCELLENT and sim >= 85.0:
        feedback = "Excellent match. Your gesture closely matches the saved profile."
    elif confidence >= _TEST_CONFIDENCE_GOOD and sim >= 70.0:
        feedback = "Good match. Minor refinements could improve consistency."
    elif confidence >= _TEST_CONFIDENCE_LOW:
        feedback = "Partial match. Some aspects of the gesture differ from the saved profile."
    else:
        feedback = "The current gesture is quite different from the saved profile."

    # Find the most divergent dimensions
    dimension_labels = {
        "hand_shape": "hand shape (openness)",
        "finger_extension": "finger extension",
        "palm_orientation": "palm orientation",
        "finger_spread": "finger spread",
        "stability": "stability",
        "spatial_consistency": "spatial consistency",
    }

    max_diff_dim = ""
    max_diff_val = 0.0
    for dim in ["hand_shape", "finger_extension", "palm_orientation", "finger_spread", "stability", "spatial_consistency"]:
        c = current_dna.get(dim, 0.0)
        s = stored_dna.get(dim, 0.0)
        if c is not None and s is not None:
            diff = abs(float(c) - float(s))
            if diff > max_diff_val:
                max_diff_val = diff
                max_diff_dim = dim

    # Generate specific tips for significant differences
    for dim in ["hand_shape", "finger_extension", "palm_orientation", "finger_spread"]:
        diff = _dimension_diff(current_dna, stored_dna, dim)
        abs_diff = abs(diff)
        if abs_diff < _DIMENSION_SIGNIFICANCE:
            continue

        label = dimension_labels.get(dim, dim)
        if dim == "hand_shape":
            if diff < 0:
                tips.append(f"Your {label} is lower than expected. Open your hand more.")
            else:
                tips.append(f"Your {label} is higher than expected. Close your hand slightly.")
        elif dim == "finger_extension":
            if diff < 0:
                tips.append("Your fingers are more curled than in the saved examples. Try extending them.")
            else:
                tips.append("Your fingers are more extended than in the saved examples. Try curling them slightly.")
        elif dim == "palm_orientation":
            tips.append("Your hand orientation differs from the saved examples. Rotate your palm to match.")
        elif dim == "finger_spread":
            if diff < 0:
                tips.append("Your fingers are closer together than in the saved examples. Spread them slightly.")
            else:
                tips.append("Your fingers are more spread than in the saved examples. Bring them closer together.")

    # Stability tip (only meaningful if current has stability data)
    current_stability = current_dna.get("stability", 0.0)
    stored_stability = stored_dna.get("stability", 0.0)
    if current_stability > 0 and stored_stability > 0 and abs(current_stability - stored_stability) > 20:
        if current_stability < stored_stability:
            tips.append("Keep your hand more steady. Movement is less consistent than saved examples.")

    # If gesture was detected correctly but similarity is low
    if detected_gesture_name == expected_gesture_name and sim < 70.0:
        tips.append("The gesture was recognized, but the shape is quite different. Consider recapturing samples.")

    # If gesture was not detected, find biggest mismatch
    if detected_gesture_name != expected_gesture_name and max_diff_dim:
        label = dimension_labels.get(max_diff_dim, max_diff_dim)
        tips.insert(0, f"The biggest difference is in your {label}.")

    if not tips and confidence >= _TEST_CONFIDENCE_GOOD:
        tips.append("Your gesture shape is consistent. Keep it up.")

    return {
        "similarity": round(sim, 1),
        "match_level": match_level,
        "feedback": feedback,
        "dimensions": {
            "current": current_dna,
            "stored": {k: v for k, v in stored_dna.items() if isinstance(v, (int, float))},
        },
        "tips": tips[:4],
    }


def coach_live_feedback(
    current_landmarks: Any,
    stored_dna: Optional[Dict[str, Any]],
    detected_gesture_name: str,
    confidence: float,
) -> Dict[str, Any]:
    """Lighter coach for live mode – just feedback, not full comparison."""
    if not current_landmarks or not stored_dna:
        return {"feedback": "", "tips": []}

    current_dna = compute_current_dna(current_landmarks)
    sim = dna_similarity(current_dna, stored_dna)

    tips: List[str] = []
    feedback = ""

    if confidence >= _TEST_CONFIDENCE_EXCELLENT:
        feedback = "Excellent recognition."
    elif confidence >= _TEST_CONFIDENCE_GOOD:
        feedback = "Good recognition."
    elif confidence >= _TEST_CONFIDENCE_LOW:
        feedback = "Weak recognition. Adjust your gesture."
    else:
        feedback = "Gesture not clearly recognized."

    for dim in ["hand_shape", "finger_extension", "palm_orientation", "finger_spread"]:
        diff = _dimension_diff(current_dna, stored_dna, dim)
        if abs(diff) > _DIMENSION_SIGNIFICANCE:
            if dim == "hand_shape" and diff < -_DIMENSION_SIGNIFICANCE:
                tips.append("Open your hand more.")
            elif dim == "hand_shape" and diff > _DIMENSION_SIGNIFICANCE:
                tips.append("Close your hand slightly.")
            elif dim == "finger_extension" and diff < -_DIMENSION_SIGNIFICANCE:
                tips.append("Extend your fingers more.")
            elif dim == "finger_extension" and diff > _DIMENSION_SIGNIFICANCE:
                tips.append("Curl your fingers slightly.")
            elif dim == "palm_orientation":
                tips.append("Adjust palm orientation to face the camera.")
            elif dim == "finger_spread":
                tips.append("Adjust finger spacing.")

    return {
        "feedback": feedback,
        "similarity": round(sim, 1),
        "tips": tips[:3],
    }
