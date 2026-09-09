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

### Contextual sentence completion (Sign Language Studio text box)

The studio sentence box suggests **complete phrases and sentences**, not just the next word. Typing (or signing) `I am go` offers `I am going home`, `I am going to school`, `I am going outside`; `Where is` offers `Where is the bathroom?`, `Where is my phone?`, `Where is the hospital?`.

* **Hybrid local language model** — `services/sentence_completion_service.py` combines a hand-ranked phrase bank (`services/phrase_corpus.py`), a curated sentence corpus that also trains a trigram/bigram back-off model, and vocabulary completion for partially typed words. It is pure Python: no new dependency, no GPU, sub-millisecond predictions with an LRU cache.
* **Context aware** — only the clause being completed is used, so multi-sentence text, commas, question marks, mid-text caret edits and backspaces all behave correctly. The trailing word is evaluated both as complete and as still-being-typed, which is what turns `I am go` into `I am going home` instead of `I am go home`.
* **Precision over volume** — candidates are scored, de-duplicated, rejected when they end on a fragment such as `Where is the`, and filtered by a confidence floor that rises as the context gets shorter. Empty, one-letter or unknown input returns nothing; at most three suggestions are ever shown.
* **Personalization as a signal, not an override** — accepted completions are remembered per context in the optional `phrase_memory` MongoDB collection (in-memory fallback otherwise). A remembered phrase is boosted by at most `+0.09` and may only displace the weakest suggestion, so the language model always keeps the leading positions.

API surface:

* `GET /api/studio/completions?text=…&caret=…&limit=3` — ranked completions for the clause ending at `caret` (also returns `clause`, `clause_start`, per-suggestion `confidence`, `source` and `personalized`).
* `POST /api/studio/completions/accept` — remembers `{ text, suggestion, caret }` after the user picks a suggestion.

The client (`static/js/studio/autocomplete.js`) debounces requests (~180 ms), caches responses, and supports ArrowUp/ArrowDown navigation, Enter or Tab to accept, Escape and outside-click to dismiss. It only binds to the studio sentence input, so A–Z recognition, word prediction, custom gestures, gesture DNA, mistake memory, gesture evolution, air mouse, air drawing and global hand scrolling are untouched. The existing word chips below the suggestions remain the word-level predictor.

### GestureForge Connect (two-person real-time gesture rooms)

A new **Connect** section (sidebar → 🤝 Connect, route `/connect`) lets two people on
different devices/browsers talk in a private room using the app's existing camera +
MediaPipe recognition:

`MediaPipe hands (existing engine) → Connect detector (stable + hold-to-send gate) → WebSocket relay → other participant`

* **Rooms** — User A clicks **Create Room** and gets a short code such as `GF-K82M`
  (share via **Copy Code**); User B enters the code and clicks **Join Room**. A room
  holds exactly two participants ("Room is full." otherwise), and codes expire after
  a bounded idle TTL. Room state and history are ephemeral in-memory only.
* **Two panels** — after joining, a desktop grid shows **You** (camera, detected
  gesture, hold progress, seat control) and **Other User** (connection status, last
  gesture, meaning, confidence), plus a shared **timeline** for gestures and text and
  a text input fallback (`[Type a message…] [Send]`).
* **Recognition is reused, not duplicated** — Connect consumes the landmark results
  already produced by `core/gestures/gesture_engine.py`; it never opens a second
  camera pipeline and it never uploads frames. Only lightweight JSON events
  (`gesture_id`, meaning/symbol, timestamp, small metadata) travel over WebSocket.
* **Saved custom gestures win** — matching runs through the existing Custom Gesture
  library (`match_frame_read_only` read-only matcher), so a trained
  `☝️ → hii`-style mapping relays exactly like the Custom Gestures page. Built-in
  defaults exist for untrained poses (☝️ Hii, 👍 Okay, ✌️ Peace, ✊ Stop, …).
* **Duplicate prevention** — a gesture must be stable for N frames and then held
  (~2 s) before it is sent **once**; re-sending requires the hand to drop/change and
  the gesture to be recognized again.
* **Gesture replay** — received custom gestures show **▶ Replay Gesture**, which
  animates the mean normalized landmark track of the real saved samples via
  `GET /api/connect/replay/<gesture_id>` (404 when no sample exists → name/symbol fallback).
* **Turn taking** — the camera feed seat is exclusive. The other participant's send
  request is queued and granted automatically when the current sender releases the
  feed, so both users can gesture in turn.
* **Isolation** — all Connect processing is gated on `active_module == "connect"` and
  an active room seat. Opening Overview, Recognition, Translation, Custom Gestures,
  Studio, Air Drawing etc. never starts Connect rooms, sockets, or gesture sending;
  leaving `/connect` closes the socket, releases the seat and resets the detector.
* **Failure handling** — invalid/expired room, room full, session mismatch, seat busy,
  camera off, and socket drops are reported with clear messages; the client
  reconnects automatically (`🔄 Reconnecting…`) and resumes the same room/role.
* **Transport** — the relay uses Flask-Sock on the same Flask dev-server port
  (`flask-sock==0.7.0` added to `requirements.txt`); no polling is used for
  communication.

### Running the two-device LAN test (PC + phone on the same Wi-Fi)

The dev server is a local-only Flask server; it binds `0.0.0.0` so other
devices on your LAN can reach it (do **not** expose it beyond your LAN):

1. Install dependencies and start the server on the PC:

   ```bash
   pip install -r requirements.txt
   python app.py
   ```

   The startup log prints the LAN URL (e.g. `http://192.168.1.105:5000/connect`).
   If it does not, find the PC's LAN IP with `ipconfig` (Windows) or
   `ip addr` (Linux/macOS) and use `http://<LAN-IP>:5000/connect`.

2. **Device A (PC):** open `http://<LAN-IP>:5000/connect` → **Create Room**
   and note the code shown (e.g. `GF-DH6G`) → **📋 Copy Code**.

3. **Device B (phone):** on the same Wi-Fi, open the *same*
   `http://<LAN-IP>:5000/connect` → enter the room code → **Join Room**.

Both devices talk to the same Flask + WebSocket server: the WebSocket URL is
built from `window.location` (protocol + host of the opened page), so no
`127.0.0.1`/`localhost` is hardcoded in the Connect client. Both sides show
**Other User → Connected**; gestures (seat holder only, take turns) and text
messages relay in both directions.

Troubleshooting: allow Python through the PC's firewall, keep both devices on
the same network/subnet, and check that the router does not enable
AP/client-isolation (which blocks device-to-device traffic).

### MongoDB configuration

Set `MONGODB_URI` and `MONGODB_DATABASE` in the environment (the conventional `MONGO_URI`, `MONGODB_DB_NAME`, `MONGO_DB_NAME`, or `MONGO_DATABASE` aliases are also accepted). Optional bounded timeout settings are `MONGODB_SERVER_SELECTION_TIMEOUT_MS`, `MONGODB_CONNECT_TIMEOUT_MS`, `MONGODB_SOCKET_TIMEOUT_MS`, and `MONGODB_MAX_POOL_SIZE`. The application lazily pings MongoDB, creates validators/indexes, and reports storage health without making camera startup depend on the server.

All legacy routes, camera controls, sentence actions, translation endpoints, sign recognition endpoints, and dataset/training endpoints remain available. If MongoDB is unavailable, these additions report degraded storage while the base camera, MediaPipe, A–Z, sentence, translation, cursor, click, scroll, dataset, and model-testing behavior continues unchanged.
