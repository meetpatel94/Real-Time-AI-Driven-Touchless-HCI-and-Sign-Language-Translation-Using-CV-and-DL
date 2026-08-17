import os

class Config:
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

    RECOGNITION_MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "gesture_model.h5")
    RECOGNITION_ALT_MODEL_PATH = os.path.join(MODEL_DIR, "sign_alphabet_model.keras")
    LABEL_ENCODER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "label_encoder.pkl")
    CLASS_NAMES_JSON_PATH = os.path.join(MODEL_DIR, "class_names.json")
    RECOGNITION_CONFIDENCE_THRESHOLD = 0.70