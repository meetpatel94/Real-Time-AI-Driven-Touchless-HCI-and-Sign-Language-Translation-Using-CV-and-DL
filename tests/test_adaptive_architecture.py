import unittest
from unittest.mock import patch

from core.adaptive.feature_extractor import DerivedFeatureExtractor
from core.adaptive.observation import GestureObservation, MotionFeatures
from database.mongo_database import MongoDatabase
from models.personalization import (
    CalibrationSample,
    CustomGestureMapping,
    FeatureSignature,
    LearnedGesture,
)
from models.user_profile import InteractionEvent, UserProfile
from repositories.interaction_event_repository import InteractionEventRepository
from repositories.personalization_repository import (
    CalibrationSessionRepository,
    CorrectionRepository,
    CustomMappingRepository,
    LearnedGestureRepository,
)
from repositories.user_profile_repository import UserProfileRepository
from services.interaction_history_service import InteractionHistoryService
from services.intent_interpretation_service import ContextAwareIntentInterpreter
from services.personalization_service import PersonalizationService
from services.unknown_gesture_service import UnknownGestureDetector
from services.user_profile_service import UserProfileService


class MockResult:
    def __init__(self, matched_count=1, deleted_count=1):
        self.matched_count = matched_count
        self.deleted_count = deleted_count


class MockCursor:
    def __init__(self, documents):
        self.documents = list(documents)

    def sort(self, keys):
        for key, direction in reversed(list(keys)):
            self.documents.sort(key=lambda item: str(item.get(key, "")), reverse=direction < 0)
        return self

    def limit(self, amount):
        self.documents = self.documents[:amount]
        return self

    def __iter__(self):
        return iter(self.documents)


class MockCollection:
    def __init__(self):
        self.documents = {}
        self.indexes = []

    @classmethod
    def _matches(cls, document, query):
        for field, value in query.items():
            if field == "$or":
                if not any(cls._matches(document, option) for option in value):
                    return False
            elif isinstance(value, dict) and "$exists" in value:
                if (field in document) != bool(value["$exists"]):
                    return False
            elif document.get(field) != value:
                return False
        return True

    def create_index(self, keys, **kwargs):
        self.indexes.append((keys, kwargs))

    def find_one(self, query, sort=None):
        matches = [document for document in self.documents.values() if all(document.get(key) == value for key, value in query.items())]
        if sort and matches:
            for key, direction in reversed(list(sort)):
                matches.sort(key=lambda item: str(item.get(key, "")), reverse=direction < 0)
        return dict(matches[0]) if matches else None

    def find(self, query):
        return MockCursor([
            dict(document) for document in self.documents.values()
            if all(document.get(key) == value for key, value in query.items())
        ])

    def replace_one(self, query, document, upsert=False):
        key = query.get("_id")
        existing = self.documents.get(key)
        if existing is not None and not self._matches(existing, query):
            if upsert:
                raise RuntimeError("duplicate _id in mocked MongoDB")
            return MockResult(matched_count=0)
        if existing is None and not upsert:
            return MockResult(matched_count=0)
        self.documents[key] = dict(document)
        return MockResult()

    def insert_one(self, document):
        self.documents[document["_id"]] = dict(document)

    def update_one(self, query, updates):
        document = self.find_one(query)
        if document is None:
            return MockResult(matched_count=0)
        for key, value in updates.get("$set", {}).items():
            document[key] = value
        for key, value in updates.get("$inc", {}).items():
            document[key] = document.get(key, 0) + value
        self.documents[document["_id"]] = document
        return MockResult()

    def delete_one(self, query):
        document = self.find_one(query)
        if document is None:
            return MockResult(deleted_count=0)
        del self.documents[document["_id"]]
        return MockResult(deleted_count=1)

    def delete_many(self, query):
        for key in list(self.documents):
            if all(self.documents[key].get(field) == value for field, value in query.items()):
                del self.documents[key]
        return MockResult(deleted_count=1)


