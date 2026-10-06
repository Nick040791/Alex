import os
import time
import json
import asyncio
import cv2
import numpy as np
from contextlib import asynccontextmanager
from typing import Set, Dict, Any, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, HTTPException, Request, File, UploadFile, Form
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import database
from camera_client import CameraClient
from motion import MotionDetector
from ai_vision import AIVisionEngine

# Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
CAPTURES_DIR = os.path.join(BASE_DIR, "captures")
MODELS_DIR = os.path.join(BASE_DIR, "models")
FACES_DIR = os.path.join(CAPTURES_DIR, "faces")
os.makedirs(CAPTURES_DIR, exist_ok=True)
os.makedirs(FACES_DIR, exist_ok=True)

# Initialize DB
database.init_db()

# AI Vision Engine
ai_vision = AIVisionEngine(MODELS_DIR)
ai_vision.update_config(
    objects_enabled=database.get_setting("ai_objects_enabled", "true").lower() == "true",
    faces_enabled=database.get_setting("ai_faces_enabled", "true").lower() == "true",
    object_confidence=float(database.get_setting("ai_object_confidence", "0.45")),
    face_confidence=float(database.get_setting("ai_face_confidence", "0.60")),
    face_match_threshold=float(database.get_setting("ai_face_match_threshold", "0.363"))
)

# Load default or saved settings
default_host = database.get_setting("tablet_host", "100.105.4.70")
default_http_port = int(database.get_setting("tablet_http_port", "8080"))
default_rtsp_port = int(database.get_setting("tablet_rtsp_port", "8554"))
default_sensitivity = int(database.get_setting("motion_sensitivity", "60"))
default_min_area = int(database.get_setting("motion_min_area", "1800"))
default_cooldown = float(database.get_setting("motion_cooldown", "6.0"))
default_motion_enabled = database.get_setting("motion_enabled", "true").lower() == "true"

camera_client = CameraClient(host=default_host, http_port=default_http_port, rtsp_port=default_rtsp_port)
motion_detector = MotionDetector(
    sensitivity=default_sensitivity,
    min_area=default_min_area,
    cooldown=default_cooldown
)
motion_detector.enabled = default_motion_enabled

# Active WebSocket connections
active_websockets: Set[WebSocket] = set()
background_motion_task: Optional[asyncio.Task] = None

# Thread-safe container for current processed annotated frame
latest_annotated_jpeg: Optional[bytes] = None
latest_annotated_lock = asyncio.Lock()

async def broadcast_ws(message: Dict[str, Any]):
    """Broadcast JSON message to all connected dashboard WebSockets"""
    if not active_websockets:
        return
    disconnected = set()
    payload = json.dumps(message)
    for ws in list(active_websockets):
        try:
            await ws.send_text(payload)
        except Exception:
            disconnected.add(ws)
    for ws in disconnected:
        active_websockets.discard(ws)

