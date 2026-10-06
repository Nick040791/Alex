import os
import cv2
import numpy as np
from typing import List, Dict, Any, Tuple, Optional
import database

COCO_CLASSES = [
    'person', 'bicycle', 'car', 'motorcycle', 'airplane', 'bus', 'train', 'truck', 'boat',
    'traffic light', 'fire hydrant', 'stop sign', 'parking meter', 'bench', 'bird', 'cat',
    'dog', 'horse', 'sheep', 'cow', 'elephant', 'bear', 'zebra', 'giraffe', 'backpack',
    'umbrella', 'handbag', 'tie', 'suitcase', 'frisbee', 'skis', 'snowboard', 'sports ball',
    'kite', 'baseball bat', 'baseball glove', 'skateboard', 'surfboard', 'tennis racket',
    'bottle', 'wine glass', 'cup', 'fork', 'knife', 'spoon', 'bowl', 'banana', 'apple',
    'sandwich', 'orange', 'broccoli', 'carrot', 'hot dog', 'pizza', 'donut', 'cake',
    'chair', 'couch', 'potted plant', 'bed', 'dining table', 'toilet', 'tv', 'laptop',
    'mouse', 'remote', 'keyboard', 'cell phone', 'microwave', 'oven', 'toaster', 'sink',
    'refrigerator', 'book', 'clock', 'vase', 'scissors', 'teddy bear', 'hair drier', 'toothbrush'
]

# Security relevant categories
SECURITY_ICONS = {
    'person': '👤 Person',
    'car': '🚗 Vehicle',
    'truck': '🚚 Truck',
    'bus': '🚌 Bus',
    'motorcycle': '🏍️ Motorcycle',
    'bicycle': '🚲 Bicycle',
    'dog': '🐕 Pet (Dog)',
    'cat': '🐈 Pet (Cat)',
    'bird': '🐦 Bird',
    'bear': '🐻 Bear',
    'backpack': '📦 Package / Bag',
    'suitcase': '🧳 Luggage',
    'cell phone': '📱 Phone'
}