class MockMongoDatabase:
    """In-memory mock of the repository-facing Mongo database boundary."""

    def __init__(self, available=True):
        self.available = available
        self.collections = {}
        self.errors = []

    def collection(self, name):
        if not self.available:
            return None
        return self.collections.setdefault(name, MockCollection())

    def mark_unavailable(self, error):
        self.errors.append(str(error))
        self.available = False

    def health(self):
        return {"available": self.available, "database": "mock", "error": self.errors[-1] if self.errors else ""}


def make_repos(database):
    return (
        CalibrationSessionRepository(database),
        LearnedGestureRepository(database),
        CorrectionRepository(database),
        CustomMappingRepository(database),
    )


class DynamicDatabaseMock:
    """Database-shaped double with PyMongo's dynamic collection attributes."""

    name = "direct-mock"

    def __init__(self):
        self.collections = {}

    def __getitem__(self, name):
        return self.collections.setdefault(name, MockCollection())

    def __getattr__(self, name):
        return self[name]


class SchemaMockDatabase:
    def __init__(self):
        self.collections = {}
        self.validators = {}

    def create_collection(self, name, **options):
        self.validators[name] = options
        self.collections.setdefault(name, MockCollection())

    def command(self, *args, **kwargs):
        return {"ok": 1}

    def __getitem__(self, name):
        return self.collections.setdefault(name, MockCollection())


class SchemaMockClient:
    def __init__(self, *args, **kwargs):
        self.admin = self
        self.database = SchemaMockDatabase()

    def command(self, *args, **kwargs):
        return {"ok": 1}

    def __getitem__(self, name):
        return self.database

    def close(self):
        pass


class CredentialFailingClient:
    def __init__(self, *args, **kwargs):
        self.admin = self

    def command(self, *args, **kwargs):
        raise RuntimeError(
            "authentication failed for mongodb://operator:super-secret@example.invalid"
        )

    def close(self):
        pass


