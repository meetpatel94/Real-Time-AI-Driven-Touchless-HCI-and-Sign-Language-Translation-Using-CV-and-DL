import os


def _env_int(name: str, default: int, minimum: int = None) -> int:
    try:
        value = int(os.environ.get(name, default))
        if minimum is not None and value < minimum:
            return default
        return value
    except (TypeError, ValueError):
        return default


def _env_text(names, default: str) -> str:
    for name in names:
        value = os.environ.get(name)
        if value is not None and value.strip():
            return value.strip()
    return default


class Config:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    SECRET_KEY = os.environ.get("SECRET_KEY", "gestureforge-ai-secret-key-2026")
    CAMERA_INDEX = 0
    FRAME_WIDTH = 640
    FRAME_HEIGHT = 480
    FPS_TARGET = 30
    
    # Cursor Mapping, Smoothing & Sensitivity
    CURSOR_SMOOTHING = 0.35
    CURSOR_DEADZONE = 2
    CURSOR_EDGE_MARGIN = 0.08
    DEFAULT_CURSOR_SENSITIVITY = 0.50  # 50% default
    
    # Dwell Selection Parameters
    DWELL_DURATION_SECONDS = 1.2
    DWELL_COOLDOWN_SECONDS = 0.5

    # Right-Hand Vertical Swipe Scrolling Parameters
    SCROLL_DISPLACEMENT_THRESHOLD = 0.11  # Normalized Y distance (11% of camera frame height)
    SCROLL_WINDOW_SECONDS = 0.28          # Time window to complete deliberate swipe
    SCROLL_COOLDOWN_SECONDS = 0.38        # 380ms debounce between successive scrolls
    DEFAULT_SCROLL_AMOUNT = 300           # Medium default scroll ticks

    # Right-Fist Confirmation Debounce Settings
    FIST_CONSECUTIVE_FRAMES = 3
    FIST_COOLDOWN_SECONDS = 0.6

    # Dataset Collection Parameters
    DATASET_BASE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "dataset", "sign_alphabet")
    DATASET_TARGET_PER_CLASS = 500
    DATASET_IMAGE_SIZE = (160, 160)
    DATASET_CAPTURE_INTERVAL = 0.15
    DATASET_CROP_PADDING = 0.12
    DATASET_MIN_LANDMARK_DELTA = 0.015

    # Model Training & Inference Parameters
    MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "sign_alphabet")
    TRAINING_STATUS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "training", "status")
    TRAINING_STATUS_FILE = os.path.join(TRAINING_STATUS_DIR, "sign_alphabet_training_status.json")
    TRAINING_BATCH_SIZE = 16
    TRAINING_EPOCHS_DEFAULT = 15
    TRAINING_VAL_SPLIT = 0.20
    TRAINING_LEARNING_RATE = 0.001

    RECOGNITION_MODEL_PATH = os.path.join(BASE_DIR, "models", "gesture_model.h5")
    RECOGNITION_ALT_MODEL_PATH = os.path.join(MODEL_DIR, "sign_alphabet_model.keras")
    LABEL_ENCODER_PATH = os.path.join(BASE_DIR, "models", "label_encoder.pkl")
    CLASS_NAMES_JSON_PATH = os.path.join(MODEL_DIR, "class_names.json")
    RECOGNITION_CONFIDENCE_THRESHOLD = 0.70

    # Human-adaptive persistence. The base model, A-Z dataset and webcam
    # frames remain local; only derived personalization documents use MongoDB.
    DEFAULT_PROFILE_ID = _env_text(("GESTUREFORGE_PROFILE_ID",), "local-user")
    MONGODB_URI = _env_text(
        ("MONGODB_URI", "MONGO_URI"), "mongodb://127.0.0.1:27017"
    )
    MONGODB_DATABASE = _env_text(
        ("MONGODB_DATABASE", "MONGODB_DB_NAME", "MONGO_DB_NAME", "MONGO_DATABASE"),
        "gestureforge",
    )
    MONGODB_SERVER_SELECTION_TIMEOUT_MS = _env_int(
        "MONGODB_SERVER_SELECTION_TIMEOUT_MS", 750, minimum=1
    )
    MONGODB_CONNECT_TIMEOUT_MS = _env_int(
        "MONGODB_CONNECT_TIMEOUT_MS", 750, minimum=1
    )
    MONGODB_SOCKET_TIMEOUT_MS = _env_int(
        "MONGODB_SOCKET_TIMEOUT_MS", 1500, minimum=1
    )
    MONGODB_MAX_POOL_SIZE = _env_int("MONGODB_MAX_POOL_SIZE", 10, minimum=1)

    ADAPTIVE_UNKNOWN_MIN_SAMPLES = 3
    ADAPTIVE_UNKNOWN_HOLD_SECONDS = 0.18
    ADAPTIVE_HISTORY_LIMIT = 40

    # Personalization safety gates and bounded calibration settings.
    PERSONALIZATION_MIN_VALIDATED_SAMPLES = 3
    PERSONALIZATION_MIN_RELIABILITY = 0.70
    PERSONALIZATION_BASE_RELIABLE_THRESHOLD = 0.85
    PERSONALIZATION_MATCH_MIN_CONFIDENCE = 0.68
    PERSONALIZATION_MAX_CALIBRATION_SAMPLES = 20
    PERSONALIZATION_LATEST_OBSERVATION_TTL_SECONDS = 8.0
    PERSONALIZATION_ACTION_COOLDOWN_SECONDS = 0.80
