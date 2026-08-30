import tempfile
import unittest
from pathlib import Path

from core.adaptive.observation import GestureObservation, MotionFeatures
from database.sqlite_database import SQLiteDatabase
from models.user_profile import InteractionEvent
from repositories.interaction_event_repository import InteractionEventRepository
from repositories.user_profile_repository import UserProfileRepository
from services.interaction_history_service import InteractionHistoryService
from services.intent_interpretation_service import ContextAwareIntentInterpreter
from services.unknown_gesture_service import UnknownGestureDetector
from services.user_profile_service import UserProfileService


class AdaptiveArchitectureTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        database = SQLiteDatabase(str(Path(self.temp_dir.name) / "adaptive.sqlite3"))
        self.profile_service = UserProfileService(UserProfileRepository(database))
        self.event_repository = InteractionEventRepository(database)
        self.history_service = InteractionHistoryService(
            self.event_repository,
            self.profile_service,
        )
        self.profile = self.profile_service.update_profile(
            "test operator",
            {"display_name": "Test operator", "cursor_sensitivity": 0.8},
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    @staticmethod
    def observation(gesture="NONE", motion=None):
        return GestureObservation(
            timestamp=1.0,
            handedness="right",
            gesture=gesture,
            finger_states={
                "index": gesture == "ONE_FINGER",
                "middle": gesture == "TWO_FINGER",
                "ring": False,
                "pinky": False,
            },
            palm_center=(0.5, 0.5),
            hand_scale=0.4,
            tracking_quality=1.0,
            motion=motion or MotionFeatures(sample_count=3),
        )

    def test_profile_and_transition_persist_without_frames(self):
        event = InteractionEvent(
            user_id=self.profile.user_id,
            occurred_at="2026-08-30T00:00:00+00:00",
            module="mouse",
            hand="right",
            gesture="ONE_FINGER",
            finger_count=1,
            motion_direction="stationary",
            motion_speed=0.0,
            displacement=0.0,
            tracking_quality=1.0,
            is_unknown=False,
            unknown_status="KNOWN_GESTURE",
            unknown_reason="",
            intent="cursor.move",
            intent_confidence=0.9,
            action_taken=True,
        )
        saved = self.history_service.record_transition(event)
        self.assertIsNotNone(saved)
        self.assertEqual(len(self.history_service.recent_events(self.profile.user_id)), 1)
        persisted = UserProfileService(
            UserProfileRepository(SQLiteDatabase(str(Path(self.temp_dir.name) / "adaptive.sqlite3")))
        ).get_profile(self.profile.user_id)
        self.assertEqual(persisted.display_name, "Test operator")
        self.assertEqual(persisted.interaction_count, 1)

    def test_unknown_pose_requires_temporal_persistence(self):
        detector = UnknownGestureDetector(min_samples=3, hold_seconds=0)
        unknown = None
        pose = self.observation(
            motion=MotionFeatures(
                displacement=0.1,
                speed=0.4,
                direction="diagonal",
                sample_count=4,
            )
        )
        for _ in range(3):
            unknown = detector.evaluate(pose, None, {}, self.profile, {"active_module": "mouse"})
        self.assertTrue(unknown.is_unknown)
        self.assertEqual(unknown.status, "UNKNOWN_GESTURE")

    def test_context_history_changes_interpretation_confidence(self):
        interpreter = ContextAwareIntentInterpreter()
        pose = self.observation("ONE_FINGER")
        known = UnknownGestureDetector(min_samples=1, hold_seconds=0).evaluate(
            pose, None, {}, self.profile, {"active_module": "mouse"}
        )
        first = interpreter.interpret(
            pose, None, known,
            {"active_module": "mouse", "gesture_enabled": True, "recent_intents": []},
            self.profile,
        )
        repeated = interpreter.interpret(
            pose, None, known,
            {"active_module": "mouse", "gesture_enabled": True, "recent_intents": ["cursor.move"]},
            self.profile,
        )
        self.assertEqual(first.name, "cursor.move")
        self.assertEqual(repeated.name, "cursor.move")
        self.assertGreater(repeated.confidence, first.confidence)


if __name__ == "__main__":
    unittest.main()
