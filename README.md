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

`MediaPipe landmarks → GestureObservationBuilder → MotionTracker → UnknownGestureDetector → ContextAwareIntentInterpreter → runtime state/history`

* **Personalized User Profile** — `models/user_profile.py`, `services/user_profile_service.py`, and the repository layer persist preferences and lightweight usage counters. The local app uses SQLite (`data/gestureforge.sqlite3`) because it is a single-camera desktop-style application; no MongoDB service is required. A repository adapter can be added later for a multi-user deployment.
* **Unknown Gesture Detection** — `services/unknown_gesture_service.py` combines existing gesture geometry, sign-model confidence, tracking quality, and temporal persistence. Unknown observations are surfaced with a reason and never issue an OS command.
* **Context-Aware Intent Interpretation** — `services/intent_interpretation_service.py` produces explainable intent candidates using gesture evidence, motion direction/speed, active workspace, sentence/sign context, profile mode, and recent user-specific intent history. The existing engine remains the executor for backward compatibility, making this an explicit seam for a learned policy in the next phase.
* **Bounded history** — only meaningful intent/unknown transitions are persisted, not webcam frames or every 30 FPS observation. `database/sqlite_database.py`, `repositories/`, and `services/interaction_history_service.py` keep storage out of routes and computer-vision code.

### Adaptive API surface

* `GET /api/profile` and `PATCH /api/profile` — read/update the active local profile.
* `POST /api/profile/reset` — reset preferences while retaining interaction history.
* `GET /api/adaptive/status` — profile plus the current gesture, temporal, unknown-detector, and intent snapshot.
* `GET /api/adaptive/events` — inspect persisted adaptive transitions.
* `POST /api/adaptive/feedback` — attach explicit feedback to a recorded event for future personalization phases.

All legacy routes, camera controls, sentence actions, translation endpoints, sign recognition endpoints, and dataset/training endpoints remain available.
