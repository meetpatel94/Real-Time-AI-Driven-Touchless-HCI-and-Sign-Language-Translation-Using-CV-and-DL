import os
import json
from flask import Blueprint, render_template, jsonify, request, send_file, url_for
from core.sign_language.dataset_collector import dataset_collector
from core.sign_alphabet.dataset_manager import dataset_manager
from core.sign_alphabet.training_state import training_state
from core.sign_alphabet.trainer import trainer
from services.state_service import global_state
from config import Config

alphabet_bp = Blueprint("alphabet", __name__)

@alphabet_bp.route("/alphabet")
@alphabet_bp.route("/sign-alphabet")
def alphabet():
    global_state.update_state({"active_module": "alphabet"})
    return render_template("alphabet/sign_alphabet.html")

# --- Dataset APIs ---
@alphabet_bp.route("/sign-alphabet/dataset-info", methods=["GET"])
@alphabet_bp.route("/api/alphabet/dataset-info", methods=["GET"])
def get_dataset_info():
    overview = dataset_manager.get_dataset_overview()
    return jsonify(overview)

@alphabet_bp.route("/sign-alphabet/class-preview/<letter>", methods=["GET"])
@alphabet_bp.route("/api/alphabet/class-preview/<letter>", methods=["GET"])
def get_class_preview(letter):
    sample_path = dataset_manager.get_class_preview(letter.upper())
    if sample_path and os.path.isfile(sample_path):
        return send_file(sample_path, mimetype="image/jpeg")
    return jsonify({"error": "Preview not found"}), 404

# --- Training APIs ---
@alphabet_bp.route("/sign-alphabet/start-training", methods=["POST"])
@alphabet_bp.route("/api/alphabet/train/start", methods=["POST"])
def start_training():
    data = request.get_json() or {}
    epochs = int(data.get("epochs", Config.TRAINING_EPOCHS_DEFAULT))
    batch_size = int(data.get("batch_size", Config.TRAINING_BATCH_SIZE))

    success = trainer.start_training(epochs=epochs, batch_size=batch_size)
    if not success:
        return jsonify({"success": False, "message": "Training already in progress."}), 400
    return jsonify({"success": True, "message": "Training started in background."})

@alphabet_bp.route("/sign-alphabet/stop-training", methods=["POST"])
@alphabet_bp.route("/api/alphabet/train/stop", methods=["POST"])
def stop_training():
    trainer.stop_training()
    return jsonify({"success": True, "message": "Stop signal sent to trainer."})

@alphabet_bp.route("/sign-alphabet/training-status", methods=["GET"])
@alphabet_bp.route("/api/alphabet/train/status", methods=["GET"])
def get_training_status():
    return jsonify(training_state.get_state())

@alphabet_bp.route("/sign-alphabet/model-info", methods=["GET"])
@alphabet_bp.route("/api/alphabet/model-info", methods=["GET"])
def get_model_info():
    model_path = os.path.join(Config.MODEL_DIR, "sign_alphabet_model.keras")
    classes_path = os.path.join(Config.MODEL_DIR, "class_names.json")
    exists = os.path.isfile(model_path)
    
    classes = []
    if os.path.isfile(classes_path):
        try:
            with open(classes_path, "r") as f:
                classes = json.load(f)
        except Exception:
            pass

    size_mb = round(os.path.getsize(model_path) / (1024 * 1024), 2) if exists else 0.0

    return jsonify({
        "model_exists": exists,
        "model_size_mb": size_mb,
        "classes": classes,
        "total_classes": len(classes),
        "model_architecture": "MobileNetV2 Transfer Learning",
        "input_shape": [160, 160, 3]
    })

@alphabet_bp.route("/sign-alphabet/export-model", methods=["GET"])
@alphabet_bp.route("/api/alphabet/export-model", methods=["GET"])
def export_model():
    model_path = os.path.join(Config.MODEL_DIR, "sign_alphabet_model.keras")
    if not os.path.isfile(model_path):
        return jsonify({"success": False, "error": "No trained model available to export."}), 404
    return send_file(model_path, as_attachment=True, download_name="sign_alphabet_model.keras")