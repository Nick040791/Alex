// Ring IP Cam Dashboard Controller
let ws = null;
let audioCtx = null;
let soundAlertsEnabled = true;
let deviceNotificationsEnabled = false;
let overlayEnabled = true;
let currentEventInModal = null;
let serviceWorkerRegistration = null;

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

// Device Vibration helper (supported on Android/mobile browsers)
function triggerDeviceVibration(pattern = [250, 100, 250]) {
  if (navigator.vibrate) {
    try {
      navigator.vibrate(pattern);
    } catch (e) {
      // Ignore vibration error if restricted
    }
  }
}

// Toast Alert Popup
let toastTimer = null;
function showToast(message, duration = 3000) {
  const toast = document.getElementById("toast");
  if (!toast) return;
  toast.innerHTML = message;
  toast.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    toast.classList.remove("show");
  }, duration);
}

// ============================================================
// DEVICE PUSH & BROWSER NOTIFICATIONS
// ============================================================

// Register Service Worker for PWA & Background Alerts
async function initServiceWorker() {
  if ('serviceWorker' in navigator) {
    try {
      const reg = await navigator.serviceWorker.register('/sw.js');
      serviceWorkerRegistration = reg;
      console.log('Ring Cam Service Worker active with scope:', reg.scope);
    } catch (err) {
      console.warn('Service Worker registration failed:', err);
    }
  }
  updateNotificationIndicator();
}

function updateNotificationIndicator() {
  const indicator = document.getElementById("notifIndicator");
  const icon = document.getElementById("notifIcon");
  const banner = document.getElementById("notifBanner");

  if (!("Notification" in window)) {
    if (indicator) indicator.style.display = "none";
    return;
  }

  if (Notification.permission === "granted") {
    deviceNotificationsEnabled = true;
    if (indicator) {
      indicator.classList.add("enabled");
      indicator.title = "Device alerts enabled";
    }
    if (icon) icon.innerText = "🔔";
    if (banner) banner.style.display = "none";
  } else if (Notification.permission === "denied") {
    deviceNotificationsEnabled = false;
    if (indicator) {
      indicator.classList.remove("enabled");
      indicator.title = "Notifications blocked in browser";
    }
    if (icon) icon.innerText = "🔕";
    if (banner) banner.style.display = "none";
  } else {
    // "default" - unprompted
    deviceNotificationsEnabled = false;
    if (indicator) {
      indicator.classList.remove("enabled");
      indicator.title = "Click to enable device alerts";
    }
    if (icon) icon.innerText = "🔔";
    // Check if user dismissed previously in this session
    if (banner && !sessionStorage.getItem("notif_banner_dismissed")) {
      banner.style.display = "block";
    }
  }
}

async function toggleNotifications() {
  if (!("Notification" in window)) {
    showToast("⚠️ Web notifications are not supported in this browser.");
    return;
  }

  if (Notification.permission === "granted") {
    showToast("✅ Device notifications are already active!");
    sendTestNotification();
    return;
  }

  try {
    const permission = await Notification.requestPermission();
    if (permission === "granted") {
      deviceNotificationsEnabled = true;
      triggerDeviceVibration([150, 80, 150]);
      updateNotificationIndicator();
      showToast("🔔 Device alerts enabled! You will receive system notifications on motion.");
      
      // Send welcome test notification
      sendDeviceNotification("Ring Cam Alert Enabled", "You will now receive notifications when motion is detected.", null);
    } else {
      updateNotificationIndicator();
      showToast("⚠️ Notifications were blocked. Enable them in your browser site settings.");
    }
  } catch (err) {
    console.error("Permission error", err);
  }
}

function enableNotificationsFromBanner() {
  dismissNotifBanner();
  toggleNotifications();
}

function dismissNotifBanner() {
  const banner = document.getElementById("notifBanner");
  if (banner) banner.style.display = "none";
  sessionStorage.setItem("notif_banner_dismissed", "1");
}

