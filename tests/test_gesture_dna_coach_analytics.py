"""Tests for Gesture DNA, AI Coach, and Analytics features.

Validates:
1. DNA is generated from actual samples
2. DNA changes when samples change
3. Current gesture can be compared against stored DNA
4. AI Coach gives feedback based on actual differences
5. Good gestures receive positive feedback
6. Poor gestures receive corrective feedback
7. Analytics use actual recognition events
8. Recognition success rate is correct
9. Confidence statistics are correct
10. Correction count is correct
11. Variation count is correct
12. Historical performance is calculated correctly
13. No fake/random analytics values
14. Existing project functionality remains unchanged
15. Custom gesture results remain isolated
"""

import json
import math
import os
import shutil
import tempfile
import unittest

from core.custom_gestures.feature_extractor import (
    CustomGestureFeatureExtractor,
    feature_extractor,
    mean_vector,
    vector_distance,
)
from core.custom_gestures.gesture_dna import (
    aggregate_dna,
    compute_current_dna,
    compute_dna_from_samples,
    dna_match_level,
    dna_similarity,
    extract_sample_dna,
    _clamp_pct,
)
from core.custom_gestures.gesture_coach import (
    coach_capture_feedback,
    coach_live_feedback,
    coach_test_feedback,
)
from core.custom_gestures.gesture_analytics import (
    compute_analytics,
    log_recognition_event,
    _compute_evolution_timeline,
)
from core.custom_gestures.service import CustomGestureService


def _make_landmarks(offset_x=0.0, offset_y=0.0, open_hand=True):
    """Create synthetic MediaPipe-like landmarks for testing.

    Returns a list of 21 dicts with x, y, z, visibility.
    """
    # Wrist at center, fingers extending upward
    wrist = {"x": 0.5 + offset_x, "y": 0.7 + offset_y, "z": 0.0, "visibility": 1.0}
    # Thumb tip
    thumb_tip = {"x": 0.3 + offset_x, "y": 0.5 + offset_y, "z": 0.0, "visibility": 1.0}
    # Index finger
    index_mcp = {"x": 0.42 + offset_x, "y": 0.55 + offset_y, "z": 0.0, "visibility": 1.0}
    index_pip = {"x": 0.42 + offset_x, "y": 0.45 + offset_y, "z": 0.0, "visibility": 1.0}
    index_dip = {"x": 0.42 + offset_x, "y": 0.38 + offset_y, "z": 0.0, "visibility": 1.0}
    index_tip = {"x": 0.42 + offset_x, "y": 0.30 + offset_y, "z": 0.0, "visibility": 1.0}
    # Middle finger
    middle_mcp = {"x": 0.50 + offset_x, "y": 0.52 + offset_y, "z": 0.0, "visibility": 1.0}
    middle_pip = {"x": 0.50 + offset_x, "y": 0.42 + offset_y, "z": 0.0, "visibility": 1.0}
    middle_dip = {"x": 0.50 + offset_x, "y": 0.35 + offset_y, "z": 0.0, "visibility": 1.0}
    middle_tip = {"x": 0.50 + offset_x, "y": 0.28 + offset_y, "z": 0.0, "visibility": 1.0}
    # Ring finger
    ring_mcp = {"x": 0.58 + offset_x, "y": 0.55 + offset_y, "z": 0.0, "visibility": 1.0}
    ring_pip = {"x": 0.58 + offset_x, "y": 0.45 + offset_y, "z": 0.0, "visibility": 1.0}
    ring_dip = {"x": 0.58 + offset_x, "y": 0.38 + offset_y, "z": 0.0, "visibility": 1.0}
    ring_tip = {"x": 0.58 + offset_x, "y": 0.30 + offset_y, "z": 0.0, "visibility": 1.0}
    # Pinky
    pinky_mcp = {"x": 0.65 + offset_x, "y": 0.58 + offset_y, "z": 0.0, "visibility": 1.0}
    pinky_pip = {"x": 0.65 + offset_x, "y": 0.50 + offset_y, "z": 0.0, "visibility": 1.0}
    pinky_dip = {"x": 0.65 + offset_x, "y": 0.43 + offset_y, "z": 0.0, "visibility": 1.0}
    pinky_tip = {"x": 0.65 + offset_x, "y": 0.35 + offset_y, "z": 0.0, "visibility": 1.0}
    # Thumb joints
    thumb_cmc = {"x": 0.35 + offset_x, "y": 0.62 + offset_y, "z": 0.0, "visibility": 1.0}
    thumb_mcp = {"x": 0.33 + offset_x, "y": 0.56 + offset_y, "z": 0.0, "visibility": 1.0}
    thumb_ip = {"x": 0.31 + offset_x, "y": 0.52 + offset_y, "z": 0.0, "visibility": 1.0}

    if not open_hand:
        # Curl fingers toward wrist
        for pt in [index_tip, index_dip, middle_tip, middle_dip,
                   ring_tip, ring_dip, pinky_tip, pinky_dip,
                   thumb_tip, thumb_ip]:
            pt["y"] = 0.65 + offset_y

    return [wrist, thumb_cmc, thumb_mcp, thumb_ip, thumb_tip,
            index_mcp, index_pip, index_dip, index_tip,
            middle_mcp, middle_pip, middle_dip, middle_tip,
            ring_mcp, ring_pip, ring_dip, ring_tip,
            pinky_mcp, pinky_pip, pinky_dip, pinky_tip]


