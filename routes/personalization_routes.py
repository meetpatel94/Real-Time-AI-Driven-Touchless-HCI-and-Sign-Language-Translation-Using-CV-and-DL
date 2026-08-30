"""HTTP API for explicit calibration, correction learning, and mappings."""

from flask import Blueprint, jsonify, request

from core.gestures.gesture_engine import gesture_engine
from services.adaptive_intent_service import adaptive_intent_service
from services.interaction_history_service import interaction_history_service
from services.personalization_service import personalization_service
from services.user_profile_service import user_profile_service


personalization_bp = Blueprint("personalization", __name__)


def _requested_profile_id():
    return request.headers.get("X-GestureForge-Profile") or request.args.get("profile_id")


def _profile_id():
    previous_profile_id = user_profile_service.active_profile_id
    profile = user_profile_service.activate(_requested_profile_id())
    if previous_profile_id != profile.user_id:
        # Do not let a profile switch reuse another user's live pose or
        # temporal state while the camera thread is still running.
        gesture_engine.reset_adaptive_state()
    # Keep all explicit personalization requests off the camera hot path by
    # priming the user-scoped snapshots at the HTTP boundary.
    personalization_service.preload_learning_data(profile.user_id)
    interaction_history_service.preload_user(profile.user_id)
    return profile.user_id


def _payload():
    payload = request.get_json(silent=True)
    return payload if isinstance(payload, dict) else {}


def _result_response(result, success_status=200):
    if result.get("success"):
        return jsonify(result), success_status
    message = str(result.get("error", "Personalization request failed.")).lower()
    storage = result.get("storage")
    storage_unavailable = isinstance(storage, dict) and storage.get("available") is False
    status = 503 if storage_unavailable or "mongodb" in message or "storage" in message else 400
    return jsonify(result), status


@personalization_bp.route("/api/personalization/status", methods=["GET"])
def personalization_status():
    profile_id = _profile_id()
    data = personalization_service.get_data(profile_id)
    return jsonify({"success": True, **data})


@personalization_bp.route("/api/personalization", methods=["GET"])
def personalization_data():
    profile_id = _profile_id()
    return jsonify({"success": True, **personalization_service.get_data(profile_id)})


@personalization_bp.route("/api/personalization/calibration", methods=["GET"])
def get_calibration():
    profile_id = _profile_id()
    session_id = request.args.get("session_id")
    session = personalization_service.get_calibration(profile_id, session_id)
    return jsonify({
        "success": True,
        "profile_id": profile_id,
        "calibration": session.to_dict(personalization_service.storage_available) if session else None,
        "storage": personalization_service.storage_status(),
    })


@personalization_bp.route("/api/personalization/calibration/start", methods=["POST"])
def start_calibration():
    profile_id = _profile_id()
    payload = _payload()
    target = payload.get("target", payload.get("target_label", payload.get("gesture")))
    result = personalization_service.start_calibration(
        profile_id,
        target=target,
        required_samples=payload.get("required_samples"),
        display_name=payload.get("display_name"),
    )
    return _result_response(result, success_status=201)


@personalization_bp.route("/api/personalization/calibration/sample", methods=["POST"])
def capture_calibration_sample():
    profile_id = _profile_id()
    payload = _payload()
    result = personalization_service.request_sample(
        profile_id,
        session_id=payload.get("session_id"),
    )
    return _result_response(result)


@personalization_bp.route("/api/personalization/calibration/complete", methods=["POST"])
def complete_calibration():
    profile_id = _profile_id()
    payload = _payload()
    result = personalization_service.complete_calibration(
        profile_id,
        session_id=payload.get("session_id"),
        display_name=payload.get("display_name"),
    )
    return _result_response(result, success_status=201)


@personalization_bp.route("/api/personalization/learned", methods=["GET"])
@personalization_bp.route("/api/personalization/learned-data", methods=["GET"])
def learned_gestures():
    profile_id = _profile_id()
    data = personalization_service.get_data(profile_id)
    return jsonify({
        "success": True,
        "profile_id": profile_id,
        "storage": data["storage"],
        "learned_gestures": data["learned_gestures"],
        "mappings": data["mappings"],
    })


@personalization_bp.route("/api/personalization/corrections", methods=["GET", "POST"])
@personalization_bp.route("/api/personalization/correction", methods=["POST"])
def corrections():
    profile_id = _profile_id()
    if request.method == "GET":
        try:
            limit = int(request.args.get("limit", 50))
        except (TypeError, ValueError):
            limit = 50
        return jsonify({
            "success": True,
            "profile_id": profile_id,
            "corrections": personalization_service.list_corrections(profile_id, limit),
            "storage": personalization_service.storage_status(),
        })

    payload = _payload()
    result = personalization_service.record_correction(
        profile_id,
        correct_label=payload.get("correct_label", payload.get("label")),
        base_label=payload.get("base_label", "NONE"),
        base_confidence=payload.get("base_confidence", 0.0),
        correct_intent=payload.get("correct_intent", payload.get("intent", "")),
        validated=payload.get("validated") is True,
    )
    return _result_response(result, success_status=201)


@personalization_bp.route("/api/personalization/mappings", methods=["GET", "POST"])
def mappings():
    profile_id = _profile_id()
    if request.method == "GET":
        data = personalization_service.get_data(profile_id)
        return jsonify({
            "success": True,
            "profile_id": profile_id,
            "mappings": data["mappings"],
            "storage": data["storage"],
        })

    payload = _payload()
    result = personalization_service.create_mapping(
        profile_id,
        learned_gesture_id=payload.get("learned_gesture_id", payload.get("gesture_id")),
        action=payload.get("action"),
        name=payload.get("name"),
    )
    return _result_response(result, success_status=201)


@personalization_bp.route("/api/personalization/mappings/<mapping_id>", methods=["DELETE"])
@personalization_bp.route("/api/personalization/mapping/<mapping_id>", methods=["DELETE"])
def delete_mapping(mapping_id):
    profile_id = _profile_id()
    if not personalization_service.delete_mapping(profile_id, mapping_id):
        storage = personalization_service.storage_status()
        status = 503 if not storage.get("available") else 404
        error = "MongoDB is unavailable." if status == 503 else "Mapping was not found."
        return jsonify({"success": False, "error": error}), status
    return jsonify({"success": True, "mapping_id": mapping_id})


@personalization_bp.route("/api/personalization/reset", methods=["POST"])
def reset_personalization():
    profile_id = _profile_id()
    result = personalization_service.reset(profile_id)
    interaction_history_service.clear_user(profile_id)
    adaptive_intent_service.reset()
    return _result_response(result)
