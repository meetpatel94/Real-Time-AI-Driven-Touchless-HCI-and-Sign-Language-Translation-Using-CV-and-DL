"""HTTP boundary for profiles and adaptive reasoning telemetry."""

from flask import Blueprint, jsonify, request

from core.gestures.gesture_engine import gesture_engine
from core.mouse.scroll_controller import scroll_controller
from services.adaptive_intent_service import adaptive_intent_service
from services.adaptive_runtime_service import adaptive_runtime_service
from services.interaction_history_service import interaction_history_service
from services.personalization_service import personalization_service
from services.user_profile_service import user_profile_service


adaptive_bp = Blueprint("adaptive", __name__)


def _requested_profile_id():
    return request.headers.get("X-GestureForge-Profile") or request.args.get("profile_id")


def _activate_profile():
    previous_profile_id = user_profile_service.active_profile_id
    profile = user_profile_service.activate(_requested_profile_id())
    if previous_profile_id != profile.user_id:
        # A profile switch must not inherit another user's temporal candidate,
        # latest pose, or adaptive runtime snapshot.
        gesture_engine.reset_adaptive_state()
    # Load user-owned matching data on the request path when the active profile
    # changes, never from the camera frame loop.
    personalization_service.preload_learning_data(profile.user_id)
    interaction_history_service.preload_user(profile.user_id)
    return profile


def _apply_runtime_preferences(profile):
    """Apply saved preferences through existing controllers, not duplicate logic."""
    gesture_engine.mapper.set_sensitivity(profile.cursor_sensitivity)
    scroll_controller.set_sensitivity(profile.scroll_sensitivity)


def _profile_response(profile):
    return {"success": True, "profile": profile.to_dict()}


@adaptive_bp.route("/api/profile", methods=["GET"])
def get_profile():
    profile = _activate_profile()
    _apply_runtime_preferences(profile)
    return jsonify(_profile_response(profile))


@adaptive_bp.route("/api/profile", methods=["PATCH", "PUT"])
def update_profile():
    previous_profile_id = user_profile_service.active_profile_id
    profile_id = _requested_profile_id()
    changes = request.get_json(silent=True)
    if changes is None:
        changes = {}
    if not isinstance(changes, dict):
        return jsonify({"success": False, "error": "Profile payload must be a JSON object."}), 400

    profile = user_profile_service.update_profile(profile_id, changes)
    if previous_profile_id != profile.user_id:
        gesture_engine.reset_adaptive_state()
    else:
        adaptive_intent_service.reset()
    _apply_runtime_preferences(profile)
    personalization_service.preload_learning_data(profile.user_id)
    interaction_history_service.preload_user(profile.user_id)
    return jsonify(_profile_response(profile))


@adaptive_bp.route("/api/profile/reset", methods=["POST"])
def reset_profile():
    previous_profile_id = user_profile_service.active_profile_id
    profile = user_profile_service.reset_profile(_requested_profile_id())
    if previous_profile_id != profile.user_id:
        gesture_engine.reset_adaptive_state()
    else:
        adaptive_intent_service.reset()
    _apply_runtime_preferences(profile)
    personalization_service.preload_learning_data(profile.user_id)
    interaction_history_service.preload_user(profile.user_id)
    return jsonify(_profile_response(profile))


@adaptive_bp.route("/api/adaptive/status", methods=["GET"])
def get_adaptive_status():
    profile = _activate_profile()
    runtime = adaptive_runtime_service.get_snapshot(profile.user_id)
    # A camera-off page may not have published a frame yet; still return the
    # current persisted profile rather than the runtime fallback label.
    runtime["profile_id"] = profile.user_id
    runtime["profile_name"] = profile.display_name
    runtime["adaptive_enabled"] = bool(profile.adaptive_enabled)
    runtime["interaction_mode"] = profile.interaction_mode
    return jsonify({
        "success": True,
        "profile": profile.to_dict(),
        "runtime": runtime,
        "personalization_storage": personalization_service.storage_status(),
    })


@adaptive_bp.route("/api/adaptive/events", methods=["GET"])
def get_adaptive_events():
    profile = _activate_profile()
    try:
        limit = int(request.args.get("limit", 20))
    except (TypeError, ValueError):
        limit = 20
    events = interaction_history_service.recent_events(profile.user_id, limit=limit)
    return jsonify({
        "success": True,
        "profile_id": profile.user_id,
        "events": [event.to_dict() for event in events],
        "storage": personalization_service.storage_status(),
    })


@adaptive_bp.route("/api/adaptive/feedback", methods=["POST"])
def record_adaptive_feedback():
    profile = _activate_profile()
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        payload = {}
    event_id = payload.get("event_id")
    if event_id is None:
        event_id = interaction_history_service.latest_event_id(profile.user_id)
    if event_id is None:
        return jsonify({"success": False, "error": "event_id is required."}), 400
    event_id = str(event_id).strip()
    if not event_id:
        return jsonify({"success": False, "error": "event_id is required."}), 400

    feedback = payload.get("feedback")
    if not interaction_history_service.set_feedback(profile.user_id, event_id, feedback):
        storage = personalization_service.storage_status()
        status = 503 if not storage.get("available") else 404
        error = "MongoDB is unavailable." if status == 503 else "Unknown event or invalid feedback."
        return jsonify({"success": False, "error": error}), status
    return jsonify({"success": True, "event_id": event_id, "feedback": str(feedback or "").strip().lower()})
