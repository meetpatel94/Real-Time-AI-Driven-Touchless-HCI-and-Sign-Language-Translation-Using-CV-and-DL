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

### Two-Device Camera Testing over LAN

Test the real two-device setup: **PC = User 1 (creator)** and **Mobile =
User 2 (joiner)**, both on the same Wi-Fi. Each device uses its **own local
camera** (`navigator.mediaDevices.getUserMedia()`); camera capture and
gesture recognition run locally on each device. Only the recognized
gesture/result (or text) crosses the WebSocket relay — **never webcam
video**.

**Why HTTPS is required for the phone:** mobile browsers only allow
`getUserMedia()` (camera access) from a **secure context**.
`https://127.0.0.1` counts as secure, but a plain `http://192.168.x.x` LAN
address does not — so a phone opening the HTTP LAN URL is told
*"Camera access requires HTTPS when using a LAN address"* (the Connect page
detects the insecure context and explains this instead of the misleading
"This browser does not provide a local camera."). The PC on
`127.0.0.1` is unaffected.

**Development scope:** this is local LAN development infrastructure only —
**not production HTTPS**, and the dev server must never be exposed to the
public internet. No webcam frames are stored or uploaded; the existing
privacy model is unchanged.

#### Setup (PC, one-time per network)

1. **Install mkcert** (a tool that creates locally trusted certificates):
   https://github.com/FiloSottile/mkcert#installation
   * Windows: `winget install FiloSottile.mkcert` (or `choco install mkcert`)
   * macOS: `brew install mkcert`
   * Linux: see the mkcert README (download or distro package)
2. **Install the local mkcert CA** once (into the PC's OS/browser trust
   store):

   ```bash
   mkcert -install
   ```
3. **Generate the certificate** — it must cover `127.0.0.1`, `localhost`,
   **and** your current LAN IP (the helper detects the LAN IP dynamically,
   nothing is hardcoded):

   ```bash
   python scripts/generate_lan_certificate.py
   ```

   This runs `mkcert 127.0.0.1 localhost <LAN-IP>` (via the `-cert-file` /
   `-key-file` flags) and places the files inside `certs/` with the exact
   names the server auto-detects:

   ```
   certs/gestureforge-lan-cert.pem
   certs/gestureforge-lan-key.pem
   ```

   If your LAN IP changes (different Wi-Fi), re-run the script.
4. **Start GestureForge** on the PC:

   ```bash
   pip install -r requirements.txt
   python app.py
   ```

   With the certificate pair present, the server **automatically starts
   with HTTPS** and prints:

   ```
   GestureForge server started

   Transport mode: HTTPS (local mkcert development certificate)

   Local:
   https://127.0.0.1:5000

   LAN:
   https://192.168.29.98:5000

   Connect:
   https://192.168.29.98:5000/connect
   ```

   Without certificate files it stays on plain HTTP on `0.0.0.0:5000`
   (`http://127.0.0.1:5000` desktop development keeps working; the phone
   camera simply needs the certificate step above).
