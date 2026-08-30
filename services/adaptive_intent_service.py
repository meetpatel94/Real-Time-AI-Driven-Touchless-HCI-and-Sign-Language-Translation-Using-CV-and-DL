"""Orchestrator joining profile, temporal observations, unknown detection and intent."""

from datetime import datetime, timezone
from typing import Dict, Optional

from core.adaptive.observation import GestureObservation
from models.user_profile import InteractionEvent
from services.adaptive_runtime_service import adaptive_runtime_service
from services.interaction_history_service import interaction_history_service
from services.intent_interpretation_service import intent_interpreter
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
        sign_snapshot = sign_snapshot or {}
        context["recent_intents"] = interaction_history_service.recent_intents(profile.user_id, limit=8)

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
            "snapshot": snapshot,
        }

    def reset(self) -> Dict[str, object]:
        unknown_gesture_service.reset()
        profile = user_profile_service.get_active_profile()
        return {"snapshot": adaptive_runtime_service.clear(profile)}


adaptive_intent_service = AdaptiveIntentService()