async def motion_worker_loop():
    """Background coroutine analyzing camera frames for motion and AI recognition"""
    global latest_annotated_jpeg
    last_broadcast_time = 0.0
    last_motion_state = False
    ai_frame_counter = 0

    while True:
        try:
            frame, raw_jpeg, is_connected = camera_client.get_latest_frame()
            if frame is not None:
                motion_detected, is_new_event, annotated_frame, motion_score, trigger_thresh, boxes = motion_detector.process_frame(frame)
                ai_frame_counter += 1

                # Periodically or on motion, run AI detection for the live view overlay
                if is_connected and (motion_detected or ai_frame_counter % 3 == 0):
                    annotated_frame, live_objects, live_faces, live_summary = ai_vision.process_and_annotate(annotated_frame, draw_ai=True)

                # Encode annotated frame for the overlay stream
                ret, ann_bytes = cv2.imencode('.jpg', annotated_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
                if ret:
                    latest_annotated_jpeg = ann_bytes.tobytes()

                now = time.time()

                # Trigger new Ring motion event with AI Recognition
                if is_new_event and is_connected:
                    timestamp_int = int(now)
                    clean_filename = f"motion_{timestamp_int}_clean.jpg"
                    ann_filename = f"motion_{timestamp_int}_annotated.jpg"
                    clean_path = os.path.join(CAPTURES_DIR, clean_filename)
                    ann_path = os.path.join(CAPTURES_DIR, ann_filename)

                    # Run deep AI recognition on event snapshot
                    ai_ann_frame, event_objects, event_faces, event_summary = ai_vision.process_and_annotate(frame.copy(), draw_ai=True)

                    # Save frames to disk
                    cv2.imwrite(clean_path, frame)
                    cv2.imwrite(ann_path, ai_ann_frame)

                    # Store event in SQLite with AI objects and faces
                    event_record = database.add_event(
                        event_type="motion",
                        image_path=f"captures/{clean_filename}",
                        annotated_path=f"captures/{ann_filename}",
                        motion_score=motion_score,
                        boxes=boxes,
                        notes=f"AI: {event_summary}",
                        objects=event_objects,
                        faces=event_faces,
                        summary_label=event_summary
                    )

                    # Broadcast instant alert to dashboards & mobile devices
                    await broadcast_ws({
                        "type": "motion_event",
                        "event": event_record
                    })

                # Broadcast live motion meter and status (every 100ms or on trigger)
                if motion_detected != last_motion_state or (now - last_broadcast_time) > 0.1:
                    last_motion_state = motion_detected
                    last_broadcast_time = now
                    await broadcast_ws({
                        "type": "motion_status",
                        "motion_detected": motion_detected,
                        "motion_score": motion_score,
                        "trigger_threshold": trigger_thresh,
                        "sensitivity": motion_detector.sensitivity,
                        "zone": motion_detector.zone,
                        "is_connected": is_connected,
                        "active_viewers": len(active_websockets)
                    })

            await asyncio.sleep(0.06)  # ~16 FPS processing rate
        except asyncio.CancelledError:
            break
        except Exception as e:
            await asyncio.sleep(0.5)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    camera_client.start()
    motion_task = asyncio.create_task(motion_worker_loop())
    yield
    # Shutdown
    motion_task.cancel()
    camera_client.stop()

app = FastAPI(title="Ring IP Cam Dashboard", lifespan=lifespan)

# Mount captures and static files
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/captures", StaticFiles(directory=CAPTURES_DIR), name="captures")

# Models
class SettingsUpdate(BaseModel):
    tablet_host: Optional[str] = None
    tablet_http_port: Optional[int] = None
    tablet_rtsp_port: Optional[int] = None
    motion_sensitivity: Optional[int] = None
    motion_min_area: Optional[int] = None
    motion_cooldown: Optional[float] = None
    motion_zone: Optional[str] = None
    motion_enabled: Optional[bool] = None

class AISettingsUpdate(BaseModel):
    objects_enabled: Optional[bool] = None
    faces_enabled: Optional[bool] = None
    object_confidence: Optional[float] = None
    face_confidence: Optional[float] = None
    face_match_threshold: Optional[float] = None

class FaceEnrollRequest(BaseModel):
    name: str
    image_path: Optional[str] = None
    from_live: Optional[bool] = False

class ControlParam(BaseModel):
    value: Optional[str] = None

# Streaming generators
def mjpeg_frame_generator(overlay: bool = True):
    """Generate multipart MJPEG stream"""
    while True:
        try:
            if overlay and latest_annotated_jpeg is not None:
                frame_data = latest_annotated_jpeg
            else:
                _, frame_data, _ = camera_client.get_latest_frame()
                if frame_data is None:
                    time.sleep(0.05)
                    continue

            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + frame_data + b"\r\n"
            )
            time.sleep(0.06)  # ~16 FPS delivery
        except Exception:
            time.sleep(0.1)

@app.get("/")
async def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))

@app.get("/sw.js")
async def service_worker():
    return FileResponse(
        os.path.join(STATIC_DIR, "sw.js"),
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/"}
    )

@app.get("/manifest.json")
async def web_manifest():
    return FileResponse(
        os.path.join(STATIC_DIR, "manifest.json"),
        media_type="application/manifest+json"
    )

@app.get("/stream/live")
async def live_stream(overlay: bool = Query(True)):
    """MJPEG live video stream endpoint"""
    return StreamingResponse(
        mjpeg_frame_generator(overlay=overlay),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )

