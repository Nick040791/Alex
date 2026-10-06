// Ring IP Cam Dashboard Controller
let ws = null;
let audioCtx = null;
let soundAlertsEnabled = true;
let desktopNotificationsEnabled = false;
let overlayEnabled = true;
let currentEventInModal = null;

// Initialize Web Audio Context on user interaction
function getAudioContext() {
  if (!audioCtx) {
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (AudioContextClass) {
      audioCtx = new AudioContextClass();
    }
  }
  if (audioCtx && audioCtx.state === 'suspended') {
    audioCtx.resume();
  }
  return audioCtx;
}

// Iconic Ring Chime Synthesizer (Realistic 2-tone harmonic Ding-Dong)
function playRingChime() {
  if (!soundAlertsEnabled) return;
  const ctx = getAudioContext();
  if (!ctx) return;

  const now = ctx.currentTime;

  // Tone 1: High "Ding" (approx 880 Hz / A5)
  const osc1 = ctx.createOscillator();
  const gain1 = ctx.createGain();
  osc1.type = 'sine';
  osc1.frequency.setValueAtTime(880, now);
  osc1.frequency.exponentialRampToValueAtTime(860, now + 0.6);

  gain1.gain.setValueAtTime(0, now);
  gain1.gain.linearRampToValueAtTime(0.4, now + 0.02);
  gain1.gain.exponentialRampToValueAtTime(0.001, now + 0.7);

  osc1.connect(gain1);
  gain1.connect(ctx.destination);
  osc1.start(now);
  osc1.stop(now + 0.7);

  // Tone 2: Lower "Dong" (approx 660 Hz / E5) starting 250ms later
  const osc2 = ctx.createOscillator();
  const gain2 = ctx.createGain();
  osc2.type = 'sine';
  osc2.frequency.setValueAtTime(659.25, now + 0.25);
  osc2.frequency.exponentialRampToValueAtTime(645, now + 1.2);

  gain2.gain.setValueAtTime(0, now + 0.25);
  gain2.gain.linearRampToValueAtTime(0.45, now + 0.27);
  gain2.gain.exponentialRampToValueAtTime(0.001, now + 1.3);

  osc2.connect(gain2);
  gain2.connect(ctx.destination);
  osc2.start(now + 0.25);
  osc2.stop(now + 1.3);
}

// Shutter sound for snapshots
function playShutterSound() {
  const ctx = getAudioContext();
  if (!ctx) return;
  const now = ctx.currentTime;
  const osc = ctx.createOscillator();
  const gain = ctx.createGain();
  osc.type = 'triangle';
  osc.frequency.setValueAtTime(1200, now);
  osc.frequency.exponentialRampToValueAtTime(300, now + 0.08);

  gain.gain.setValueAtTime(0.3, now);
  gain.gain.exponentialRampToValueAtTime(0.01, now + 0.08);

  osc.connect(gain);
  gain.connect(ctx.destination);
  osc.start(now);
  osc.stop(now + 0.09);
}

// Desktop notification
function showDesktopNotification(title, body) {
  if (desktopNotificationsEnabled && "Notification" in window && Notification.permission === "granted") {
    new Notification(title, {
      body: body,
      icon: "/static/favicon.ico"
    });
  }
}

// Connect WebSocket
function connectWebSocket() {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const wsUrl = `${protocol}//${window.location.host}/ws`;

  ws = new WebSocket(wsUrl);

  ws.onopen = () => {
    console.log("WebSocket connected to Ring Cam server");
    document.getElementById("statusDot").classList.add("online");
    document.getElementById("statusText").innerText = "Online";
  };

  ws.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data);
      handleWsMessage(msg);
    } catch (e) {
      console.error("WS Parse error", e);
    }
  };

  ws.onclose = () => {
    console.warn("WebSocket disconnected, retrying in 3s...");
    document.getElementById("statusDot").classList.remove("online");
    document.getElementById("statusText").innerText = "Reconnecting";
    setTimeout(connectWebSocket, 3000);
  };
}