def _make_landmarks_obj(offset_x=0.0, offset_y=0.0, open_hand=True):
    """Return landmarks as a list of point dicts (MediaPipe-like).

    The feature extractor's points_from_landmarks expects either:
    - An object with a .landmark attribute
    - A direct iterable of point dicts
    
    We use a simple namespace object with a .landmark attribute.
    """
    class _Landmarks:
        def __init__(self, pts):
            self.landmark = pts
    return _Landmarks(_make_landmarks(offset_x, offset_y, open_hand))


class TestGestureDNA(unittest.TestCase):
    """Feature 1: Gesture DNA tests."""

    def test_extract_sample_dna_returns_all_dimensions(self):
        landmarks = _make_landmarks_obj()
        dna = extract_sample_dna(landmarks)
        for dim in ["hand_shape", "finger_extension", "palm_orientation",
                     "finger_spread", "tracking_quality"]:
            self.assertIn(dim, dna)
            self.assertIsInstance(dna[dim], float)
            self.assertGreaterEqual(dna[dim], 0.0)
            self.assertLessEqual(dna[dim], 100.0)

    def test_tracking_quality_is_high_for_valid_landmarks(self):
        landmarks = _make_landmarks_obj()
        dna = extract_sample_dna(landmarks)
        self.assertGreater(dna["tracking_quality"], 90.0)

    def test_aggregate_dna_with_multiple_samples(self):
        dna_list = [
            extract_sample_dna(_make_landmarks_obj(offset_x=0.0)),
            extract_sample_dna(_make_landmarks_obj(offset_x=0.01)),
            extract_sample_dna(_make_landmarks_obj(offset_x=-0.01)),
        ]
        agg = aggregate_dna(dna_list)
        self.assertEqual(agg["sample_count"], 3)
        for dim in ["hand_shape", "finger_extension", "palm_orientation", "stability", "spatial_consistency"]:
            self.assertIn(dim, agg)
            self.assertGreaterEqual(agg[dim], 0.0)
            self.assertLessEqual(agg[dim], 100.0)

    def test_aggregate_dna_empty_returns_zeros(self):
        agg = aggregate_dna([])
        self.assertEqual(agg["sample_count"], 0)
        self.assertEqual(agg["hand_shape"], 0.0)

    def test_dna_similarity_identical_is_high(self):
        dna1 = extract_sample_dna(_make_landmarks_obj())
        dna2 = extract_sample_dna(_make_landmarks_obj())
        # Identical landmarks should give 100% similarity
        sim = dna_similarity(dna1, dna2)
        self.assertGreater(sim, 95.0)

    def test_dna_similarity_different_is_lower(self):
        dna_open = extract_sample_dna(_make_landmarks_obj(open_hand=True))
        dna_closed = extract_sample_dna(_make_landmarks_obj(open_hand=False))
        sim = dna_similarity(dna_open, dna_closed)
        # Different poses should have lower similarity
        self.assertLess(sim, 90.0)

    def test_dna_match_level_thresholds(self):
        self.assertEqual(dna_match_level(95.0), "Excellent")
        self.assertEqual(dna_match_level(75.0), "High")
        self.assertEqual(dna_match_level(55.0), "Moderate")
        self.assertEqual(dna_match_level(35.0), "Low")
        self.assertEqual(dna_match_level(15.0), "Very Low")

    def test_compute_dna_from_samples_reads_files(self):
        tmpdir = tempfile.mkdtemp()
        try:
            paths = []
            for i in range(5):
                offset = i * 0.005
                sample = feature_extractor.sample_representation(_make_landmarks_obj(offset_x=offset))
                path = os.path.join(tmpdir, f"sample_{i:03d}.json")
                with open(path, "w") as f:
                    json.dump(sample, f)
                paths.append(path)
            dna = compute_dna_from_samples(paths, lambda p: json.load(open(p)))
            self.assertEqual(dna["sample_count"], 5)
            self.assertGreater(dna["stability"], 0.0)
            self.assertGreater(dna["hand_shape"], 0.0)
        finally:
            shutil.rmtree(tmpdir)

    def test_dna_changes_when_samples_change(self):
        landmarks_open = _make_landmarks_obj(open_hand=True)
        landmarks_closed = _make_landmarks_obj(open_hand=False)
        dna_open = extract_sample_dna(landmarks_open)
        dna_closed = extract_sample_dna(landmarks_closed)
        # Finger extension should differ between open and closed hand
        self.assertNotAlmostEqual(dna_open["finger_extension"], dna_closed["finger_extension"], places=1)

    def test_compute_current_dna(self):
        landmarks = _make_landmarks_obj()
        dna = compute_current_dna(landmarks)
        self.assertGreater(dna["hand_shape"], 0.0)
        self.assertGreater(dna["finger_extension"], 0.0)
        # Single observation has 0 stability/spatial
        self.assertEqual(dna["stability"], 0.0)

    def test_compute_current_dna_none_returns_empty(self):
        dna = compute_current_dna(None)
        self.assertEqual(dna, {})


