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
        # Force route ids through the same sanitizer used by folders so path
        # traversal attempts never reach shutil.rmtree.
        sanitize_gesture_name(gesture_id)
        result = custom_gesture_service.delete_gesture(gesture_id)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    return _json_result(result)
