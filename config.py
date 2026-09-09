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

    # Global Right-Hand Vertical Scrolling Parameters.  Coordinates are
    # MediaPipe-normalized and are deliberately independent of custom-gesture
    # labels.  Scrolling requires a stable OPEN RIGHT HAND plus a clearly
    # dominant vertical movement; sideways/diagonal travel never scrolls.
    SCROLL_DISPLACEMENT_THRESHOLD = 0.045  # Shared legacy threshold (intent layer)
    SCROLL_WINDOW_SECONDS = 0.38           # Retained for the adaptive intent layer
    SCROLL_COOLDOWN_SECONDS = 0.42         # Retained for the adaptive intent layer

    # Open-palm posture gates (scale-invariant MediaPipe landmark ratios).
    SCROLL_POSE_MIN_FRAMES = 4             # Stable posture frames before arming
    SCROLL_POSE_STABILITY_TOLERANCE = 0.28 # Max pose-signature drift between frames
    # Calibrated against real MediaPipe tracks: open palms measure
    # wrist->tip 1.38-1.97 and MCP->tip 0.66-1.40, while fists measure
    # wrist->tip 0.59-1.23 and MCP->tip 0.15-0.34 (palm length == 1.0).
    SCROLL_POSE_EXTENSION_MARGIN = 1.08    # wrist->tip must exceed wrist->PIP
    SCROLL_POSE_MIN_EXTENSION_SPAN = 1.30  # wrist->tip minimum (open vs fist)
    SCROLL_POSE_STRAIGHTNESS_RATIO = 1.60  # MCP->tip must exceed MCP->PIP
    SCROLL_POSE_MIN_FINGER_LENGTH = 0.55   # MCP->tip minimum (uncurled finger)
    SCROLL_POSE_MAX_SPREAD_RATIO = 1.25    # Adjacent fingertips stay together

    # Motion gates (normalized units, and seconds).
    SCROLL_SMOOTHING = 0.45                # EMA weight for palm-center tracking
    SCROLL_MAX_HISTORY = 24                # Frames kept (~0.8s at 30 FPS)
    SCROLL_VELOCITY_WINDOW_SECONDS = 0.20  # Window used to measure hand speed
    SCROLL_MIN_SAMPLES = 3                 # Samples required inside that window
    SCROLL_MIN_SPEED = 0.22                # Dead-zone: min vertical speed (units/s)
    SCROLL_VERTICAL_DOMINANCE = 1.5        # |vy| must exceed |vx| * this factor
    SCROLL_TRIGGER_DISTANCE = 0.035        # Dead-zone travel before a stroke scrolls
    SCROLL_DIRECTION_FRAMES = 3            # Consecutive frames agreeing on direction
    SCROLL_DIRECTION_JITTER = 0.004        # Tolerated per-frame counter-movement
    SCROLL_MAX_FRAME_TRAVEL = 0.22         # Bigger jumps are tracking glitches

    # Smooth, rate-limited emission.  Amounts are px/second x elapsed time so
    # the browser can ease them; each event stays far below one page jump.
    SCROLL_MIN_EVENT_INTERVAL = 0.07       # Fastest event rate (~14 events/s)
    SCROLL_MAX_EVENT_INTERVAL = 0.15       # Elapsed cap after a pause
    SCROLL_MAX_AMOUNT = 220                # Hard upper bound for a single event
    SCROLL_SPEED_MIN_PX_PER_SEC = 240.0    # Slowest continuous scroll speed
    SCROLL_SPEED_GAIN = 700.0              # Extra px/s per normalized unit/s
    SCROLL_MAX_SPEED_PX_PER_SEC = 1200.0   # Hard cap for intentional fast sweeps
    SCROLL_SPEED_FACTOR_LOW = 0.6          # Low scroll-speed multiplier
    SCROLL_SPEED_FACTOR_MEDIUM = 1.0       # Medium scroll-speed multiplier
    SCROLL_SPEED_FACTOR_HIGH = 1.45        # High scroll-speed multiplier

    DEFAULT_SCROLL_AMOUNT = 300            # Single OS scroll step (legacy mappings)
    HAND_SCROLL_EVENT_BUFFER_SIZE = 80     # Small, in-memory browser event relay

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

    # Custom Gesture Library (isolated from A-Z model and global predictions)
    CUSTOM_GESTURE_BASE_DIR = os.path.join(BASE_DIR, "data", "custom_gestures")
    CUSTOM_GESTURE_DEFAULT_SAMPLES = 30
    CUSTOM_GESTURE_MIN_READY_SAMPLES = 3
    CUSTOM_GESTURE_CAPTURE_INTERVAL = 0.12
    CUSTOM_GESTURE_SIMILARITY_THRESHOLD = 0.85
    CUSTOM_GESTURE_MAX_MATCH_DISTANCE = 0.45
    CUSTOM_GESTURE_STABILITY_FRAMES = 3
    CUSTOM_GESTURE_SMOOTHING_WINDOW = 6
    CUSTOM_GESTURE_MIN_TRACKING_QUALITY = 0.90

    # Custom Gesture self-learning (conservative; Custom Gestures only).
    # Unknown gesture discovery: repeated stable unknown observations are
    # clustered into candidates the user must explicitly approve.
    CUSTOM_GESTURE_LEARNING_ENABLED = True
    CUSTOM_GESTURE_CANDIDATE_MIN_OBSERVATIONS = 8
    CUSTOM_GESTURE_CANDIDATE_CLUSTER_THRESHOLD = 0.85
    CUSTOM_GESTURE_CANDIDATE_TTL_SECONDS = 300
    CUSTOM_GESTURE_CANDIDATE_STABILITY_WINDOW = 8
    CUSTOM_GESTURE_CANDIDATE_STABILITY_THRESHOLD = 0.05
    CUSTOM_GESTURE_CANDIDATE_MAX_OBSERVATIONS = 48
    CUSTOM_GESTURE_IGNORED_SIGNATURE_TTL_DAYS = 14
    # Mistake memory: personalized corrections for the custom matcher only.
    CUSTOM_GESTURE_CORRECTION_MIN_EVIDENCE = 1.0
    CUSTOM_GESTURE_CORRECTION_SIGNATURE_THRESHOLD = 0.85
    CUSTOM_GESTURE_CORRECTION_MAX_AGE_DAYS = 90
    CUSTOM_GESTURE_CORRECTION_MAX_STORED = 500
    # Gesture evolution: valid user variations of an existing gesture.
    CUSTOM_GESTURE_EVOLUTION_MIN_CONFIDENCE = 0.88
    CUSTOM_GESTURE_EVOLUTION_MIN_SIMILARITY = 0.75
    CUSTOM_GESTURE_EVOLUTION_MAX_SIMILARITY = 0.98
    CUSTOM_GESTURE_EVOLUTION_NOVELTY_THRESHOLD = 0.97
    CUSTOM_GESTURE_EVOLUTION_MIN_OBSERVATIONS = 3
    CUSTOM_GESTURE_EVOLUTION_MAX_PENDING_CLUSTERS = 4

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

    # ------------------------------------------------------------------
    # Connect — isolated two-person gesture communication rooms.
    # Only the /connect page activates room WebSockets, gesture sending and
    # gesture relaying. No conversation payloads are persisted to disk.
    # ------------------------------------------------------------------
    CONNECT_WS_PATH = "/ws/connect"
    CONNECT_ROOM_CODE_PREFIX = "GF"
    CONNECT_ROOM_CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
    CONNECT_ROOM_CODE_LENGTH = 4
    CONNECT_ROOM_MAX_PARTICIPANTS = 2
    CONNECT_ROOM_HISTORY_LIMIT = 80          # Ephemeral in-memory catch-up
    CONNECT_ROOM_IDLE_TTL_SECONDS = 1800     # Purge after 30 min w/o clients
    CONNECT_ROOM_MAX_AGE_SECONDS = 86400     # Hard 24h room lifetime
    CONNECT_ROOM_SWEEP_INTERVAL_SECONDS = 60
    # A gesture must stay stable for this long before it is sent once.
    # Changing or dropping the hand before the hold cancels the send, which
    # prevents the same gesture from being sent on every camera frame.
    CONNECT_GESTURE_HOLD_SECONDS = 2.0
    CONNECT_GESTURE_MIN_STABLE_FRAMES = 4
    CONNECT_CUSTOM_GESTURE_STABLE_FRAMES = 3
    CONNECT_POSE_TRACKING_MIN_QUALITY = 0.90
    CONNECT_LOCAL_PROGRESS_INTERVAL = 0.10   # Local status push throttle (s)

