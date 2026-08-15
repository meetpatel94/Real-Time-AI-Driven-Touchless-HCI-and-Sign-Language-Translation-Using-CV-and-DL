import os
import json
import pickle
import threading
import numpy as np
import tensorflow as tf
from typing import List, Optional
from config import Config
from services.logging_service import logger
from core.recognition.recognition_state import recognition_state

class ModelManager:
    """Singleton model loader and optimized inference graph executor."""
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(ModelManager, cls).__new__(cls)
                cls._instance._initialize()
            return cls._instance

    def _initialize(self):
        self.model: Optional[tf.keras.Model] = None
        self.predict_fn = None
        self.classes: List[str] = [chr(i) for i in range(ord('A'), ord('Z') + 1)]
        self.load_model()

    def load_model(self):
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        model_paths = [
            getattr(Config, "RECOGNITION_MODEL_PATH", os.path.join(base_dir, "models", "gesture_model.h5")),
            getattr(Config, "RECOGNITION_ALT_MODEL_PATH", os.path.join(base_dir, "models", "sign_alphabet", "sign_alphabet_model.keras"))
        ]

        loaded_path = None
        for p in model_paths:
            if os.path.isfile(p):
                loaded_path = p
                break

        if not loaded_path:
            recognition_state.set_model_status("MODEL NOT FOUND", "None")
            logger.warning("ModelManager: No model file found.")
            return

        try:
            logger.info(f"ModelManager: Loading model from {loaded_path}...")
            self.model = tf.keras.models.load_model(loaded_path, compile=False)
            model_name = os.path.basename(loaded_path)
            
            # Compile optimized execution graph with tf.function for low-overhead CPU inference
            @tf.function(reduce_retracing=True)
            def _fast_predict(tensor):
                return self.model(tensor, training=False)

            self.predict_fn = _fast_predict

            # Warm-up pass
            dummy_input = tf.zeros((1, 160, 160, 3), dtype=tf.float32)
            _ = self.predict_fn(dummy_input)
            
            recognition_state.set_model_status("READY", model_name)
            logger.info("ModelManager: Model loaded and optimized execution graph ready.")
        except Exception as e:
            recognition_state.set_model_status("MODEL ERROR", "None")
            logger.error(f"ModelManager: Failed to load model: {e}")

        # Load Class Mappings
        label_enc_path = getattr(Config, "LABEL_ENCODER_PATH", os.path.join(base_dir, "models", "label_encoder.pkl"))
        class_json_path = getattr(Config, "CLASS_NAMES_JSON_PATH", os.path.join(base_dir, "models", "sign_alphabet", "class_names.json"))

        if os.path.isfile(label_enc_path):
            try:
                with open(label_enc_path, "rb") as f:
                    self.classes = pickle.load(f)
            except Exception as e:
                logger.error(f"ModelManager: Error loading label encoder: {e}")
        elif os.path.isfile(class_json_path):
            try:
                with open(class_json_path, "r") as f:
                    self.classes = json.load(f)
            except Exception as e:
                logger.error(f"ModelManager: Error loading class names: {e}")

    def predict(self, input_tensor: np.ndarray) -> np.ndarray:
        if self.predict_fn is None:
            return np.zeros((len(self.classes),), dtype=np.float32)
        
        tensor = tf.convert_to_tensor(input_tensor, dtype=tf.float32)
        preds = self.predict_fn(tensor)
        return preds.numpy()[0]

model_manager = ModelManager()