class TestGestureCoach(unittest.TestCase):
    """Feature 2: AI Gesture Coach tests."""

    def test_coach_capture_good_sample(self):
        landmarks = _make_landmarks_obj()
        fb = coach_capture_feedback(landmarks)
        self.assertIn("sample_quality", fb)
        self.assertGreater(fb["sample_quality"], 50.0)
        self.assertIn("issues", fb)
        self.assertIn("guidance", fb)

    def test_coach_capture_no_hand(self):
        fb = coach_capture_feedback(None)
        self.assertEqual(fb["sample_quality"], 0.0)
        self.assertIn("No hand detected.", fb["issues"])

    def test_coach_capture_with_stored_dna(self):
        stored_dna = extract_sample_dna(_make_landmarks_obj(open_hand=True))
        current = _make_landmarks_obj(open_hand=True)
        fb = coach_capture_feedback(current, stored_dna)
        # Same pose → high quality, few issues
        self.assertGreater(fb["sample_quality"], 50.0)

    def test_coach_capture_different_from_stored(self):
        stored_dna = extract_sample_dna(_make_landmarks_obj(open_hand=True))
        current = _make_landmarks_obj(open_hand=False)
        fb = coach_capture_feedback(current, stored_dna)
        # Different pose → more issues
        self.assertGreater(len(fb["issues"]), 0)

    def test_coach_test_excellent_match(self):
        stored_dna = extract_sample_dna(_make_landmarks_obj())
        current = _make_landmarks_obj()
        fb = coach_test_feedback(
            current_landmarks=current,
            stored_dna=stored_dna,
            expected_gesture_name="hi",
            detected_gesture_name="hi",
            confidence=95.0,
            similarity_pct=95.0,
        )
        self.assertIn("Excellent", fb["feedback"])
        self.assertGreater(fb["similarity"], 90.0)

    def test_coach_test_low_match_gives_tips(self):
        stored_dna = extract_sample_dna(_make_landmarks_obj(open_hand=True))
        current = _make_landmarks_obj(open_hand=False)
        fb = coach_test_feedback(
            current_landmarks=current,
            stored_dna=stored_dna,
            expected_gesture_name="hi",
            detected_gesture_name="Unknown",
            confidence=45.0,
            similarity_pct=45.0,
        )
        self.assertGreater(len(fb["tips"]), 0)
        self.assertLess(fb["similarity"], 85.0)

    def test_coach_live_good(self):
        stored_dna = extract_sample_dna(_make_landmarks_obj())
        current = _make_landmarks_obj()
        fb = coach_live_feedback(current, stored_dna, "hi", 92.0)
        self.assertIn("Excellent", fb["feedback"])

    def test_coach_live_no_landmarks(self):
        fb = coach_live_feedback(None, {}, "hi", 0.0)
        self.assertEqual(fb["feedback"], "")


