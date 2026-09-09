"""Route-level tests for the studio contextual completion endpoints.

The blueprint is registered on a bare Flask app so the tests never touch the
camera, MediaPipe or MongoDB.  They are skipped when Flask is not installed;
the engine behaviour is covered by test_sentence_completion.py.
"""

import json
import unittest

try:
    from flask import Flask

    HAS_FLASK = True
except ImportError:  # pragma: no cover
    HAS_FLASK = False


@unittest.skipUnless(HAS_FLASK, "Flask is not installed")
class StudioCompletionRouteTests(unittest.TestCase):
    def setUp(self):
        from routes.studio_routes import studio_bp
        from services.sentence_completion_service import sentence_completion_service

        self.service = sentence_completion_service
        self.service.clear_cache()
        self.service.clear_memory("route-test-user")

        self.app = Flask(__name__)
        self.app.register_blueprint(studio_bp)
        self.client = self.app.test_client()

    def tearDown(self):
        self.service.clear_cache()
        self.service.clear_memory("route-test-user")

    def completions(self, text, **params):
        params.setdefault("text", text)
        response = self.client.get("/api/studio/completions", query_string=params)
        self.assertEqual(response.status_code, 200)
        return response.get_json()

    def test_endpoint_returns_contextual_completions(self):
        payload = self.completions("I am go")
        self.assertTrue(payload["success"])
        self.assertEqual(payload["clause"], "I am go")
        self.assertIn("I am going home", [item["text"] for item in payload["suggestions"]])

    def test_endpoint_never_returns_more_than_three(self):
        payload = self.completions("I need", limit=10)
        self.assertLessEqual(len(payload["suggestions"]), 3)

    def test_empty_text_is_not_an_error(self):
        payload = self.completions("")
        self.assertTrue(payload["success"])
        self.assertEqual(payload["suggestions"], [])

    def test_caret_is_honoured(self):
        text = "Where is the bathroom? I need"
        payload = self.completions(text, caret=len(text))
        self.assertEqual(payload["clause"], "I need")
        self.assertEqual(payload["clause_start"], 23)
        self.assertIn("I need help", [item["text"] for item in payload["suggestions"]])

    def test_mid_text_caret_only_completes_the_clause(self):
        # Editing before the end of the sentence must not touch the tail.
        payload = self.completions("I am going home now", caret=4)
        self.assertEqual(payload["clause"], "I am")
        self.assertEqual(payload["clause_start"], 0)

    def test_response_is_cached_on_repeat(self):
        first = self.completions("Can you")
        second = self.completions("Can you")
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertEqual(
            [item["text"] for item in first["suggestions"]],
            [item["text"] for item in second["suggestions"]],
        )

    def test_accept_endpoint_remembers_the_phrase(self):
        response = self.client.post(
            "/api/studio/completions/accept",
            data=json.dumps(
                {"text": "I need", "caret": 6, "suggestion": "I need a break", "user_id": "route-test-user"}
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["remembered"])

        snapshot = self.service.memory_snapshot("route-test-user")
        self.assertTrue(any(item["context"] == "i need" for item in snapshot))

    def test_accept_endpoint_rejects_unrelated_payload(self):
        response = self.client.post(
            "/api/studio/completions/accept",
            data=json.dumps({"text": "Where is", "suggestion": "I need help", "user_id": "route-test-user"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()["remembered"])

    def test_accept_endpoint_survives_bad_json(self):
        response = self.client.post(
            "/api/studio/completions/accept", data="not-json", content_type="application/json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()["remembered"])

    def test_word_prediction_endpoint_is_untouched(self):
        response = self.client.get("/api/studio/suggestions", query_string={"prefix": "hel"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("HELLO", response.get_json()["suggestions"])


if __name__ == "__main__":
    unittest.main()
