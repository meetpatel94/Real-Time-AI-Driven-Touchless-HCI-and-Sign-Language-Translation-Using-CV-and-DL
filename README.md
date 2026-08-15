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