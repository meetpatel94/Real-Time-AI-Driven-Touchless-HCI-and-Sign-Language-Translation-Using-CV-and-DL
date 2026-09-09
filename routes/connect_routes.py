"""Connect: two-person gesture communication room — page + REST helpers.

WebSocket messaging itself is registered in ``register_connect_socket`` (see
``routes/connect_socket.py``) so Flask can create the SocketIO-free Flask-Sock
instance inside the app factory.
"""

from flask import Blueprint, jsonify, render_template, request

from config import Config
from core.connect.pose_dictionary import BUILTIN_GESTURE_DICTIONARY
from core.custom_gestures.service import custom_gesture_service
from services.state_service import global_state

connect_bp = Blueprint("connect", __name__)


@connect_bp.route("/connect")
def connect_page():
    global_state.update_state({"active_module": "connect"})
    return render_template("connect/index.html")


@connect_bp.route("/api/connect/health", methods=["GET"])
def connect_health():
    """Lightweight reachability probe for LAN / two-device Connect testing.

    Reports only whether this server answers on the address the tester used,
    which transport (http/https) that request used, and the WebSocket path
    the page will dial (clients derive it from window.location — see
    static/js/connect/connect.js). It works over both HTTP and HTTPS and
    never touches room state, recognition, or any camera logic.

    ``transport`` comes from the request itself: when the dev server runs
    with the mkcert certificate it answers this probe over TLS, so the
    value is ``"https"`` and a tester can confirm the secure context that
    unlocks the phone camera.
    """
    return jsonify(
        {
            "ok": True,
            "status": "healthy",
            "service": "gestureforge-connect",
            "transport": "https" if request.is_secure else "http",
            "ws_path": Config.CONNECT_WS_PATH,
        }
    )


@connect_bp.route("/api/connect/mappings", methods=["GET"])
def gesture_mappings():
    """Cached client-side mapping table (built-ins + saved custom gestures).

    Built-ins expose their meanings and saved custom gestures expose the
    existing derived feature vectors needed for browser-local matching. No
    camera frame, image, or continuous landmark stream leaves recognition.
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
        # Connect reuses the Custom Gestures library's stored feature vectors
        # for browser-local matching. These are derived representations of
        # saved samples, not webcam frames or a second gesture database/model.
        # Keep the access read-only and scoped to this endpoint so the existing
        # Custom Gestures workflow remains the source of truth.
        cached = custom_gesture_service._load_cache()  # noqa: SLF001 - Connect read-only adapter
        for gesture in custom_gesture_service.list_gestures():
            sample_count = int(gesture.get("sample_count", 0) or 0)
            gesture_id = gesture.get("gesture_id", "")
            cached_gesture = cached.get(gesture_id, {}) if isinstance(cached, dict) else {}
            prototype = cached_gesture.get("prototype", [])
            sample_features = cached_gesture.get("sample_features", [])
            variation_features = cached_gesture.get("variation_features", [])
            if not isinstance(prototype, list):
                prototype = []
            if not isinstance(sample_features, list):
                sample_features = []
            if not isinstance(variation_features, list):
                variation_features = []
            ready = (
                bool(gesture.get("enabled", True))
                and sample_count >= int(gesture.get("target_samples", 1) or 1)
                and bool(prototype or sample_features)
            )
            customs.append({
                "kind": "custom",
                "gesture_id": gesture_id,
                "gesture_name": gesture.get("gesture_name", ""),
                "description": gesture.get("description", ""),
                "hand": gesture.get("hand", "either"),
                "sample_count": sample_count,
                "enabled": bool(gesture.get("enabled", True)),
                "has_replay": sample_count > 0,
                "ready": ready,
                # These are existing normalized feature vectors only. Never
                # include raw or continuous landmarks in a WebSocket message.
                "prototype": prototype if ready else [],
                "sample_features": sample_features[:120] if ready else [],
                "variation_features": variation_features[:40] if ready else [],
                "similarity_threshold": float(
                    cached_gesture.get("similarity_threshold", getattr(custom_gesture_service, "similarity_threshold", 0.85))
                    or 0.85
                ),
            })
    except Exception as exc:  # pragma: no cover - fail open for UI mapping
        from services.logging_service import logger
        logger.warning(f"Connect custom gesture mapping refresh failed: {exc}")

    return jsonify({
        "success": True,
        "ws_path": Config.CONNECT_WS_PATH,
        "builtins": builtins,
        "customs": customs,
        "local_recognition": {
            "hold_seconds": float(getattr(Config, "CONNECT_GESTURE_HOLD_SECONDS", 2.0)),
            "min_stable_frames": int(getattr(Config, "CONNECT_GESTURE_MIN_STABLE_FRAMES", 4)),
            "custom_stable_frames": int(getattr(Config, "CONNECT_CUSTOM_GESTURE_STABLE_FRAMES", 3)),
            "pose_min_quality": float(getattr(Config, "CONNECT_POSE_TRACKING_MIN_QUALITY", 0.90)),
            "custom_min_quality": float(getattr(custom_gesture_service, "min_tracking_quality", 0.90)),
            "custom_max_match_distance": float(getattr(custom_gesture_service, "max_match_distance", 0.45)),
        },
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
