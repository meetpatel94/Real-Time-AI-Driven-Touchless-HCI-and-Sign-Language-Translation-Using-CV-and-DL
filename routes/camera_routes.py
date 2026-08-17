from flask import Blueprint, Response, jsonify, request
import time
from core.camera.camera_manager import camera_manager
from core.camera.frame_processor import FrameProcessor
from core.gestures.gesture_engine import gesture_engine
from core.mouse.scroll_controller import scroll_controller
from services.state_service import global_state

camera_bp = Blueprint("camera", __name__)

def generate_frames():
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
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + jpeg_bytes + b'\r\n')
        
        time.sleep(0.033)

@camera_bp.route("/video_feed")
def video_feed():
    return Response(generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

@camera_bp.route("/api/camera/toggle", methods=["POST"])
def toggle_camera():
    current_state = global_state.get_state()["camera_enabled"]
    target_state = not current_state

    if target_state:
        success = camera_manager.start()
        if not success:
            return jsonify({"status": "error", "message": "Failed to access webcam."}), 500
    else:
        camera_manager.stop()

    return jsonify({"status": "success", "camera_enabled": global_state.get_state()["camera_enabled"]})

@camera_bp.route("/api/gesture/toggle", methods=["POST"])
def toggle_gesture():
    state = global_state.get_state()
    if not state["camera_enabled"]:
        return jsonify({"status": "error", "message": "Cannot enable gestures while camera is OFF"}), 400

    target_state = not state["gesture_enabled"]
    global_state.set_gesture_state(target_state)

    return jsonify({"status": "success", "gesture_enabled": global_state.get_state()["gesture_enabled"]})

@camera_bp.route("/api/mouse/sensitivity", methods=["POST"])
def update_sensitivity():
    data = request.get_json() or {}
    val = float(data.get("sensitivity", 0.50))
    gesture_engine.mapper.set_sensitivity(val)
    return jsonify({"status": "success", "sensitivity": val})

@camera_bp.route("/api/mouse/scroll-sensitivity", methods=["POST"])
def update_scroll_sensitivity():
    data = request.get_json() or {}
    level = data.get("level", "medium")
    scroll_controller.set_sensitivity(level)
    return jsonify({"status": "success", "level": level})

@camera_bp.route("/api/state", methods=["GET"])
def get_state():
    return jsonify(global_state.get_state())