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

### GestureForge Connect (two-person, simultaneous gesture rooms)

The **Connect** section (sidebar → 🤝 Connect, route `/connect`) lets two people on
different devices/browsers communicate as a true bidirectional room:

`User 1 local camera → local MediaPipe/custom matching → gesture meaning → WebSocket → User 2`
`User 2 local camera → local MediaPipe/custom matching → gesture meaning → WebSocket → User 1`

* **Rooms and roles** — the creator clicks **Create Room** and is clearly shown as
  **User 1 / Creator**. The joiner enters the short code (for example `GF-K82M`)
  and is shown as **User 2 / Joiner**. A room holds exactly two participants and
  expires after a bounded idle TTL; room state and history are ephemeral in memory.
* **Independent devices** — each browser requests and processes its own camera
  locally. Both camera and recognition states can be active simultaneously. No
  camera ownership, request queue, handover, or turn-taking state exists in the
  live Connect path.
* **Lightweight transport** — WebSocket messages contain only recognized gesture
  ids/meanings/symbols, confidence metadata, or text. Webcam frames and continuous
  landmarks never cross the WebSocket. The existing Custom Gesture library's
  saved derived features remain the source of truth; Connect does not create a
  second gesture database or recognition model.
* **Gesture behavior** — built-in poses include ☝️ Hii, 👍 Okay, ✌️ Peace, ✊ Stop,
  and more. Saved custom gestures win over built-ins. Temporal stability,
  confidence thresholds, a roughly two-second hold-to-send gate, and duplicate
  suppression are applied independently on each device.
* **Shared conversation** — gesture and text events from both users appear in the
  same timeline. Received custom gestures keep **▶ Replay Gesture**, backed by
  `GET /api/connect/replay/<gesture_id>` and the existing saved samples.
* **Isolation and recovery** — only the `/connect` page opens its room socket and
  local recognition loop. Other modules are unchanged. Invalid/expired rooms,
  room-full protection, session mismatch, camera errors, and socket drops are
  reported; reconnect resumes the same room and role.
* **Transport** — the relay uses Flask-Sock on the same Flask dev-server port
  (`flask-sock==0.7.0`); no polling is used for communication.

### Two-Device HTTPS LAN Testing (PC + phone on the same Wi-Fi)

Mobile browsers only allow `navigator.mediaDevices.getUserMedia()` (camera
access) from a **secure context**. `https://127.0.0.1` counts as secure, but
a plain `http://192.168.x.x` LAN address does not — so a phone opening the
HTTP LAN URL sees *"This browser does not provide a local camera."* while the
PC (using `127.0.0.1`) works fine.

By default `python app.py` serves **plain HTTP on `0.0.0.0:5000`**, so the
page is reachable from every interface: `http://127.0.0.1:5000/connect` on
the PC and `http://<LAN-IP>:5000/connect` from the same PC or another device
(subject to the PC firewall). A TLS-only port would break those plain-HTTP
LAN URLs — an HTTPS dev port is therefore always opt-in, never a silent
default. For the phone-camera step, enable HTTPS with option 1 or 2 below;
only the transport changes to HTTPS/WSS. Gesture recognition, MediaPipe,
Custom Gestures, the WebSocket room/relay architecture, and all existing
Connect UI behavior are unchanged.

1. **Install/generate the development certificate (recommended: mkcert).**
   Certificates are never committed to the repo (see `.gitignore`); each
   developer generates their own local certificate:

   ```bash
   # one-time install of mkcert: https://github.com/FiloSottile/mkcert#installation
   mkcert -install

   # generate a certificate that covers localhost AND your PC's LAN IP
   # (find your LAN IP first — see step 3), e.g.:
   mkcert -cert-file certs/gestureforge-lan-cert.pem \
          -key-file certs/gestureforge-lan-key.pem \
          127.0.0.1 localhost 192.168.29.98
   ```

   If you skip this step, either set `GESTUREFORGE_FORCE_HTTPS=1` when
   starting the app to use Flask's **ad-hoc self-signed certificate**
   (`pip install pyopenssl`, already in `requirements.txt`) — a
   **DEVELOPMENT / LAN TESTING ONLY** fallback, not a production security
   solution; each browser will show a one-time "connection is not private"
   warning; choose *Advanced → Proceed* to continue. Without certificate
   files or that flag the server stays on plain HTTP, which cannot give the
   phone camera access (browsers require a secure context). See
   `certs/README.md` for details and troubleshooting.