@app.get("/api/snapshot")
async def snapshot(annotated: bool = Query(False)):
    """Capture instant still snapshot"""
    frame, raw_jpeg, is_connected = camera_client.get_latest_frame()
    if frame is None:
        raise HTTPException(status_code=503, detail="Camera frame unavailable")

    now = time.time()
    filename = f"manual_{int(now)}.jpg"
    filepath = os.path.join(CAPTURES_DIR, filename)

    if annotated:
        _, _, ann_frame, score, boxes = motion_detector.process_frame(frame)
        cv2.imwrite(filepath, ann_frame)
    else:
        cv2.imwrite(filepath, frame)

    event_record = database.add_event(
        event_type="manual_snapshot",
        image_path=f"captures/{filename}",
        annotated_path=None,
        motion_score=0.0,
        notes="Manual snapshot"
    )

    await broadcast_ws({
        "type": "snapshot_taken",
        "event": event_record
    })

    return event_record

@app.get("/api/status")
async def get_status():
    """System and tablet camera telemetry"""
    remote_status = await camera_client.get_remote_status()
    ping_ok = camera_client.check_ping()
    
    return {
        "connected": camera_client.is_connected,
        "connection_error": camera_client.connection_error,
        "stream_type": camera_client.stream_type,
        "ping_ok": ping_ok,
        "tablet_host": camera_client.host,
        "tablet_http_port": camera_client.http_port,
        "tablet_rtsp_port": camera_client.rtsp_port,
        "remote_status": remote_status,
        "motion": {
            "enabled": motion_detector.enabled,
            "sensitivity": motion_detector.sensitivity,
            "min_area": motion_detector.min_area,
            "cooldown": motion_detector.cooldown,
            "zone": motion_detector.zone,
            "trigger_threshold": motion_detector.trigger_threshold,
            "is_active": motion_detector.is_motion_active,
            "score": motion_detector.motion_score
        },
        "ai": {
            "objects_enabled": ai_vision.objects_enabled,
            "faces_enabled": ai_vision.faces_enabled,
            "object_confidence": ai_vision.object_confidence,
            "face_confidence": ai_vision.face_confidence,
            "face_match_threshold": ai_vision.face_match_threshold,
            "enrolled_faces_count": len(ai_vision.known_faces)
        },
        "active_viewers": len(active_websockets)
    }

# Tablet Hardware Controls
@app.post("/api/control/switch")
async def switch_camera():
    res = await camera_client.switch_camera()
    motion_detector.reset_background()
    await broadcast_ws({"type": "control_action", "action": "switch", "result": res})
    return res

@app.post("/api/control/flashlight")
async def toggle_flashlight():
    res = await camera_client.toggle_flashlight()
    await broadcast_ws({"type": "control_action", "action": "flashlight", "result": res})
    return res

@app.post("/api/control/rotate")
async def rotate_camera(param: ControlParam):
    val = param.value or "90"
    res = await camera_client.set_rotation(val)
    motion_detector.reset_background()
    await broadcast_ws({"type": "control_action", "action": "rotate", "value": val, "result": res})
    return res

@app.post("/api/control/format")
async def set_format(param: ControlParam):
    val = param.value or "1280x720"
    res = await camera_client.set_format(val)
    motion_detector.reset_background()
    return res

@app.post("/api/control/reset")
async def reset_camera():
    res = await camera_client.reset_camera()
    motion_detector.reset_background()
    return res

# Events History API
@app.get("/api/events")
async def list_events(limit: int = 50, offset: int = 0):
    events = database.get_events(limit=limit, offset=offset)
    return {"events": events}

@app.delete("/api/events/{event_id}")
async def remove_event(event_id: int):
    success = database.delete_event(event_id)
    if not success:
        raise HTTPException(status_code=404, detail="Event not found")
    await broadcast_ws({"type": "event_deleted", "id": event_id})
    return {"success": True}

@app.delete("/api/events")
async def clear_events():
    database.clear_all_events()
    await broadcast_ws({"type": "events_cleared"})
    return {"success": True}

# Settings API
@app.get("/api/settings")
async def get_settings():
    return {
        "tablet_host": camera_client.host,
        "tablet_http_port": camera_client.http_port,
        "tablet_rtsp_port": camera_client.rtsp_port,
        "motion_sensitivity": motion_detector.sensitivity,
        "motion_min_area": motion_detector.min_area,
        "motion_cooldown": motion_detector.cooldown,
        "motion_zone": motion_detector.zone,
        "motion_trigger_threshold": motion_detector.trigger_threshold,
        "motion_enabled": motion_detector.enabled
    }