class AIVisionEngine:
    def __init__(self, models_dir: str):
        self.models_dir = models_dir
        self.yolo_path = os.path.join(models_dir, 'yolov8n.onnx')
        self.yunet_path = os.path.join(models_dir, 'face_detection_yunet.onnx')
        self.sface_path = os.path.join(models_dir, 'face_recognition_sface.onnx')
        
        self.objects_enabled = True
        self.faces_enabled = True
        self.object_confidence = 0.45
        self.face_confidence = 0.60
        self.face_match_threshold = 0.363  # OpenCV SFace recommended cosine similarity threshold
        
        # Load Models
        self.yolo_net: Optional[cv2.dnn.Net] = None
        self.face_detector: Optional[cv2.FaceDetectorYN] = None
        self.face_recognizer: Optional[cv2.FaceRecognizerSF] = None
        
        self._load_models()
        self.known_faces: List[Dict[str, Any]] = []
        self.reload_known_faces()

    def _load_models(self):
        try:
            if os.path.exists(self.yolo_path):
                self.yolo_net = cv2.dnn.readNetFromONNX(self.yolo_path)
                self.yolo_net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
        except Exception as e:
            print("Error loading YOLO model:", e)

        try:
            if os.path.exists(self.yunet_path):
                self.face_detector = cv2.FaceDetectorYN.create(
                    model=self.yunet_path,
                    config='',
                    input_size=(640, 360),
                    score_threshold=self.face_confidence,
                    nms_threshold=0.3,
                    top_k=5000
                )
        except Exception as e:
            print("Error loading YuNet face detector:", e)

        try:
            if os.path.exists(self.sface_path):
                self.face_recognizer = cv2.FaceRecognizerSF.create(
                    model=self.sface_path,
                    config=''
                )
        except Exception as e:
            print("Error loading SFace recognizer:", e)

    def reload_known_faces(self):
        """Reload enrolled faces from SQLite database"""
        self.known_faces = []
        try:
            db_faces = database.get_known_faces()
            for f in db_faces:
                raw_bytes = f["embedding_blob"]
                feat = np.frombuffer(raw_bytes, dtype=np.float32).reshape((1, 128))
                self.known_faces.append({
                    "id": f["id"],
                    "name": f["name"],
                    "thumbnail_path": f["thumbnail_path"],
                    "feature": feat
                })
        except Exception as e:
            print("Error reloading known faces:", e)

    def detect_objects(self, frame: np.ndarray) -> List[Dict[str, Any]]:
        """Run YOLOv8 object detection on frame"""
        if not self.objects_enabled or self.yolo_net is None or frame is None:
            return []

        h, w = frame.shape[:2]
        blob = cv2.dnn.blobFromImage(frame, 1.0 / 255.0, (640, 640), swapRB=True, crop=False)
        self.yolo_net.setInput(blob)
        out = self.yolo_net.forward()

        # Parse YOLOv8 outputs: out shape is (1, 84, 8400)
        predictions = np.squeeze(out).T
        scores_data = predictions[:, 4:]
        class_ids = np.argmax(scores_data, axis=1)
        confidences = np.max(scores_data, axis=1)

        mask = confidences >= self.object_confidence
        filtered_boxes = predictions[mask, :4]
        filtered_scores = confidences[mask]
        filtered_classes = class_ids[mask]

        if len(filtered_boxes) == 0:
            return []

        # Convert cx, cy, w, h from 640x640 space to original image space
        x_scale = w / 640.0
        y_scale = h / 640.0

        boxes_list = []
        for box in filtered_boxes:
            cx, cy, bw, bh = box
            x = int((cx - bw / 2.0) * x_scale)
            y = int((cy - bh / 2.0) * y_scale)
            bw = int(bw * x_scale)
            bh = int(bh * y_scale)
            boxes_list.append([x, y, bw, bh])

        indices = cv2.dnn.NMSBoxes(
            boxes_list,
            filtered_scores.tolist(),
            score_threshold=self.object_confidence,
            nms_threshold=0.45
        )

        detections = []
        if len(indices) > 0:
            for idx in indices.flatten():
                cid = int(filtered_classes[idx])
                label = COCO_CLASSES[cid] if cid < len(COCO_CLASSES) else f"obj_{cid}"
                box = boxes_list[idx]
                conf = round(float(filtered_scores[idx]), 2)
                detections.append({
                    "label": label,
                    "friendly_label": SECURITY_ICONS.get(label, label.title()),
                    "confidence": conf,
                    "box": {
                        "x": max(0, box[0]),
                        "y": max(0, box[1]),
                        "w": box[2],
                        "h": box[3]
                    }
                })

        return detections

    def detect_and_recognize_faces(self, frame: np.ndarray) -> List[Dict[str, Any]]:
        """Detect faces using YuNet and match against enrolled faces using SFace"""
        if not self.faces_enabled or self.face_detector is None or frame is None:
            return []

        h, w = frame.shape[:2]
        self.face_detector.setInputSize((w, h))
        faces = self.face_detector.detect(frame)

        if faces[1] is None or len(faces[1]) == 0:
            return []

        face_results = []
        for face_data in faces[1]:
            score = float(face_data[-1])
            if score < self.face_confidence:
                continue

            bx, by, bw, bh = map(int, face_data[:4])
            
            # Extract face embedding with SFace
            matched_name = "Unknown Person"
            best_similarity = 0.0
            face_feature = None

            if self.face_recognizer is not None:
                try:
                    aligned_face = self.face_recognizer.alignCrop(frame, face_data)
                    face_feature = self.face_recognizer.feature(aligned_face)

                    # Compare against enrolled faces
                    for kf in self.known_faces:
                        sim = self.face_recognizer.match(face_feature, kf["feature"], cv2.FaceRecognizerSF_FR_COSINE)
                        if sim > best_similarity:
                            best_similarity = sim
                            if sim >= self.face_match_threshold:
                                matched_name = kf["name"]
                except Exception as e:
                    pass

            face_results.append({
                "name": matched_name,
                "confidence": round(score, 2),
                "similarity": round(float(best_similarity), 2),
                "is_known": matched_name != "Unknown Person",
                "box": {
                    "x": max(0, bx),
                    "y": max(0, by),
                    "w": max(1, bw),
                    "h": max(1, bh)
                },
                "raw_face_data": face_data,
                "feature": face_feature
            })

        return face_results

    def update_config(
        self,
        objects_enabled: Optional[bool] = None,
        faces_enabled: Optional[bool] = None,
        object_confidence: Optional[float] = None,
        face_confidence: Optional[float] = None,
        face_match_threshold: Optional[float] = None
    ):
        if objects_enabled is not None:
            self.objects_enabled = bool(objects_enabled)
        if faces_enabled is not None:
            self.faces_enabled = bool(faces_enabled)
        if object_confidence is not None:
            self.object_confidence = max(0.1, min(0.95, float(object_confidence)))
        if face_confidence is not None:
            self.face_confidence = max(0.1, min(0.95, float(face_confidence)))
            if self.face_detector is not None:
                try:
                    self.face_detector.setScoreThreshold(self.face_confidence)
                except Exception:
                    pass
        if face_match_threshold is not None:
            self.face_match_threshold = max(0.1, min(0.95, float(face_match_threshold)))

    def enroll_face_from_crop(
        self,
        name: str,
        frame: np.ndarray,
        thumbnail_disk_path: str,
        thumbnail_rel_path: Optional[str] = None
    ) -> Optional[int]:
        """Enroll a person's face from a frame and persist to SQLite"""
        if self.face_detector is None or self.face_recognizer is None or frame is None:
            return None

        h, w = frame.shape[:2]
        self.face_detector.setInputSize((w, h))
        faces = self.face_detector.detect(frame)

        if faces[1] is None or len(faces[1]) == 0:
            return None

        # Take face with highest confidence
        best_face = max(faces[1], key=lambda f: float(f[-1]))
        aligned_face = self.face_recognizer.alignCrop(frame, best_face)
        feature = self.face_recognizer.feature(aligned_face)

        # Save thumbnail crop
        os.makedirs(os.path.dirname(thumbnail_disk_path), exist_ok=True)
        cv2.imwrite(thumbnail_disk_path, aligned_face)

        # Store in database
        feat_bytes = feature.astype(np.float32).tobytes()
        rel_path = thumbnail_rel_path or thumbnail_disk_path
        face_id = database.add_known_face(name, feat_bytes, rel_path)
        self.reload_known_faces()
        return face_id

    def process_and_annotate(
        self,
        frame: np.ndarray,
        draw_ai: bool = True
    ) -> Tuple[np.ndarray, List[Dict[str, Any]], List[Dict[str, Any]], str]:
        """
        Run both object recognition and face identification, and annotate frame.
        
        Returns:
            - annotated_frame (np.ndarray)
            - detected_objects (list)
            - detected_faces (list)
            - summary_label (str) e.g. "👤 Person (Nick)", "🚗 Vehicle", etc.
        """
        if frame is None:
            return frame, [], [], "Live"

        annotated = frame.copy() if draw_ai else frame

        # Run detections
        objects = self.detect_objects(frame)
        faces = self.detect_and_recognize_faces(frame)

        # Generate smart Ring-like summary label
        summary_tags = []
        known_person_names = [f["name"] for f in faces if f.get("is_known")]
        unknown_faces_count = sum(1 for f in faces if not f.get("is_known"))
        person_objects = [o for o in objects if o["label"] == "person"]

        if known_person_names:
            summary_tags.append(f"👤 {', '.join(known_person_names)}")
        elif person_objects or unknown_faces_count > 0:
            summary_tags.append("👤 Person Detected")

        vehicle_objects = [o for o in objects if o["label"] in ["car", "truck", "motorcycle", "bus"]]
        if vehicle_objects:
            summary_tags.append("🚗 Vehicle")

        pet_objects = [o for o in objects if o["label"] in ["dog", "cat", "bird"]]
        if pet_objects:
            summary_tags.append("🐕 Pet")

        package_objects = [o for o in objects if o["label"] in ["backpack", "suitcase", "handbag"]]
        if package_objects:
            summary_tags.append("📦 Package")

        summary_label = " + ".join(summary_tags) if summary_tags else "Motion Detected"

        if not draw_ai:
            return annotated, objects, faces, summary_label

        # Annotate Objects (YOLO)
        for obj in objects:
            b = obj["box"]
            x, y, w, h = b["x"], b["y"], b["w"], b["h"]
            lbl = obj["friendly_label"]
            conf = int(obj["confidence"] * 100)

            # Different color for different categories
            if obj["label"] == "person":
                color = (255, 168, 0)  # Ring cyan
            elif obj["label"] in ["car", "truck", "bus", "motorcycle"]:
                color = (0, 215, 255)  # Gold/Yellow
            elif obj["label"] in ["dog", "cat", "bird"]:
                color = (0, 220, 100)  # Green
            else:
                color = (200, 120, 255) # Light purple

            cv2.rectangle(annotated, (x, y), (x + w, y + h), color, 2)
            cv2.putText(
                annotated,
                f"{lbl} {conf}%",
                (x, max(20, y - 8)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                color,
                2
            )

        # Annotate Faces (YuNet + SFace)
        for face in faces:
            b = face["box"]
            x, y, w, h = b["x"], b["y"], b["w"], b["h"]
            name = face["name"]
            is_known = face.get("is_known", False)

            if is_known:
                color = (220, 50, 200) # Magenta/Purple for recognized known face
                tag = f"👤 {name} ({int(face['similarity'] * 100)}%)"
            else:
                color = (0, 165, 255)  # Orange for unknown face
                tag = "Face (Unknown)"

            cv2.rectangle(annotated, (x, y), (x + w, y + h), color, 2)
            cv2.putText(
                annotated,
                tag,
                (x, max(20, y - 8)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                color,
                2
            )

        return annotated, objects, faces, summary_label