2. **Start GestureForge** on the PC:

   ```bash
   pip install -r requirements.txt
   python app.py
   ```

   The startup banner prints (scheme reflects the active mode):

   ```
   GestureForge server started

   Local:
   http://127.0.0.1:5000

   LAN:
   http://192.168.29.98:5000

   Connect:
   http://192.168.29.98:5000/connect
   ```

   with the LAN IP auto-detected. In HTTPS mode the same URLs use
   `https://` and the banner reminds you: **"Use the HTTPS LAN URL on the
   second device."** Quick reachability check from any device:
   `http://<LAN-IP>:5000/api/connect/health` should answer
   `{"ok": true, ...}`.

3. **Find the PC's LAN IP** if it wasn't auto-detected: `ipconfig` (Windows)
   or `ip addr` / `ifconfig` (Linux/macOS), e.g. `192.168.29.98`.

4. **Connect PC and phone to the same Wi-Fi network** (and make sure the
   router does not enable AP/client isolation, which blocks device-to-device
   traffic; also allow Python through the PC's firewall).

5. **Open `https://<LAN-IP>:5000/connect` on both devices** — the PC can
   also use `https://127.0.0.1:5000/connect`.

6. **Allow camera permission on both devices** when the browser prompts —
   this normal permission prompt only appears because the page is now served
   over HTTPS.

7. **PC creates the room** (**Create Room**, note the code, e.g. `GF-DH6G`).

8. **Mobile joins using the room code** (enter the code → **Join Room**).

Both devices talk to the same Flask + WebSocket server. The client builds the
WebSocket URL from `window.location.protocol`/`window.location.host` (see
`static/js/connect/connect.js`), so opening the page over `https://` makes it
connect with `wss://` automatically — no `ws://`, `127.0.0.1`, or `localhost`
is ever hardcoded. Both sides show **Other User → Connected**; both local
cameras run MediaPipe/gesture recognition independently and relay gestures in
both directions (User 1 ⇄ User 2), while text messages keep working the same
way as before.

**Important:** plain `http://<LAN-IP>:5000/connect` may still load the page,
but mobile browsers will typically refuse camera access on that insecure
origin — always use the `https://` LAN URL on the second device.

To force plain HTTP while a certificate pair exists in `certs/` (e.g. behind
an external HTTPS-terminating proxy), set `GESTUREFORGE_DISABLE_HTTPS=1`
before starting the app. HTTPS without certificate files is opt-in via
`GESTUREFORGE_FORCE_HTTPS=1` (ad-hoc self-signed).

### MongoDB configuration

Set `MONGODB_URI` and `MONGODB_DATABASE` in the environment (the conventional `MONGO_URI`, `MONGODB_DB_NAME`, `MONGO_DB_NAME`, or `MONGO_DATABASE` aliases are also accepted). Optional bounded timeout settings are `MONGODB_SERVER_SELECTION_TIMEOUT_MS`, `MONGODB_CONNECT_TIMEOUT_MS`, `MONGODB_SOCKET_TIMEOUT_MS`, and `MONGODB_MAX_POOL_SIZE`. The application lazily pings MongoDB, creates validators/indexes, and reports storage health without making camera startup depend on the server.

All legacy routes, camera controls, sentence actions, translation endpoints, sign recognition endpoints, and dataset/training endpoints remain available. If MongoDB is unavailable, these additions report degraded storage while the base camera, MediaPipe, A–Z, sentence, translation, cursor, click, scroll, dataset, and model-testing behavior continues unchanged.