class AdaptiveArchitectureTests(unittest.TestCase):
    def setUp(self):
        self.database = MockMongoDatabase()
        self.profile_service = UserProfileService(UserProfileRepository(self.database))
        self.event_repository = InteractionEventRepository(self.database)
        self.history_service = InteractionHistoryService(self.event_repository, self.profile_service)
        self.profile = self.profile_service.update_profile(
            "test operator",
            {"display_name": "Test operator", "cursor_sensitivity": 0.8},
        )
        repos = make_repos(self.database)
        self.personalization = PersonalizationService(*repos)

    @staticmethod
    def observation(gesture="NONE", motion=None, signature=None, handedness="right"):
        signature = signature or FeatureSignature(
            landmark_geometry=[0.10] * 63,
            proportion_features=[0.20] * 13,
            trajectory_features=[0.0] * 7,
            temporal_pattern=[1.0] + [0.0] * 8,
        )
        return GestureObservation(
            timestamp=1.0,
            handedness=handedness,
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
            motion=motion or MotionFeatures(sample_count=3, stability=1.0),
            derived_features=signature.to_document(),
        )

    def test_mongodb_bootstrap_uses_validators_and_indexes(self):
        database = MongoDatabase(
            uri="mongodb://mock",
            database_name="mock-gestureforge",
            client_factory=SchemaMockClient,
        )
        self.assertIsNotNone(database.collection("user_profiles"))
        self.assertTrue(database.available)
        self.assertEqual(database.database_name, "mock-gestureforge")
        self.assertEqual(set(database._database.validators), set(database.COLLECTIONS))
        for name in database.COLLECTIONS:
            self.assertTrue(database._database[name].indexes)
        index_options = {
            name: {
                tuple(keys): options.get("unique", False)
                for keys, options in database._database[name].indexes
            }
            for name in database.COLLECTIONS
        }
        self.assertTrue(index_options["user_profiles"][(('user_id', 1),)])
        self.assertTrue(index_options["learned_gestures"][(('user_id', 1), ('gesture_key', 1))])
        self.assertTrue(index_options["custom_gesture_mappings"][(('user_id', 1), ('learned_gesture_id', 1))])
        self.assertFalse(index_options["learned_gestures"].get((('user_id', 1),), False))

    def test_mongodb_connection_errors_redact_credentials(self):
        database = MongoDatabase(
            uri="mongodb://operator:super-secret@example.invalid",
            database_name="mock-gestureforge",
            client_factory=CredentialFailingClient,
        )
        self.assertIsNone(database.collection("user_profiles"))
        self.assertNotIn("super-secret", database.last_error)
        self.assertIn("<redacted>", database.last_error)

    def test_profile_and_transition_persist_in_mock_mongodb_without_frames(self):
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
            temporal_features={"speed": 0.2, "landmarks": [[1, 2, 3]], "trajectory": [[0.1, 0.2]]},
            context_snapshot={"active_module": "mouse", "frame": "must-not-persist"},
        )
        saved = self.history_service.record_transition(event)
        self.assertIsNotNone(saved)
        self.assertEqual(len(self.history_service.recent_events(self.profile.user_id)), 1)
        persisted = UserProfileService(UserProfileRepository(self.database)).get_profile(self.profile.user_id)
        self.assertEqual(persisted.display_name, "Test operator")
        self.assertEqual(persisted.interaction_count, 1)
        profile_document = self.database.collections["user_profiles"].documents[self.profile.user_id]
        self.assertNotIn("frame", profile_document)
        self.assertNotIn("video", profile_document)
        event_document = self.database.collections["interaction_events"].documents[saved.event_id]
        self.assertNotIn("landmarks", event_document["temporal_features"])
        self.assertNotIn("frame", event_document["context_snapshot"])
        self.assertEqual(event_document["temporal_features"]["trajectory"], [[0.1, 0.2]])

    def test_profile_repository_does_not_trust_a_cross_user_stable_id(self):
        collection = self.database.collection("user_profiles")
        collection.documents["alice"] = {
            "_id": "alice",
            "user_id": "bob",
            "display_name": "Bob",
        }
        repository = UserProfileRepository(self.database)
        self.assertIsNone(repository.get("alice"))
        self.assertFalse(repository.save(UserProfile(user_id="alice")))
        self.assertEqual(collection.documents["alice"]["user_id"], "bob")

    def test_repositories_accept_a_direct_pymongo_database_shape(self):
        database = DynamicDatabaseMock()
        profile_repository = UserProfileRepository(database)
        profile = UserProfileService(profile_repository).get_profile("direct")
        self.assertEqual(profile.user_id, "direct")

        event = InteractionEvent(
            user_id="direct",
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
        self.assertIsNotNone(InteractionEventRepository(database).add(event))

        learned = LearnedGesture(
            user_id="direct",
            gesture_key="custom:wave",
            display_name="Wave",
            target_type="custom",
            target_value="wave",
            centroid=FeatureSignature(landmark_geometry=[0.1] * 63),
            validated_examples=3,
            reliability=0.8,
            match_distance_threshold=0.2,
        )
        learned_repository = LearnedGestureRepository(database)
        self.assertTrue(learned_repository.save(learned))
        self.assertEqual(learned_repository.get("direct", learned.learned_id).learned_id, learned.learned_id)

    def test_history_deduplicates_motion_jitter_and_retries_after_storage_recovery(self):
        unavailable = MockMongoDatabase(available=False)
        profile_service = UserProfileService(UserProfileRepository(unavailable))
        history = InteractionHistoryService(InteractionEventRepository(unavailable), profile_service)
        event = InteractionEvent(
            user_id="offline",
            occurred_at="2026-08-30T00:00:00+00:00",
            module="mouse",
            hand="right",
            gesture="ONE_FINGER",
            finger_count=1,
            motion_direction="right",
            motion_speed=0.1,
            displacement=0.02,
            tracking_quality=1.0,
            is_unknown=False,
            unknown_status="KNOWN_GESTURE",
            unknown_reason="",
            intent="cursor.move",
            intent_confidence=0.9,
            action_taken=True,
        )
        self.assertIsNone(history.record_transition(event))
        unavailable.available = True
        self.assertIsNotNone(history.record_transition(event))
        jittered = InteractionEvent(**{**event.__dict__, "event_id": None, "motion_direction": "left"})
        self.assertIsNone(history.record_transition(jittered))
        self.assertEqual(len(unavailable.collections["interaction_events"].documents), 1)

    def test_rate_limited_transition_is_persisted_after_staying_stable(self):
        database = MockMongoDatabase()
        profile_service = UserProfileService(UserProfileRepository(database))
        history = InteractionHistoryService(InteractionEventRepository(database), profile_service)
        first = InteractionEvent(
            user_id="rate-limited",
            occurred_at="2026-08-30T00:00:00+00:00",
            module="mouse",
            hand="right",
            gesture="ONE_FINGER",
            finger_count=1,
            motion_direction="right",
            motion_speed=0.1,
            displacement=0.02,
            tracking_quality=1.0,
            is_unknown=False,
            unknown_status="KNOWN_GESTURE",
            unknown_reason="",
            intent="cursor.move",
            intent_confidence=0.9,
            action_taken=True,
        )
        next_event = InteractionEvent(**{
            **first.__dict__,
            "event_id": None,
            "intent": "scroll.up",
        })
        with patch("services.interaction_history_service.time.monotonic", side_effect=[10.0, 10.1, 10.6]):
            self.assertIsNotNone(history.record_transition(first))
            self.assertIsNone(history.record_transition(next_event))
            self.assertIsNotNone(history.record_transition(next_event))
        self.assertEqual(len(database.collections["interaction_events"].documents), 2)

    def test_low_quality_frames_do_not_count_toward_unknown_persistence(self):
        detector = UnknownGestureDetector(min_samples=3, hold_seconds=0)
        low_quality = self.observation()
        low_quality.tracking_quality = 0.40
        for _ in range(3):
            result = detector.evaluate(
                low_quality, None, {}, self.profile, {"active_module": "mouse"}
            )
            self.assertFalse(result.is_unknown)
            self.assertEqual(result.status, "LOW_TRACKING_QUALITY")

        stable = self.observation(
            motion=MotionFeatures(
                displacement=0.1,
                speed=0.4,
                direction="diagonal",
                sample_count=4,
            )
        )
        result = detector.evaluate(stable, None, {}, self.profile, {"active_module": "mouse"})
        self.assertFalse(result.is_unknown)

        unstable = self.observation(motion=MotionFeatures(sample_count=3, stability=0.10))
        result = detector.evaluate(unstable, None, {}, self.profile, {"active_module": "mouse"})
        self.assertFalse(result.is_unknown)
        self.assertEqual(result.status, "TRANSITIONING")

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

    def test_derived_features_are_normalized_and_do_not_include_landmark_objects(self):
        class Point:
            def __init__(self, x, y, z=0.0):
                self.x, self.y, self.z = x, y, z

        class Landmarks:
            landmark = [Point(index * 0.01, index * 0.02, -index * 0.001) for index in range(21)]

        signature = DerivedFeatureExtractor.extract(Landmarks(), MotionFeatures(sample_count=3))
        self.assertEqual(len(signature.landmark_geometry), 63)
        self.assertEqual(signature.landmark_geometry[:3], [0.0, 0.0, 0.0])
        document = signature.to_document()
        self.assertNotIn("landmark", document)
        self.assertNotIn("image", document)
        self.assertTrue(all(isinstance(value, float) for value in document["landmark_geometry"]))

    def test_persisted_boolean_text_is_deserialized_without_truthiness_regressions(self):
        profile = UserProfile.from_document({
            "user_id": "flags",
            "adaptive_enabled": "false",
            "learning_enabled": "0",
        })
        self.assertFalse(profile.adaptive_enabled)
        self.assertFalse(profile.learning_enabled)

        bounded_preferences = UserProfile.from_document({
            "user_id": "preferences",
            "intent_preferences": {
                "preferred_intent": "cursor.move",
                "landmark_dump": [1, 2, 3],
                "nested_blob": {"frame": "not allowed"},
            },
        })
        self.assertEqual(bounded_preferences.to_document()["intent_preferences"], {
            "preferred_intent": "cursor.move",
        })

        event = InteractionEvent.from_document({
            "_id": "event-flags",
            "user_id": "flags",
            "occurred_at": "2026-08-30T00:00:00+00:00",
            "is_unknown": "false",
            "action_taken": "0",
        })
        self.assertFalse(event.is_unknown)
        self.assertFalse(event.action_taken)

        sample = CalibrationSample.from_document({"validated": "false"})
        mapping = CustomGestureMapping.from_document({"_id": "mapping-flags", "enabled": "false"})
        self.assertFalse(sample.validated)
        self.assertFalse(mapping.enabled)

        bounded_learning = LearnedGesture.from_document({
            "reliability": 100,
            "match_distance_threshold": 99,
        })
        self.assertEqual(bounded_learning.reliability, 1.0)
        self.assertEqual(bounded_learning.match_distance_threshold, 0.60)

        profile_service = UserProfileService(UserProfileRepository(MockMongoDatabase()))
        contradictory = profile_service.update_profile(
            "contradictory",
            {"interaction_mode": "legacy", "adaptive_enabled": True},
        )
        self.assertFalse(contradictory.adaptive_enabled)
        self.assertEqual(contradictory.interaction_mode, "legacy")

    def test_camera_stream_reset_invalidates_stale_calibration_observations(self):
        result = self.personalization.start_calibration(
            self.profile.user_id, "custom:wave", required_samples=3
        )
        session_id = result["calibration"]["session_id"]
        self.personalization.register_observation(self.profile.user_id, self.observation())
        self.personalization.clear_latest_observations(self.profile.user_id)

        pending = self.personalization.request_sample(self.profile.user_id, session_id)
        self.assertTrue(pending["pending"])
        self.assertEqual(pending["calibration"]["accepted_samples"], 0)

    def test_pending_capture_waits_for_the_expected_calibration_hand(self):
        result = self.personalization.start_calibration(
            self.profile.user_id, "B", required_samples=3
        )
        session_id = result["calibration"]["session_id"]
        self.personalization.request_sample(self.profile.user_id, session_id)
        self.personalization.register_observation(self.profile.user_id, self.observation(handedness="right"))
        self.assertEqual(
            self.personalization.get_calibration(self.profile.user_id, session_id).accepted_count,
            0,
        )
        self.personalization.register_observation(self.profile.user_id, self.observation(handedness="left"))
        self.assertEqual(
            self.personalization.get_calibration(self.profile.user_id, session_id).accepted_count,
            1,
        )

    def test_personalized_predictions_are_scoped_to_the_calibrated_hand(self):
        signature = FeatureSignature(landmark_geometry=[0.1] * 63)
        sign = LearnedGesture(
            user_id=self.profile.user_id,
            gesture_key="sign:A",
            display_name="A",
            target_type="sign",
            target_value="A",
            centroid=signature,
            validated_examples=3,
            reliability=0.8,
            match_distance_threshold=0.2,
        )
        control = LearnedGesture(
            user_id=self.profile.user_id,
            gesture_key="custom:wave",
            display_name="Wave",
            target_type="custom",
            target_value="wave",
            centroid=signature,
            validated_examples=3,
            reliability=0.8,
            match_distance_threshold=0.2,
        )
        self.personalization.learned_repository.save(sign)
        self.personalization.learned_repository.save(control)
        self.assertFalse(
            self.personalization.create_mapping(self.profile.user_id, sign.learned_id, "click")["success"]
        )
        self.personalization.mapping_repository.save(CustomGestureMapping(
            user_id=self.profile.user_id,
            learned_gesture_id=control.learned_id,
            name="Wave click",
            action="click",
        ))
        # A legacy/malformed sign mapping must not turn the left-hand sign
        # stream into an OS-action stream.
        self.personalization.mapping_repository.save(CustomGestureMapping(
            user_id=self.profile.user_id,
            learned_gesture_id=sign.learned_id,
            name="Unsafe sign mapping",
            action="click",
        ))
        left = self.observation(handedness="left")
        right = self.observation(handedness="right")

        left_decision = self.personalization.match(self.profile.user_id, left, "NONE", 0.0, self.profile)
        right_decision = self.personalization.match(self.profile.user_id, right, "NONE", 0.0, self.profile)
        self.assertEqual(left_decision.personalized_label, "A")
        self.assertIsNone(left_decision.mapping_action)
        self.assertEqual(right_decision.personalized_label, "wave")
        self.assertEqual(right_decision.mapping_action, "click")

        invalid_quality = self.observation()
        invalid_quality.tracking_quality = float("nan")
        invalid_decision = self.personalization.match(
            self.profile.user_id, invalid_quality, "NONE", 0.0, self.profile
        )
        self.assertFalse(invalid_decision.used)

    def test_calibration_requires_explicit_stable_samples_and_personalized_mapping_reaches_matcher(self):
        result = self.personalization.start_calibration(self.profile.user_id, "custom:wave", required_samples=3)
        self.assertTrue(result["success"])
        session_id = result["calibration"]["session_id"]
        unstable = self.observation(motion=MotionFeatures(sample_count=1, stability=1.0))
        rejected = self.personalization.capture_sample(self.profile.user_id, session_id, unstable)
        self.assertFalse(rejected["accepted"])
        self.assertEqual(rejected["calibration"]["rejected_samples"], 1)
        stable = self.observation()
        for _ in range(3):
            accepted = self.personalization.capture_sample(self.profile.user_id, session_id, stable)
            self.assertTrue(accepted["accepted"])
        completed = self.personalization.complete_calibration(self.profile.user_id, session_id, "Wave")
        self.assertTrue(completed["success"])
        learned_id = completed["learned_gesture"]["id"]
        mapping = self.personalization.create_mapping(self.profile.user_id, learned_id, "click")
        self.assertTrue(mapping["success"])
        decision = self.personalization.match(self.profile.user_id, stable, "NONE", 0.0, self.profile)
        self.assertTrue(decision.used)
        self.assertEqual(decision.mapping_action, "click")
        reliable_base = self.personalization.match(self.profile.user_id, stable, "ONE_FINGER", 0.95, self.profile)
        self.assertFalse(reliable_base.used)
        self.assertIn("Reliable base", reliable_base.reason)

    def test_corrections_are_explicit_and_unavailable_mongodb_fails_open(self):
        stable = self.observation(gesture="NONE", handedness="left")
        not_validated = self.personalization.record_correction(
            self.profile.user_id, "B", stable, validated=False
        )
        self.assertFalse(not_validated["success"])
        validated = self.personalization.record_correction(
            self.profile.user_id, "B", stable, validated=True
        )
        self.assertTrue(validated["success"])
        self.assertEqual(len(self.personalization.list_corrections(self.profile.user_id)), 1)

        unavailable = MockMongoDatabase(available=False)
        unavailable_profile_service = UserProfileService(UserProfileRepository(unavailable))
        profile = unavailable_profile_service.get_profile("offline")
        self.assertEqual(profile.user_id, "offline")
        offline_repos = make_repos(unavailable)
        offline_learning = PersonalizationService(*offline_repos)
        started = offline_learning.start_calibration("offline", "custom:test")
        self.assertTrue(started["success"])
        self.assertFalse(started["calibration"]["storage_available"])
        decision = offline_learning.match("offline", stable, "NONE", 0.0, profile)
        self.assertFalse(decision.used)


if __name__ == "__main__":
    unittest.main()
