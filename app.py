from flask import Flask
import atexit
from config import Config
from services.logging_service import logger
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

    app.register_blueprint(main_bp)
    app.register_blueprint(camera_bp)
    app.register_blueprint(overview_bp)
    app.register_blueprint(drawing_bp)
    app.register_blueprint(alphabet_bp)
    app.register_blueprint(recognition_bp)
    app.register_blueprint(studio_bp)
    app.register_blueprint(sentence_bp)
    app.register_blueprint(translation_bp)

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

app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True, use_reloader=False)