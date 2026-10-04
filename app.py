"""GestureForge application factory.

The local desktop runtime and the Vercel serverless runtime deliberately share
one Flask application.  The former starts the original OpenCV/MediaPipe/
TensorFlow workers; the latter registers the same pages and HTTP APIs without
trying to open a cloud-server camera, spawn persistent workers, or control a
visitor's operating system.
"""

from __future__ import annotations

import atexit

from flask import Flask

from config import Config
from database.mongo_database import mongo_database
from services.logging_service import logger
from services.runtime_capabilities import runtime_capabilities


def create_app() -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)

    @app.context_processor
    def inject_runtime_capabilities():
        """Expose only public deployment feature flags to Jinja templates."""
        return {"runtime": runtime_capabilities.to_template_config()}

    # Register all pages/API blueprints in both environments.  Individual routes
    # return a clear deployment-safe response for an operation that genuinely
    # needs local camera hardware, TensorFlow, a writable filesystem, or a
    # persistent WebSocket process.
    from routes.main_routes import main_bp
    from routes.camera_routes import camera_bp
    from routes.overview_routes import overview_bp
    from routes.drawing_routes import drawing_bp
    from routes.mouse_routes import mouse_bp
    from routes.alphabet_routes import alphabet_bp
    from routes.sign_recognition_routes import recognition_bp
    from routes.studio_routes import studio_bp
    from routes.sentence_routes import sentence_bp
    from routes.translation_routes import translation_bp
    from routes.custom_gesture_routes import custom_gesture_bp
    from routes.adaptive_routes import adaptive_bp
    from routes.personalization_routes import personalization_bp
    from routes.connect_routes import connect_bp

    for blueprint in (
        main_bp,
        camera_bp,
        overview_bp,
        drawing_bp,
        mouse_bp,
        alphabet_bp,
        recognition_bp,
        studio_bp,
        sentence_bp,
        translation_bp,
        custom_gesture_bp,
        adaptive_bp,
        personalization_bp,
        connect_bp,
    ):
        app.register_blueprint(blueprint)

    if runtime_capabilities.websocket_available:
        # Flask-Sock needs a persistent process/socket and is therefore local
        # runtime only.  The Connect page reports this limitation in Vercel
        # rather than leaving an upgrade loop or a broken WebSocket connection.
        from flask_sock import Sock
        from routes.connect_socket import register_connect_socket

        sock = Sock(app)
        register_connect_socket(sock)

    if runtime_capabilities.server_ai_available:
        # Importing these modules is intentionally delayed.  Their OpenCV,
        # MediaPipe and TensorFlow imports are not part of the Vercel runtime.
        from core.gestures.gesture_engine import gesture_engine

        gesture_engine.start()
        atexit.register(cleanup)
    else:
        # Preserve the recognition API shape while making the deployment state
        # explicit to the UI instead of claiming a cloud model is ready.
        from core.recognition.recognition_state import recognition_state

        recognition_state.set_model_status("BROWSER CAMERA ONLY", "Local model required")
        logger.info(
            "GestureForge started in serverless mode: camera, TensorFlow "
            "inference, training, PyAutoGUI and WebSockets are disabled."
        )

    logger.info("GestureForge AI application initialized successfully (%s).", runtime_capabilities.deployment_label)
    return app


def cleanup() -> None:
    """Stop only the local desktop resources that this process started."""
    if not runtime_capabilities.server_ai_available:
        return
    logger.info("Shutting down local background services...")
    try:
        from core.gestures.gesture_engine import gesture_engine
        from core.camera.camera_manager import camera_manager

        gesture_engine.stop()
        camera_manager.stop()
    finally:
        mongo_database.close()


app = create_app()


if __name__ == "__main__":
    # Keep the existing LAN-friendly desktop development behavior.  Vercel
    # imports ``app`` through api/index.py and never executes this block.
    from services.dev_tls import detect_lan_ip, print_startup_banner, resolve_ssl_context

    HOST = "0.0.0.0"
    PORT = 5000
    lan_ip = detect_lan_ip()
    ssl_context, mode, detail = resolve_ssl_context(lan_ip)
    print_startup_banner(HOST, PORT, mode, detail, lan_ip)

    run_kwargs = dict(host=HOST, port=PORT, debug=True, use_reloader=False)
    if ssl_context is not None:
        run_kwargs["ssl_context"] = ssl_context
    app.run(**run_kwargs)
