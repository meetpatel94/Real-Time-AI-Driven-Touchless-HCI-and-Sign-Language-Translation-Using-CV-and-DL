"""Sign Alphabet page, dataset inspection and model-management endpoints."""

from __future__ import annotations

import json
import os

from flask import Blueprint, jsonify, render_template, request, send_file

from config import Config
from core.sign_alphabet.dataset_manager import dataset_manager
from core.sign_alphabet.training_state import training_state
from services.runtime_capabilities import runtime_capabilities
from services.state_service import global_state

if runtime_capabilities.background_workers_available:
    # TensorFlow is a local-desktop dependency.  Do not import it while Vercel
    # is building/loading the Flask function.
    from core.sign_alphabet.trainer import trainer
else:
    trainer = None

alphabet_bp = Blueprint("alphabet", __name__)

_TRAINING_UNAVAILABLE = (
    "Model training needs TensorFlow, a persistent worker and writable model "
    "storage, so it is available only in the local desktop runtime."
)


@alphabet_bp.route("/alphabet")
@alphabet_bp.route("/sign-alphabet")
def alphabet():
    global_state.update_state({"active_module": "alphabet"})
    return render_template("alphabet/sign_alphabet.html")


@alphabet_bp.route("/sign-alphabet/dataset-info", methods=["GET"])
@alphabet_bp.route("/api/alphabet/dataset-info", methods=["GET"])
def get_dataset_info():
    overview = dataset_manager.get_dataset_overview()
    overview["dataset_writable"] = runtime_capabilities.background_workers_available
    overview["message"] = (
        "Dataset collection and changes are local-runtime only."
        if not runtime_capabilities.background_workers_available
        else ""
    )
    return jsonify(overview)


@alphabet_bp.route("/sign-alphabet/class-preview/<letter>", methods=["GET"])
@alphabet_bp.route("/api/alphabet/class-preview/<letter>", methods=["GET"])
def get_class_preview(letter):
    sample_path = dataset_manager.get_class_preview(letter.upper())
    if sample_path and os.path.isfile(sample_path):
        return send_file(sample_path, mimetype="image/jpeg")
    return jsonify({"error": "Preview not found"}), 404


@alphabet_bp.route("/sign-alphabet/start-training", methods=["POST"])
@alphabet_bp.route("/api/alphabet/train/start", methods=["POST"])
def start_training():
    if trainer is None:
        return jsonify({"success": False, "message": _TRAINING_UNAVAILABLE, "code": "local_runtime_only"}), 409

    data = request.get_json(silent=True) or {}
    try:
        epochs = max(1, min(100, int(data.get("epochs", Config.TRAINING_EPOCHS_DEFAULT))))
        batch_size = max(1, min(256, int(data.get("batch_size", Config.TRAINING_BATCH_SIZE))))
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "Epochs and batch size must be numbers."}), 400

    success = trainer.start_training(epochs=epochs, batch_size=batch_size)
    if not success:
        return jsonify({"success": False, "message": "Training already in progress."}), 400
    return jsonify({"success": True, "message": "Training started in background."})


@alphabet_bp.route("/sign-alphabet/stop-training", methods=["POST"])
@alphabet_bp.route("/api/alphabet/train/stop", methods=["POST"])
def stop_training():
    if trainer is None:
        return jsonify({"success": False, "message": _TRAINING_UNAVAILABLE, "code": "local_runtime_only"}), 409
    trainer.stop_training()
    return jsonify({"success": True, "message": "Stop signal sent to trainer."})


@alphabet_bp.route("/sign-alphabet/training-status", methods=["GET"])
@alphabet_bp.route("/api/alphabet/train/status", methods=["GET"])
def get_training_status():
    state = training_state.get_state()
    if trainer is None:
        state.update({
            "status": "LOCAL RUNTIME ONLY",
            "device": "Vercel serverless",
            "error_message": _TRAINING_UNAVAILABLE,
            "training_available": False,
        })
    else:
        state["training_available"] = True
    return jsonify(state)


@alphabet_bp.route("/sign-alphabet/model-info", methods=["GET"])
@alphabet_bp.route("/api/alphabet/model-info", methods=["GET"])
def get_model_info():
    model_path = os.path.join(Config.MODEL_DIR, "sign_alphabet_model.keras")
    classes_path = os.path.join(Config.MODEL_DIR, "class_names.json")
    exists = os.path.isfile(model_path)

    classes = []
    if os.path.isfile(classes_path):
        try:
            with open(classes_path, "r", encoding="utf-8") as file_handle:
                classes = json.load(file_handle)
        except (OSError, ValueError):
            pass

    size_mb = round(os.path.getsize(model_path) / (1024 * 1024), 2) if exists else 0.0
    return jsonify({
        "model_exists": exists,
        "model_size_mb": size_mb,
        "classes": classes,
        "total_classes": len(classes),
        "model_architecture": "MobileNetV2 Transfer Learning",
        "input_shape": [160, 160, 3],
        "server_inference_available": runtime_capabilities.server_ai_available,
        "training_available": runtime_capabilities.background_workers_available,
        "deployment_message": (
            "The trained model is packaged for download, but TensorFlow inference and training run locally."
            if not runtime_capabilities.server_ai_available
            else ""
        ),
    })


@alphabet_bp.route("/sign-alphabet/export-model", methods=["GET"])
@alphabet_bp.route("/api/alphabet/export-model", methods=["GET"])
def export_model():
    model_path = os.path.join(Config.MODEL_DIR, "sign_alphabet_model.keras")
    if not os.path.isfile(model_path):
        return jsonify({"success": False, "error": "No trained model available to export."}), 404
    return send_file(model_path, as_attachment=True, download_name="sign_alphabet_model.keras")
