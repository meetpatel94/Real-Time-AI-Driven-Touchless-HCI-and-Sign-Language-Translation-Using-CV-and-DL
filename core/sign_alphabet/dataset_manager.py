import os
import glob
from typing import Dict, Any, List, Optional
from config import Config
from services.logging_service import logger

class DatasetManager:
    """Manages dataset inspection, class enumeration, and size calculation without in-RAM loading."""
    
    def __init__(self, dataset_dir: str = Config.DATASET_BASE_DIR):
        self.dataset_dir = dataset_dir

    def get_dataset_overview(self) -> Dict[str, Any]:
        if not os.path.exists(self.dataset_dir):
            return {
                "exists": False,
                "total_images": 0,
                "total_classes": 0,
                "classes": {},
                "dataset_size_mb": 0.0,
                "val_split": Config.TRAINING_VAL_SPLIT,
                "training_images": 0,
                "validation_images": 0
            }

        class_counts: Dict[str, int] = {}
        total_size_bytes = 0
        valid_extensions = {".jpg", ".jpeg", ".png", ".bmp"}

        # Scan directories without loading image payloads
        for item in sorted(os.listdir(self.dataset_dir)):
            item_path = os.path.join(self.dataset_dir, item)
            if os.path.isdir(item_path):
                img_count = 0
                for entry in os.scandir(item_path):
                    if entry.is_file():
                        _, ext = os.path.splitext(entry.name)
                        if ext.lower() in valid_extensions:
                            img_count += 1
                            total_size_bytes += entry.stat().st_size
                class_counts[item] = img_count

        total_images = sum(class_counts.values())
        total_classes = len(class_counts)
        dataset_size_mb = round(total_size_bytes / (1024 * 1024), 2)
        val_images = int(total_images * Config.TRAINING_VAL_SPLIT)
        train_images = total_images - val_images

        return {
            "exists": True,
            "total_images": total_images,
            "total_classes": total_classes,
            "classes": class_counts,
            "dataset_size_mb": dataset_size_mb,
            "val_split": Config.TRAINING_VAL_SPLIT,
            "training_images": train_images,
            "validation_images": val_images
        }

    def get_class_preview(self, class_name: str) -> Optional[str]:
        """Returns the relative file path of the first sample in a class folder."""
        class_dir = os.path.join(self.dataset_dir, class_name)
        if not os.path.isdir(class_dir):
            return None

        valid_extensions = ("*.jpg", "*.jpeg", "*.png")
        for ext in valid_extensions:
            samples = glob.glob(os.path.join(class_dir, ext))
            if samples:
                return samples[0]
        return None

dataset_manager = DatasetManager()