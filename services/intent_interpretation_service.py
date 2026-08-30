"""Context-aware intent interpretation, separate from legacy command execution."""

from typing import Dict, Optional

from config import Config
from core.adaptive.observation import GestureObservation, IntentResult, UnknownGestureResult


class ContextAwareIntentInterpreter:
    """Translate observations into explainable intent candidates.

    The existing GestureEngine remains the command executor.  This service adds
    a contextual decision layer and can therefore evolve independently toward a
    learned policy without changing cursor, click, scroll, or sign routes.
    """

    def interpret(
        self,
        right_observation: Optional[GestureObservation],
        left_observation: Optional[GestureObservation],
        unknown: UnknownGestureResult,
        context: Optional[Dict[str, object]],
        profile,
    ) -> IntentResult:
        context = context or {}
        active_module = str(context.get("active_module", "overview"))
        gesture_enabled = bool(context.get("gesture_enabled", False))
        sign_prediction = str(context.get("sign_prediction", "NONE") or "NONE").upper()
        sign_confidence = float(context.get("sign_confidence", 0.0) or 0.0)
        left_present = bool(context.get("left_hand_detected", left_observation is not None))
        sentence_active = bool(context.get("sentence_active", False))
        recent_intents = list(context.get("recent_intents", []))[:8]
        # recent_intents is ordered newest-first by the history service.
        last_intent = recent_intents[0] if recent_intents else "IDLE"

        context_used = {
            "active_module": active_module,
            "gesture_enabled": gesture_enabled,
            "sentence_active": sentence_active,
            "sign_prediction": sign_prediction,
            "sign_confidence": round(sign_confidence, 1),
            "last_intent": last_intent,
            "recent_intents": recent_intents,
            "profile_mode": getattr(profile, "interaction_mode", "adaptive"),
        }

        if unknown.is_unknown:
            return IntentResult(
                name="UNKNOWN_GESTURE",
                confidence=unknown.score,
                actionable=False,
                source="gesture+temporal+confidence",
                reason=unknown.reason,
                candidates=("UNKNOWN_GESTURE", "WAIT_FOR_STABLE_TRACK"),
                context_used=context_used,
            )

        if unknown.status in {"TRANSITIONING", "LOW_TRACKING_QUALITY"}:
            return IntentResult(
                name="hand.transition",
                confidence=0.45,
                actionable=False,
                source="landmarks+temporal",
                reason=unknown.reason,
                candidates=("hand.transition", "UNKNOWN_GESTURE"),
                context_used=context_used,
            )

        # No hand is an intentional idle state in both adaptive and legacy
        # modes; it should not appear as a legacy action in the UI/history.
        if right_observation is None and left_observation is None:
            return IntentResult(context_used=context_used)

        adaptive_enabled = bool(getattr(profile, "adaptive_enabled", True)) and (
            str(getattr(profile, "interaction_mode", "adaptive")).lower() == "adaptive"
        )
        if not adaptive_enabled:
            return IntentResult(
                name="LEGACY_ROUTING",
                confidence=1.0,
                actionable=False,
                source="legacy-compatibility",
                reason="Adaptive interpretation is disabled; existing gesture routing remains authoritative.",
                candidates=("LEGACY_ROUTING",),
                context_used=context_used,
            )

        # A recognized left-hand sign is an observation until the right-hand
        # confirmation gesture arrives.  The sentence context makes the same
        # pose useful for the Studio and recognition workflows.
        # Personalized matching is an input to the adaptive intent engine, not
        # a parallel command path. The matcher has already enforced evidence and
        # reliable-base safety gates.
        personalization = context.get("personalization") or {}
        if hasattr(personalization, "to_dict"):
            personalization = personalization.to_dict()
        if isinstance(personalization, dict):
            context_used["personalization_source"] = personalization.get("source", "BASE_MODEL")
            context_used["personalization_confidence"] = round(float(personalization.get("confidence", 0.0) or 0.0), 3)
            if personalization.get("used") and personalization.get("mapping_action"):
                action_names = {
                    "back": "navigation.back",
                    "scroll_up": "scroll.up",
                    "scroll_down": "scroll.down",
                    "click": "selection.click",
                }
                action = str(personalization.get("mapping_action")).lower()
                intent_name = action_names.get(action)
                if intent_name:
                    return IntentResult(
                        name=intent_name,
                        confidence=float(personalization.get("confidence", 0.0) or 0.0),
                        actionable=gesture_enabled,
                        source="user-learned-mapping",
                        reason="An explicitly mapped personal gesture was matched without replacing a reliable base prediction.",
                        candidates=(intent_name, "LEGACY_ROUTING"),
                        context_used=context_used,
                    )

        sign_threshold = float(Config.RECOGNITION_CONFIDENCE_THRESHOLD) * 100.0
        personalized_sign = (
            isinstance(personalization, dict)
            and personalization.get("used")
            and not personalization.get("mapping_action")
            and len(str(personalization.get("personalized_label", "") or "")) == 1
            and "A" <= str(personalization.get("personalized_label", "") or "").upper() <= "Z"
            and float(personalization.get("confidence", 0.0) or 0.0)
            >= float(Config.PERSONALIZATION_MATCH_MIN_CONFIDENCE)
        )
        known_sign = left_present and (
            (sign_prediction not in {"NONE", "UNKNOWN"} and sign_confidence >= sign_threshold)
            or personalized_sign
        )
        if known_sign and right_observation and right_observation.gesture == "CLOSED_FIST":
            return IntentResult(
                name="sign.commit",
                confidence=min(0.99, 0.86 + (sign_confidence / 1000.0)),
                actionable=gesture_enabled,
                source="sign+gesture+context+history",
                reason="A supported sign is followed by the user's confirmation pose.",
                candidates=("sign.commit", "sign.observe"),
                context_used=context_used,
            )

        if known_sign and active_module in {"studio", "recognition"}:
            confidence = min(0.98, 0.70 + (sign_confidence / 333.0))
            if last_intent == "sign.observe":
                confidence = min(0.99, confidence + 0.03)
            return IntentResult(
                name="sign.observe",
                confidence=confidence,
                actionable=False,
                source="sign-model+context+history",
                reason="The left-hand alphabet prediction is stable in a sign-aware module.",
                candidates=("sign.observe", "sign.commit"),
                context_used=context_used,
            )

        if right_observation is None:
            if left_observation is not None:
                return IntentResult(
                    name="hand.observe",
                    confidence=0.55,
                    actionable=False,
                    source="landmarks+temporal",
                    reason="A hand is tracked while the supported sign/command is still resolving.",
                    candidates=("hand.observe", "sign.observe"),
                    context_used=context_used,
                )
            return IntentResult(context_used=context_used)

        gesture = right_observation.gesture
        motion = right_observation.motion

        # Vertical motion becomes a scroll intent only when the page/module
        # context suggests navigation.  In a sentence workflow, a one-finger
        # pose remains cursor-oriented even if the hand is moving vertically.
        scroll_context = active_module in {"overview", "mouse", "alphabet", "drawing"} and not sentence_active
        if (
            scroll_context
            and motion.direction in {"up", "down"}
            and motion.displacement >= Config.SCROLL_DISPLACEMENT_THRESHOLD * 0.70
            and motion.sample_count >= 3
        ):
            content_direction = "down" if motion.direction == "up" else "up"
            return IntentResult(
                name="scroll." + content_direction,
                confidence=min(0.97, 0.76 + min(0.18, motion.displacement)),
                actionable=gesture_enabled,
                source="gesture+temporal+context+history",
                reason="A deliberate vertical trajectory is more consistent with page navigation than a static pose.",
                candidates=("scroll." + content_direction, "cursor.move"),
                context_used=context_used,
            )

        if gesture == "ONE_FINGER":
            return IntentResult(
                name="cursor.move",
                confidence=0.95 if last_intent == "cursor.move" else 0.91,
                actionable=gesture_enabled,
                source="gesture+temporal+context+history",
                reason="The index-led pose is stable and the current context permits cursor control.",
                candidates=("cursor.move", "scroll.up", "scroll.down"),
                context_used=context_used,
            )

        if gesture == "TWO_FINGER":
            name = "selection.continue" if last_intent == "selection.dwell" else "selection.dwell"
            return IntentResult(
                name=name,
                confidence=0.94,
                actionable=gesture_enabled,
                source="gesture+temporal+context+history",
                reason="The two-finger pose is interpreted as a dwell selection and its continuation is tracked temporally.",
                candidates=("selection.dwell", "selection.click"),
                context_used=context_used,
            )

        if gesture == "CLOSED_FIST":
            return IntentResult(
                name="interaction.pause",
                confidence=0.86,
                actionable=False,
                source="gesture+context+history",
                reason="A closed fist has no sign to commit in the current context, so it is treated as a safe pause.",
                candidates=("interaction.pause", "sign.commit"),
                context_used=context_used,
            )

        if gesture in {"THREE_FINGER", "FOUR_FINGER", "FIVE_FINGER"}:
            return IntentResult(
                name="gesture.observe",
                confidence=0.78,
                actionable=False,
                source="gesture+temporal+context",
                reason="The pose is known to the geometric classifier but has no command binding in the current system.",
                candidates=("gesture.observe", "UNKNOWN_GESTURE"),
                context_used=context_used,
            )

        return IntentResult(
            name="UNKNOWN_GESTURE",
            confidence=0.65,
            actionable=False,
            source="gesture+temporal+context",
            reason="No supported intent could be resolved from the current hand observation.",
            candidates=("UNKNOWN_GESTURE", "WAIT_FOR_STABLE_TRACK"),
            context_used=context_used,
        )


intent_interpreter = ContextAwareIntentInterpreter()