class TestGestureAnalytics(unittest.TestCase):
    """Feature 3: Gesture Quality & Analytics tests."""

    def test_log_recognition_event(self):
        metadata = {"learning_stats": {}}
        log_recognition_event(metadata, 90.0, 85.0, True, "live")
        stats = metadata["learning_stats"]
        self.assertEqual(len(stats["recognition_log"]), 1)
        event = stats["recognition_log"][0]
        self.assertEqual(event["confidence"], 90.0)
        self.assertEqual(event["similarity"], 85.0)
        self.assertTrue(event["matched"])
        self.assertEqual(event["source"], "live")

    def test_log_recognition_event_caps_at_max(self):
        metadata = {"learning_stats": {"recognition_log": []}}
        for i in range(600):
            log_recognition_event(metadata, 80.0, 75.0, True, "live")
        self.assertLessEqual(len(metadata["learning_stats"]["recognition_log"]), 500)

    def test_compute_analytics_no_events(self):
        metadata = {
            "gesture_id": "test",
            "gesture_name": "Test",
            "sample_count": 0,
            "target_samples": 30,
            "variation_count": 0,
            "learning_stats": {},
        }
        result = compute_analytics(metadata, [], [], lambda p: {})
        analytics = result["analytics"]
        self.assertEqual(analytics["recognition_count"], 0)
        # With 0 samples, diversity = 0%, so quality = 0%
        self.assertEqual(analytics["quality_score"], 0.0)

    def test_compute_analytics_with_events(self):
        metadata = {
            "gesture_id": "test",
            "gesture_name": "Test",
            "sample_count": 10,
            "target_samples": 30,
            "variation_count": 2,
            "learning_stats": {
                "detections": 80,
                "corrections_caused": 5,
                "recognition_log": [
                    {"at": "2025-01-01T10:00:00Z", "confidence": 90.0, "similarity": 88.0, "matched": True, "source": "live"},
                    {"at": "2025-01-01T11:00:00Z", "confidence": 85.0, "similarity": 82.0, "matched": True, "source": "live"},
                    {"at": "2025-01-02T10:00:00Z", "confidence": 45.0, "similarity": 40.0, "matched": False, "source": "test"},
                ],
            },
        }
        result = compute_analytics(metadata, [], [], lambda p: {})
        analytics = result["analytics"]
        self.assertEqual(analytics["recognition_count"], 3)
        self.assertEqual(analytics["successful_count"], 2)
        self.assertEqual(analytics["unknown_count"], 1)
        # Success rate: detections=80, corrections_caused=5
        # 80 / (80 + 5) = 94.1%
        self.assertAlmostEqual(analytics["recognition_success_rate"], 94.1, places=1)
        # Avg confidence: (90 + 85 + 45) / 3 = 73.3
        self.assertAlmostEqual(analytics["avg_confidence"], 73.3, places=1)
        # Avg similarity: (88 + 82 + 40) / 3 = 70.0
        self.assertAlmostEqual(analytics["avg_similarity"], 70.0, places=1)

    def test_recognition_success_rate_correct(self):
        metadata = {
            "gesture_id": "test",
            "gesture_name": "Test",
            "sample_count": 10,
            "target_samples": 10,
            "variation_count": 0,
            "learning_stats": {
                "detections": 90,
                "corrections_caused": 10,
            },
        }
        result = compute_analytics(metadata, [], [], lambda p: {})
        # 90 / (90 + 10) = 90.0%
        self.assertAlmostEqual(result["analytics"]["recognition_success_rate"], 90.0, places=1)

    def test_correction_count_correct(self):
        metadata = {
            "gesture_id": "test",
            "gesture_name": "Test",
            "sample_count": 5,
            "target_samples": 5,
            "variation_count": 0,
            "learning_stats": {
                "corrections_caused": 3,
                "corrections_received": 2,
                "corrections_applied": 1,
            },
        }
        result = compute_analytics(metadata, [], [], lambda p: {})
        self.assertEqual(result["analytics"]["corrections_caused"], 3)
        self.assertEqual(result["analytics"]["corrections_received"], 2)
        self.assertEqual(result["analytics"]["corrections_applied"], 1)

    def test_variation_count_correct(self):
        metadata = {
            "gesture_id": "test",
            "gesture_name": "Test",
            "sample_count": 5,
            "target_samples": 5,
            "variation_count": 7,
            "learning_stats": {},
        }
        result = compute_analytics(metadata, [], [], lambda p: {})
        self.assertEqual(result["analytics"]["variation_count"], 7)

    def test_evolution_timeline_calculated(self):
        log = [
            {"at": "2025-01-01T10:00:00Z", "confidence": 90.0, "matched": True},
            {"at": "2025-01-01T11:00:00Z", "confidence": 80.0, "matched": True},
            {"at": "2025-01-02T10:00:00Z", "confidence": 50.0, "matched": False},
            {"at": "2025-01-03T10:00:00Z", "confidence": 95.0, "matched": True},
        ]
        timeline = _compute_evolution_timeline(log)
        self.assertEqual(len(timeline), 3)  # 3 unique days
        self.assertEqual(timeline[0]["date"], "2025-01-01")
        self.assertEqual(timeline[0]["event_count"], 2)
        self.assertAlmostEqual(timeline[0]["success_rate"], 100.0, places=1)
        self.assertEqual(timeline[1]["date"], "2025-01-02")
        self.assertAlmostEqual(timeline[1]["success_rate"], 0.0, places=1)
        self.assertEqual(timeline[2]["date"], "2025-01-03")

    def test_quality_score_not_random(self):
        """Quality score must be deterministic for same inputs."""
        metadata = {
            "gesture_id": "test",
            "gesture_name": "Test",
            "sample_count": 10,
            "target_samples": 30,
            "variation_count": 0,
            "learning_stats": {
                "detections": 80,
                "corrections_caused": 5,
                "recognition_log": [
                    {"at": "2025-01-01T10:00:00Z", "confidence": 90.0, "similarity": 88.0, "matched": True, "source": "live"},
                ],
            },
        }
        r1 = compute_analytics(metadata, [], [], lambda p: {})
        r2 = compute_analytics(metadata, [], [], lambda p: {})
        self.assertEqual(r1["analytics"]["quality_score"], r2["analytics"]["quality_score"])

    def test_low_quality_warning_when_success_rate_low(self):
        metadata = {
            "gesture_id": "test",
            "gesture_name": "Test",
            "sample_count": 10,
            "target_samples": 30,
            "variation_count": 0,
            "learning_stats": {
                "detections": 30,
                "corrections_caused": 20,
                "recognition_log": [
                    {"at": "2025-01-01T10:00:00Z", "confidence": 40.0, "similarity": 35.0, "matched": False, "source": "live"},
                    {"at": "2025-01-01T11:00:00Z", "confidence": 45.0, "similarity": 40.0, "matched": False, "source": "live"},
                    {"at": "2025-01-01T12:00:00Z", "confidence": 50.0, "similarity": 45.0, "matched": True, "source": "live"},
                    {"at": "2025-01-01T13:00:00Z", "confidence": 38.0, "similarity": 33.0, "matched": False, "source": "live"},
                    {"at": "2025-01-01T14:00:00Z", "confidence": 42.0, "similarity": 37.0, "matched": False, "source": "live"},
                ],
            },
        }
        result = compute_analytics(metadata, [], [], lambda p: {})
        self.assertTrue(result["analytics"]["low_quality_warning"])
        self.assertGreater(len(result["analytics"]["low_quality_reasons"]), 0)


