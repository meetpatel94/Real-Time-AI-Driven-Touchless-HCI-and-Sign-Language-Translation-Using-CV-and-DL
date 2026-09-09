"""Connect: two-person gesture communication room — page + REST helpers.

WebSocket messaging itself is registered in ``register_connect_socket`` (see
``routes/connect_socket.py``) so Flask can create the SocketIO-free Flask-Sock
instance inside the app factory.
"""

from flask import Blueprint, jsonify, render_template

from config import Config
from core.connect.pose_dictionary import BUILTIN_GESTURE_DICTIONARY
from core.custom_gestures.service import custom_gesture_service
from services.state_service import global_state

connect_bp = Blueprint("connect", __name__)


@connect_bp.route("/connect")
def connect_page():
    global_state.update_state({"active_module": "connect"})
    return render_template("connect/index.html")


@connect_bp.route("/api/connect/mappings", methods=["GET"])
def gesture_mappings():
    """Cached client-side mapping table (built-ins + saved custom gestures).

    Only gesture ids/meanings are exposed; no camera data ever leaves the
    recognition loop through this API.
    """
    builtins = [
        {
            "kind": "pose",
            "gesture_id": gesture_id,
            "symbol": entry["symbol"],
            "meaning": entry["meaning"],
            "hint": entry["hint"],
        }
        for gesture_id, entry in sorted(BUILTIN_GESTURE_DICTIONARY.items())
    ]
    customs = []
    try:
        for gesture in custom_gesture_service.list_gestures():
            sample_count = int(gesture.get("sample_count", 0) or 0)
            customs.append({
                "kind": "custom",
                "gesture_id": gesture.get("gesture_id", ""),
                "gesture_name": gesture.get("gesture_name", ""),
                "description": gesture.get("description", ""),
                "hand": gesture.get("hand", "either"),
                "sample_count": sample_count,
                "enabled": bool(gesture.get("enabled", True)),
                "has_replay": sample_count > 0,
            })
    except Exception as exc:  # pragma: no cover - fail open for UI mapping
        from services.logging_service import logger
        logger.warning(f"Connect custom gesture mapping refresh failed: {exc}")

    return jsonify({
        "success": True,
        "ws_path": Config.CONNECT_WS_PATH,
        "builtins": builtins,
        "customs": customs,
    })


@connect_bp.route("/api/connect/replay/<gesture_id>", methods=["GET"])
def replay_sample(gesture_id):
    """Serve an existing saved Custom Gesture sample for client-side replay.

    Returns the mean normalized MediaPipe landmark track of the real saved
    samples so the receiver can animate the exact gesture the sender taught.
    Unknown gestures and gestures without samples return a clear 404/null.
    """
    try:
        replay = custom_gesture_service.get_replay_sample(gesture_id)
    except ValueError:
        replay = None
    if replay is None:
        return jsonify({"success": False, "error": "Custom gesture not found."}), 404
    return jsonify({"success": True, "replay": replay})
