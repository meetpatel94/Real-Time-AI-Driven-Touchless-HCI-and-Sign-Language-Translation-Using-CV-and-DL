"""Route-level tests for the Custom Gesture self-learning endpoints.

These tests monkeypatch the custom gesture service singleton used by the
blueprint so the real data folder is never touched.  They are skipped when
Flask is not installed (e.g. minimal CI environments); the service-level
behavior is fully covered by test_custom_gesture_learning.py.
"""

import json
import math
import tempfile
import unittest
from types import SimpleNamespace

try:
    from flask import Flask
    HAS_FLASK = True
except ImportError:  # pragma: no cover
    HAS_FLASK = False

from core.custom_gestures.service import CustomGestureService
from tests.test_custom_gesture_learning import (
    fist_shape,
    one_finger_shape,
    open_palm_shape,
    transformed_hand,
)


@unittest.skipUnless(HAS_FLASK, "Flask is not installed")
class CustomGestureLearningRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import routes.custom_gesture_routes as routes_module

        cls._routes_module = routes_module
        cls._original_service = routes_module.custom_gesture_service
        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(routes_module.custom_gesture_bp)
        cls.client = app.test_client()

    def setUp(self):
        # Fresh service per test so gesture/candidate state never leaks.
        self._tmp = tempfile.TemporaryDirectory()
        self.service = CustomGestureService(
            base_dir=self._tmp.name,
            capture_interval=0.0,
            stability_frames=2,
            smoothing_window=4,
            similarity_threshold=0.85,
            max_match_distance=0.45,
            event_sink=lambda event: True,
        )
        self._routes_module.custom_gesture_service = self.service

    def tearDown(self):
        self._routes_module.custom_gesture_service = self._original_service
        self._tmp.cleanup()

    def capture(self, name, shape, samples=3):
        result = self.service.start_capture(name, hand="right", target_samples=samples)
        self.assertTrue(result["success"], result)
        for index in range(samples):
            self.service.process_frame(
                None,
                transformed_hand(shape, offset=(index * 0.002, 0.0), scale=1.0 + index * 0.01, rotation_degrees=index),
                active_module="custom_gestures",
            )
        self.assertTrue(self.service.get_runtime_status()["capture_completed"])

    def test_custom_gestures_page_contains_learning_ui(self):
        """The existing page template must carry the learning UI hooks."""
        import os

        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        template_path = os.path.join(repo_root, "templates", "custom_gestures", "index.html")
        with open(template_path, "r", encoding="utf-8") as handle:
            html = handle.read()
        for marker in (
            "custom-candidates-section",
            "custom-candidate-list",
            "custom-correction-prompt",
            "btn-correct-prediction",
            "custom-correction-applied",
            "custom-modal-overlay",
            "candidate-prefill-note",
            "js/custom_gestures/custom_gestures.js",
            # Original page content stays intact.
            "btn-show-create",
            "btn-start-live",
            "custom-gesture-list",
            "btn-refresh-library",
        ):
            self.assertIn(marker, html)

    def test_learning_status_endpoint(self):
        response = self.client.get("/api/custom-gestures/learning/status")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertIn("candidates", payload["learning"])
        self.assertIn("correction_count", payload["learning"])

    def test_correction_endpoints(self):
        self.capture("hello", one_finger_shape())
        self.capture("hi", open_palm_shape())
        self.service.start_live_recognition()
        for _ in range(4):
            self.service.process_frame(None, transformed_hand(one_finger_shape()), "custom_gestures")

        ok = self.client.post(
            "/api/custom-gestures/corrections",
            json={"predicted_gesture_id": "hello", "correct_gesture_id": "hi"},
        )
        self.assertEqual(ok.status_code, 201, ok.get_json())
        self.assertTrue(ok.get_json()["success"])

        invalid = self.client.post(
            "/api/custom-gestures/corrections",
            json={"predicted_gesture_id": "hello", "correct_gesture_id": "hello"},
        )
        self.assertEqual(invalid.status_code, 400)

        listed = self.client.get("/api/custom-gestures/corrections")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.get_json()["corrections"]), 1)

    def test_candidate_learn_ignore_and_evolution_endpoints(self):
        self.capture("hi", one_finger_shape())
        self.service.start_live_recognition()
        for _ in range(25):
            self.service.process_frame(None, transformed_hand(fist_shape()), "custom_gestures")

        status = self.client.get("/api/custom-gestures/learning/status").get_json()
        candidates = status["learning"]["candidates"]
        self.assertEqual(len(candidates), 1)
        candidate_id = candidates[0]["candidate_id"]

        learned = self.client.post(f"/api/custom-gestures/learning/candidates/{candidate_id}/learn")
        self.assertEqual(learned.status_code, 200, learned.get_json())
        self.assertTrue(learned.get_json()["success"])
        self.assertGreaterEqual(learned.get_json()["observation_count"], 8)

        started = self.client.post(
            "/api/custom-gestures/capture/start",
            json={
                "gesture_name": "fist",
                "hand": "right",
                "target_samples": 5,
                "candidate_id": candidate_id,
            },
        )
        self.assertEqual(started.status_code, 201, started.get_json())
        self.assertTrue(started.get_json()["runtime"]["capture_completed"])

        library = self.client.get("/api/custom-gestures").get_json()
        ids = [g["gesture_id"] for g in library["gestures"]]
        self.assertIn("fist", ids)

        # Candidate is consumed: learning status is clean now.
        status = self.client.get("/api/custom-gestures/learning/status").get_json()
        self.assertEqual(status["learning"]["candidates"], [])

        evolution = self.client.get("/api/custom-gestures/hi/evolution")
        self.assertEqual(evolution.status_code, 200)
        self.assertEqual(evolution.get_json()["evolution"]["gesture_id"], "hi")

        accept = self.client.post("/api/custom-gestures/hi/variations/accept")
        self.assertEqual(accept.status_code, 400)  # nothing pending yet

        missing = self.client.get("/api/custom-gestures/nope/evolution")
        self.assertEqual(missing.status_code, 404)

        # Ignore flow on a fresh candidate (live mode again).
        self.service.start_live_recognition()
        for _ in range(25):
            self.service.process_frame(None, transformed_hand(open_palm_shape()), "custom_gestures")
        status = self.client.get("/api/custom-gestures/learning/status").get_json()
        self.assertEqual(len(status["learning"]["candidates"]), 1)
        other_id = status["learning"]["candidates"][0]["candidate_id"]

        ignored = self.client.post(f"/api/custom-gestures/learning/candidates/{other_id}/ignore")
        self.assertEqual(ignored.status_code, 200, ignored.get_json())
        status = self.client.get("/api/custom-gestures/learning/status").get_json()
        self.assertEqual(status["learning"]["candidates"], [])
        self.assertEqual(status["learning"]["ignored_candidate_count"], 1)

        gone = self.client.post(f"/api/custom-gestures/learning/candidates/{other_id}/ignore")
        self.assertEqual(gone.status_code, 404)


if __name__ == "__main__":
    unittest.main()
