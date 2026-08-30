# ⚡ GestureForge AI

**GestureForge AI** is an AI-powered human-computer interaction (HCI) platform supporting real-time hand gesture interactions, global OS-level cursor control, sign alphabet dataset collection, model training, and low-latency sign language recognition.

---

## 🌟 Key Features

* **Global Air Gestures (OS-Level Control):**
  * ☝️ **One Finger (Index):** Smooth, low-latency control of the physical Windows/OS mouse cursor across all monitors and applications.
  * ☝️+🖕 **Two Fingers (Index + Middle):** Centralized dwell selection ($0\% \rightarrow 100\%$) triggering native OS left clicks with anti-repeat cooldown locks.
* **Sign Alphabet Recognition:**
  * Real-time inference using an optimized **MobileNetV2 Transfer Learning** model ($160 \times 160$ input, $26$ A–Z classes).
  * Decoupled producer-consumer inference pipeline (~$10$ FPS inference rate) preventing webcam MJPEG stuttering.
  * Rolling 3-frame temporal smoothing for instant gesture transitions ($A \rightarrow B$).
* **Dataset Collection & Management:**
  * Automated bounding box calculation and square letterboxed capture ($160 \times 160$ RGB JPEG).
  * Dynamic letter progress tracking, class resets, and automatic class completion thresholds.
* **Model Training Engine:**
  * Streaming `tf.data` training with `AUTOTUNE` prefetching, lightweight augmentation layers, `EarlyStopping`, and `ReduceLROnPlateau`.
* **Model Evaluation Suite:**
  * Dedicated evaluation tooling (`evaluation/evaluate_model.py`) generating confusion matrices, class metric breakdowns, and full HTML evaluation reports.

---

## 🛠️ Technology Stack

* **Backend & Web Server:** Python 3.9+, Flask (Application Factory & Blueprints)
* **Personalization Store:** MongoDB via PyMongo (the required persistence target; runtime degrades safely when unavailable)
* **Computer Vision & Tracking:** OpenCV, MediaPipe Hands
* **Deep Learning Framework:** TensorFlow 2.x, Keras (MobileNetV2)
* **OS Automation:** PyAutoGUI
* **Metrics & Plotting:** Scikit-learn, Pandas, Matplotlib, Seaborn
* **Frontend:** Vanilla JavaScript (ES6+), HTML5, CSS3 (Modular Layout)

---

## 📂 Project Architecture

The existing runtime remains the source of truth for camera ownership and command execution:

`CameraManager → GestureEngine → MediaPipe Hands → existing mouse/sign controllers → Flask state APIs`

Sign recognition continues to use the decoupled producer/consumer path:

`GestureEngine → InferenceWorker → Predictor/ModelManager → RecognitionState → Studio/Recognition clients`

### Human-adaptive foundation

The upgrade adds an independent reasoning layer at the MediaPipe boundary without replacing either pipeline:

`MediaPipe landmarks → GestureObservationBuilder → MotionTracker/DerivedFeatureExtractor → PersonalizationService.match → UnknownGestureDetector → ContextAwareIntentInterpreter → runtime state/history`

* **Personalized User Profile** — `models/user_profile.py`, `services/user_profile_service.py`, and the MongoDB repository layer persist preferences and lightweight usage counters. `MONGODB_URI` and `MONGODB_DATABASE` configure the store; bounded timeouts and fail-open handling keep the legacy runtime available when MongoDB is unavailable.
* **Personalized Gesture Learning** — `core/adaptive/feature_extractor.py` derives normalized geometry, proportions, trajectories, speed, duration, and temporal movement characteristics. Only explicitly accepted stable calibration samples and validated corrections can update `learned_gestures`; raw landmarks, webcam video, recordings, the A–Z dataset, and the trained base model are never stored in MongoDB.
* **Confidence-gated matching** — `services/personalization_service.py` requires validated evidence, falls back to the base model for low-confidence/unstable matches, and never overrides a reliable base prediction. User mappings for Back, Scroll Up/Down, and Click execute through the existing mouse controllers only when Adaptive Mode and gesture controls are enabled.
* **Unknown Gesture Detection** — `services/unknown_gesture_service.py` combines existing gesture geometry, sign-model confidence, tracking quality, and temporal persistence. Unknown observations are surfaced with a reason and never issue an OS command.
* **Context-Aware Intent Interpretation** — `services/intent_interpretation_service.py` produces explainable intent candidates using gesture evidence, motion direction/speed, active workspace, sentence/sign context, profile mode, recent history, and the personalized decision. The existing engine remains the executor for backward compatibility.
* **Bounded history** — only meaningful intent/unknown transitions are persisted, not webcam frames or every 30 FPS observation. `database/mongo_database.py`, `repositories/`, and `services/interaction_history_service.py` keep storage out of routes and computer-vision code. `database/migrate_sqlite_to_mongo.py` is a one-time, opt-in importer for legacy installs; SQLite is not used at runtime.

### Adaptive API surface

* `GET /api/profile` and `PATCH /api/profile` — read/update the active local profile.
* `POST /api/profile/reset` — reset preferences while retaining interaction history.
* `GET /api/adaptive/status` — profile plus the current gesture, temporal, unknown-detector, and intent snapshot.
* `GET /api/adaptive/events` — inspect persisted adaptive transitions.
* `POST /api/adaptive/feedback` — attach explicit feedback to a recorded event.
* `GET /api/personalization` or `/api/personalization/status` — retrieve learned gesture/mapping metadata and storage/privacy status.
* `POST /api/personalization/calibration/start`, `/sample`, `/complete` — start, explicitly capture, and complete stable calibration.
* `GET|POST /api/personalization/corrections` — inspect or explicitly validate a correction; ordinary predictions never write corrections.
* `GET|POST /api/personalization/mappings` and `DELETE /api/personalization/mappings/<id>` — manage user-owned Back, Scroll Up/Down, and Click mappings.
* `POST /api/personalization/reset` — remove this profile's calibration, learned gestures, corrections, mappings, and adaptive interaction history (the separate profile reset still retains history).

### MongoDB configuration

Set `MONGODB_URI` and `MONGODB_DATABASE` in the environment (the conventional `MONGO_URI`, `MONGODB_DB_NAME`, `MONGO_DB_NAME`, or `MONGO_DATABASE` aliases are also accepted). Optional bounded timeout settings are `MONGODB_SERVER_SELECTION_TIMEOUT_MS`, `MONGODB_CONNECT_TIMEOUT_MS`, `MONGODB_SOCKET_TIMEOUT_MS`, and `MONGODB_MAX_POOL_SIZE`. The application lazily pings MongoDB, creates validators/indexes, and reports storage health without making camera startup depend on the server.

All legacy routes, camera controls, sentence actions, translation endpoints, sign recognition endpoints, and dataset/training endpoints remain available. If MongoDB is unavailable, these additions report degraded storage while the base camera, MediaPipe, A–Z, sentence, translation, cursor, click, scroll, dataset, and model-testing behavior continues unchanged.
