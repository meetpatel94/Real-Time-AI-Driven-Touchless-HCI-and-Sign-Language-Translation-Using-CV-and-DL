import tensorflow as tf
from tensorflow.keras import layers, models
from typing import Tuple

def build_mobilenet_v2_classifier(
    input_shape: Tuple[int, int, int] = (160, 160, 3),
    num_classes: int = 26
) -> Tuple[tf.keras.Model, tf.keras.Model]:
    """
    Builds a lightweight MobileNetV2 transfer learning model with built-in data augmentation.
    """
    # 1. Lightweight Data Augmentation Layer (runs efficiently on CPU/GPU)
    data_augmentation = tf.keras.Sequential([
        layers.RandomFlip("horizontal"),
        layers.RandomRotation(0.08),
        layers.RandomZoom(0.10),
        layers.RandomContrast(0.10)
    ], name="data_augmentation")

    # 2. Pre-trained Base Model (ImageNet weights)
    base_model = tf.keras.applications.MobileNetV2(
        input_shape=input_shape,
        include_top=False,
        weights="imagenet"
    )
    base_model.trainable = False  # Freeze for Stage 1

    # 3. Model Architecture
    inputs = tf.keras.Input(shape=input_shape)
    x = data_augmentation(inputs)
    x = tf.keras.applications.mobilenet_v2.preprocess_input(x)
    x = base_model(x, training=False)
    x = layers.GlobalAveragePooling2D()(x)
    x = layers.Dropout(0.3)(x)
    outputs = layers.Dense(num_classes, activation="softmax")(x)

    model = tf.keras.Model(inputs, outputs, name="GestureForge_SignAlphabet_MobileNetV2")
    return model, base_model