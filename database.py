import sqlite3
import json
import os
import time
from datetime import datetime
from typing import List, Dict, Any, Optional

DB_PATH = os.path.join(os.path.dirname(__file__), "events.db")

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
            notes TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

def add_event(
    event_type: str,
    image_path: str,
    annotated_path: Optional[str] = None,
    motion_score: float = 0.0,
    boxes: Optional[List[Dict[str, int]]] = None,
    notes: Optional[str] = None
) -> Dict[str, Any]:
    now = time.time()
    dt_str = datetime.fromtimestamp(now).strftime("%Y-%m-%d %I:%M:%S %p")
    boxes_json = json.dumps(boxes) if boxes else None
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO events (timestamp, datetime_str, event_type, image_path, annotated_path, motion_score, boxes_json, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (now, dt_str, event_type, image_path, annotated_path, motion_score, boxes_json, notes))
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
        "notes": notes
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
        return d
    return None

def delete_event(event_id: int) -> bool:
    event = get_event_by_id(event_id)
    if event:
        # Remove files if they exist
        for k in ["image_path", "annotated_path"]:
            p = event.get(k)
            if p and os.path.exists(p):
                try:
                    os.remove(p)
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
    events = get_events(limit=1000)
    for ev in events:
        delete_event(ev["id"])

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
