from flask import Blueprint, render_template, jsonify, request
from core.recognition.recognition_state import recognition_state
from services.translation_service import translation_service
from services.word_suggestion_service import word_suggestion_service
from services.state_service import global_state

studio_bp = Blueprint("studio", __name__)

@studio_bp.route("/sign-language-studio")
def sign_language_studio():
    global_state.update_state({"active_module": "studio"})
    return render_template("studio/index.html")

@studio_bp.route("/api/studio/status", methods=["GET"])
def get_studio_status():
    return jsonify(recognition_state.get_snapshot())

@studio_bp.route("/api/studio/suggestions", methods=["GET"])
def get_word_suggestions():
    prefix = request.args.get("prefix", "").strip()
    suggestions = word_suggestion_service.get_suggestions(prefix, max_results=5)
    return jsonify({
        "success": True,
        "prefix": prefix,
        "suggestions": suggestions
    })

@studio_bp.route("/api/studio/translate", methods=["POST"])
def translate():
    data = request.get_json() or {}
    text = data.get("text", "")
    target_lang = data.get("target_lang", "English")

    if not text.strip():
        return jsonify({
            "success": True,
            "translated_text": "",
            "target_lang": target_lang
        })

    translated = translation_service.translate_text(text, target_lang)
    return jsonify({
        "success": True,
        "original_text": text,
        "translated_text": translated,
        "target_lang": target_lang
    })

@studio_bp.route("/api/studio/sentence/action", methods=["POST"])
def manage_sentence():
    data = request.get_json() or {}
    action = data.get("action")
    text = data.get("text", "")

    if action == "clear":
        recognition_state.clear_sentence()
    elif action == "backspace":
        recognition_state.backspace_sentence()
    elif action == "set":
        recognition_state.set_sentence(text)

    return jsonify({"success": True, "sentence": recognition_state.get_snapshot()["sentence"]})