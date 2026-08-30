"""Orchestrator joining profile, temporal observations, unknown detection and intent."""

from datetime import datetime, timezone
from typing import Dict, Optional

from core.adaptive.observation import GestureObservation
from models.user_profile import InteractionEvent
from services.adaptive_runtime_service import adaptive_runtime_service
from services.interaction_history_service import interaction_history_service
from services.intent_interpretation_service import intent_interpreter
from services.personalization_service import personalization_service
from services.unknown_gesture_service import unknown_gesture_service
from services.user_profile_service import user_profile_service


class AdaptiveIntentService:
    """Single integration point called by the existing MediaPipe loop."""

    def process_frame(
        self,
        right_observation: Optional[GestureObservation],
        left_observation: Optional[GestureObservation],
        context: Optional[Dict[str, object]] = None,
        profile=None,
        sign_snapshot: Optional[Dict[str, object]] = None,
    ) -> Dict[str, object]:
        context = dict(context or {})
        profile = profile or user_profile_service.get_active_profile()
        sign_snapshot = dict(sign_snapshot or {})
        context.setdefault(
            "sign_prediction",
            sign_snapshot.get("prediction", sign_snapshot.get("raw_prediction", "NONE")),
        )
        context.setdefault(
            "sign_confidence",
            sign_snapshot.get("confidence", sign_snapshot.get("raw_confidence", 0.0)),
        )
        context["recent_intents"] = (
            interaction_history_service.recent_intents(profile.user_id, limit=8)
            if getattr(profile, "learning_enabled", True) else []
        )

        # Feed the same observation built by GestureEngine into personalization;
        # this is an in-memory latest sample only until the user explicitly asks
        # for calibration/correction. No automatic learning occurs here.
        observation = right_observation or left_observation
        if right_observation is not None:
            base_label = right_observation.gesture
            base_confidence = 1.0 if right_observation.gesture != "NONE" else 0.0
        else:
            base_label = str(sign_snapshot.get("prediction", sign_snapshot.get("raw_prediction", "NONE")))
            base_confidence = float(sign_snapshot.get("confidence", sign_snapshot.get("raw_confidence", 0.0)) or 0.0)
        sign_label = str(sign_snapshot.get("prediction", sign_snapshot.get("raw_prediction", "NONE")))
        sign_confidence = float(sign_snapshot.get("confidence", sign_snapshot.get("raw_confidence", 0.0)) or 0.0)
        personalization_service.register_observations(
            profile.user_id,
            [
                (right_observation, base_label, base_confidence),
                (left_observation, sign_label, sign_confidence),
            ],
        )
        personalization_observation = observation
        personalization = personalization_service.match(
            profile.user_id,
            observation,
            base_label=base_label,
            base_confidence=base_confidence,
            profile=profile,
        )
        # A left-hand sign can be personalized while the right hand is held in
        # the existing confirmation/control pose. Keep right-hand mappings
        # authoritative when they already match; otherwise evaluate the left
        # sign observation as the adaptive fallback.
        if not personalization.used and left_observation is not None:
            left_personalization = personalization_service.match(
                profile.user_id,
                left_observation,
                base_label=sign_label,
                base_confidence=sign_confidence,
                profile=profile,
            )
            if left_personalization.used and context.get("active_module") in {"recognition", "studio"}:
                personalization = left_personalization
                personalization_observation = left_observation
        context["personalization"] = personalization.to_dict()
        context["personalization_calibration"] = personalization_service.active_calibration_status(profile.user_id)

        # A learned sign is allowed to fill a low-confidence base result, but
        # only the adaptive intent interpreter consumes it. Reliable base sign
        # predictions were already protected by the matcher.
        if (
            personalization.used
            and personalization.personalized_label
            and personalization.mapping_action is None
            and personalization_observation is left_observation
        ):
            context["sign_prediction"] = personalization.personalized_label
            context["sign_confidence"] = personalization.confidence * 100.0
            sign_snapshot["raw_prediction"] = personalization.personalized_label
            sign_snapshot["prediction"] = personalization.personalized_label
            sign_snapshot["raw_confidence"] = personalization.confidence * 100.0
            sign_snapshot["confidence"] = personalization.confidence * 100.0

        unknown = unknown_gesture_service.evaluate(
            right_observation,
            left_observation,
            sign_snapshot,
            profile,
            context,
        )
        intent = intent_interpreter.interpret(
            right_observation,
            left_observation,
            unknown,
            context,
            profile,
        )
        snapshot = adaptive_runtime_service.publish(
            profile,
            right_observation,
            left_observation,
            unknown,
            intent,
            context,
        )

        if profile.learning_enabled:
            observation = right_observation or left_observation
            if observation is not None or unknown.is_unknown:
                event = InteractionEvent(
                    user_id=profile.user_id,
                    occurred_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    module=str(context.get("active_module", "overview")),
                    hand=observation.handedness if observation else unknown.hand,
                    gesture=observation.gesture if observation else "NONE",
                    finger_count=observation.finger_count if observation else 0,
                    motion_direction=observation.motion.direction if observation else "stationary",
                    motion_speed=observation.motion.speed if observation else 0.0,
                    displacement=observation.motion.displacement if observation else 0.0,
                    tracking_quality=observation.tracking_quality if observation else 0.0,
                    is_unknown=unknown.is_unknown,
                    unknown_status=unknown.status,
                    unknown_reason=unknown.reason,
                    intent=intent.name,
                    intent_confidence=intent.confidence,
                    action_taken=intent.actionable,
                    temporal_features=observation.motion.to_dict() if observation else {},
                    context_snapshot=intent.context_used,
                )
                interaction_history_service.record_transition(event)

        return {
            "unknown": unknown,
            "intent": intent,
            "personalization": personalization,
            "snapshot": snapshot,
        }

    def reset(self) -> Dict[str, object]:
        unknown_gesture_service.reset()
        profile = user_profile_service.get_active_profile()
        # A camera-off/profile reset invalidates the in-process latest pose so
        # explicit calibration/correction APIs cannot accept stale camera data.
        personalization_service.clear_latest_observations(profile.user_id)
        return {"snapshot": adaptive_runtime_service.clear(profile)}


adaptive_intent_service = AdaptiveIntentService()
