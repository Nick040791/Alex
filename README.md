# Ring IP Cam Dashboard (Tailscale)

A full-featured Ring-like security camera system powered by an Android tablet running the [IP Cam](https://github.com/tobi01001/IP_Cam) APK over Tailscale.

## Architecture

- **Tablet (Camera & Sensors)**: `k12e-row` at `100.105.4.70`
  - Runs the IP Cam app (Ktor HTTP server on port `8080` and RTSP on port `8554`).
  - Provides MJPEG streaming, JPEG snapshots, flashlight control, and camera switching.
- **Server / Hub**: `serverdeskhq` at `100.79.108.38`
  - Pulls the video feed from the tablet over Tailscale.
  - Runs computer vision motion detection (OpenCV MOG2 background subtractor).
  - Automatically captures snapshots when movement is detected.
  - Relays video stream and pushes real-time WebSocket events.
  - Serves the Ring web dashboard on port `8088`.
- **Clients**: Any device on the Tailscale network (e.g. `clientdesk`, smartphones, laptops) can access:
  - **HTTPS (Secure & PWA Ready)**: `https://serverdeskhq.tail4f9ce7.ts.net:8445`
  - **HTTP (Direct IP)**: `http://100.79.108.38:8088`

## Features

- **Live Streaming**: Zero-lag MJPEG stream with optional real-time motion bounding boxes overlay.
- **Ring Doorbell Chimes & Audio**: Iconic two-tone Ring chime synthesizer synthesized via Web Audio API.
- **Spotlight Control**: Remotely toggle the tablet's rear flashlight / torch (`/toggleFlashlight`).
- **Flip Camera**: Switch between Front and Back camera (`/switch`).
- **Rotate Feed**: 90° / 180° / 270° orientation control (`/setRotation`).
- **Smart Motion Detection**:
  - Sensitivity slider (subtle to major motion).
  - Minimum motion area threshold to ignore bugs or small noise.
  - Event cooldown to group continuous movement sessions.
  - Visual cyan motion bounding boxes.
- **Event Activity Timeline**:
  - Automatic timestamped event captures with motion intensity score.
  - Lightbox modal to inspect full-resolution photos, download, or delete.
  - Persistent SQLite storage (`events.db`).
- **Instant Alerts**: WebSockets sync motion status, chimes, and viewer count across all open browser tabs.
- **AI Object Recognition (YOLOv8)**:
  - Real-time classification for 80 COCO categories (People, Cars, Trucks, Motorcycles, Bicycles, Dogs, Cats, Birds, Packages, Bags).
  - Smart labeled bounding boxes with confidence scores.
- **Facial Detection & Recognition (YuNet + SFace)**:
  - High-speed YuNet face landmark detection.
  - SFace 128-D cosine embedding face matcher.
  - Name and enroll known people directly from any event snapshot or live stream.
  - Automatically identifies family members and visitors (e.g. `👤 Nick`).
- **PWA & Mobile Push Notifications**:
  - Installable Progressive Web App with service worker background support.
  - Native browser & mobile device push notifications with vibration patterns.
  - Rich notifications identifying specific detected persons or objects (e.g. `⚠️ Ring Cam: 👤 Nick + 🚗 Vehicle`).

## Quick Start

1. **On the Tablet (`100.105.4.70`)**:
   - Open the **IP Cam** app.
   - Grant Camera permissions.
   - Tap **"Start Server"**.
   - Confirm it shows running on port `8080`.

2. **Access the Dashboard**:
   - **HTTPS (Recommended for Mobile / Notifications)**: Open **`https://serverdeskhq.tail4f9ce7.ts.net:8445`**
   - **HTTP**: Open `http://100.79.108.38:8088`

3. **Face Enrollment**:
   - **From Captured Events**: Open any event in the Activity Feed and type a name under "Name This Face" -> Click **👤 Enroll Face**.
   - **From Live View or Photo Upload**: Go to **Settings (⚙️) -> 👤 AI & Known Faces** -> Click **📸 Capture from Live View** or **📁 Upload Photo**.

4. **To Run as a Background System Service**:
   ```bash
   sudo cp /home/mrnicholas/Dev/Alex/ring-cam.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now ring-cam
   ```