@app.post("/api/settings")
async def update_settings(payload: SettingsUpdate):
    if payload.tablet_host or payload.tablet_http_port or payload.tablet_rtsp_port:
        h = payload.tablet_host or camera_client.host
        hp = payload.tablet_http_port or camera_client.http_port
        rp = payload.tablet_rtsp_port or camera_client.rtsp_port
        camera_client.update_target(h, hp, rp)
        database.set_setting("tablet_host", h)
        database.set_setting("tablet_http_port", str(hp))
        database.set_setting("tablet_rtsp_port", str(rp))

    if payload.motion_sensitivity is not None:
        motion_detector.update_config(sensitivity=payload.motion_sensitivity)
        database.set_setting("motion_sensitivity", str(payload.motion_sensitivity))

    if payload.motion_min_area is not None:
        motion_detector.update_config(min_area=payload.motion_min_area)
        database.set_setting("motion_min_area", str(payload.motion_min_area))

    if payload.motion_cooldown is not None:
        motion_detector.update_config(cooldown=payload.motion_cooldown)
        database.set_setting("motion_cooldown", str(payload.motion_cooldown))

    if payload.motion_zone is not None:
        motion_detector.update_config(zone=payload.motion_zone)
        database.set_setting("motion_zone", payload.motion_zone)

    if payload.motion_enabled is not None:
        motion_detector.update_config(enabled=payload.motion_enabled)
        database.set_setting("motion_enabled", str(payload.motion_enabled).lower())

    await broadcast_ws({
        "type": "settings_updated",
        "sensitivity": motion_detector.sensitivity,
        "zone": motion_detector.zone,
        "trigger_threshold": motion_detector.trigger_threshold,
        "enabled": motion_detector.enabled
    })
    return {
        "success": True,
        "sensitivity": motion_detector.sensitivity,
        "zone": motion_detector.zone,
        "trigger_threshold": motion_detector.trigger_threshold
    }

# Facial & Object Recognition AI API
@app.get("/api/faces")
async def list_faces():
    """List all enrolled faces"""
    faces = database.get_known_faces()
    cleaned = [
        {
            "id": f["id"],
            "name": f["name"],
            "thumbnail_path": f["thumbnail_path"],
            "created_at": f["created_at"]
        }
        for f in faces
    ]
    return {"faces": cleaned}

