import json
import math
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from core.custom_gestures.feature_extractor import feature_extractor
from core.custom_gestures.service import CustomGestureService


class FakeLandmarks:
    def __init__(self, points):
        self.landmark = [SimpleNamespace(x=x, y=y, z=z, visibility=1.0) for x, y, z in points]


def transformed_hand(points, offset=(0.0, 0.0), scale=1.0, rotation_degrees=0.0):
    theta = math.radians(rotation_degrees)
    cos_t = math.cos(theta)
    sin_t = math.sin(theta)
    transformed = []
    for x, y, z in points:
        rx = x * cos_t - y * sin_t
        ry = x * sin_t + y * cos_t
        transformed.append((0.5 + offset[0] + rx * scale, 0.6 + offset[1] + ry * scale, z * scale))
    return FakeLandmarks(transformed)


def one_finger_shape():
    return [
        (0.00, 0.00, 0.00),
        (-0.05, -0.05, 0.00), (-0.08, -0.10, 0.00), (-0.10, -0.14, 0.00), (-0.12, -0.18, 0.00),
        (-0.04, -0.16, 0.00), (-0.04, -0.32, 0.00), (-0.04, -0.47, 0.00), (-0.04, -0.64, 0.00),
        (0.02, -0.14, 0.00), (0.05, -0.20, 0.00), (0.06, -0.18, 0.00), (0.04, -0.15, 0.00),
        (0.08, -0.11, 0.00), (0.10, -0.16, 0.00), (0.11, -0.14, 0.00), (0.09, -0.10, 0.00),
        (0.13, -0.07, 0.00), (0.15, -0.11, 0.00), (0.16, -0.09, 0.00), (0.14, -0.05, 0.00),
    ]


def open_palm_shape():
    return [
        (0.00, 0.00, 0.00),
        (-0.08, -0.06, 0.00), (-0.14, -0.17, 0.00), (-0.19, -0.28, 0.00), (-0.24, -0.38, 0.00),
        (-0.08, -0.18, 0.00), (-0.11, -0.34, 0.00), (-0.13, -0.49, 0.00), (-0.15, -0.64, 0.00),
        (0.00, -0.20, 0.00), (0.00, -0.38, 0.00), (0.00, -0.55, 0.00), (0.00, -0.72, 0.00),
        (0.08, -0.18, 0.00), (0.11, -0.34, 0.00), (0.14, -0.48, 0.00), (0.17, -0.62, 0.00),
        (0.15, -0.14, 0.00), (0.21, -0.27, 0.00), (0.26, -0.39, 0.00), (0.31, -0.50, 0.00),
    ]


def fist_shape():
    # All fingers folded, only the thumb extended: clearly different from
    # both the index-finger shape and the open palm used elsewhere.
    return [
        (0.00, 0.00, 0.00),
        (-0.05, -0.05, 0.00), (-0.10, -0.09, 0.00), (-0.14, -0.12, 0.00), (-0.18, -0.14, 0.00),
        (0.02, -0.10, 0.00), (0.01, -0.14, 0.00), (0.02, -0.13, 0.00), (0.04, -0.10, 0.00),
        (0.08, -0.12, 0.00), (0.11, -0.15, 0.00), (0.14, -0.14, 0.00), (0.16, -0.11, 0.00),
        (0.14, -0.10, 0.00), (0.17, -0.13, 0.00), (0.20, -0.12, 0.00), (0.22, -0.09, 0.00),
        (0.18, -0.08, 0.00), (0.21, -0.11, 0.00), (0.24, -0.10, 0.00), (0.26, -0.07, 0.00),
    ]


def fanned_index_shape(points, degrees=6.0):
    """Rotate the index-finger chain around its MCP joint.

    A genuine *shape* variation: translation/scale/rotation are removed by
    the feature extractor, so only a relative finger-orientation change makes
    a real variation for evolution to learn.
    """
    pts = [list(point) for point in points]
    pivot = pts[5]
    theta = math.radians(degrees)
    cos_t = math.cos(theta)
    sin_t = math.sin(theta)
    for index in (5, 6, 7, 8):
        x, y, z = pts[index]
        rx = (x - pivot[0]) * cos_t - (y - pivot[1]) * sin_t
        ry = (x - pivot[0]) * sin_t + (y - pivot[1]) * cos_t
        pts[index] = [pivot[0] + rx, pivot[1] + ry, z]
    return [tuple(point) for point in pts]


