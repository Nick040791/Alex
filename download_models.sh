#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODELS_DIR="$DIR/models"
mkdir -p "$MODELS_DIR"

echo "Downloading YuNet face detector (ONNX)..."
curl -sSL -o "$MODELS_DIR/face_detection_yunet.onnx" "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"

echo "Downloading SFace face recognizer (ONNX)..."
curl -sSL -o "$MODELS_DIR/face_recognition_sface.onnx" "https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx"

echo "Downloading YOLOv8 Nano detector (ONNX)..."
curl -sSL -o "$MODELS_DIR/yolov8n.onnx" "https://github.com/ultralytics/assets/releases/download/v8.2.0/yolov8n.onnx"

echo "All AI vision models successfully downloaded to $MODELS_DIR."
