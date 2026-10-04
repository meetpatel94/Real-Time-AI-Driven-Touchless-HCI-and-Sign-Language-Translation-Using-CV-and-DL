"""Camera and shared runtime state endpoints.

Desktop installations keep the original OpenCV MJPEG stream and GestureEngine.
On Vercel, a Python function cannot see a visitor's webcam, so these endpoints
remain available but explicitly direct the UI to the browser camera path.
"""

from __future__ import annotations

import time

from flask import Blueprint, Response, jsonify, request

from services.runtime_capabilities import runtime_capabilities
from services.state_service import global_state

if runtime_capabilities.server_camera_available:
    # These dependencies are intentionally never imported by the Vercel Python
    # function.  See static/js/global/browser_camera.js for the browser stream.
    from core.camera.camera_manager import camera_manager
    from core.camera.frame_processor import FrameProcessor
    from core.gestures.gesture_engine import gesture_engine
    from core.mouse.scroll_controller import scroll_controller
else:
    camera_manager = None
    gesture_engine = None
    scroll_controller = None

camera_bp = Blueprint("camera", __name__)

_LOCAL_ONLY_MESSAGE = (
    "This operation needs the local desktop runtime. In the Vercel demo, "
    "camera preview runs in your browser; server-side AI, cursor control and "
    "hand-scroll control are unavailable."
)


def _state_payload():
    payload = global_state.get_state()
    payload["runtime"] = runtime_capabilities.to_client_config()
    payload["camera_mode"] = "browser" if runtime_capabilities.browser_camera_required else "server"
    return payload


def generate_frames():
    """Original local MJPEG generator; not created in serverless mode."""
    while True:
        state = global_state.get_state()
        if state["camera_enabled"]:
            frame = camera_manager.get_display_frame()
            if frame is None:
                frame = FrameProcessor.create_placeholder_frame(message="INITIALIZING...")
        else:
            frame = FrameProcessor.create_placeholder_frame(message="CAMERA OFF")

        jpeg_bytes = FrameProcessor.encode_to_jpeg(frame, quality=75)
        if jpeg_bytes:
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + jpeg_bytes + b"\r\n"
            )
        time.sleep(0.033)


@camera_bp.route("/video_feed")
def video_feed():
    if not runtime_capabilities.server_camera_available:
        return jsonify({
            "success": False,
            "code": "browser_camera_required",
            "message": "Use the in-page browser camera preview on this Vercel deployment.",
        }), 409
    return Response(generate_frames(), mimetype="multipart/x-mixed-replace; boundary=frame")


@camera_bp.route("/api/camera/toggle", methods=["POST"])
def toggle_camera():
    if not runtime_capabilities.server_camera_available:
        return jsonify({
            "status": "browser_camera_required",
            "camera_enabled": False,
            "message": "Camera permission is requested directly by your browser in this Vercel demo.",
            "runtime": runtime_capabilities.to_client_config(),
        }), 409

    current_state = global_state.get_state()["camera_enabled"]
    target_state = not current_state

    if target_state:
        success = camera_manager.start()
        if not success:
            return jsonify({"status": "error", "message": "Failed to access webcam."}), 500
    else:
        camera_manager.stop()
        gesture_engine.reset_adaptive_state()

    return jsonify({"status": "success", "camera_enabled": global_state.get_state()["camera_enabled"]})


@camera_bp.route("/api/gesture/toggle", methods=["POST"])
def toggle_gesture():
    if not runtime_capabilities.server_ai_available:
        return jsonify({
            "status": "local_only",
            "gesture_enabled": False,
            "message": "Server-side gesture execution is available when the application runs locally.",
        }), 409

    state = global_state.get_state()
    if not state["camera_enabled"]:
        return jsonify({"status": "error", "message": "Cannot enable gestures while camera is OFF"}), 400

    target_state = not state["gesture_enabled"]
    global_state.set_gesture_state(target_state)
    return jsonify({"status": "success", "gesture_enabled": global_state.get_state()["gesture_enabled"]})


@camera_bp.route("/api/mouse/sensitivity", methods=["POST"])
def update_sensitivity():
    data = request.get_json(silent=True) or {}
    try:
        val = max(0.10, min(1.0, float(data.get("sensitivity", 0.50))))
    except (TypeError, ValueError):
        val = 0.50

    if runtime_capabilities.system_control_available:
        gesture_engine.mapper.set_sensitivity(val)
        message = "Cursor sensitivity updated."
    else:
        message = "Saved for your profile; system cursor control is local-runtime only."
    return jsonify({"status": "success", "sensitivity": val, "message": message})


@camera_bp.route("/api/mouse/scroll-sensitivity", methods=["POST"])
def update_scroll_sensitivity():
    data = request.get_json(silent=True) or {}
    level = str(data.get("level", "medium")).lower()
    if level not in {"low", "medium", "high"}:
        level = "medium"

    if runtime_capabilities.system_control_available:
        scroll_controller.set_sensitivity(level)
        message = "Scroll sensitivity updated."
    else:
        message = "Saved for your profile; hand-scroll control is local-runtime only."
    return jsonify({"status": "success", "level": level, "message": message})


@camera_bp.route("/api/hand-scroll/events", methods=["GET"])
def get_hand_scroll_events():
    """Return browser-scroll events from the local GestureEngine when present."""
    return jsonify(global_state.hand_scroll_events_since(request.args.get("after", 0)))


@camera_bp.route("/api/state", methods=["GET"])
def get_state():
    return jsonify(_state_payload())
