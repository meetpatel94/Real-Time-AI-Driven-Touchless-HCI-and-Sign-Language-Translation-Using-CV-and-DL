from flask import Flask
import atexit
from config import Config
from services.logging_service import logger
from database.mongo_database import mongo_database
from core.camera.camera_manager import camera_manager
from core.gestures.gesture_engine import gesture_engine

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    # Register Blueprints
    from routes.main_routes import main_bp
    from routes.camera_routes import camera_bp
    from routes.overview_routes import overview_bp
    from routes.drawing_routes import drawing_bp
    from routes.alphabet_routes import alphabet_bp
    from routes.sign_recognition_routes import recognition_bp
    from routes.studio_routes import studio_bp
    from routes.sentence_routes import sentence_bp
    from routes.translation_routes import translation_bp
    from routes.custom_gesture_routes import custom_gesture_bp
    from routes.adaptive_routes import adaptive_bp
    from routes.personalization_routes import personalization_bp
    from routes.connect_routes import connect_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(camera_bp)
    app.register_blueprint(overview_bp)
    app.register_blueprint(drawing_bp)
    app.register_blueprint(alphabet_bp)
    app.register_blueprint(recognition_bp)
    app.register_blueprint(studio_bp)
    app.register_blueprint(sentence_bp)
    app.register_blueprint(translation_bp)
    app.register_blueprint(custom_gesture_bp)
    app.register_blueprint(adaptive_bp)
    app.register_blueprint(personalization_bp)
    app.register_blueprint(connect_bp)

    # Connect real-time relay: WebSocket endpoint on the same dev-server port.
    from flask_sock import Sock
    sock = Sock(app)
    from routes.connect_socket import register_connect_socket
    register_connect_socket(sock)

    # Initialize Gesture Engine thread
    gesture_engine.start()

    # Register teardown
    atexit.register(cleanup)

    logger.info("GestureForge AI application initialized successfully.")
    return app

def cleanup():
    logger.info("Shutting down background services...")
    gesture_engine.stop()
    camera_manager.stop()
    mongo_database.close()

app = create_app()

if __name__ == "__main__":
    # Bind to 0.0.0.0 (not only 127.0.0.1) so other devices on the same Wi-Fi
    # can reach the same Flask + WebSocket server — e.g. a phone opening
    # http://192.168.1.105:5000/connect next to the PC running this file.
    # This is the Flask development server for LOCAL LAN testing only; it is
    # not a production deployment and must not be exposed to the internet.
    HOST = "0.0.0.0"
    PORT = 5000

    def _lan_connect_url():
        """Best-effort LAN URL for the Connect two-device test."""
        import socket
        candidates = []
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            # No packets are sent; connect() only picks a route for the IP.
            probe.connect(("8.8.8.8", 80))
            candidates.append(probe.getsockname()[0])
            probe.close()
        except Exception:
            pass
        try:
            for address in socket.gethostbyname_ex(socket.gethostname())[2]:
                if address.startswith(("192.168.", "10.", "172.")):
                    candidates.append(address)
        except Exception:
            pass
        for address in candidates:
            if not address.startswith("127."):
                return f"http://{address}:{PORT}/connect"
        return None

    lan_url = _lan_connect_url()
    if lan_url:
        print(f"* GestureForge Connect (two-device LAN test): {lan_url}")
        print("* Open that address on the PC and on the phone (same Wi-Fi) to test rooms.")

    app.run(host=HOST, port=PORT, debug=True, use_reloader=False)