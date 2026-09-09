import json
import math
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from core.custom_gestures.feature_extractor import feature_extractor
from core.custom_gestures.service import CustomGestureService, sanitize_gesture_name


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
    # Wrist-origin synthetic hand with the index finger extended and other
    # fingers folded. Tests use this as sample data only; production matching
    # never hardcodes finger counts or labels.
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


class CustomGestureLibraryTests(unittest.TestCase):
    def make_service(self, tmp_path):
        return CustomGestureService(
            base_dir=str(tmp_path),
            capture_interval=0.0,
            min_ready_samples=3,
            stability_frames=2,
            smoothing_window=4,
            similarity_threshold=0.85,
            max_match_distance=0.45,
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

    def test_sanitize_gesture_name_blocks_paths(self):
        self.assertEqual(sanitize_gesture_name("Hi there!"), "hi_there")
        self.assertEqual(sanitize_gesture_name("../hi"), "hi")
        with self.assertRaises(ValueError):
            sanitize_gesture_name("../../")

    def test_feature_normalization_is_position_scale_and_rotation_tolerant(self):
        base = transformed_hand(one_finger_shape(), offset=(0.0, 0.0), scale=1.0, rotation_degrees=0.0)
        moved = transformed_hand(one_finger_shape(), offset=(0.12, -0.08), scale=1.45, rotation_degrees=8.0)

        base_features = feature_extractor.feature_vector(base)
        moved_features = feature_extractor.feature_vector(moved)
        avg_delta = sum(abs(a - b) for a, b in zip(base_features, moved_features)) / len(base_features)

        self.assertLess(avg_delta, 0.04)

    def test_capture_creates_own_folder_metadata_and_samples(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            service = self.make_service(tmp_path)
            self.capture_gesture(service, "hi", one_finger_shape(), samples=3)

            gesture_dir = tmp_path / "hi"
            self.assertTrue(gesture_dir.is_dir())
            samples = sorted(gesture_dir.glob("sample_*.json"))
            self.assertEqual([path.name for path in samples], ["sample_001.json", "sample_002.json", "sample_003.json"])

            metadata = json.loads((gesture_dir / "metadata.json").read_text())
            self.assertEqual(metadata["gesture_name"], "hi")
            self.assertEqual(metadata["sample_count"], 3)
            self.assertEqual(metadata["status"], "Ready")
            self.assertTrue(metadata["prototype"])

            sample_payload = json.loads(samples[0].read_text())
            self.assertEqual(sample_payload["gesture_id"], "hi")
            self.assertEqual(len(sample_payload["raw_landmarks"]), 21)
            self.assertEqual(len(sample_payload["normalized_landmarks"]), 21)
            self.assertTrue(sample_payload["features"])

    def test_custom_matching_is_enabled_scoped_and_delete_is_folder_scoped(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            service = self.make_service(tmp_path)
            self.capture_gesture(service, "hi", one_finger_shape(), samples=3)
            self.capture_gesture(service, "stop", open_palm_shape(), samples=3)

            service.start_live_recognition()
            for _ in range(2):
                service.process_frame(None, transformed_hand(one_finger_shape(), offset=(0.05, 0.03), scale=0.9, rotation_degrees=-4), "custom_gestures")
            status = service.get_runtime_status()
            self.assertEqual(status["prediction"], "hi")
            self.assertGreaterEqual(status["confidence"], 85.0)

            service.toggle_gesture("hi", enabled=False)
            service.start_live_recognition()
            for _ in range(2):
                service.process_frame(None, transformed_hand(one_finger_shape(), offset=(0.05, 0.03), scale=0.9, rotation_degrees=-4), "custom_gestures")
            status = service.get_runtime_status()
            self.assertEqual(status["prediction"], "Unknown")

            delete_result = service.delete_gesture("hi")
            self.assertTrue(delete_result["success"])
            self.assertFalse((tmp_path / "hi").exists())
            self.assertTrue((tmp_path / "stop").exists())

    def test_either_hand_matching_is_mirror_tolerant(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            service = self.make_service(tmp_path)
            self.capture_gesture(service, "hi", one_finger_shape(), hand="either", samples=3)
            mirrored_shape = [(-x, y, z) for x, y, z in one_finger_shape()]

            service.start_live_recognition()
            for _ in range(2):
                service.process_frame(transformed_hand(mirrored_shape, rotation_degrees=3), None, "custom_gestures")
            status = service.get_runtime_status()

            self.assertEqual(status["prediction"], "hi")
            self.assertGreaterEqual(status["confidence"], 85.0)

    def test_processing_outside_custom_section_stops_live_recognition(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            service = self.make_service(tmp_path)
            self.capture_gesture(service, "hi", one_finger_shape(), samples=3)
            service.start_live_recognition()

            service.process_frame(None, transformed_hand(one_finger_shape()), active_module="recognition")
            status = service.get_runtime_status()

            self.assertEqual(status["mode"], "idle")
            self.assertEqual(status["prediction"], "Unknown")


if __name__ == "__main__":
    unittest.main()