function handleWsMessage(msg) {
  switch (msg.type) {
    case "motion_status":
      updateMotionMeter(msg.motion_score, msg.trigger_threshold, msg.motion_detected);
      if (msg.active_viewers !== undefined) {
        document.getElementById("viewersCount").innerText = `${msg.active_viewers} watching`;
      }
      break;

    case "motion_event":
      // Flash banner
      triggerMotionBanner();
      // Play Ring Chime
      playRingChime();
      // Push notification
      showDesktopNotification("Ring Cam Alert", `Motion detected at ${msg.event.datetime_str}`);
      // Prepend to activity feed
      prependActivityItem(msg.event);
      break;

    case "snapshot_taken":
      playShutterSound();
      prependActivityItem(msg.event);
      break;

    case "ring_chime":
      playRingChime();
      break;

    case "event_deleted":
      removeActivityItem(msg.id);
      break;

    case "events_cleared":
      loadEvents();
      break;

    case "settings_updated":
      if (msg.sensitivity !== undefined) {
        updateSensitivityUI(msg.sensitivity);
      }
      if (msg.zone !== undefined) {
        updateZoneUI(msg.zone);
      }
      break;

    case "control_action":
      fetchTelemetry();
      break;
  }
}

// Live Motion Energy Meter
function updateMotionMeter(score, threshold, isTriggered) {
  const banner = document.getElementById("motionBanner");
  const scoreElem = document.getElementById("telemetryMotionScore");
  const liveVal = document.getElementById("liveMeterValue");
  const threshVal = document.getElementById("liveThresholdValue");
  const meterFill = document.getElementById("meterFill");
  const meterLine = document.getElementById("meterThresholdLine");
  const statusBadge = document.getElementById("motionStatusBadge");

  const safeScore = score || 0.0;
  const safeThresh = threshold || 2.0;

  if (scoreElem) scoreElem.innerText = `${safeScore.toFixed(1)}%`;
  if (liveVal) liveVal.innerText = `${safeScore.toFixed(1)}%`;
  if (threshVal) threshVal.innerText = `${safeThresh.toFixed(1)}%`;

  // Scale meter between 0% and max(6.0%, threshold * 2.2)
  const maxScale = Math.max(6.0, safeThresh * 2.2);
  const fillPct = Math.min(100, Math.max(0, (safeScore / maxScale) * 100));
  const linePct = Math.min(100, Math.max(2, (safeThresh / maxScale) * 100));

  if (meterFill) {
    meterFill.style.width = `${fillPct}%`;
    if (isTriggered) {
      meterFill.classList.add("triggered");
    } else {
      meterFill.classList.remove("triggered");
    }
  }

  if (meterLine) {
    meterLine.style.left = `${linePct}%`;
  }

  if (statusBadge) {
    if (isTriggered) {
      statusBadge.classList.add("triggered");
      statusBadge.innerText = "MOTION TRIGGERED";
    } else {
      statusBadge.classList.remove("triggered");
      statusBadge.innerText = "MONITORING";
    }
  }

  if (banner) {
    if (isTriggered) {
      banner.classList.add("active");
    } else {
      banner.classList.remove("active");
    }
  }
}

function triggerMotionBanner() {
  const banner = document.getElementById("motionBanner");
  if (banner) banner.classList.add("active");
  setTimeout(() => {
    if (banner) banner.classList.remove("active");
  }, 4000);
}

// Live Motion Tuning Controller
let motionSaveTimeout = null;

function saveMotionSettingsDebounced(payload) {
  clearTimeout(motionSaveTimeout);
  motionSaveTimeout = setTimeout(async () => {
    try {
      await fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
    } catch (e) {
      console.error("Save settings error", e);
    }
  }, 150);
}

function onLiveSensitivityInput(val) {
  const num = parseInt(val);
  updateSensitivityUI(num);
  saveMotionSettingsDebounced({ motion_sensitivity: num });
}

function updateSensitivityUI(num) {
  const slider = document.getElementById("liveSensitivitySlider");
  const label = document.getElementById("liveSensitivityLabel");
  const desc = document.getElementById("sensitivityDesc");

  if (slider && slider.value != num) slider.value = num;
  if (label) label.innerText = `${num}%`;

  if (desc) {
    if (num >= 85) desc.innerText = "Ultra High (Pet / Distant movement)";
    else if (num >= 70) desc.innerText = "High Sensitivity (Active alerts)";
    else if (num >= 50) desc.innerText = "Balanced (Human Detection)";
    else desc.innerText = "Low (Major motion / Vehicles)";
  }

  // Update preset buttons state
  document.querySelectorAll(".preset-btn").forEach(btn => btn.classList.remove("active"));
  if (num === 35) document.getElementById("presetLow")?.classList.add("active");
  else if (num === 60) document.getElementById("presetMed")?.classList.add("active");
  else if (num === 80) document.getElementById("presetHigh")?.classList.add("active");
  else if (num === 95) document.getElementById("presetMax")?.classList.add("active");
}