class TestServiceIntegration(unittest.TestCase):
    """Test the service-level methods for DNA, Coach, Analytics."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.service = CustomGestureService(base_dir=self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_gesture_dna_no_gesture(self):
        result = self.service.gesture_dna("nonexistent")
        self.assertFalse(result["success"])

    def test_gesture_dna_with_samples(self):
        # Create a gesture with samples
        gesture_id = "test_wave"
        os.makedirs(os.path.join(self.tmpdir, gesture_id))
        for i in range(3):
            sample = feature_extractor.sample_representation(_make_landmarks_obj(offset_x=i * 0.005))
            sample.update({
                "schema_version": 1,
                "gesture_id": gesture_id,
                "gesture_name": "wave",
                "description": "Test wave",
                "hand": "either",
                "captured_handedness": "right",
                "sample_index": i + 1,
            })
            path = os.path.join(self.tmpdir, gesture_id, f"sample_{i+1:03d}.json")
            with open(path, "w") as f:
                json.dump(sample, f)

        metadata = {
            "schema_version": 1,
            "gesture_id": gesture_id,
            "gesture_name": "wave",
            "description": "Test wave",
            "hand": "either",
            "enabled": True,
            "target_samples": 3,
            "sample_count": 3,
            "status": "Ready",
            "prototype": [],
            "feature_count": 0,
        }
        meta_path = os.path.join(self.tmpdir, gesture_id, "metadata.json")
        with open(meta_path, "w") as f:
            json.dump(metadata, f)

        result = self.service.gesture_dna(gesture_id)
        self.assertTrue(result["success"])
        dna = result["dna"]
        self.assertEqual(dna["sample_count"], 3)
        self.assertGreater(dna["hand_shape"], 0.0)
        self.assertGreater(dna["finger_extension"], 0.0)

    def test_gesture_analytics_with_no_events(self):
        gesture_id = "test_empty"
        os.makedirs(os.path.join(self.tmpdir, gesture_id))
        metadata = {
            "schema_version": 1,
            "gesture_id": gesture_id,
            "gesture_name": "empty",
            "description": "",
            "hand": "either",
            "enabled": True,
            "target_samples": 5,
            "sample_count": 0,
            "status": "No samples",
            "prototype": [],
            "feature_count": 0,
            "learning_stats": {},
        }
        meta_path = os.path.join(self.tmpdir, gesture_id, "metadata.json")
        with open(meta_path, "w") as f:
            json.dump(metadata, f)

        result = self.service.gesture_analytics(gesture_id)
        self.assertTrue(result["success"])
        self.assertEqual(result["analytics"]["recognition_count"], 0)

    def test_gesture_full_details_returns_all_sections(self):
        gesture_id = "test_full"
        os.makedirs(os.path.join(self.tmpdir, gesture_id))
        # Add a sample
        sample = feature_extractor.sample_representation(_make_landmarks_obj())
        sample.update({
            "schema_version": 1,
            "gesture_id": gesture_id,
            "gesture_name": "full_test",
            "description": "Full test",
            "hand": "either",
            "captured_handedness": "right",
            "sample_index": 1,
        })
        sample_path = os.path.join(self.tmpdir, gesture_id, "sample_001.json")
        with open(sample_path, "w") as f:
            json.dump(sample, f)

        metadata = {
            "schema_version": 1,
            "gesture_id": gesture_id,
            "gesture_name": "full_test",
            "description": "Full test",
            "hand": "either",
            "enabled": True,
            "target_samples": 1,
            "sample_count": 1,
            "status": "Ready",
            "prototype": [],
            "feature_count": 0,
            "learning_stats": {},
        }
        meta_path = os.path.join(self.tmpdir, gesture_id, "metadata.json")
        with open(meta_path, "w") as f:
            json.dump(metadata, f)

        result = self.service.gesture_full_details(gesture_id)
        self.assertTrue(result["success"])
        self.assertIn("gesture", result)
        self.assertIn("dna", result)
        self.assertIn("analytics", result)
        self.assertIn("coach", result)
        self.assertIn("dna_comparison", result)

    def test_isolation_no_leak_to_other_features(self):
        """Custom gesture service must not affect global state."""
        self.service.mark_inactive()
        runtime = self.service.get_runtime_status()
        self.assertEqual(runtime["mode"], "idle")
        self.assertFalse(runtime["active"])


class TestClampAndEdgeCases(unittest.TestCase):
    """Edge case tests."""

    def test_clamp_pct_boundaries(self):
        self.assertEqual(_clamp_pct(150.0), 100.0)
        self.assertEqual(_clamp_pct(-50.0), 0.0)
        self.assertEqual(_clamp_pct(50.0), 50.0)

    def test_dna_similarity_empty(self):
        self.assertEqual(dna_similarity({}, {}), 0.0)
        self.assertEqual(dna_similarity(None, None), 0.0)

    def test_dna_match_level_boundaries(self):
        self.assertEqual(dna_match_level(100.0), "Excellent")
        self.assertEqual(dna_match_level(0.0), "Very Low")


if __name__ == "__main__":
    unittest.main()
