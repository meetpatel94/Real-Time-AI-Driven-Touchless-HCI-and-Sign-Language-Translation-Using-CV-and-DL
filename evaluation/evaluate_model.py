import os
import sys
import gc
import json
import io
import pickle
import numpy as np
import pandas as pd
import tensorflow as tf
import matplotlib.pyplot as plt
import seaborn as sns
from sklearn.metrics import classification_report, confusion_matrix, precision_recall_fscore_support

# Ensure project root is on sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config import Config

# Configuration Constants
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "evaluation_results")
os.makedirs(OUTPUT_DIR, exist_ok=True)
RANDOM_SEED = 42
BATCH_SIZE = 32
IMAGE_SIZE = (160, 160)
VAL_SPLIT = 0.20

def find_model_path():
    candidates = [
        getattr(Config, "RECOGNITION_MODEL_PATH", os.path.join(PROJECT_ROOT, "models", "gesture_model.h5")),
        getattr(Config, "RECOGNITION_ALT_MODEL_PATH", os.path.join(PROJECT_ROOT, "models", "sign_alphabet", "sign_alphabet_model.keras")),
        os.path.join(PROJECT_ROOT, "models", "sign_alphabet_model.keras"),
        os.path.join(PROJECT_ROOT, "models", "gesture_model.h5")
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    return None

def find_dataset_dir():
    candidates = [
        getattr(Config, "DATASET_BASE_DIR", os.path.join(PROJECT_ROOT, "data", "dataset", "sign_alphabet")),
        os.path.join(PROJECT_ROOT, "data", "dataset", "sign_alphabet"),
        os.path.join(PROJECT_ROOT, "dataset", "sign_alphabet"),
        os.path.join(PROJECT_ROOT, "dataset", "alphabet")
    ]
    for d in candidates:
        if os.path.isdir(d):
            return d
    return None

def find_labels(dataset_classes):
    enc_path = getattr(Config, "LABEL_ENCODER_PATH", os.path.join(PROJECT_ROOT, "models", "label_encoder.pkl"))
    json_path = getattr(Config, "CLASS_NAMES_JSON_PATH", os.path.join(PROJECT_ROOT, "models", "sign_alphabet", "class_names.json"))
    
    if os.path.isfile(enc_path):
        try:
            with open(enc_path, "rb") as f:
                return pickle.load(f)
        except Exception:
            pass
    if os.path.isfile(json_path):
        try:
            with open(json_path, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return dataset_classes

def print_header(title):
    print("\n" + "=" * 50)
    print(f" {title}")
    print("=" * 50)

def main():
    print_header("GESTUREFORGE AI - MODEL EVALUATION")
    
    dataset_dir = find_dataset_dir()
    if not dataset_dir or not os.path.exists(dataset_dir):
        print(f"[-] ERROR: Dataset directory not found. Looked in: {dataset_dir}")
        sys.exit(1)

    model_path = find_model_path()
    if not model_path or not os.path.exists(model_path):
        print(f"[-] ERROR: Trained model file not found.")
        sys.exit(1)

    # 1. Dataset Class Enumeration
    class_counts = {}
    valid_exts = {".jpg", ".jpeg", ".png", ".bmp"}
    for c in sorted(os.listdir(dataset_dir)):
        c_path = os.path.join(dataset_dir, c)
        if os.path.isdir(c_path):
            imgs = [f for f in os.listdir(c_path) if os.path.splitext(f)[1].lower() in valid_exts]
            class_counts[c] = len(imgs)

    total_images = sum(class_counts.values())
    total_classes = len(class_counts)
    val_count = int(total_images * VAL_SPLIT)
    train_count = total_images - val_count

    print(f"\nDataset Information:")
    print(f"  Location       : {dataset_dir}")
    print(f"  Classes        : {total_classes} (A-Z)")
    print(f"  Total Images   : {total_images:,}")
    print(f"  Train Images   : {train_count:,} ({int((1-VAL_SPLIT)*100)}%)")
    print(f"  Validation     : {val_count:,} ({int(VAL_SPLIT*100)}%)")

    # 2. Model Loading
    print(f"\nLoading Model:")
    print(f"  Model Path     : {model_path}")
    model = tf.keras.models.load_model(model_path, compile=False)
    
    # Extract Model Details
    input_shape = model.input_shape if hasattr(model, 'input_shape') else (None, 160, 160, 3)
    model_size_mb = round(os.path.getsize(model_path) / (1024 * 1024), 2)
    total_params = model.count_params()
    
    # Save Model Summary
    stream = io.StringIO()
    model.summary(print_fn=lambda x: stream.write(x + "\n"))
    model_summary_str = stream.getvalue()
    with open(os.path.join(OUTPUT_DIR, "model_summary.txt"), "w") as f:
        f.write(model_summary_str)

    print(f"  Architecture   : MobileNetV2 Transfer Learning")
    print(f"  Input Shape    : {input_shape[1:]}")
    print(f"  Total Params   : {total_params:,}")
    print(f"  Model Size     : {model_size_mb} MB")

    # 3. Deterministic Validation Dataset Streaming Pipeline
    val_ds = tf.keras.utils.image_dataset_from_directory(
        dataset_dir,
        validation_split=VAL_SPLIT,
        subset="validation",
        seed=RANDOM_SEED,
        image_size=IMAGE_SIZE,
        batch_size=BATCH_SIZE,
        shuffle=False,
        label_mode="int"
    )

    class_names = val_ds.class_names
    class_names = find_labels(class_names)
    num_classes = len(class_names)
    all_label_indices = list(range(num_classes))

    # 4. Stream Evaluation without loading 13,000 images in memory
    print_header("RUNNING INFERENCE ON VALIDATION SET")
    y_true = []
    y_pred_probs = []
    
    total_batches = tf.data.experimental.cardinality(val_ds).numpy()
    processed_images = 0
    
    for batch_idx, (images, labels) in enumerate(val_ds):
        preds = model(images, training=False).numpy()
        y_pred_probs.append(preds)
        y_true.extend(labels.numpy())
        processed_images += len(labels)
        
        progress = int((batch_idx + 1) / total_batches * 25)
        bar = "█" * progress + "-" * (25 - progress)
        print(f"\r  Evaluating: [{bar}] {int((batch_idx+1)/total_batches*100)}% | Images: {processed_images:,}/{val_count:,}", end="", flush=True)

    print("\n  [✓] Inference complete.")

    y_pred_probs = np.vstack(y_pred_probs)
    y_pred = np.argmax(y_pred_probs, axis=1)
    y_true = np.array(y_true)

    # Calculate Overall Metrics
    precision, recall, f1, _ = precision_recall_fscore_support(y_true, y_pred, average="weighted", zero_division=0)
    macro_precision, macro_recall, macro_f1, _ = precision_recall_fscore_support(y_true, y_pred, average="macro", zero_division=0)
    accuracy = float(np.mean(y_pred == y_true))
    
    # Compute Cross-Entropy Loss
    loss_fn = tf.keras.losses.SparseCategoricalCrossentropy()
    loss = float(loss_fn(y_true, y_pred_probs).numpy())

    print_header("MODEL PERFORMANCE RESULTS")
    print(f"  Overall Accuracy  : {accuracy * 100:.2f}%")
    print(f"  Weighted Precision: {precision * 100:.2f}%")
    print(f"  Weighted Recall   : {recall * 100:.2f}%")
    print(f"  Weighted F1 Score : {f1 * 100:.2f}%")
    print(f"  Macro F1 Score    : {macro_f1 * 100:.2f}%")
    print(f"  Validation Loss   : {loss:.4f}")

    # 5. Confusion Matrices (Raw & Normalized)
    cm = confusion_matrix(y_true, y_pred, labels=all_label_indices)
    row_sums = cm.sum(axis=1)[:, np.newaxis]
    cm_norm = np.divide(cm.astype('float'), row_sums, out=np.zeros_like(cm, dtype=float), where=row_sums != 0)

    plt.figure(figsize=(14, 12))
    sns.heatmap(cm, annot=True, fmt="d", cmap="Blues", xticklabels=class_names, yticklabels=class_names)
    plt.title("Confusion Matrix — Sign Alphabet Recognition", fontsize=14, fontweight="bold", pad=15)
    plt.xlabel("Predicted Alphabet", fontsize=12)
    plt.ylabel("True Alphabet", fontsize=12)
    plt.tight_layout()
    plt.savefig(os.path.join(OUTPUT_DIR, "confusion_matrix.png"), dpi=300)
    plt.close()

    plt.figure(figsize=(14, 12))
    sns.heatmap(cm_norm, annot=True, fmt=".2f", cmap="Blues", xticklabels=class_names, yticklabels=class_names)
    plt.title("Normalized Confusion Matrix — Sign Alphabet Recognition", fontsize=14, fontweight="bold", pad=15)
    plt.xlabel("Predicted Alphabet", fontsize=12)
    plt.ylabel("True Alphabet", fontsize=12)
    plt.tight_layout()
    plt.savefig(os.path.join(OUTPUT_DIR, "confusion_matrix_normalized.png"), dpi=300)
    plt.close()

    # 6. Classification Report (CSV & DataFrame)
    clf_dict = classification_report(
        y_true,
        y_pred,
        labels=all_label_indices,
        target_names=class_names,
        output_dict=True,
        zero_division=0
    )
    report_df = pd.DataFrame(clf_dict).transpose()
    report_df.to_csv(os.path.join(OUTPUT_DIR, "classification_report.csv"))

    # 7. Dataset Class Distribution Plot
    plt.figure(figsize=(12, 5))
    plt.bar(list(class_counts.keys()), list(class_counts.values()), color="#38bdf8", edgecolor="#0284c7")
    plt.title("Dataset Distribution by Sign Class", fontsize=13, fontweight="bold", pad=12)
    plt.xlabel("Alphabet Class", fontsize=11)
    plt.ylabel("Number of Images", fontsize=11)
    plt.grid(axis="y", linestyle="--", alpha=0.5)
    plt.tight_layout()
    plt.savefig(os.path.join(OUTPUT_DIR, "dataset_class_distribution.png"), dpi=300)
    plt.close()

    # 8. Train / Validation Split Graph
    plt.figure(figsize=(6, 5))
    split_labels = ["Training Set", "Validation Set"]
    split_sizes = [train_count, val_count]
    colors = ["#22c55e", "#38bdf8"]
    plt.bar(split_labels, split_sizes, color=colors, width=0.5)
    for i, v in enumerate(split_sizes):
        pct = round(v / total_images * 100, 1)
        plt.text(i, v + (total_images * 0.02), f"{v:,}\n({pct}%)", ha="center", fontweight="bold")
    plt.ylim(0, max(split_sizes) * 1.2)
    plt.title("Train / Validation Split", fontsize=13, fontweight="bold")
    plt.ylabel("Image Count")
    plt.grid(axis="y", linestyle="--", alpha=0.5)
    plt.tight_layout()
    plt.savefig(os.path.join(OUTPUT_DIR, "train_validation_split.png"), dpi=300)
    plt.close()

    # 9. Per-Class F1 Score Graph
    per_class_f1_scores = [clf_dict[c]["f1-score"] for c in class_names]
    plt.figure(figsize=(12, 5))
    plt.bar(class_names, per_class_f1_scores, color="#a855f7", edgecolor="#7e22ce")
    plt.axhline(0.95, color="#22c55e", linestyle="--", alpha=0.7, label="95% Target")
    plt.title("Per-Class F1 Score — Sign Alphabet", fontsize=13, fontweight="bold", pad=12)
    plt.xlabel("Alphabet Class", fontsize=11)
    plt.ylabel("F1 Score", fontsize=11)
    plt.ylim(0, 1.05)
    plt.legend(loc="lower right")
    plt.grid(axis="y", linestyle="--", alpha=0.5)
    plt.tight_layout()
    plt.savefig(os.path.join(OUTPUT_DIR, "per_class_f1.png"), dpi=300)
    plt.close()

    # 10. Grouped Precision / Recall / F1 Graph
    x = np.arange(len(class_names))
    width = 0.25
    plt.figure(figsize=(15, 6))
    plt.bar(x - width, [clf_dict[c]["precision"] for c in class_names], width, label="Precision", color="#38bdf8")
    plt.bar(x, [clf_dict[c]["recall"] for c in class_names], width, label="Recall", color="#22c55e")
    plt.bar(x + width, [clf_dict[c]["f1-score"] for c in class_names], width, label="F1 Score", color="#f59e0b")
    plt.xlabel("Alphabet Class", fontsize=11)
    plt.ylabel("Score", fontsize=11)
    plt.title("Class-wise Precision, Recall and F1 Score", fontsize=13, fontweight="bold", pad=12)
    plt.xticks(x, class_names)
    plt.ylim(0, 1.08)
    plt.legend(loc="lower right")
    plt.grid(axis="y", linestyle="--", alpha=0.5)
    plt.tight_layout()
    plt.savefig(os.path.join(OUTPUT_DIR, "class_metrics.png"), dpi=300)
    plt.close()

    # 11. Performance Graphs (Accuracy/Loss Curves from Training History if available)
    history_file = os.path.join(PROJECT_ROOT, "models", "sign_alphabet", "training_history.json")
    history_found = False
    if os.path.isfile(history_file):
        try:
            with open(history_file, "r") as f:
                hist = json.load(f)
            if "accuracy" in hist and "val_accuracy" in hist:
                epochs_range = range(1, len(hist["accuracy"]) + 1)
                
                plt.figure(figsize=(8, 5))
                plt.plot(epochs_range, hist["accuracy"], label="Training Accuracy", color="#38bdf8", lw=2)
                plt.plot(epochs_range, hist["val_accuracy"], label="Validation Accuracy", color="#22c55e", lw=2)
                plt.title("Training vs Validation Accuracy", fontsize=13, fontweight="bold")
                plt.xlabel("Epoch")
                plt.ylabel("Accuracy")
                plt.legend(loc="lower right")
                plt.grid(True, linestyle="--", alpha=0.5)
                plt.tight_layout()
                plt.savefig(os.path.join(OUTPUT_DIR, "accuracy_curve.png"), dpi=300)
                plt.close()

                plt.figure(figsize=(8, 5))
                plt.plot(epochs_range, hist["loss"], label="Training Loss", color="#f87171", lw=2)
                plt.plot(epochs_range, hist["val_loss"], label="Validation Loss", color="#fb923c", lw=2)
                plt.title("Training vs Validation Loss", fontsize=13, fontweight="bold")
                plt.xlabel("Epoch")
                plt.ylabel("Loss")
                plt.legend(loc="upper right")
                plt.grid(True, linestyle="--", alpha=0.5)
                plt.tight_layout()
                plt.savefig(os.path.join(OUTPUT_DIR, "loss_curve.png"), dpi=300)
                plt.close()
                history_found = True
        except Exception:
            pass
    
    if not history_found:
        print("  [*] Note: Training history file not found; accuracy/loss curves were skipped.")

    # 12. Top Confused Classes Analysis
    confusions = []
    for i in range(len(class_names)):
        for j in range(len(class_names)):
            if i != j and cm[i, j] > 0:
                confusions.append((class_names[i], class_names[j], int(cm[i, j])))

    confusions.sort(key=lambda item: item[2], reverse=True)
    with open(os.path.join(OUTPUT_DIR, "top_confusions.txt"), "w") as f:
        f.write("========================================\n")
        f.write("TOP CONFUSED CLASSES (Actual -> Predicted)\n")
        f.write("========================================\n\n")
        if not confusions:
            f.write("No confusions detected! Model achieved 100% precision on validation set.\n")
        else:
            for idx, (act, pred, cnt) in enumerate(confusions[:15], 1):
                f.write(f"{idx:2d}. {act} → {pred} : {cnt} instances\n")

    # 13. Best and Weakest Performing Classes
    sorted_classes = sorted([(c, clf_dict[c]["f1-score"]) for c in class_names], key=lambda item: item[1], reverse=True)
    best_classes = sorted_classes[:5]
    weakest_classes = sorted_classes[-5:][::-1]

    # 14. Final Evaluation Summary Text
    summary_text = f"""========================================
GESTUREFORGE AI
SIGN ALPHABET MODEL EVALUATION SUMMARY
========================================

Dataset:
  Total Images       : {total_images:,}
  Classes            : {total_classes} (A-Z)
  Training Split     : {train_count:,} ({int((1-VAL_SPLIT)*100)}%)
  Validation Split   : {val_count:,} ({int(VAL_SPLIT*100)}%)

Model Architecture:
  Framework          : TensorFlow / Keras (MobileNetV2 Transfer Learning)
  Input Resolution   : {input_shape[1]}x{input_shape[2]} RGB
  Parameters         : {total_params:,}
  Model Size         : {model_size_mb} MB

Performance:
  Validation Accuracy: {accuracy * 100:.2f}%
  Weighted Precision : {precision * 100:.2f}%
  Weighted Recall    : {recall * 100:.2f}%
  Weighted F1 Score  : {f1 * 100:.2f}%
  Macro F1 Score     : {macro_f1 * 100:.2f}%
  Validation Loss    : {loss:.4f}

Top 5 Best Performing Classes:
{chr(10).join([f"  - Class '{c}': F1 Score = {score*100:.2f}%" for c, score in best_classes])}

5 Weakest Performing Classes:
{chr(10).join([f"  - Class '{c}': F1 Score = {score*100:.2f}%" for c, score in weakest_classes])}

Top Confused Pairs (Actual -> Predicted):
{chr(10).join([f"  - {act} -> {pred} ({cnt} occurrences)" for act, pred, cnt in confusions[:5]]) if confusions else "  - None (Zero Misclassifications)"}

========================================
"""
    with open(os.path.join(OUTPUT_DIR, "evaluation_summary.txt"), "w") as f:
        f.write(summary_text)

    # 15. HTML Report Generation
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>GestureForge AI — Model Evaluation Report</title>
    <style>
        body {{ font-family: 'Segoe UI', system-ui, sans-serif; background-color: #0f172a; color: #f8fafc; margin: 0; padding: 2rem; }}
        .container {{ max-width: 1200px; margin: 0 auto; }}
        h1, h2, h3 {{ color: #38bdf8; }}
        .metrics-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem; margin: 1.5rem 0; }}
        .metric-card {{ background: #1e293b; border: 1px solid #334155; border-radius: 8px; padding: 1.2rem; text-align: center; }}
        .metric-title {{ font-size: 0.8rem; color: #94a3b8; text-transform: uppercase; font-weight: 600; }}
        .metric-val {{ font-size: 2rem; font-weight: 800; color: #22c55e; margin-top: 0.3rem; }}
        .metric-val.loss {{ color: #38bdf8; }}
        .chart-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 1.5rem; margin: 2rem 0; }}
        .chart-card {{ background: #1e293b; border: 1px solid #334155; border-radius: 8px; padding: 1.5rem; }}
        .chart-card img {{ width: 100%; border-radius: 6px; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 1rem; font-size: 0.9rem; }}
        th, td {{ padding: 0.6rem 1rem; border: 1px solid #334155; text-align: center; }}
        th {{ background: #0f172a; color: #38bdf8; font-weight: 600; }}
        tr:nth-child(even) {{ background: rgba(255,255,255,0.02); }}
    </style>
</head>
<body>
    <div class="container">
        <h1>⚡ GestureForge AI — Sign Alphabet Model Evaluation</h1>
        <p style="color: #94a3b8;">Evaluated model: <code>{os.path.basename(model_path)}</code> | Architecture: MobileNetV2 Transfer Learning</p>
        
        <div class="metrics-grid">
            <div class="metric-card"><div class="metric-title">Accuracy</div><div class="metric-val">{accuracy*100:.2f}%</div></div>
            <div class="metric-card"><div class="metric-title">Weighted Precision</div><div class="metric-val">{precision*100:.2f}%</div></div>
            <div class="metric-card"><div class="metric-title">Weighted Recall</div><div class="metric-val">{recall*100:.2f}%</div></div>
            <div class="metric-card"><div class="metric-title">Weighted F1</div><div class="metric-val">{f1*100:.2f}%</div></div>
            <div class="metric-card"><div class="metric-title">Validation Loss</div><div class="metric-val loss">{loss:.4f}</div></div>
        </div>

        <div class="chart-grid">
            <div class="chart-card"><h3>Confusion Matrix</h3><img src="confusion_matrix.png" alt="Confusion Matrix"></div>
            <div class="chart-card"><h3>Normalized Confusion Matrix</h3><img src="confusion_matrix_normalized.png" alt="Normalized Confusion Matrix"></div>
            <div class="chart-card"><h3>Dataset Distribution (A-Z)</h3><img src="dataset_class_distribution.png" alt="Dataset Distribution"></div>
            <div class="chart-card"><h3>Train / Validation Split</h3><img src="train_validation_split.png" alt="Train Validation Split"></div>
            <div class="chart-card"><h3>Per-Class F1 Score</h3><img src="per_class_f1.png" alt="Per Class F1"></div>
            <div class="chart-card"><h3>Class Metrics (Precision/Recall/F1)</h3><img src="class_metrics.png" alt="Class Metrics"></div>
        </div>

        <div class="chart-card">
            <h3>Classification Report Table</h3>
            {report_df.to_html(classes='table', float_format='%.4f')}
        </div>
    </div>
</body>
</html>
"""
    with open(os.path.join(OUTPUT_DIR, "evaluation_report.html"), "w") as f:
        f.write(html_content)

    print_header("EVALUATION COMPLETE")
    print(f"  Accuracy  : {accuracy * 100:.2f}%")
    print(f"  Precision : {precision * 100:.2f}%")
    print(f"  Recall    : {recall * 100:.2f}%")
    print(f"  F1 Score  : {f1 * 100:.2f}%")
    print(f"\nReports saved to: {OUTPUT_DIR}/\n")
    print("Generated Artifacts:")
    print("  ✓ Confusion Matrix (Raw & Normalized)")
    print("  ✓ Dataset Distribution Graph")
    print("  ✓ Train / Validation Split Graph")
    print("  ✓ Per-Class F1 & Class-wise Metric Graphs")
    if history_found:
        print("  ✓ Training Accuracy & Loss Curves")
    print("  ✓ Classification Report CSV")
    print("  ✓ Top Confusions Log")
    print("  ✓ Evaluation Summary Text")
    print("  ✓ Full HTML Evaluation Report")
    print("=" * 50 + "\n")

    gc.collect()

if __name__ == "__main__":
    main()