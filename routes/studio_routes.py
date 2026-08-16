from flask import Blueprint, render_template, jsonify, request
from core.recognition.recognition_state import recognition_state
from services.state_service import global_state

studio_bp = Blueprint("studio", __name__)

@studio_bp.route("/sign-language-studio")
def sign_language_studio():
    global_state.update_state({"active_module": "studio"})
    return render_template("studio/index.html")

@studio_bp.route("/api/studio/status", methods=["GET"])
def get_studio_status():
    """Lightweight polling endpoint delivering dual-hand state and built sentence."""
    return jsonify(recognition_state.get_snapshot())

@studio_bp.route("/api/studio/sentence/action", methods=["POST"])
def manage_sentence():
    """Manual UI fallbacks for clear/backspace."""
    data = request.get_json() or {}
    action = data.get("action")
    
    if action == "clear":
        recognition_state.clear_sentence()
    elif action == "backspace":
        recognition_state.backspace_sentence()
        
    return jsonify({"success": True, "sentence": recognition_state.get_snapshot()["sentence"]})