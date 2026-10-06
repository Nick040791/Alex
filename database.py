import sqlite3
import json
import os
import time
from datetime import datetime
from typing import List, Dict, Any, Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "events.db")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            datetime_str TEXT NOT NULL,
            event_type TEXT NOT NULL,
            image_path TEXT NOT NULL,
            annotated_path TEXT,
            motion_score REAL DEFAULT 0.0,
            boxes_json TEXT,
            notes TEXT,
            objects_json TEXT,
            faces_json TEXT,
            summary_label TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS known_faces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            embedding_blob BLOB NOT NULL,
            thumbnail_path TEXT,
            created_at REAL NOT NULL
        )
    """)

    # Migrate columns if they don't exist yet
    cursor.execute("PRAGMA table_info(events)")
    existing_cols = [row[1] for row in cursor.fetchall()]
    for col in ["objects_json", "faces_json", "summary_label"]:
        if col not in existing_cols:
            try:
                cursor.execute(f"ALTER TABLE events ADD COLUMN {col} TEXT")
            except Exception:
                pass

    conn.commit()
    conn.close()

def add_event(
    event_type: str,
    image_path: str,
    annotated_path: Optional[str] = None,
    motion_score: float = 0.0,
    boxes: Optional[List[Dict[str, int]]] = None,
    notes: Optional[str] = None,
    objects: Optional[List[Dict[str, Any]]] = None,
    faces: Optional[List[Dict[str, Any]]] = None,
    summary_label: Optional[str] = None
) -> Dict[str, Any]:
    now = time.time()
    dt_str = datetime.fromtimestamp(now).strftime("%Y-%m-%d %I:%M:%S %p")
    boxes_json = json.dumps(boxes) if boxes else None
    objects_json = json.dumps(objects) if objects else None
    faces_json = json.dumps(faces) if faces else None
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO events (
            timestamp, datetime_str, event_type, image_path, annotated_path,
            motion_score, boxes_json, notes, objects_json, faces_json, summary_label
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        now, dt_str, event_type, image_path, annotated_path,
        motion_score, boxes_json, notes, objects_json, faces_json, summary_label
    ))
    event_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    return {
        "id": event_id,
        "timestamp": now,
        "datetime_str": dt_str,
        "event_type": event_type,
        "image_path": image_path,
        "annotated_path": annotated_path,
        "motion_score": motion_score,
        "boxes": boxes or [],
        "notes": notes,
        "objects": objects or [],
        "faces": faces or [],
        "summary_label": summary_label or ("Motion Detected" if event_type == "motion" else "Snapshot")
    }

def get_events(limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM events ORDER BY timestamp DESC LIMIT ? OFFSET ?
    """, (limit, offset))
    rows = cursor.fetchall()
    conn.close()
    
    events = []
    for r in rows:
        d = dict(r)
        d["boxes"] = json.loads(d["boxes_json"]) if d.get("boxes_json") else []
        d["objects"] = json.loads(d["objects_json"]) if d.get("objects_json") else []
        d["faces"] = json.loads(d["faces_json"]) if d.get("faces_json") else []
        if not d.get("summary_label"):
            d["summary_label"] = "Motion Detected" if d.get("event_type") == "motion" else "Snapshot"
        events.append(d)
    return events

def get_event_by_id(event_id: int) -> Optional[Dict[str, Any]]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM events WHERE id = ?", (event_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        d = dict(row)
        d["boxes"] = json.loads(d["boxes_json"]) if d.get("boxes_json") else []
        d["objects"] = json.loads(d["objects_json"]) if d.get("objects_json") else []
        d["faces"] = json.loads(d["faces_json"]) if d.get("faces_json") else []
        return d
    return None

def delete_event(event_id: int) -> bool:
    event = get_event_by_id(event_id)
    if event:
        for k in ["image_path", "annotated_path"]:
            p = event.get(k)
            if p:
                fp = p if os.path.isabs(p) else os.path.join(BASE_DIR, p)
                if os.path.exists(fp):
                    try:
                        os.remove(fp)
                    except Exception:
                        pass
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM events WHERE id = ?", (event_id,))
        conn.commit()
        conn.close()
        return True
    return False

def clear_all_events():
    events = get_events(limit=5000)
    for ev in events:
        delete_event(ev["id"])

# Known Faces Management
def add_known_face(name: str, embedding_bytes: bytes, thumbnail_path: Optional[str] = None) -> int:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO known_faces (name, embedding_blob, thumbnail_path, created_at)
        VALUES (?, ?, ?, ?)
    """, (name, embedding_bytes, thumbnail_path, time.time()))
    face_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return face_id

def get_known_faces() -> List[Dict[str, Any]]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, thumbnail_path, created_at, embedding_blob FROM known_faces ORDER BY name ASC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def delete_known_face(face_id: int) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT thumbnail_path FROM known_faces WHERE id = ?", (face_id,))
    row = cursor.fetchone()
    if row and row[0]:
        fp = row[0] if os.path.isabs(row[0]) else os.path.join(BASE_DIR, row[0])
        if os.path.exists(fp):
            try:
                os.remove(fp)
            except Exception:
                pass
    cursor.execute("DELETE FROM known_faces WHERE id = ?", (face_id,))
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return deleted

# Settings Store
def get_setting(key: str, default: str = "") -> str:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else default

def set_setting(key: str, value: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO settings (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
    """, (key, value))
    conn.commit()
    conn.close()
