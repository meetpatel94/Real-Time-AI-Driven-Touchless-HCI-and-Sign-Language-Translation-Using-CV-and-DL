"""Routes for the isolated Custom Gesture Library."""

from flask import Blueprint, jsonify, render_template, request

from core.custom_gestures.service import custom_gesture_service, sanitize_gesture_name
from services.state_service import global_state


custom_gesture_bp = Blueprint("custom_gestures", __name__)


def _payload():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _json_result(result, success_status=200):
    status = success_status if result.get("success") else 400
    if not result.get("success") and "not found" in str(result.get("error", "")).lower():
        status = 404
    return jsonify(result), status


@custom_gesture_bp.route("/custom-gestures")
def custom_gestures_page():
    global_state.update_state({"active_module": "custom_gestures"})
    return render_template("custom_gestures/index.html")


@custom_gesture_bp.route("/api/custom-gestures", methods=["GET"])
def list_custom_gestures():
    return jsonify({"success": True, "gestures": custom_gesture_service.list_gestures()})


@custom_gesture_bp.route("/api/custom-gestures/<gesture_id>", methods=["GET"])
def get_custom_gesture(gesture_id):
    try:
        gesture = custom_gesture_service.get_gesture(gesture_id, include_samples=True)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    if gesture is None:
        return jsonify({"success": False, "error": "Custom gesture not found."}), 404
    return jsonify({"success": True, "gesture": gesture})


@custom_gesture_bp.route("/api/custom-gestures/capture/start", methods=["POST"])
def start_custom_gesture_capture():
    payload = _payload()
    name = payload.get("gesture_name", payload.get("name", ""))
    target = payload.get("target_samples", payload.get("number_of_samples", payload.get("samples", None)))
    try:
        result = custom_gesture_service.start_capture(
            gesture_name=name,
            description=payload.get("description", ""),
            hand=payload.get("hand", "either"),
            target_samples=target,
            replace=payload.get("replace") is True,
            candidate_id=payload.get("candidate_id") or None,
        )
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    return _json_result(result, success_status=201)


@custom_gesture_bp.route("/api/custom-gestures/capture/stop", methods=["POST"])
def stop_custom_gesture_capture():
    return jsonify(custom_gesture_service.stop_capture())


@custom_gesture_bp.route("/api/custom-gestures/capture/status", methods=["GET"])
def custom_gesture_capture_status():
    return jsonify({"success": True, "runtime": custom_gesture_service.get_runtime_status()})


@custom_gesture_bp.route("/api/custom-gestures/live/start", methods=["POST"])
def start_custom_gesture_live():
    return _json_result(custom_gesture_service.start_live_recognition())


@custom_gesture_bp.route("/api/custom-gestures/test/start", methods=["POST"])
def start_custom_gesture_test():
    payload = _payload()
    gesture_id = payload.get("gesture_id", payload.get("id", ""))
    try:
        result = custom_gesture_service.start_test(gesture_id)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    return _json_result(result)


@custom_gesture_bp.route("/api/custom-gestures/recognition/status", methods=["GET"])
def custom_gesture_recognition_status():
    return jsonify({"success": True, "runtime": custom_gesture_service.get_runtime_status()})


@custom_gesture_bp.route("/api/custom-gestures/recognition/stop", methods=["POST"])
def stop_custom_gesture_recognition():
    return jsonify(custom_gesture_service.stop_recognition())


@custom_gesture_bp.route("/api/custom-gestures/<gesture_id>", methods=["PATCH", "PUT"])
def update_custom_gesture(gesture_id):
    try:
        result = custom_gesture_service.update_gesture(gesture_id, _payload())
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    return _json_result(result)


@custom_gesture_bp.route("/api/custom-gestures/<gesture_id>/toggle", methods=["POST"])
def toggle_custom_gesture(gesture_id):
    payload = _payload()
    enabled = payload.get("enabled") if "enabled" in payload else None
    try:
        result = custom_gesture_service.toggle_gesture(gesture_id, enabled=enabled)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    return _json_result(result)


@custom_gesture_bp.route("/api/custom-gestures/<gesture_id>", methods=["DELETE"])
def delete_custom_gesture(gesture_id):
    try:
        sanitize_gesture_name(gesture_id)
        result = custom_gesture_service.delete_gesture(gesture_id)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    return _json_result(result)


# ----------------------------------------------------------------------
# Self-learning (unknown discovery, mistake memory, gesture evolution).
# ----------------------------------------------------------------------
@custom_gesture_bp.route("/api/custom-gestures/learning/status", methods=["GET"])
def custom_gesture_learning_status():
    return jsonify(custom_gesture_service.learning_status())


@custom_gesture_bp.route("/api/custom-gestures/learning/candidates/<candidate_id>/learn", methods=["POST"])
def learn_custom_gesture_candidate(candidate_id):
    result = custom_gesture_service.prepare_candidate_learning(candidate_id)
    if not result.get("success"):
        status = 404 if "no longer" in str(result.get("error", "")).lower() else 400
        return jsonify(result), status
    return jsonify(result)