function applyPreset(val) {
  onLiveSensitivityInput(val);
}

function setZone(zone) {
  updateZoneUI(zone);
  saveMotionSettingsDebounced({ motion_zone: zone });
}

function updateZoneUI(zone) {
  document.querySelectorAll(".zone-btn").forEach(btn => btn.classList.remove("active"));
  if (zone === "bottom") document.getElementById("zoneBottom")?.classList.add("active");
  else if (zone === "center") document.getElementById("zoneCenter")?.classList.add("active");
  else document.getElementById("zoneAll")?.classList.add("active");
}

function setCooldown(val) {
  saveMotionSettingsDebounced({ motion_cooldown: parseFloat(val) });
}

function toggleMotionDetection(enabled) {
  saveMotionSettingsDebounced({ motion_enabled: enabled });
  const badge = document.getElementById("motionStatusBadge");
  if (!enabled && badge) {
    badge.innerText = "DISABLED";
    badge.classList.remove("triggered");
  }
}

async function loadLiveMotionSettings() {
  try {
    const res = await fetch("/api/settings");
    const s = await res.json();
    if (s.motion_sensitivity !== undefined) updateSensitivityUI(s.motion_sensitivity);
    if (s.motion_zone) updateZoneUI(s.motion_zone);
    
    const cd = document.getElementById("quickCooldown");
    if (cd && s.motion_cooldown) cd.value = String(parseInt(s.motion_cooldown));

    const toggle = document.getElementById("quickMotionToggle");
    if (toggle && s.motion_enabled !== undefined) toggle.checked = s.motion_enabled;
  } catch (e) {
    console.error("Failed to load initial motion settings", e);
  }
}

// Activity Feed Rendering
async function loadEvents() {
  try {
    const res = await fetch("/api/events?limit=40");
    const data = await res.json();
    const list = document.getElementById("activityList");
    const countBadge = document.getElementById("eventCountBadge");

    countBadge.innerText = data.events.length;
    if (data.events.length === 0) {
      list.innerHTML = `<div class="empty-state">No motion events recorded yet. When movement is detected, snapshots will appear here.</div>`;
      return;
    }

    list.innerHTML = "";
    data.events.forEach((ev) => {
      list.appendChild(createActivityItemElem(ev));
    });
  } catch (e) {
    console.error("Failed to load events", e);
  }
}

function createActivityItemElem(ev) {
  const item = document.createElement("div");
  item.className = "activity-item";
  item.id = `event-${ev.id}`;
  item.onclick = (e) => {
    if (!e.target.closest(".btn-delete-event")) {
      openLightbox(ev);
    }
  };

  const isMotion = ev.event_type === "motion";
  const badgeClass = isMotion ? "motion" : "snapshot";
  const badgeText = isMotion ? `Motion ${ev.motion_score}%` : "Snapshot";
  const displayImg = ev.annotated_path || ev.image_path;

  item.innerHTML = `
    <img src="/${displayImg}" class="activity-thumb" loading="lazy" alt="Event preview" />
    <div class="activity-info">
      <div class="activity-title">
        <span class="activity-badge ${badgeClass}">${badgeText}</span>
      </div>
      <div class="activity-time">${ev.datetime_str}</div>
    </div>
    <button class="btn-delete-event" title="Delete event" onclick="deleteEvent(${ev.id})">
      ✕
    </button>
  `;
  return item;
}

function prependActivityItem(ev) {
  const list = document.getElementById("activityList");
  const emptyState = list.querySelector(".empty-state");
  if (emptyState) emptyState.remove();

  const item = createActivityItemElem(ev);
  item.style.animation = "modalIn 0.3s ease";
  list.insertBefore(item, list.firstChild);

  const countBadge = document.getElementById("eventCountBadge");
  countBadge.innerText = parseInt(countBadge.innerText || "0") + 1;
}

function removeActivityItem(id) {
  const item = document.getElementById(`event-${id}`);
  if (item) item.remove();
  const countBadge = document.getElementById("eventCountBadge");
  const count = Math.max(0, parseInt(countBadge.innerText || "1") - 1);
  countBadge.innerText = count;
}

