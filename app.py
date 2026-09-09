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
    # https://192.168.1.105:5000/connect next to the PC running this file.
    # This is the Flask development server for LOCAL LAN testing only; it is
    # not a production deployment and must not be exposed to the internet.
    #
    # HTTPS is required here (not just "nice to have"): mobile browsers only
    # allow navigator.mediaDevices.getUserMedia() (camera access) from a
    # secure context. https://127.0.0.1 is secure, but a plain
    # http://192.168.x.x LAN address is not, which is why a phone opening the
    # HTTP LAN URL sees "This browser does not provide a local camera." while
    # the PC (using 127.0.0.1) works. See services/dev_tls.py for the
    # certificate resolution/fallback logic.
    from services.dev_tls import resolve_ssl_context, print_startup_banner, detect_lan_ip

    HOST = "0.0.0.0"
    PORT = 5000

    lan_ip = detect_lan_ip()
    ssl_context, mode, detail = resolve_ssl_context(lan_ip)
    print_startup_banner(HOST, PORT, mode, detail, lan_ip)

    run_kwargs = dict(host=HOST, port=PORT, debug=True, use_reloader=False)
    if ssl_context is not None:
        run_kwargs["ssl_context"] = ssl_context

    app.run(**run_kwargs)