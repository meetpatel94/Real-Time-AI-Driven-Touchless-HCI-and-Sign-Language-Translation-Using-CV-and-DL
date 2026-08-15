import os
import json
import threading
import time
from typing import Dict, Any
from config import Config
from services.logging_service import logger

class TrainingStateService:
    """Thread-safe persistent training status manager."""
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(TrainingStateService, cls).__new__(cls)
                cls._instance._initialize()
            return cls._instance

    def _initialize(self):
        self.status_file = Config.TRAINING_STATUS_FILE
        os.makedirs(os.path.dirname(self.status_file), exist_ok=True)
        self.state_lock = threading.Lock()
        
        self.state = {
            "status": "IDLE",  # IDLE, PREPARING, TRAINING, COMPLETED, STOPPED, ERROR
            "device": "CPU",
            "current_epoch": 0,
            "total_epochs": Config.TRAINING_EPOCHS_DEFAULT,
            "progress_percent": 0.0,
            "train_accuracy": 0.0,
            "val_accuracy": 0.0,
            "train_loss": 0.0,
            "val_loss": 0.0,
            "elapsed_seconds": 0,
            "estimated_remaining_seconds": 0,
            "best_val_accuracy": 0.0,
            "best_epoch": 0,
            "error_message": "",
            "start_time": 0.0,
            "last_updated": time.time()
        }
        self._load_from_disk()

    def update(self, updates: Dict[str, Any]) -> None:
        with self.state_lock:
            self.state.update(updates)
            self.state["last_updated"] = time.time()
            self._save_to_disk()

    def get_state(self) -> Dict[str, Any]:
        with self.state_lock:
            return self.state.copy()

    def _save_to_disk(self) -> None:
        try:
            with open(self.status_file, "w") as f:
                json.dump(self.state, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to persist training status: {e}")

    def _load_from_disk(self) -> None:
        if os.path.exists(self.status_file):
            try:
                with open(self.status_file, "r") as f:
                    saved_state = json.load(f)
                    if saved_state.get("status") in ["TRAINING", "PREPARING"]:
                        saved_state["status"] = "STOPPED"
                    self.state.update(saved_state)
            except Exception as e:
                logger.error(f"Failed to load training status from disk: {e}")

training_state = TrainingStateService()