async function deleteEvent(id) {
  try {
    await fetch(`/api/events/${id}`, { method: "DELETE" });
    removeActivityItem(id);
    if (currentEventInModal && currentEventInModal.id === id) {
      closeModal("lightboxModal");
    }
  } catch (e) {
    console.error("Delete failed", e);
  }
}

async function clearAllEvents() {
  if (confirm("Are you sure you want to clear all recorded motion events?")) {
    try {
      await fetch("/api/events", { method: "DELETE" });
      loadEvents();
    } catch (e) {
      console.error("Clear failed", e);
    }
  }
}

// Lightbox Modal
function openLightbox(ev) {
  currentEventInModal = ev;
  const modal = document.getElementById("lightboxModal");
  const img = document.getElementById("lightboxImg");
  const title = document.getElementById("lightboxTitle");
  const time = document.getElementById("lightboxTime");
  const score = document.getElementById("lightboxScore");
  const downloadLink = document.getElementById("lightboxDownload");

  const displayImg = ev.annotated_path || ev.image_path;
  img.src = `/${displayImg}`;
  title.innerText = ev.event_type === "motion" ? "Motion Event Detail" : "Snapshot Detail";
  time.innerText = ev.datetime_str;
  score.innerText = ev.event_type === "motion" ? `Motion Area: ${ev.motion_score}%` : "Manual Capture";
  downloadLink.href = `/${ev.image_path}`;

  modal.classList.add("open");
}

function deleteCurrentLightboxEvent() {
  if (currentEventInModal) {
    deleteEvent(currentEventInModal.id);
  }
}

// Telemetry Poller
async function fetchTelemetry() {
  try {
    const res = await fetch("/api/status");
    const data = await res.json();

    document.getElementById("telemetryHost").innerText = `${data.tablet_host}:${data.tablet_http_port}`;
    document.getElementById("telemetryStream").innerText = (data.stream_type || "mjpeg").toUpperCase();
    
    const pingElem = document.getElementById("telemetryPing");
    pingElem.innerText = data.ping_ok ? "Reachable" : "Unreachable";
    pingElem.style.color = data.ping_ok ? "var(--ring-success)" : "var(--ring-alert)";

    if (data.remote_status) {
      const activeCam = data.remote_status.camera || "Back";
      document.getElementById("telemetryCamera").innerText = activeCam.toUpperCase();
      
      const flashBtn = document.getElementById("btnFlashlight");
      if (data.remote_status.flashlight) {
        flashBtn.classList.add("active");
        flashBtn.innerHTML = `🔦 Spotlight ON`;
      } else {
        flashBtn.classList.remove("active");
      }
    }
  } catch (e) {
    console.error("Telemetry fetch error", e);
  }
}

// Hardware & Camera Controls
async function triggerFlashlight() {
  getAudioContext();
  try {
    const res = await fetch("/api/control/flashlight", { method: "POST" });
    const data = await res.json();
    fetchTelemetry();
  } catch (e) {
    console.error("Flashlight error", e);
  }
}

async function switchCamera() {
  getAudioContext();
  try {
    const res = await fetch("/api/control/switch", { method: "POST" });
    const data = await res.json();
    fetchTelemetry();
  } catch (e) {
    console.error("Switch error", e);
  }
}

let currentRotation = 0;
async function rotateCamera() {
  currentRotation = (currentRotation + 90) % 360;
  try {
    await fetch("/api/control/rotate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ value: String(currentRotation) })
    });
  } catch (e) {
    console.error("Rotate error", e);
  }
}

async function takeSnapshot() {
  getAudioContext();
  try {
    playShutterSound();
    const btn = document.getElementById("btnSnapshot");
    btn.style.transform = "scale(0.95)";
    setTimeout(() => { btn.style.transform = "none"; }, 150);

    const res = await fetch("/api/snapshot?annotated=true");
    const event = await res.json();
    prependActivityItem(event);
  } catch (e) {
    console.error("Snapshot error", e);
  }
}

function triggerChime() {
  getAudioContext();
  playRingChime();
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "trigger_chime" }));
  }
}