5. **If the phone cannot reach the server:** allow inbound **TCP port 5000**
   for Python through the PC's **Windows Firewall**. On first LAN connection
   Windows usually shows a dialog ("Windows Firewall has blocked some
   features of this app") — tick *Private networks* and click *Allow
   access*. Or add the rule manually (elevated terminal):

   ```bat
   netsh advfirewall firewall add rule name="GestureForge LAN (TCP 5000)" dir=in action=allow protocol=TCP localport=5000
   ```

#### Test run (both devices)

6. **PC opens** `https://<LAN-IP>:5000/connect` (the PC may also use
   `https://127.0.0.1:5000/connect`).
7. **Mobile is connected to the same Wi-Fi** network as the PC (and the
   router must not enable AP/client isolation, which blocks
   device-to-device traffic).
8. **Mobile opens the same HTTPS URL** — `https://<LAN-IP>:5000/connect` —
   in Chrome (or another modern browser).
9. **Mobile must trust the mkcert CA** (see below). The page must show a
   normal green padlock, not a certificate warning.
10. **Allow the camera permission** when each browser prompts for it.
11. **PC creates the room** (**Create Room** — the PC is shown as
    **You / User 1 / Creator**, note the code, e.g. `GF-DH6G`).
12. **Mobile joins using the room code** (enter the code → **Join Room** —
    the phone is shown as **You / User 2 / Joiner**).

Both sides now show **Other User** (User 2 on the PC, User 1 on the phone)
as connected. Each device can independently turn its camera ON/OFF, enable
recognition, show and send gestures, and send/receive text. The WebSocket URL
is built from `window.location.protocol` / `window.location.host`
(`static/js/connect/connect.js`), so the HTTPS page automatically uses
`wss://` — no `ws://`, IP, or host is hardcoded. Quick reachability/transport
check from any device: `https://<LAN-IP>:5000/api/connect/health` answers
`{"ok": true, "status": "healthy", "transport": "https", "ws_path": "/ws/connect"}`.

#### Mobile certificate trust (Android)

The phone **must trust the mkcert CA**; proceeding past a certificate
warning is not a reliable substitute, because `getUserMedia` needs a
*trusted* secure context. Practical steps:

1. **Locate the CA on the PC** — run `mkcert -CAROOT` and open the folder
   it prints (typically `%USERPROFILE%\.local\share\mkcert` on Windows,
   `~/.local/share/mkcert` on Linux, `~/Library/Application Support/mkcert`
   on macOS). The CA file is `rootCA.pem`.
2. **Copy `rootCA.pem` to the phone** by any file-transfer method (USB,
   email, a chat app, or a quick local share on the same Wi-Fi).
3. On the phone: **Settings → Security → Encryption & credentials (or
   "Installed certificates") → Install a certificate → CA certificate**,
   select the copied `rootCA.pem`, name it (e.g. `gestureforge-mkcert`) and
   confirm. On Android 13+ the same option lives under
   **Settings → Security → Additional settings → Install a certificate**.
4. **Fully close the mobile browser and reopen**
   `https://<LAN-IP>:5000/connect` (some browsers cache the trust decision).
   The padlock should be green and the camera permission prompt should
   appear when you turn the camera on.

If the phone still reports a certificate problem: confirm the certificate
was installed as a **CA** certificate (not a client certificate), that the
file used was the mkcert `rootCA.pem` (not the leaf certificate), and that
it hasn't expired (mkcert CAs are valid for ~3 years, leaf certificates ~2
years). The most reliable test browser is the device's default Chrome.

**iOS note:** user CA installation on iOS requires installing a profile that
wraps `rootCA.pem` as a trusted root; iOS camera access over a self-signed
LAN certificate is also stricter than Android's. For the two-device test,
an Android device with the mkcert CA installed is the recommended
companion device.

#### Troubleshooting the camera

The Connect page reports specific, actionable camera states (the camera area
keeps the last message visible):

* **Insecure context** — *"Camera access requires HTTPS when using a LAN
  address. Open GestureForge using the HTTPS LAN address."* → use the
  `https://` URL (certificate steps above).
* **Permission denied** — allow camera access for the site in the browser's
  site settings, then turn the camera on again.
* **No camera found** — enable/connect a camera (the front camera is
  preferred, never forced; any camera works).
* **Camera already in use** — close the other app holding the camera.
* **Browser without `getUserMedia`** — *"This browser does not provide a
  local camera."* is shown only for that genuine case; use a modern browser.

### MongoDB configuration

Set `MONGODB_URI` and `MONGODB_DATABASE` in the environment (the conventional `MONGO_URI`, `MONGODB_DB_NAME`, `MONGO_DB_NAME`, or `MONGO_DATABASE` aliases are also accepted). Optional bounded timeout settings are `MONGODB_SERVER_SELECTION_TIMEOUT_MS`, `MONGODB_CONNECT_TIMEOUT_MS`, `MONGODB_SOCKET_TIMEOUT_MS`, and `MONGODB_MAX_POOL_SIZE`. The application lazily pings MongoDB, creates validators/indexes, and reports storage health without making camera startup depend on the server.

All legacy routes, camera controls, sentence actions, translation endpoints, sign recognition endpoints, and dataset/training endpoints remain available. If MongoDB is unavailable, these additions report degraded storage while the base camera, MediaPipe, A–Z, sentence, translation, cursor, click, scroll, dataset, and model-testing behavior continues unchanged.
