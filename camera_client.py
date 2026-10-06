import time
import asyncio
import threading
import subprocess
import cv2
import numpy as np
import httpx
from typing import Optional, Dict, Any, Tuple

class CameraClient:
    def __init__(self, host: str = "100.105.4.70", http_port: int = 8080, rtsp_port: int = 8554):
        self.host = host
        self.http_port = http_port
        self.rtsp_port = rtsp_port
        
        self.latest_frame: Optional[np.ndarray] = None
        self.latest_frame_bytes: Optional[bytes] = None
        self.latest_frame_time: float = 0.0
        
        self.is_connected = False
        self.connection_error: Optional[str] = "Connecting..."
        self.stream_type = "mjpeg"  # "mjpeg" or "rtsp"
        
        self.status_data: Dict[str, Any] = {
            "status": "initializing",
            "camera": "unknown",
            "flashlight": False,
            "connections": 0,
            "targetFps": 10,
            "host": host,
            "http_port": http_port,
            "rtsp_port": rtsp_port
        }
        
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.http_port}"

    @property
    def rtsp_url(self) -> str:
        return f"rtsp://{self.host}:{self.rtsp_port}/camera"

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._capture_loop, daemon=True, name="CameraCaptureWorker")
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def update_target(self, host: str, http_port: int = 8080, rtsp_port: int = 8554):
        with self._lock:
            self.host = host
            self.http_port = http_port
            self.rtsp_port = rtsp_port
            self.is_connected = False
            self.connection_error = "Target updated, reconnecting..."

    def check_ping(self) -> bool:
        """Check if tablet responds to ICMP ping"""
        try:
            res = subprocess.run(
                ["ping", "-c", "1", "-W", "1", self.host],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            return res.returncode == 0
        except Exception:
            return False

    async def get_remote_status(self) -> Dict[str, Any]:
        """Fetch status from tablet HTTP API"""
        url = f"{self.base_url}/status"
        try:
            async with httpx.AsyncClient(timeout=2.5) as client:
                res = await client.get(url)
                if res.status_code == 200:
                    data = res.json()
                    with self._lock:
                        self.status_data.update(data)
                        self.status_data["host"] = self.host
                        self.status_data["http_port"] = self.http_port
                        self.status_data["online"] = True
                    return self.status_data
        except Exception as e:
            with self._lock:
                self.status_data["online"] = False
                self.status_data["error"] = str(e)
        return self.status_data

    async def switch_camera(self) -> Dict[str, Any]:
        url = f"{self.base_url}/switch"
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                res = await client.get(url)
                if res.status_code == 200:
                    return res.json()
                return {"success": False, "error": f"HTTP {res.status_code}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def toggle_flashlight(self) -> Dict[str, Any]:
        url = f"{self.base_url}/toggleFlashlight"
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                res = await client.get(url)
                if res.status_code == 200:
                    return res.json()
                return {"success": False, "error": f"HTTP {res.status_code}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def set_rotation(self, value: str) -> Dict[str, Any]:
        url = f"{self.base_url}/setRotation"
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                res = await client.get(url, params={"value": value})
                if res.status_code == 200:
                    return res.json()
                return {"success": False, "error": f"HTTP {res.status_code}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def set_format(self, value: str) -> Dict[str, Any]:
        url = f"{self.base_url}/setFormat"
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                res = await client.get(url, params={"value": value})
                if res.status_code == 200:
                    return res.json()
                return {"success": False, "error": f"HTTP {res.status_code}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def reset_camera(self) -> Dict[str, Any]:
        url = f"{self.base_url}/resetCamera"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                res = await client.get(url)
                if res.status_code == 200:
                    return res.json() if res.headers.get("content-type", "").startswith("application/json") else {"success": True}
                return {"success": False, "error": f"HTTP {res.status_code}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def fetch_snapshot_bytes(self) -> Optional[bytes]:
        url = f"{self.base_url}/snapshot"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                res = await client.get(url)
                if res.status_code == 200:
                    return res.content
        except Exception:
            pass
        return None

    def get_latest_frame(self) -> Tuple[Optional[np.ndarray], Optional[bytes], bool]:
        """
        Returns (frame_ndarray, frame_jpeg_bytes, is_connected)
        If offline, generates a standby status graphic.
        """
        with self._lock:
            if self.is_connected and self.latest_frame is not None:
                return self.latest_frame.copy(), self.latest_frame_bytes, True
        
        # Standby graphic
        standby_frame = self._generate_standby_frame()
        _, jpeg_bytes = cv2.imencode('.jpg', standby_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        return standby_frame, jpeg_bytes.tobytes(), False

    def _generate_standby_frame(self) -> np.ndarray:
        """Create a stylish Ring-like standby frame with connection diagnostics"""
        w, h = 1280, 720
        img = np.zeros((h, w, 3), dtype=np.uint8)
        # Gradient dark slate background
        for y in range(h):
            factor = y / float(h)
            img[y, :] = [int(15 + 10 * factor), int(18 + 12 * factor), int(28 + 15 * factor)]

        # Draw Ring circular logo / radar
        center = (w // 2, h // 2 - 60)
        cv2.circle(img, center, 80, (40, 50, 70), 2)
        cv2.circle(img, center, 55, (255, 168, 0), 3)  # Ring cyan accent
        cv2.circle(img, center, 20, (255, 168, 0), -1)

        # Pulse animation circle based on current time
        pulse_r = int(55 + (time.time() * 20) % 45)
        cv2.circle(img, center, pulse_r, (120, 90, 30), 1)

        # Status text
        ping_ok = self.check_ping()
        title = "RING IP CAM - WAITING FOR STREAM"
        subtitle = f"Device: {self.host} (Tailscale)"
        status_line1 = f"Tablet Tailscale Ping: {'OK (ONLINE)' if ping_ok else 'UNREACHABLE'}"
        err_msg = self.connection_error or "Connecting to stream..."
        tip = "Tip: In the IP Cam app on the tablet, tap 'Start Server' to begin broadcasting."

        cv2.putText(img, title, (w // 2 - 270, h // 2 + 80), cv2.FONT_HERSHEY_DUPLEX, 0.85, (255, 255, 255), 2)
        cv2.putText(img, subtitle, (w // 2 - 170, h // 2 + 120), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (180, 200, 220), 1)
        
        ping_color = (0, 220, 100) if ping_ok else (0, 70, 240)
        cv2.putText(img, status_line1, (w // 2 - 180, h // 2 + 160), cv2.FONT_HERSHEY_SIMPLEX, 0.6, ping_color, 1)
        cv2.putText(img, f"Status: {err_msg}", (w // 2 - 200, h // 2 + 195), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (140, 160, 180), 1)
        cv2.putText(img, tip, (w // 2 - 310, h // 2 + 240), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 200, 255), 1)

        return img

    def _is_port_open(self, port: int, timeout: float = 0.5) -> bool:
        import socket
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(timeout)
                return s.connect_ex((self.host, port)) == 0
        except Exception:
            return False

    def _capture_loop(self):
        """Worker thread to continuously pull MJPEG frames, with RTSP fallback"""
        while not self._stop_event.is_set():
            # Check if HTTP port is listening
            if self._is_port_open(self.http_port):
                success = self._read_mjpeg_stream()
                if success:
                    continue

            # If HTTP port is not open, check if RTSP port is open and stream_type allows it
            elif self._is_port_open(self.rtsp_port) and self.stream_type == "rtsp":
                rtsp_success = self._read_rtsp_stream()
                if rtsp_success:
                    continue

            # Sleep between retry attempts
            time.sleep(2.0)

    def _read_mjpeg_stream(self) -> bool:
        stream_url = f"{self.base_url}/stream"
        try:
            with httpx.Client(timeout=httpx.Timeout(connect=3.0, read=10.0, write=5.0, pool=5.0)) as client:
                with client.stream("GET", stream_url) as response:
                    if response.status_code != 200:
                        with self._lock:
                            self.is_connected = False
                            self.connection_error = f"HTTP {response.status_code}"
                        return False

                    with self._lock:
                        self.is_connected = True
                        self.connection_error = None
                        self.stream_type = "mjpeg"

                    buffer = bytearray()
                    for chunk in response.iter_bytes(chunk_size=16384):
                        if self._stop_event.is_set():
                            break
                        buffer.extend(chunk)
                        
                        # Search for JPEG Start of Image (0xFF, 0xD8) and End of Image (0xFF, 0xD9)
                        a = buffer.find(b'\xff\xd8')
                        b = buffer.find(b'\xff\xd9')
                        if a != -1 and b != -1 and b > a:
                            jpg_bytes = bytes(buffer[a:b+2])
                            del buffer[:b+2]
                            
                            frame = cv2.imdecode(np.frombuffer(jpg_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
                            if frame is not None:
                                with self._lock:
                                    self.latest_frame = frame
                                    self.latest_frame_bytes = jpg_bytes
                                    self.latest_frame_time = time.time()
                                    self.is_connected = True
                                    self.connection_error = None
            return True
        except Exception as e:
            with self._lock:
                self.is_connected = False
                self.connection_error = str(e)
            return False

    def _read_rtsp_stream(self) -> bool:
        """Attempt to read from RTSP port using cv2.VideoCapture"""
        try:
            cap = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)
            cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 3000)
            cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 3000)
            
            if not cap.isOpened():
                return False

            with self._lock:
                self.is_connected = True
                self.connection_error = None
                self.stream_type = "rtsp"

            fail_count = 0
            while not self._stop_event.is_set():
                ret, frame = cap.read()
                if not ret or frame is None:
                    fail_count += 1
                    if fail_count > 10:
                        break
                    time.sleep(0.05)
                    continue

                fail_count = 0
                _, jpg_bytes = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
                raw_bytes = jpg_bytes.tobytes()

                with self._lock:
                    self.latest_frame = frame
                    self.latest_frame_bytes = raw_bytes
                    self.latest_frame_time = time.time()
                    self.is_connected = True
                    self.connection_error = None

            cap.release()
            return True
        except Exception as e:
            with self._lock:
                self.is_connected = False
                self.connection_error = f"RTSP: {str(e)}"
            return False