// Send system notification to mobile device / desktop
function sendDeviceNotification(title, body, imageUrl) {
  if (!("Notification" in window) || Notification.permission !== "granted") {
    return;
  }

  // Vibrate mobile device
  triggerDeviceVibration([250, 100, 250]);

  const notifOptions = {
    body: body,
    icon: '/static/icon-192.png',
    badge: '/static/icon-192.png',
    tag: 'ring-motion-alert',
    renotify: true,
    vibrate: [250, 100, 250],
    data: { url: '/' }
  };

  if (imageUrl) {
    notifOptions.image = imageUrl.startsWith('/') ? imageUrl : `/${imageUrl}`;
  }

  // Priority 1: Use active Service Worker (enables rich notifications & action buttons on mobile)
  if (serviceWorkerRegistration && serviceWorkerRegistration.showNotification) {
    serviceWorkerRegistration.showNotification(title, notifOptions).catch(() => {
      // Fallback
      new Notification(title, notifOptions);
    });
  } else if (navigator.serviceWorker && navigator.serviceWorker.controller) {
    navigator.serviceWorker.controller.postMessage({
      type: 'SHOW_NOTIFICATION',
      title: title,
      options: notifOptions
    });
  } else {
    // Fallback: Standard browser Notification
    try {
      new Notification(title, notifOptions);
    } catch (e) {
      console.warn("Notification error", e);
    }
  }
}

function sendTestNotification() {
  if (Notification.permission !== "granted") {
    toggleNotifications();
    return;
  }
  getAudioContext();
  playRingChime();
  sendDeviceNotification("Ring Cam Test Alert", "Motion detection alerts are functioning properly on this device!", null);
  showToast("📲 Test alert sent to your device!");
}

// ============================================================
// WEBSOCKET COMMUNICATION
// ============================================================

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
        const vElem = document.getElementById("viewersCount");
        if (vElem) vElem.innerText = `${msg.active_viewers} watching`;
      }
      break;

    case "motion_event":
      // Flash banner
      triggerMotionBanner();
      // Play Ring Chime audio
      playRingChime();
      // Vibrate mobile device
      triggerDeviceVibration([300, 150, 300]);
      // Trigger system device notification with snapshot preview
      sendDeviceNotification(
        "⚠️ Ring Cam: Motion Detected",
        `Motion detected at ${msg.event.datetime_str}`,
        msg.event.annotated_path || msg.event.image_path
      );
      // Prepend to activity feed
      prependActivityItem(msg.event);
      break;

    case "snapshot_taken":
      playShutterSound();
      triggerDeviceVibration([80]);
      prependActivityItem(msg.event);
      showToast("📸 Snapshot captured!");
      break;

    case "ring_chime":
      playRingChime();
      triggerDeviceVibration([200, 100, 200]);
      showToast("🛎️ Doorbell Chime!");
      break;

    case "event_deleted":
      removeActivityItem(msg.id);
      break;

    case "events_cleared":
      loadEvents();
      break;

    case "settings_updated":
      if (msg.sensitivity !== undefined) updateSensitivityUI(msg.sensitivity);
      if (msg.zone !== undefined) updateZoneUI(msg.zone);
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
  const safeThresh = threshold || 1.8;

  if (scoreElem) scoreElem.innerText = `${safeScore.toFixed(1)}%`;
  if (liveVal) liveVal.innerText = `${safeScore.toFixed(1)}%`;
  if (threshVal) threshVal.innerText = `${safeThresh.toFixed(1)}%`;

  // Scale meter dynamically
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