@custom_gesture_bp.route("/api/custom-gestures/learning/candidates/<candidate_id>/ignore", methods=["POST"])
def ignore_custom_gesture_candidate(candidate_id):
    result = custom_gesture_service.ignore_candidate(candidate_id)
    if not result.get("success"):
        status = 404 if "no longer" in str(result.get("error", "")).lower() else 400
        return jsonify(result), status
    return jsonify(result)


@custom_gesture_bp.route("/api/custom-gestures/corrections", methods=["POST"])
def record_custom_gesture_correction():
    payload = _payload()
    predicted = payload.get("predicted_gesture_id", payload.get("predicted", ""))
    correct = payload.get("correct_gesture_id", payload.get("correct", ""))
    if not predicted or not correct:
        return jsonify({"success": False, "error": "Both predicted and corrected gestures are required."}), 400
    try:
        result = custom_gesture_service.record_correction(predicted, correct)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    return _json_result(result, success_status=201)


@custom_gesture_bp.route("/api/custom-gestures/corrections", methods=["GET"])
def list_custom_gesture_corrections():
    limit = request.args.get("limit", 20, type=int)
    return jsonify(custom_gesture_service.list_corrections(limit=limit))


@custom_gesture_bp.route("/api/custom-gestures/<gesture_id>/evolution", methods=["GET"])
def custom_gesture_evolution(gesture_id):
    try:
        result = custom_gesture_service.evolution_details(gesture_id)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    if not result.get("success"):
        status = 404 if "not found" in str(result.get("error", "")).lower() else 400
        return jsonify(result), status
    return jsonify(result)


@custom_gesture_bp.route("/api/custom-gestures/<gesture_id>/variations/accept", methods=["POST"])
def accept_custom_gesture_variations(gesture_id):
    try:
        result = custom_gesture_service.accept_pending_variations(gesture_id)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    return _json_result(result)


@custom_gesture_bp.route("/api/custom-gestures/<gesture_id>/variations/ignore", methods=["POST"])
def ignore_custom_gesture_variations(gesture_id):
    try:
        result = custom_gesture_service.discard_pending_variations(gesture_id)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    return _json_result(result)


# ----------------------------------------------------------------------
# Feature 1: Gesture DNA
# ----------------------------------------------------------------------
@custom_gesture_bp.route("/api/custom-gestures/<gesture_id>/dna", methods=["GET"])
def custom_gesture_dna(gesture_id):
    try:
        result = custom_gesture_service.gesture_dna(gesture_id)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    if not result.get("success"):
        status = 404 if "not found" in str(result.get("error", "")).lower() else 400
        return jsonify(result), status
    return jsonify(result)


@custom_gesture_bp.route("/api/custom-gestures/<gesture_id>/dna/compare", methods=["GET"])
def custom_gesture_dna_compare(gesture_id):
    try:
        result = custom_gesture_service.current_dna_and_comparison(gesture_id)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    if not result.get("success"):
        status = 404 if "not found" in str(result.get("error", "")).lower() else 400
        return jsonify(result), status
    return jsonify(result)


# ----------------------------------------------------------------------
# Feature 2: AI Gesture Coach
# ----------------------------------------------------------------------
@custom_gesture_bp.route("/api/custom-gestures/<gesture_id>/coach", methods=["GET"])
def custom_gesture_coach(gesture_id):
    try:
        result = custom_gesture_service.coach_feedback(gesture_id)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    if not result.get("success"):
        status = 404 if "not found" in str(result.get("error", "")).lower() else 400
        return jsonify(result), status
    return jsonify(result)


@custom_gesture_bp.route("/api/custom-gestures/capture/coach", methods=["GET"])
def custom_gesture_capture_coach():
    return jsonify(custom_gesture_service.capture_coach_feedback())


# ----------------------------------------------------------------------
# Feature 3: Gesture Quality & Analytics
# ----------------------------------------------------------------------
@custom_gesture_bp.route("/api/custom-gestures/<gesture_id>/analytics", methods=["GET"])
def custom_gesture_analytics(gesture_id):
    try:
        result = custom_gesture_service.gesture_analytics(gesture_id)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    if not result.get("success"):
        status = 404 if "not found" in str(result.get("error", "")).lower() else 400
        return jsonify(result), status
    return jsonify(result)


@custom_gesture_bp.route("/api/custom-gestures/<gesture_id>/details", methods=["GET"])
def custom_gesture_full_details(gesture_id):
    """Combined DNA + Coach + Analytics endpoint for the gesture details UI."""
    try:
        result = custom_gesture_service.gesture_full_details(gesture_id)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    if not result.get("success"):
        status = 404 if "not found" in str(result.get("error", "")).lower() else 400
        return jsonify(result), status
    return jsonify(result)
