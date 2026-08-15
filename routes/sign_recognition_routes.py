from flask import Blueprint, render_template, jsonify, request
from core.recognition.recognition_state import recognition_state
from services.state_service import global_state

recognition_bp = Blueprint("recognition", __name__)

@recognition_bp.route("/sign-recognition")
@recognition_bp.route("/recognition")
def recognition():
    global_state.update_state({"active_module": "recognition"})
    return render_template("recognition/index.html")

@recognition_bp.route("/api/recognition/status", methods=["GET"])
@recognition_bp.route("/sign-recognition/status", methods=["GET"])
def get_status():
    return jsonify(recognition_state.get_snapshot())

@recognition_bp.route("/api/recognition/prediction", methods=["GET"])
@recognition_bp.route("/sign-recognition/prediction", methods=["GET"])
def get_prediction():
    return jsonify(recognition_state.get_snapshot())

@recognition_bp.route("/api/recognition/mark-test", methods=["POST"])
@recognition_bp.route("/sign-recognition/test", methods=["POST"])
def mark_test():
    data = request.get_json() or {}
    expected = data.get("expected", "A")
    result = recognition_state.mark_test(expected)
    return jsonify(result)

@recognition_bp.route("/api/recognition/reset-tests", methods=["POST"])
@recognition_bp.route("/sign-recognition/reset-tests", methods=["POST"])
def reset_tests():
    stats = recognition_state.reset_tests()
    return jsonify({"success": True, "stats": stats})