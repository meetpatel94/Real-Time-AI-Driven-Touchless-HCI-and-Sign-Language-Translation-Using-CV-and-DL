import os
import time
import json
import threading
import tensorflow as tf
from typing import Optional
from config import Config
from core.sign_alphabet.dataset_manager import dataset_manager
from core.sign_alphabet.training_state import training_state
from core.sign_alphabet.model import build_mobilenet_v2_classifier
from services.logging_service import logger

class TrainingWorker:
    """Thread-safe background trainer using streaming tf.data pipelines."""
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(TrainingWorker, cls).__new__(cls)
                cls._instance._initialize()
            return cls._instance

    def _initialize(self):
        self.training_thread: Optional[threading.Thread] = None
        self.stop_requested = threading.Event()
        self.is_busy = False

    def detect_device(self) -> str:
        gpus = tf.config.list_physical_devices('GPU')
        return f"GPU ({len(gpus)} detected)" if gpus else "CPU"

    def start_training(self, epochs: int = Config.TRAINING_EPOCHS_DEFAULT, batch_size: int = Config.TRAINING_BATCH_SIZE) -> bool:
        with self._lock:
            if self.is_busy:
                return False

            self.stop_requested.clear()
            self.is_busy = True
            self.training_thread = threading.Thread(
                target=self._run_training_pipeline,
                args=(epochs, batch_size),
                daemon=True
            )
            self.training_thread.start()
            return True

    def stop_training(self) -> None:
        if self.is_busy:
            logger.info("Sign Alphabet Training: Stop requested by user.")
            self.stop_requested.set()

    def _run_training_pipeline(self, total_epochs: int, batch_size: int):
        device_name = self.detect_device()
        dataset_dir = Config.DATASET_BASE_DIR
        model_save_dir = Config.MODEL_DIR
        os.makedirs(model_save_dir, exist_ok=True)

        training_state.update({
            "status": "PREPARING",
            "device": device_name,
            "current_epoch": 0,
            "total_epochs": total_epochs,
            "progress_percent": 0.0,
            "error_message": "",
            "start_time": time.time(),
            "elapsed_seconds": 0
        })

        try:
            # 1. Dataset Verification
            info = dataset_manager.get_dataset_overview()
            if not info["exists"] or info["total_images"] == 0:
                raise ValueError("Dataset is empty or directory was not found.")

            image_size = Config.DATASET_IMAGE_SIZE
            seed = 42

            # 2. Streaming tf.data Loading without caching all images in RAM
            train_ds = tf.keras.utils.image_dataset_from_directory(
                dataset_dir,
                validation_split=Config.TRAINING_VAL_SPLIT,
                subset="training",
                seed=seed,
                image_size=image_size,
                batch_size=batch_size,
                label_mode="int"
            )

            val_ds = tf.keras.utils.image_dataset_from_directory(
                dataset_dir,
                validation_split=Config.TRAINING_VAL_SPLIT,
                subset="validation",
                seed=seed,
                image_size=image_size,
                batch_size=batch_size,
                label_mode="int"
            )

            class_names = train_ds.class_names
            num_classes = len(class_names)

            # Persist class mapping
            with open(os.path.join(model_save_dir, "class_names.json"), "w") as f:
                json.dump(class_names, f, indent=2)

            # Optimize pipeline for streaming with AUTOTUNE prefetching
            AUTOTUNE = tf.data.AUTOTUNE
            train_ds = train_ds.prefetch(buffer_size=AUTOTUNE)
            val_ds = val_ds.prefetch(buffer_size=AUTOTUNE)

            # 3. Model Construction
            model, base_model = build_mobilenet_v2_classifier(
                input_shape=(image_size[0], image_size[1], 3),
                num_classes=num_classes
            )

            model.compile(
                optimizer=tf.keras.optimizers.Adam(learning_rate=Config.TRAINING_LEARNING_RATE),
                loss=tf.keras.losses.SparseCategoricalCrossentropy(),
                metrics=["accuracy"]
            )

            # 4. Custom Callback for UI Updates & Safe Cancellation
            worker_ref = self
            start_time = time.time()

            class LiveProgressCallback(tf.keras.callbacks.Callback):
                def on_epoch_begin(self, epoch, logs=None):
                    if worker_ref.stop_requested.is_set():
                        self.model.stop_training = True
                        return
                    training_state.update({
                        "status": "TRAINING",
                        "current_epoch": epoch + 1
                    })

                def on_epoch_end(self, epoch, logs=None):
                    logs = logs or {}
                    current_epoch = epoch + 1
                    progress = round((current_epoch / total_epochs) * 100.0, 1)
                    now = time.time()
                    elapsed = int(now - start_time)
                    
                    time_per_epoch = elapsed / current_epoch if current_epoch > 0 else 0
                    remaining = int(time_per_epoch * (total_epochs - current_epoch))

                    t_acc = float(logs.get("accuracy", 0.0))
                    v_acc = float(logs.get("val_accuracy", 0.0))
                    t_loss = float(logs.get("loss", 0.0))
                    v_loss = float(logs.get("val_loss", 0.0))

                    current_state = training_state.get_state()
                    best_v_acc = max(current_state.get("best_val_accuracy", 0.0), v_acc)
                    best_ep = current_epoch if v_acc >= best_v_acc else current_state.get("best_epoch", 1)

                    training_state.update({
                        "current_epoch": current_epoch,
                        "progress_percent": progress,
                        "train_accuracy": round(t_acc * 100.0, 2),
                        "val_accuracy": round(v_acc * 100.0, 2),
                        "train_loss": round(t_loss, 4),
                        "val_loss": round(v_loss, 4),
                        "elapsed_seconds": elapsed,
                        "estimated_remaining_seconds": max(0, remaining),
                        "best_val_accuracy": round(best_v_acc * 100.0, 2),
                        "best_epoch": best_ep
                    })

                    if worker_ref.stop_requested.is_set():
                        self.model.stop_training = True

            best_model_path = os.path.join(model_save_dir, "sign_alphabet_model.keras")
            callbacks = [
                LiveProgressCallback(),
                tf.keras.callbacks.EarlyStopping(monitor="val_accuracy", patience=3, restore_best_weights=True),
                tf.keras.callbacks.ReduceLROnPlateau(monitor="val_loss", factor=0.5, patience=2, min_lr=1e-6),
                tf.keras.callbacks.ModelCheckpoint(filepath=best_model_path, monitor="val_accuracy", save_best_only=True)
            ]

            # 5. Execute Stage 1 Training
            model.fit(
                train_ds,
                validation_data=val_ds,
                epochs=total_epochs,
                callbacks=callbacks
            )

            if self.stop_requested.is_set():
                training_state.update({"status": "STOPPED"})
                logger.info("Training successfully stopped.")
            else:
                # Save final model state
                model.save(best_model_path)
                training_state.update({
                    "status": "COMPLETED",
                    "progress_percent": 100.0,
                    "estimated_remaining_seconds": 0
                })
                logger.info(f"Model training completed and saved to {best_model_path}")

        except Exception as e:
            logger.error(f"Training failed with error: {e}", exc_info=True)
            training_state.update({
                "status": "ERROR",
                "error_message": str(e)
            })
        finally:
            with self._lock:
                self.is_busy = False

trainer = TrainingWorker()