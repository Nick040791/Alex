import os
import time
import json
import asyncio
import cv2
import numpy as np
from contextlib import asynccontextmanager
from typing import Set, Dict, Any, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, HTTPException, Request
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import database
from camera_client import CameraClient
from motion import MotionDetector

# Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
CAPTURES_DIR = os.path.join(BASE_DIR, "captures")
os.makedirs(CAPTURES_DIR, exist_ok=True)

# Initialize DB
database.init_db()

# Load default or saved settings
default_host = database.get_setting("tablet_host", "100.105.4.70")
default_http_port = int(database.get_setting("tablet_http_port", "8080"))
default_rtsp_port = int(database.get_setting("tablet_rtsp_port", "8554"))
default_sensitivity = int(database.get_setting("motion_sensitivity", "25"))
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
    """Background coroutine analyzing camera frames for motion events"""
    global latest_annotated_jpeg
    last_broadcast_time = 0.0
    last_motion_state = False

    while True:
        try:
            frame, raw_jpeg, is_connected = camera_client.get_latest_frame()
            if frame is not None:
                motion_detected, is_new_event, annotated_frame, motion_score, trigger_thresh, boxes = motion_detector.process_frame(frame)
                
                # Encode annotated frame for the overlay stream
                ret, ann_bytes = cv2.imencode('.jpg', annotated_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
                if ret:
                    latest_annotated_jpeg = ann_bytes.tobytes()

                now = time.time()

                # Trigger new Ring motion event
                if is_new_event and is_connected:
                    timestamp_int = int(now)
                    clean_filename = f"motion_{timestamp_int}_clean.jpg"
                    ann_filename = f"motion_{timestamp_int}_annotated.jpg"
                    clean_path = os.path.join(CAPTURES_DIR, clean_filename)
                    ann_path = os.path.join(CAPTURES_DIR, ann_filename)

                    # Save frames to disk
                    cv2.imwrite(clean_path, frame)
                    cv2.imwrite(ann_path, annotated_frame)

                    # Store event in SQLite
                    event_record = database.add_event(
                        event_type="motion",
                        image_path=f"captures/{clean_filename}",
                        annotated_path=f"captures/{ann_filename}",
                        motion_score=motion_score,
                        boxes=boxes,
                        notes="Ring Motion Alert"
                    )

                    # Broadcast instant alert to dashboards
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