// ============================================================
// SENSITIVITY & MOTION SETTINGS
// ============================================================

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
    showToast("Motion detection paused");
  } else if (enabled) {
    showToast("Motion detection active");
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

// ============================================================
// MOBILE NAVIGATION CONTROLLER
// ============================================================

function switchMobileTab(tab) {
  const isMobile = window.innerWidth <= 768;
  document.querySelectorAll(".mob-tab").forEach(t => t.classList.remove("active"));

  const tabElem = document.getElementById(`tab${tab.charAt(0).toUpperCase() + tab.slice(1)}`);
  if (tabElem) tabElem.classList.add("active");

  const secVideo = document.getElementById("sectionVideo");
  const secTuning = document.getElementById("sectionTuning");
  const secActivity = document.getElementById("sectionActivity");

  if (!isMobile) {
    // Desktop: all visible
    if (secVideo) secVideo.style.display = "flex";
    if (secTuning) secTuning.style.display = "flex";
    if (secActivity) secActivity.style.display = "flex";
    return;
  }

  // Mobile mode: show chosen section or smooth scroll
  if (tab === "live") {
    if (secVideo) {
      secVideo.style.display = "flex";
      secVideo.scrollIntoView({ behavior: 'smooth' });
    }
    if (secTuning) secTuning.style.display = "flex";
    if (secActivity) secActivity.style.display = "none";
  } else if (tab === "tuning") {
    if (secVideo) secVideo.style.display = "flex";
    if (secTuning) {
      secTuning.style.display = "flex";
      secTuning.scrollIntoView({ behavior: 'smooth' });
    }
    if (secActivity) secActivity.style.display = "none";
  } else if (tab === "activity") {
    if (secVideo) secVideo.style.display = "none";
    if (secTuning) secTuning.style.display = "none";
    if (secActivity) {
      secActivity.style.display = "flex";
      secActivity.scrollIntoView({ behavior: 'smooth' });
    }
  }
}

// Reset view on window resize
window.addEventListener("resize", () => {
  if (window.innerWidth > 768) {
    const secVideo = document.getElementById("sectionVideo");
    const secTuning = document.getElementById("sectionTuning");
    const secActivity = document.getElementById("sectionActivity");
    if (secVideo) secVideo.style.display = "flex";
    if (secTuning) secTuning.style.display = "flex";
    if (secActivity) secActivity.style.display = "flex";
  }
});

// ============================================================
// ACTIVITY FEED RENDERING
// ============================================================

async function loadEvents() {
  try {
    const res = await fetch("/api/events?limit=40");
    const data = await res.json();
    const list = document.getElementById("activityList");
    const countBadge = document.getElementById("eventCountBadge");
    const mobBadge = document.getElementById("mobEventBadge");

    const total = data.events.length;
    if (countBadge) countBadge.innerText = total;
    if (mobBadge) mobBadge.innerText = total;

    if (total === 0) {
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
  const mobBadge = document.getElementById("mobEventBadge");
  const newCount = parseInt(countBadge.innerText || "0") + 1;
  if (countBadge) countBadge.innerText = newCount;
  if (mobBadge) mobBadge.innerText = newCount;
}

function removeActivityItem(id) {
  const item = document.getElementById(`event-${id}`);
  if (item) item.remove();
  const countBadge = document.getElementById("eventCountBadge");
  const mobBadge = document.getElementById("mobEventBadge");
  const count = Math.max(0, parseInt(countBadge.innerText || "1") - 1);
  if (countBadge) countBadge.innerText = count;
  if (mobBadge) mobBadge.innerText = count;
}

async function deleteEvent(id) {
  try {
    await fetch(`/api/events/${id}`, { method: "DELETE" });
    removeActivityItem(id);
    if (currentEventInModal && currentEventInModal.id === id) {
      closeModal("lightboxModal");
    }
    showToast("Event deleted");
  } catch (e) {
    console.error("Delete failed", e);
  }
}

async function clearAllEvents() {
  if (confirm("Are you sure you want to clear all recorded motion events?")) {
    try {
      await fetch("/api/events", { method: "DELETE" });
      loadEvents();
      showToast("Activity history cleared");
    } catch (e) {
      console.error("Clear failed", e);
    }
  }
}

// ============================================================
// LIGHTBOX MODAL
// ============================================================

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

// ============================================================
// TELEMETRY & HARDWARE CONTROLS
// ============================================================

async function fetchTelemetry() {
  try {
    const res = await fetch("/api/status");
    const data = await res.json();

    const hostElem = document.getElementById("telemetryHost");
    if (hostElem) hostElem.innerText = data.tablet_host;
    
    const streamElem = document.getElementById("telemetryStream");
    if (streamElem) streamElem.innerText = (data.stream_type || "mjpeg").toUpperCase();
    
    const pingElem = document.getElementById("telemetryPing");
    if (pingElem) {
      pingElem.innerText = data.ping_ok ? "OK" : "DOWN";
      pingElem.style.color = data.ping_ok ? "var(--ring-success)" : "var(--ring-alert)";
    }

    if (data.remote_status) {
      const activeCam = data.remote_status.camera || "Back";
      const camElem = document.getElementById("telemetryCamera");
      if (camElem) camElem.innerText = activeCam.toUpperCase();
      
      const flashBtn = document.getElementById("btnFlashlight");
      if (flashBtn) {
        if (data.remote_status.flashlight) {
          flashBtn.classList.add("active");
          flashBtn.innerHTML = `🔦 Spotlight ON`;
        } else {
          flashBtn.classList.remove("active");
          flashBtn.innerHTML = `🔦 Spotlight`;
        }
      }
    }
  } catch (e) {
    console.error("Telemetry fetch error", e);
  }
}

async function triggerFlashlight() {
  getAudioContext();
  try {
    await fetch("/api/control/flashlight", { method: "POST" });
    fetchTelemetry();
    showToast("Spotlight toggled");
  } catch (e) {
    console.error("Flashlight error", e);
  }
}

async function switchCamera() {
  getAudioContext();
  try {
    await fetch("/api/control/switch", { method: "POST" });
    fetchTelemetry();
    showToast("Camera switched");
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
    showToast(`Rotated to ${currentRotation}°`);
  } catch (e) {
    console.error("Rotate error", e);
  }
}

async function takeSnapshot() {
  getAudioContext();
  try {
    playShutterSound();
    const btn = document.getElementById("btnSnapshot");
    if (btn) {
      btn.style.transform = "scale(0.95)";
      setTimeout(() => { btn.style.transform = "none"; }, 150);
    }

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
  triggerDeviceVibration([200, 100, 200]);
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "trigger_chime" }));
  }
  showToast("🛎️ Ring chime sounded!");
}