function toggleOverlay() {
  overlayEnabled = !overlayEnabled;
  const feed = document.getElementById("videoFeed");
  feed.src = `/stream/live?overlay=${overlayEnabled}&t=${Date.now()}`;
  const btn = document.getElementById("btnOverlay");
  if (overlayEnabled) {
    btn.classList.add("active");
    btn.innerText = "🔲 Motion Boxes: ON";
  } else {
    btn.classList.remove("active");
    btn.innerText = "🔲 Motion Boxes: OFF";
  }
}

function toggleFullscreen() {
  const container = document.getElementById("videoContainer");
  if (!document.fullscreenElement) {
    container.requestFullscreen().catch((err) => console.error(err));
  } else {
    document.exitFullscreen();
  }
}

// Settings Modal & Save
async function openSettings() {
  try {
    const res = await fetch("/api/settings");
    const s = await res.json();

    document.getElementById("cfgTabletHost").value = s.tablet_host;
    document.getElementById("cfgTabletHttpPort").value = s.tablet_http_port;
    document.getElementById("cfgTabletRtspPort").value = s.tablet_rtsp_port;

    document.getElementById("cfgSensitivity").value = s.motion_sensitivity;
    document.getElementById("valSensitivity").innerText = s.motion_sensitivity;

    document.getElementById("cfgMinArea").value = s.motion_min_area;
    document.getElementById("valMinArea").innerText = `${s.motion_min_area} px`;

    document.getElementById("cfgCooldown").value = s.motion_cooldown;
    document.getElementById("valCooldown").innerText = `${s.motion_cooldown}s`;

    document.getElementById("cfgMotionEnabled").checked = s.motion_enabled;
    document.getElementById("cfgSoundEnabled").checked = soundAlertsEnabled;

    document.getElementById("settingsModal").classList.add("open");
  } catch (e) {
    console.error("Open settings error", e);
  }
}

async function saveSettings() {
  const payload = {
    tablet_host: document.getElementById("cfgTabletHost").value.trim(),
    tablet_http_port: parseInt(document.getElementById("cfgTabletHttpPort").value),
    tablet_rtsp_port: parseInt(document.getElementById("cfgTabletRtspPort").value),
    motion_sensitivity: parseInt(document.getElementById("cfgSensitivity").value),
    motion_min_area: parseInt(document.getElementById("cfgMinArea").value),
    motion_cooldown: parseFloat(document.getElementById("cfgCooldown").value),
    motion_enabled: document.getElementById("cfgMotionEnabled").checked
  };

  soundAlertsEnabled = document.getElementById("cfgSoundEnabled").checked;

  try {
    await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    closeModal("settingsModal");
    fetchTelemetry();
  } catch (e) {
    console.error("Save settings failed", e);
  }
}

async function resetCameraService() {
  if (confirm("Reset the tablet camera service to clear any frozen frames?")) {
    try {
      await fetch("/api/control/reset", { method: "POST" });
      alert("Camera reset command sent to tablet.");
    } catch (e) {
      console.error("Reset camera error", e);
    }
  }
}

function closeModal(id) {
  document.getElementById(id).classList.remove("open");
}

// Request desktop notification permission
function requestNotificationPermission() {
  if ("Notification" in window && Notification.permission !== "granted") {
    Notification.requestPermission().then((permission) => {
      if (permission === "granted") {
        desktopNotificationsEnabled = true;
      }
    });
  } else if ("Notification" in window && Notification.permission === "granted") {
    desktopNotificationsEnabled = true;
  }
}

// Initialization on load
window.addEventListener("DOMContentLoaded", () => {
  connectWebSocket();
  loadEvents();
  loadLiveMotionSettings();
  fetchTelemetry();
  setInterval(fetchTelemetry, 3500);

  // Setup range slider listeners
  const sens = document.getElementById("cfgSensitivity");
  if (sens) sens.oninput = (e) => document.getElementById("valSensitivity").innerText = e.target.value;

  const minA = document.getElementById("cfgMinArea");
  if (minA) minA.oninput = (e) => document.getElementById("valMinArea").innerText = `${e.target.value} px`;

  const cool = document.getElementById("cfgCooldown");
  if (cool) cool.oninput = (e) => document.getElementById("valCooldown").innerText = `${e.target.value}s`;

  // First interaction unlocks Audio
  document.body.addEventListener("click", () => {
    getAudioContext();
    requestNotificationPermission();
  }, { once: true });
});