def mean_of(vectors):
    values = [list(vector) for vector in vectors if vector]
    if not values:
        return []
    size = max(len(vector) for vector in values)
    return [round(sum(v[i] if i < len(v) else 0.0 for v in values) / len(values), 6) for i in range(size)]


class CustomGestureLearningTests(unittest.TestCase):
    def make_service(self, tmp_path):
        return CustomGestureService(
            base_dir=str(tmp_path),
            capture_interval=0.0,
            min_ready_samples=3,
            stability_frames=2,
            smoothing_window=4,
            similarity_threshold=0.85,
            max_match_distance=0.45,
            event_sink=lambda event: True,
        )

    def capture_gesture(self, service, name, shape, hand="right", samples=3):
        result = service.start_capture(name, description=f"{name} gesture", hand=hand, target_samples=samples)
        self.assertTrue(result["success"], result)
        for index in range(samples):
            service.process_frame(
                None,
                transformed_hand(shape, offset=(index * 0.002, -index * 0.001), scale=1.0 + index * 0.01, rotation_degrees=index),
                active_module="custom_gestures",
            )
        status = service.get_runtime_status()
        self.assertTrue(status["capture_completed"])
        return status

    def feed_live(self, service, shape, frames=20, hand="right", **params):
        """Feed ``frames`` live-mode frames for ``shape``; return last status."""
        defaults = {"scale": 1.0, "rotation_degrees": 0.0}
        defaults.update(params)
        service.start_live_recognition()
        status = None
        for index in range(frames):
            hand_value = transformed_hand(shape, offset=(index * 0.001, 0.0), **defaults)
            left_value = hand_value if hand == "left" else None
            right_value = hand_value if hand == "right" else None
            status = service.process_frame(left_value, right_value, active_module="custom_gestures")
        return status

    def feed_unknown_to_candidate(self, service, shape, frames=25):
        self.feed_live(service, shape, frames=frames)
        return service.learning_status()["learning"]["candidates"]

    # ------------------------------------------------------------------
    # Unknown gesture discovery
    # ------------------------------------------------------------------
    def test_single_unknown_frame_never_becomes_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = self.make_service(tmp)
            self.capture_gesture(service, "hi", one_finger_shape())
            service.start_live_recognition()
            service.process_frame(None, transformed_hand(fist_shape()), "custom_gestures")
            status = service.learning_status()
            self.assertTrue(status["success"])
            self.assertEqual(status["learning"]["candidates"], [])
            self.assertEqual(status["learning"]["pending_candidate_count"], 0)

    def test_repeated_unknown_creates_candidate_without_auto_learning(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = self.make_service(tmp)
            self.capture_gesture(service, "hi", one_finger_shape())
            self.feed_live(service, fist_shape(), frames=30)

            candidates = service.learning_status()["learning"]["candidates"]
            self.assertEqual(len(candidates), 1)
            candidate = candidates[0]
            self.assertGreaterEqual(candidate["observed_count"], 8)
            self.assertGreaterEqual(candidate["cluster_similarity"], 80.0)
            self.assertTrue(candidate["ready"])

            # Discovery must not create a permanent gesture on its own.
            self.assertEqual([g["gesture_id"] for g in service.list_gestures()], ["hi"])
            self.assertFalse((Path(tmp) / "fist").exists())

    def test_distinct_unknowns_cluster_into_separate_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = self.make_service(tmp)
            self.capture_gesture(service, "hi", one_finger_shape())
            self.feed_live(service, fist_shape(), frames=20)
            self.feed_live(service, open_palm_shape(), frames=20)

            candidates = service.learning_status()["learning"]["candidates"]
            self.assertEqual(len(candidates), 2)
            self.assertEqual(
                sorted(item["observed_count"] >= 8 for item in candidates),
                [True, True],
            )

    def test_unstable_random_frames_do_not_create_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = self.make_service(tmp)
            self.capture_gesture(service, "hi", one_finger_shape())
            service.start_live_recognition()
            # Jumping between wildly different poses every frame: the
            # temporal stability gate must reject all of them.
            for index in range(15):
                shape = fist_shape() if index % 2 == 0 else open_palm_shape()
                service.process_frame(None, transformed_hand(shape, rotation_degrees=index * 17), "custom_gestures")
            status = service.learning_status()
            self.assertEqual(status["learning"]["candidates"], [])

    def test_ignored_candidate_is_suppressed_and_not_saved(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = self.make_service(tmp)
            self.capture_gesture(service, "hi", one_finger_shape())
            candidates = self.feed_unknown_to_candidate(service, fist_shape())
            candidate = candidates[0]

            result = service.ignore_candidate(candidate["candidate_id"])
            self.assertTrue(result["success"], result)

            status = service.learning_status()
            self.assertEqual(status["learning"]["candidates"], [])
            self.assertEqual(status["learning"]["ignored_candidate_count"], 1)

            # Repeating the same gesture must not resurface a new candidate.
            self.feed_live(service, fist_shape(), frames=25)
            status = service.learning_status()
            self.assertEqual(status["learning"]["candidates"], [])
            # Nothing permanent was written for the unknown gesture.
            self.assertEqual([g["gesture_id"] for g in service.list_gestures()], ["hi"])

    def test_learn_candidate_preloads_samples_into_create_workflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            service = self.make_service(tmp_path)
            self.capture_gesture(service, "hi", one_finger_shape())
            candidates = self.feed_unknown_to_candidate(service, fist_shape())
            candidate = candidates[0]

            prepared = service.prepare_candidate_learning(candidate["candidate_id"])
            self.assertTrue(prepared["success"], prepared)
            self.assertGreaterEqual(prepared["observation_count"], 8)

            result = service.start_capture(
                "next",
                description="learned from candidate",
                hand=prepared["handedness"],
                target_samples=5,
                candidate_id=candidate["candidate_id"],
            )
            self.assertTrue(result["success"], result)
            self.assertTrue(result["runtime"]["capture_completed"])

            gesture = service.get_gesture("next")
            self.assertIsNotNone(gesture)
            self.assertEqual(gesture["sample_count"], 5)
            self.assertEqual(gesture["status"], "Ready")

            samples = sorted((tmp_path / "next").glob("sample_*.json"))
            self.assertEqual(len(samples), 5)
            first = json.loads(samples[0].read_text())
            self.assertEqual(first.get("source"), "gesture_candidate")
            self.assertEqual(first["gesture_id"], "next")

            # Candidate is consumed: it no longer surfaces and the gesture is
            # now recognized normally.
            self.assertEqual(service.learning_status()["learning"]["candidates"], [])
            status = self.feed_live(service, fist_shape(), frames=10)
            self.assertEqual(status["prediction"], "next")

    def test_unrelated_capture_does_not_consume_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = self.make_service(tmp)
            self.capture_gesture(service, "hi", one_finger_shape())
            candidates = self.feed_unknown_to_candidate(service, fist_shape())
            candidate = candidates[0]
            prepared = service.prepare_candidate_learning(candidate["candidate_id"])
            self.assertTrue(prepared["success"], prepared)

            # An unrelated capture completes in between; it must not consume
            # the candidate's captured samples.
            self.capture_gesture(service, "bye", open_palm_shape())

            result = service.start_capture(
                "fist",
                hand=prepared["handedness"],
                target_samples=4,
                candidate_id=candidate["candidate_id"],
            )
            self.assertTrue(result["success"], result)
            self.assertTrue(result["runtime"]["capture_completed"])
            self.assertEqual(service.learning_status()["learning"]["candidates"], [])

    def test_learned_candidate_gesture_is_recognizable_without_existing_library(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = self.make_service(tmp)
            # No saved gestures at all: discovery must still work and the
            # learned gesture must join the library.
            candidates = self.feed_unknown_to_candidate(service, fist_shape())
            self.assertEqual(len(candidates), 1)
            candidate = candidates[0]
            prepared = service.prepare_candidate_learning(candidate["candidate_id"])
            self.assertTrue(prepared["success"], prepared)

            result = service.start_capture(
                "fist",
                hand=prepared["handedness"],
                target_samples=4,
                candidate_id=candidate["candidate_id"],
            )
            self.assertTrue(result["success"], result)
            self.assertTrue(result["runtime"]["capture_completed"])

            service.start_live_recognition()
            status = None
            for _ in range(6):
                status = service.process_frame(None, transformed_hand(fist_shape()), "custom_gestures")
            self.assertEqual(status["prediction"], "fist")
            self.assertGreaterEqual(status["confidence"], 85.0)

    # ------------------------------------------------------------------
    # Mistake memory (personalized corrections)
    # ------------------------------------------------------------------
    def test_correction_is_stored_and_applies_to_similar_gestures(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = self.make_service(tmp)
            self.capture_gesture(service, "hello", one_finger_shape())
            self.capture_gesture(service, "hi", open_palm_shape())

            # Perform the "hello" shape so the matcher predicts hello.
            status = self.feed_live(service, one_finger_shape(), frames=6)
            self.assertEqual(status["prediction"], "hello")

            result = service.record_correction("hello", "hi")
            self.assertTrue(result["success"], result)
            self.assertTrue(result["correction"]["stored_signature"])

            stored = service.list_corrections(limit=5)
            self.assertEqual(stored["success"], True)
            self.assertEqual(len(stored["corrections"]), 1)
            self.assertEqual(stored["corrections"][0]["predicted_gesture_id"], "hello")
            self.assertEqual(stored["corrections"][0]["correct_gesture_id"], "hi")

            # Repeating a similar gesture: base matcher still says hello, but
            # the personalized correction memory relabels it to hi.
            service.start_live_recognition()
            final = None
            for _ in range(6):
                final = service.process_frame(None, transformed_hand(one_finger_shape(), rotation_degrees=2), "custom_gestures")
            self.assertEqual(final["prediction"], "hi")
            self.assertIsNotNone(final["correction"])
            self.assertEqual(final["correction"]["original_name"], "hello")
            self.assertEqual(final["correction"]["corrected_name"], "hi")

            # Isolation: only the custom matcher label changed; the saved
            # gesture definitions and base library are untouched.
            self.assertEqual(service.get_gesture("hello")["gesture_name"], "hello")
            self.assertEqual(service.get_gesture("hello")["correction_count"], 1)
            self.assertEqual(service.get_gesture("hi")["detection_count"], 0)

    def test_correction_without_signature_never_overrides_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = self.make_service(tmp)
            self.capture_gesture(service, "hello", one_finger_shape())
            self.capture_gesture(service, "hi", open_palm_shape())

            # No live observation captured: the correction has no signature.
            result = service.record_correction("hello", "hi")
            self.assertTrue(result["success"], result)
            self.assertFalse(result["correction"]["stored_signature"])

            status = self.feed_live(service, one_finger_shape(), frames=6)
            self.assertEqual(status["prediction"], "hello")
            self.assertIsNone(status["correction"])

    def test_correction_validation_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = self.make_service(tmp)
            self.capture_gesture(service, "hello", one_finger_shape())
            self.capture_gesture(service, "hi", open_palm_shape())

            self.assertFalse(service.record_correction("hello", "hello")["success"])
            self.assertFalse(service.record_correction("hello", "missing")["success"])
            self.assertFalse(service.record_correction("missing", "hi")["success"])

            service.toggle_gesture("hi", enabled=False)
            result = service.record_correction("hello", "hi")
            self.assertFalse(result["success"])
            self.assertIn("enabled", result["error"].lower())
            service.toggle_gesture("hi", enabled=True)

    def test_disabled_correction_target_is_not_applied(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = self.make_service(tmp)
            self.capture_gesture(service, "hello", one_finger_shape())
            self.capture_gesture(service, "hi", open_palm_shape())
            self.feed_live(service, one_finger_shape(), frames=6)
            self.assertTrue(service.record_correction("hello", "hi")["success"])

            service.toggle_gesture("hi", enabled=False)
            status = self.feed_live(service, one_finger_shape(), frames=6)
            self.assertEqual(status["prediction"], "hello")
            self.assertIsNone(status["correction"])

    def test_corrections_are_scoped_to_custom_gestures_storage(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            service = self.make_service(tmp_path)
            self.capture_gesture(service, "hello", one_finger_shape())
            self.capture_gesture(service, "hi", open_palm_shape())
            self.feed_live(service, one_finger_shape(), frames=6)
            service.record_correction("hello", "hi")

            # Corrections live only in the custom gesture learning storage.
            learning_files = {path.name for path in (tmp_path / "_learning").glob("*.json")}
            self.assertIn("corrections.json", learning_files)
            corrections = json.loads((tmp_path / "_learning" / "corrections.json").read_text())
            self.assertEqual(len(corrections["corrections"]), 1)
            # Gesture folders only keep their own samples.
            hello_files = {path.name for path in (tmp_path / "hello").iterdir() if path.is_file()}
            self.assertIn("metadata.json", hello_files)
            self.assertNotIn("corrections.json", hello_files)

    # ------------------------------------------------------------------
    # Gesture evolution (personalized variations)
    # ------------------------------------------------------------------
    def test_valid_variations_are_detected_and_require_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            service = self.make_service(tmp)
            self.capture_gesture(service, "hi", one_finger_shape())

            # The user now performs a slightly different version: the index
            # finger is fanned out a little, but it is still "hi".
            status = self.feed_live(service, fanned_index_shape(one_finger_shape()), frames=12)
            self.assertEqual(status["prediction"], "hi")

            details = service.evolution_details("hi")
            self.assertTrue(details["success"], details)
            pending = details["evolution"]["pending"]
            self.assertEqual(len(pending), 1)
            self.assertGreaterEqual(pending[0]["observed_count"], 3)
            self.assertTrue(pending[0]["ready"])
            self.assertGreaterEqual(pending[0]["similarity_to_gesture"], 70.0)

            # Not permanent until the user accepts.
            self.assertEqual(len(list((tmp_path / "hi" / "variations").glob("variation_*.json"))), 0)

            accepted = service.accept_pending_variations("hi")
            self.assertTrue(accepted["success"], accepted)
            self.assertEqual(len(accepted["accepted"]), 1)
            variation_files = list((tmp_path / "hi" / "variations").glob("variation_*.json"))
            self.assertEqual(len(variation_files), 1)
            variation_payload = json.loads(variation_files[0].read_text())
            self.assertEqual(variation_payload["source"], "personalized_evolution")

            metadata = json.loads((tmp_path / "hi" / "metadata.json").read_text())
            self.assertEqual(metadata["variation_count"], 1)
            self.assertGreater(len(metadata["prototype"]), 0)
            self.assertEqual(metadata["learning_stats"]["variations_accepted"], 1)

            details = service.evolution_details("hi")
            self.assertEqual(len(details["evolution"]["variations"]), 1)
            self.assertEqual(details["evolution"]["pending"], [])
            self.assertEqual(details["evolution"]["stats"]["variations_accepted"], 1)

            # The gesture still recognizes the original form.
            status = self.feed_live(service, one_finger_shape(), frames=6)
            self.assertEqual(status["prediction"], "hi")

    def test_identical_repeats_do_not_accumulate_variations(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = self.make_service(tmp)
            self.capture_gesture(service, "hi", one_finger_shape())
            # Same pose as the original samples: no novelty, no variation.
            self.feed_live(service, one_finger_shape(), frames=10)
            details = service.evolution_details("hi")
            self.assertEqual(details["evolution"]["pending"], [])
            self.assertEqual(details["evolution"]["stats"]["detections"], 1)

    def test_low_confidence_observations_are_not_learned(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = self.make_service(tmp)
            self.capture_gesture(service, "hi", one_finger_shape())
            landmarks = transformed_hand(one_finger_shape(), rotation_degrees=10)
            references = service._load_sample_features("hi")
            result = service._learning.record_evolution_observation(
                gesture_dir=service._gesture_dir("hi"),
                gesture_id="hi",
                gesture_name="hi",
                hand="right",
                features=feature_extractor.feature_vector(landmarks),
                sample_representation=feature_extractor.sample_representation(landmarks),
                confidence=0.50,
                prototype=mean_of(references),
                reference_features=references,
            )
            self.assertEqual(result["recorded"], False)
            self.assertEqual(result["reason"], "low_confidence")
            self.assertEqual(service.evolution_details("hi")["evolution"]["pending"], [])

    def test_random_unknown_frames_do_not_become_variations(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = self.make_service(tmp)
            self.capture_gesture(service, "hi", one_finger_shape())
            # A completely different pose is "unknown", not an "hi" variation.
            self.feed_live(service, open_palm_shape(), frames=12)
            details = service.evolution_details("hi")
            self.assertEqual(details["evolution"]["pending"], [])
            self.assertEqual(len(list((Path(tmp) / "hi" / "variations").glob("*.json"))), 0)
            # Nothing is auto-learned into the library.
            self.assertEqual([g["gesture_id"] for g in service.list_gestures()], ["hi"])

    def test_ignored_variations_are_discarded(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            service = self.make_service(tmp)
            self.capture_gesture(service, "hi", one_finger_shape())
            self.feed_live(service, fanned_index_shape(one_finger_shape()), frames=10)
            self.assertTrue(service.evolution_details("hi")["evolution"]["pending"])

            result = service.discard_pending_variations("hi")
            self.assertTrue(result["success"], result)
            self.assertGreaterEqual(result["discarded"], 1)
            self.assertEqual(service.evolution_details("hi")["evolution"]["pending"], [])
            self.assertEqual(len(list((tmp_path / "hi" / "variations").glob("variation_*.json"))), 0)

    def test_recognition_stats_track_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = self.make_service(tmp)
            self.capture_gesture(service, "hi", one_finger_shape())
            self.feed_live(service, one_finger_shape(), frames=6)

            details = service.evolution_details("hi")
            stats = details["evolution"]["stats"]
            self.assertGreaterEqual(stats["detections"], 1)
            self.assertGreaterEqual(stats["recognition_accuracy"], 80.0)
            self.assertTrue(stats["last_detected_at"])

    # ------------------------------------------------------------------
    # Scope / isolation guarantees
    # ------------------------------------------------------------------
    def test_learning_only_runs_inside_custom_gestures_module(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = self.make_service(tmp)
            self.capture_gesture(service, "hi", one_finger_shape())
            service.start_live_recognition()
            # The same frames while another module is active: no learning.
            for _ in range(30):
                service.process_frame(None, transformed_hand(fist_shape()), active_module="recognition")
            self.assertEqual(service.get_runtime_status()["mode"], "idle")
            self.assertEqual(service.learning_status()["learning"]["candidates"], [])

    def test_all_learning_files_stay_inside_custom_gesture_storage(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            service = self.make_service(tmp_path)
            self.capture_gesture(service, "hi", one_finger_shape())
            candidates = self.feed_unknown_to_candidate(service, fist_shape())
            service.ignore_candidate(candidates[0]["candidate_id"])
            self.feed_live(service, one_finger_shape(), frames=8)

            for path in tmp_path.rglob("*"):
                relative = path.relative_to(tmp_path)
                top = relative.parts[0]
                self.assertIn(top, {"hi", "_learning"}, f"Unexpected location: {relative}")

    def test_event_sink_receives_learning_events(self):
        events = []
        with tempfile.TemporaryDirectory() as tmp:
            service = self.make_service(tmp)
            service._learning.event_sink = lambda event: events.append(event) or True
            self.capture_gesture(service, "hi", one_finger_shape())
            candidates = self.feed_unknown_to_candidate(service, fist_shape())

            self.capture_gesture(service, "bye", open_palm_shape())
            self.feed_live(service, open_palm_shape(), frames=6)
            self.assertTrue(service.record_correction("bye", "hi")["success"])
            service.ignore_candidate(candidates[0]["candidate_id"])

        types = [event["event_type"] for event in events]
        self.assertIn("candidate_detected", types)
        self.assertIn("correction_recorded", types)
        self.assertIn("candidate_ignored", types)
        for event in events:
            self.assertTrue(event["event_id"])
            self.assertTrue(event["created_at"])
            self.assertIsInstance(event["details"], dict)


if __name__ == "__main__":
    unittest.main()