function toggleOverlay() {
  overlayEnabled = !overlayEnabled;
  const feed = document.getElementById("videoFeed");
  feed.src = `/stream/live?overlay=${overlayEnabled}&t=${Date.now()}`;
  const btn = document.getElementById("btnOverlay");
  if (overlayEnabled) {
    btn.classList.add("active");
    btn.innerText = "🔲 Boxes: ON";
  } else {
    btn.classList.remove("active");
    btn.innerText = "🔲 Boxes: OFF";
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

// ============================================================
// SETTINGS MODAL
// ============================================================

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
    showToast("Settings saved");
  } catch (e) {
    console.error("Save settings failed", e);
  }
}

async function resetCameraService() {
  if (confirm("Reset the tablet camera service to clear any frozen frames?")) {
    try {
      await fetch("/api/control/reset", { method: "POST" });
      showToast("Camera reset command dispatched");
    } catch (e) {
      console.error("Reset camera error", e);
    }
  }
}

function closeModal(id) {
  document.getElementById(id).classList.remove("open");
}

// ============================================================
// APP INITIALIZATION
// ============================================================

window.addEventListener("DOMContentLoaded", () => {
  initServiceWorker();
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

  // First touch / tap unlocks Web Audio & prompts notification
  document.body.addEventListener("click", () => {
    getAudioContext();
  }, { once: true });
});