@app.post("/api/faces/enroll")
async def enroll_face(req: FaceEnrollRequest):
    """Enroll a face from an existing capture or the live stream"""
    name = (req.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name is required")

    frame = None
    if req.from_live:
        frame, _, _ = camera_client.get_latest_frame()
        if frame is None:
            raise HTTPException(status_code=503, detail="Camera live frame unavailable")
    elif req.image_path:
        rel_p = req.image_path.lstrip("/")
        full_p = os.path.join(BASE_DIR, rel_p)
        if not os.path.exists(full_p):
            raise HTTPException(status_code=404, detail="Source image file not found")
        frame = cv2.imread(full_p)
    else:
        raise HTTPException(status_code=400, detail="Must provide image_path or from_live=true")

    if frame is None:
        raise HTTPException(status_code=400, detail="Failed to load image for face enrollment")

    timestamp_int = int(time.time())
    safe_name = "".join(c for c in name.lower() if c.isalnum() or c in ('_', '-')).strip() or "face"
    thumb_filename = f"face_{timestamp_int}_{safe_name}.jpg"
    thumb_disk_path = os.path.join(FACES_DIR, thumb_filename)
    thumb_rel_path = f"captures/faces/{thumb_filename}"

    face_id = ai_vision.enroll_face_from_crop(name, frame, thumb_disk_path, thumb_rel_path)
    if face_id is None:
        raise HTTPException(status_code=422, detail="No face detected in the image. Please use an image with a clearly visible face.")

    face_record = {
        "id": face_id,
        "name": name,
        "thumbnail_path": thumb_rel_path,
        "created_at": timestamp_int
    }

    await broadcast_ws({
        "type": "face_enrolled",
        "face": face_record
    })

    return {"success": True, "face": face_record}

@app.post("/api/faces/upload")
async def enroll_face_upload(name: str = Form(...), file: UploadFile = File(...)):
    """Enroll a face by uploading an image from the user's device"""
    name = (name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name is required")

    contents = await file.read()
    nparr = np.frombuffer(contents, np.uint8)
    frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if frame is None:
        raise HTTPException(status_code=400, detail="Uploaded file is not a valid image")

    timestamp_int = int(time.time())
    safe_name = "".join(c for c in name.lower() if c.isalnum() or c in ('_', '-')).strip() or "face"
    thumb_filename = f"face_{timestamp_int}_{safe_name}.jpg"
    thumb_disk_path = os.path.join(FACES_DIR, thumb_filename)
    thumb_rel_path = f"captures/faces/{thumb_filename}"

    face_id = ai_vision.enroll_face_from_crop(name, frame, thumb_disk_path, thumb_rel_path)
    if face_id is None:
        raise HTTPException(status_code=422, detail="No face detected in the uploaded photo. Please choose a clearer picture.")

    face_record = {
        "id": face_id,
        "name": name,
        "thumbnail_path": thumb_rel_path,
        "created_at": timestamp_int
    }

    await broadcast_ws({
        "type": "face_enrolled",
        "face": face_record
    })

    return {"success": True, "face": face_record}

@app.delete("/api/faces/{face_id}")
async def remove_face(face_id: int):
    """Delete an enrolled face"""
    success = database.delete_known_face(face_id)
    if not success:
        raise HTTPException(status_code=404, detail="Enrolled face not found")
    ai_vision.reload_known_faces()
    await broadcast_ws({"type": "face_deleted", "id": face_id})
    return {"success": True}

@app.get("/api/settings/ai")
async def get_ai_settings():
    """Get AI Vision Engine configuration"""
    return {
        "objects_enabled": ai_vision.objects_enabled,
        "faces_enabled": ai_vision.faces_enabled,
        "object_confidence": ai_vision.object_confidence,
        "face_confidence": ai_vision.face_confidence,
        "face_match_threshold": ai_vision.face_match_threshold,
        "enrolled_faces_count": len(ai_vision.known_faces)
    }

@app.post("/api/settings/ai")
async def update_ai_settings(payload: AISettingsUpdate):
    """Update AI Vision Engine configuration"""
    if payload.objects_enabled is not None:
        database.set_setting("ai_objects_enabled", str(payload.objects_enabled).lower())
    if payload.faces_enabled is not None:
        database.set_setting("ai_faces_enabled", str(payload.faces_enabled).lower())
    if payload.object_confidence is not None:
        database.set_setting("ai_object_confidence", str(payload.object_confidence))
    if payload.face_confidence is not None:
        database.set_setting("ai_face_confidence", str(payload.face_confidence))
    if payload.face_match_threshold is not None:
        database.set_setting("ai_face_match_threshold", str(payload.face_match_threshold))

    ai_vision.update_config(
        objects_enabled=payload.objects_enabled,
        faces_enabled=payload.faces_enabled,
        object_confidence=payload.object_confidence,
        face_confidence=payload.face_confidence,
        face_match_threshold=payload.face_match_threshold
    )

    await broadcast_ws({
        "type": "ai_settings_updated",
        "objects_enabled": ai_vision.objects_enabled,
        "faces_enabled": ai_vision.faces_enabled,
        "object_confidence": ai_vision.object_confidence,
        "face_confidence": ai_vision.face_confidence,
        "face_match_threshold": ai_vision.face_match_threshold
    })

    return {
        "success": True,
        "objects_enabled": ai_vision.objects_enabled,
        "faces_enabled": ai_vision.faces_enabled,
        "object_confidence": ai_vision.object_confidence,
        "face_confidence": ai_vision.face_confidence,
        "face_match_threshold": ai_vision.face_match_threshold
    }

# WebSockets for live dashboard updates
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_websockets.add(websocket)
    try:
        # Send initial status packet
        await websocket.send_text(json.dumps({
            "type": "init",
            "connected": camera_client.is_connected,
            "motion_detected": motion_detector.is_motion_active,
            "tablet_host": camera_client.host
        }))
        while True:
            # Keepalive / listen for client actions (like trigger chime)
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("type") == "trigger_chime":
                    # Broadcast doorbell chime to all connected dashboard screens!
                    await broadcast_ws({
                        "type": "ring_chime",
                        "sender": "dashboard"
                    })
            except Exception:
                pass
    except WebSocketDisconnect:
        active_websockets.discard(websocket)
    except Exception:
        active_websockets.discard(websocket)
