const ADL = [
  {
    label: "Medication",
    icon: "Rx",
    intent: "pick_object",
    payload: { label: "bottle", adl_id: "medication" },
    confirm: true,
    detail: "Target bottle · confirm before grasp · slow lift",
  },
  {
    label: "Remote",
    icon: "TV",
    intent: "pick_object",
    payload: { label: "remote", adl_id: "remote" },
    confirm: true,
    detail: "Target remote · confirm before grasp · flat-object grip",
  },
  {
    label: "Water",
    icon: "H2O",
    intent: "pick_object",
    payload: { label: "bottle", adl_id: "water" },
    confirm: true,
    detail: "Target bottle · confirm before grasp · upright carry",
  },
  {
    label: "Phone",
    icon: "Call",
    intent: "pick_object",
    payload: { label: "cell phone", adl_id: "phone" },
    confirm: true,
    detail: "Target phone · confirm before grasp · gentle close",
  },
];

const MOTION = [
  { label: "Open", intent: "open_claw" },
  { label: "Close", intent: "close_claw" },
  { label: "Lift up", intent: "lift_up" },
  { label: "Lift down", intent: "lift_down" },
  { label: "Base left", intent: "base_left" },
  { label: "Base right", intent: "base_right" },
  { label: "Rotate left", intent: "rotate_left" },
  { label: "Rotate right", intent: "rotate_right" },
];

const QUICK_COMMANDS = [
  "get my pills",
  "bring the remote",
  "open the claw",
  "go home",
  "gentle open",
  "stop",
];

const PRESETS = [
  {
    name: "Steady day",
    detail: "Balanced pace with all inputs available.",
    values: {
      motor: "moderate",
      speed: 30,
      voice: true,
      gesture: true,
      manual: true,
      fatigue: true,
      gentle: true,
      caregiver: false,
      rest: 45,
    },
  },
  {
    name: "Low energy",
    detail: "Slower moves, fewer inputs, more rest.",
    values: {
      motor: "severe",
      speed: 20,
      voice: true,
      gesture: false,
      manual: true,
      fatigue: true,
      gentle: true,
      caregiver: true,
      rest: 25,
    },
  },
  {
    name: "Practice",
    detail: "Gesture studio ready with safe manual control.",
    values: {
      motor: "early",
      speed: 35,
      voice: false,
      gesture: true,
      manual: true,
      fatigue: false,
      gentle: true,
      caregiver: false,
      rest: 60,
    },
  },
];

const FAMILIES = ["lift", "base", "rotate", "claw"];

const $ = (id) => document.getElementById(id);

const state = {
  profile: null,
  teachDraft: null,
  captureTemplate: null,
  wizardStep: 1,
  dirty: false,
  cameraStreamOn: false,
  cameraActive: false,
  radarLogical: { width: 500, height: 280 },
};

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...opts.headers },
    ...opts,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    const detail = err.detail;
    if (typeof detail === "object" && detail !== null) {
      throw new Error(detail.message || JSON.stringify(detail));
    }
    throw new Error(detail || res.statusText);
  }
  return res.json();
}

function toast(message, kind = "ok") {
  const root = $("toast-root");
  const el = document.createElement("div");
  el.className = `toast-item ${kind}`;
  el.textContent = message;
  root.appendChild(el);
  setTimeout(() => el.remove(), 3200);
}

function setWizardStep(n) {
  state.wizardStep = n;
  document.querySelectorAll(".wizard-steps .step").forEach((el) => {
    const s = parseInt(el.dataset.step, 10);
    el.classList.toggle("active", s === n);
    el.classList.toggle("done", s < n);
  });
  $("step-describe").classList.toggle("hidden", n !== 1);
  $("step-capture").classList.toggle("hidden", n < 2);
  if (n >= 2 && state.profile?.enable_gesture_input !== false) {
    startCameraPreview();
  }
}

function setActiveTab(name) {
  document.querySelectorAll(".tab").forEach((tab) => {
    const active = tab.dataset.tab === name;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
  });
  document.querySelectorAll(".tab-panel").forEach((panel) => {
    const active = panel.id === `panel-${name}`;
    panel.classList.toggle("active", active);
    panel.toggleAttribute("hidden", !active);
  });
}

function bindTabs() {
  document.querySelectorAll(".tab-nav .tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      setActiveTab(tab.dataset.tab);
      if (tab.dataset.tab === "gestures" && state.profile?.enable_gesture_input !== false) {
        startCameraPreview();
      }
    });
  });
}

function markDirty() {
  state.dirty = true;
  $("profile-dirty").classList.remove("hidden");
}

function bindProfileDirty() {
  $("profile-form").querySelectorAll("input, select").forEach((el) => {
    el.addEventListener("change", markDirty);
  });
  $("speed-pct").addEventListener("input", markDirty);
}

function applyInputModes(profile) {
  const voice = profile.enable_voice_input !== false;
  const gesture = profile.enable_gesture_input !== false;
  const manual = profile.enable_manual_input !== false;

  $("controls-card").classList.toggle("disabled-overlay", !manual);
  $("manual-badge").textContent = manual ? "Manual on" : "Manual off";
  $("manual-badge").className = manual ? "badge badge-ok" : "badge badge-off";

  $("studio-card").classList.toggle("disabled-overlay", !gesture);
  if (!gesture) {
    $("gesture-badge").textContent = "Gestures off";
    $("gesture-badge").className = "badge badge-off";
    stopCameraPreview();
  } else if (state.cameraActive) {
    $("gesture-badge").textContent = "Camera live";
    $("gesture-badge").className = "badge badge-ok";
  } else {
    $("gesture-badge").textContent = "Camera starting";
    $("gesture-badge").className = "badge badge-off";
  }

  $("voice-card").classList.toggle("disabled-overlay", !voice);

  const chips = $("input-chips");
  while (chips.firstChild) chips.removeChild(chips.firstChild);
  [
    ["Voice", voice],
    ["Gestures", gesture],
    ["Web / manual", manual],
  ].forEach(([label, on]) => {
    const c = document.createElement("span");
    c.className = `chip ${on ? "chip-on" : "chip-off"}`;
    c.textContent = label;
    chips.appendChild(c);
  });

  const banner = $("alert-banner");
  if (!voice && !gesture && !manual) {
    banner.textContent = "All control methods are off — enable at least one in Profile.";
    banner.classList.remove("hidden");
  } else if (profile.rest_reminder_due) {
    banner.textContent = "Rest reminder — stretch your hands and take a short break.";
    banner.classList.remove("hidden");
  } else {
    banner.classList.add("hidden");
  }
}

function bindControls() {
  const adl = $("adl-grid");
  ADL.forEach((item) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "btn task-btn";
    b.innerHTML = `<span class="task-icon">${item.icon}</span><span><strong>${item.label}</strong><small>${item.detail}</small></span>`;
    b.addEventListener("focus", () => showTaskContext(item));
    b.addEventListener("mouseenter", () => showTaskContext(item));
    b.addEventListener("click", () => {
      showTaskContext(item);
      sendCommand(item.intent, item.payload, item.confirm);
    });
    adl.appendChild(b);
  });

  const motion = $("motion-grid");
  MOTION.forEach((item) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "btn";
    b.textContent = item.label;
    b.addEventListener("click", () => sendCommand(item.intent, item.payload || {}));
    motion.appendChild(b);
  });

  $("btn-estop").addEventListener("click", () => sendCommand("emergency_stop", {}));
  document.querySelectorAll("[data-intent]").forEach((el) => {
    el.addEventListener("click", () => sendCommand(el.dataset.intent, {}));
  });
}

function showTaskContext(item) {
  $("task-context").textContent = `${item.label}: ${item.detail}.`;
}

async function sendCommand(intent, payload = {}, requiresConfirmation = false) {
  try {
    await api("/api/command", {
      method: "POST",
      body: JSON.stringify({
        intent,
        payload,
        requires_confirmation: requiresConfirmation || undefined,
      }),
    });
    toast(`Sent: ${intent.replace(/_/g, " ")}`);
  } catch (e) {
    toast(e.message, "err");
  }
}

async function sendSay(text) {
  const trimmed = text.trim();
  if (!trimmed) return;
  try {
    await api("/api/command/say", {
      method: "POST",
      body: JSON.stringify({ text: trimmed }),
    });
    $("voice-feedback").textContent = `Sent: “${trimmed}”`;
    $("voice-text").value = "";
    toast("Command queued");
  } catch (e) {
    toast(e.message, "err");
  }
}

function bindVoiceBar() {
  $("btn-send-say").addEventListener("click", () => sendSay($("voice-text").value));
  $("voice-text").addEventListener("keydown", (e) => {
    if (e.key === "Enter") sendSay($("voice-text").value);
  });

  const chips = $("quick-commands");
  QUICK_COMMANDS.forEach((text) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "command-chip";
    chip.textContent = text;
    chip.addEventListener("click", () => {
      $("voice-text").value = text;
      sendSay(text);
    });
    chips.appendChild(chip);
  });

  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (SpeechRecognition) {
    $("btn-voice-say").addEventListener("click", () => {
      const rec = new SpeechRecognition();
      rec.lang = "en-US";
      rec.onstart = () => {
        $("btn-voice-say").classList.add("mic-active");
      };
      rec.onend = () => {
        $("btn-voice-say").classList.remove("mic-active");
      };
      rec.onerror = () => {
        $("btn-voice-say").classList.remove("mic-active");
      };
      rec.onresult = (ev) => {
        const text = ev.results[0][0].transcript;
        $("voice-text").value = text;
        sendSay(text);
      };
      rec.start();
    });
  } else {
    $("btn-voice-say").hidden = true;
  }
}

function applyPreset(preset) {
  $("motor-level").value = preset.values.motor;
  $("speed-pct").value = preset.values.speed;
  $("speed-label").textContent = `${preset.values.speed}%`;
  $("enable-voice").checked = preset.values.voice;
  $("enable-gesture").checked = preset.values.gesture;
  $("enable-manual").checked = preset.values.manual;
  $("fatigue-slowdown").checked = preset.values.fatigue;
  $("gentle-reach").checked = preset.values.gentle;
  $("caregiver-mode").checked = preset.values.caregiver;
  $("rest-minutes").value = preset.values.rest;
  applyInputModes({
    ...(state.profile || {}),
    enable_voice_input: preset.values.voice,
    enable_gesture_input: preset.values.gesture,
    enable_manual_input: preset.values.manual,
  });
  updateComfortSummary({
    ...(state.profile || {}),
    default_speed_pct: preset.values.speed,
    rest_reminder_minutes: preset.values.rest,
    gentle_reach: preset.values.gentle,
    caregiver_mode: preset.values.caregiver,
  });
  markDirty();
  toast(`${preset.name} preset ready to save`);
}

function bindPresets() {
  const grid = $("preset-grid");
  PRESETS.forEach((preset) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "preset-card";
    button.innerHTML = `<strong>${preset.name}</strong><span>${preset.detail}</span>`;
    button.addEventListener("click", () => applyPreset(preset));
    grid.appendChild(button);
  });
}

function fillCatalogue(items) {
  const list = $("custom-catalogue");
  while (list.firstChild) list.removeChild(list.firstChild);
  $("catalogue-count").textContent = String((items || []).length);

  if (!items || !items.length) {
    const li = document.createElement("li");
    li.className = "empty";
    li.textContent = "No custom gestures yet — use the wizard above.";
    list.appendChild(li);
    return;
  }

  items.forEach((g) => {
    const li = document.createElement("li");
    const strong = document.createElement("strong");
    strong.textContent = g.display_name;
    li.appendChild(strong);
    li.appendChild(document.createTextNode(` → ${g.intent.replace(/_/g, " ")}`));
    const del = document.createElement("button");
    del.type = "button";
    del.className = "btn-link";
    del.textContent = "Remove";
    del.addEventListener("click", () => removeGesture(g.gesture_id));
    li.appendChild(del);
    list.appendChild(li);
  });
}

async function removeGesture(id) {
  if (!confirm("Remove this gesture from your catalogue?")) return;
  try {
    await api(`/api/gestures/${encodeURIComponent(id)}`, { method: "DELETE" });
    toast("Gesture removed");
    await loadProfile();
  } catch (e) {
    toast(e.message, "err");
  }
}

function fillGuide(guide) {
  const list = $("gesture-guide");
  while (list.firstChild) list.removeChild(list.firstChild);
  Object.entries(guide || {}).forEach(([key, desc]) => {
    const li = document.createElement("li");
    const strong = document.createElement("strong");
    strong.textContent = key.replace(/_/g, " ");
    li.appendChild(strong);
    li.appendChild(document.createTextNode(desc));
    list.appendChild(li);
  });
}

function fillProfile(data) {
  state.profile = data;
  $("display-name").value = data.display_name || "";
  $("motor-level").value = data.motor_level || "moderate";
  $("speed-pct").value = data.default_speed_pct ?? 30;
  $("speed-label").textContent = `${$("speed-pct").value}%`;
  $("enable-voice").checked = data.enable_voice_input !== false;
  $("enable-gesture").checked = data.enable_gesture_input !== false;
  $("enable-manual").checked = data.enable_manual_input !== false;
  $("fatigue-slowdown").checked = data.fatigue_slowdown !== false;
  $("gentle-reach").checked = data.gentle_reach !== false;
  $("caregiver-mode").checked = !!data.caregiver_mode;
  $("rest-minutes").value = data.rest_reminder_minutes ?? 45;

  const name = data.display_name || "there";
  $("hero-greeting").textContent = `Welcome, ${name}`;
  const level = (data.motor_level || "moderate").replace(/^\w/, (c) => c.toUpperCase());
  $("hero-sub").textContent = `${level} motor profile · speed ${data.default_speed_pct ?? 30}%`;

  fillGuide(data.gesture_guide);
  fillCatalogue(data.custom_gestures || []);
  applyInputModes(data);
  updateComfortSummary(data);
  state.dirty = false;
  $("profile-dirty").classList.add("hidden");
}

async function loadProfile() {
  const data = await api("/api/profile");
  fillProfile(data);
  localStorage.setItem("arthassist_profile", JSON.stringify(data));
}

$("profile-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = {
    display_name: $("display-name").value.trim(),
    motor_level: $("motor-level").value,
    quit_gesture: "peace_hold",
    default_speed_pct: parseInt($("speed-pct").value, 10),
    enable_voice_input: $("enable-voice").checked,
    enable_gesture_input: $("enable-gesture").checked,
    enable_manual_input: $("enable-manual").checked,
    fatigue_slowdown: $("fatigue-slowdown").checked,
    gentle_reach: $("gentle-reach").checked,
    caregiver_mode: $("caregiver-mode").checked,
    rest_reminder_minutes: parseInt($("rest-minutes").value, 10) || 0,
  };
  try {
    const saved = await api("/api/profile", { method: "PUT", body: JSON.stringify(body) });
    fillProfile(saved);
    localStorage.setItem("arthassist_profile", JSON.stringify(saved));
    toast("Profile saved — arm updated");
  } catch (err) {
    toast(err.message, "err");
  }
});

$("speed-pct").addEventListener("input", () => {
  $("speed-label").textContent = `${$("speed-pct").value}%`;
});

function bindStudio() {
  $("btn-interpret").addEventListener("click", async () => {
    const description = $("gesture-describe").value.trim();
    if (!description) return;
    try {
      const result = await api("/api/gestures/interpret", {
        method: "POST",
        body: JSON.stringify({ description }),
      });
      state.teachDraft = result;
      state.captureTemplate = null;
      setWizardStep(2);

      const box = $("interpret-result");
      box.classList.remove("hidden");
      while (box.firstChild) box.removeChild(box.firstChild);
      const p = document.createElement("p");
      p.textContent = `“${result.display_name}” will trigger ${result.intent.replace(/_/g, " ")}`;
      box.appendChild(p);
      if (result.builtin_match) {
        const warn = document.createElement("p");
        warn.className = "warn";
        warn.textContent = `Similar to built-in “${result.builtin_match}” — your personal version will still be saved if unique.`;
        box.appendChild(warn);
      }

      $("capture-status").textContent = "Hold your gesture steady when capture starts.";
      $("capture-fill").style.width = "0%";
      $("btn-capture-start").classList.remove("hidden");
      $("btn-capture-stop").classList.add("hidden");
      $("btn-save-gesture").classList.add("hidden");
    } catch (e) {
      toast(e.message, "err");
    }
  });

  $("btn-capture-start").addEventListener("click", async () => {
    if (!state.teachDraft) return;
    try {
      await api("/api/gestures/capture/start", {
        method: "POST",
        body: JSON.stringify({
          display_name: state.teachDraft.display_name,
          description: state.teachDraft.description,
          intent: state.teachDraft.intent,
          payload: state.teachDraft.payload,
        }),
      });
      $("capture-status").textContent = "Capturing — hold the pose…";
      $("btn-capture-start").classList.add("hidden");
      $("btn-capture-stop").classList.remove("hidden");
    } catch (e) {
      toast(e.message, "err");
    }
  });

  $("btn-capture-stop").addEventListener("click", async () => {
    try {
      const result = await api("/api/gestures/capture/stop", { method: "POST" });
      state.captureTemplate = result.template;
      setWizardStep(3);
      $("capture-status").textContent = `${result.sample_count} frames captured — ready to save.`;
      $("btn-capture-stop").classList.add("hidden");
      $("btn-save-gesture").classList.remove("hidden");
      $("capture-fill").style.width = "100%";
    } catch (e) {
      toast(e.message, "err");
    }
  });

  $("btn-save-gesture").addEventListener("click", async () => {
    if (!state.teachDraft || !state.captureTemplate) return;
    try {
      await api("/api/gestures/confirm", {
        method: "POST",
        body: JSON.stringify({
          display_name: state.teachDraft.display_name,
          description: state.teachDraft.description,
          intent: state.teachDraft.intent,
          payload: state.teachDraft.payload,
          template: state.captureTemplate,
        }),
      });
      toast(`Saved “${state.teachDraft.display_name}” to your catalogue`);
      state.teachDraft = null;
      state.captureTemplate = null;
      setWizardStep(1);
      $("interpret-result").classList.add("hidden");
      $("gesture-describe").value = "";
      await loadProfile();
    } catch (e) {
      toast(e.message, "err");
    }
  });

  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (SpeechRecognition) {
    $("btn-voice-describe").addEventListener("click", () => {
      const rec = new SpeechRecognition();
      rec.lang = "en-US";
      rec.onresult = (ev) => {
        $("gesture-describe").value = ev.results[0][0].transcript;
      };
      rec.start();
    });
  } else {
    $("btn-voice-describe").hidden = true;
  }
}

function updateArm(arm) {
  const map = { base: 250, lift: 225, rotate: 170, claw: 165 };
  const mins = { base: 10, lift: 15, rotate: 10, claw: 15 };
  ["base", "lift", "rotate", "claw"].forEach((j) => {
    const v = arm[j] ?? 0;
    const max = map[j];
    const min = mins[j];
    const pct = Math.max(0, Math.min(100, ((v - min) / (max - min)) * 100));
    $(`m-${j}`).style.width = `${pct}%`;
    $(`v-${j}`).textContent = `${v}°`;
  });
  $("v-range").textContent = arm.range_mm > 0 ? String(arm.range_mm) : "—";
}

function updateActivity(items) {
  const feed = $("activity-feed");
  while (feed.firstChild) feed.removeChild(feed.firstChild);
  if (!items || !items.length) {
    const li = document.createElement("li");
    li.textContent = "Waiting for activity…";
    feed.appendChild(li);
    return;
  }
  items.slice().reverse().forEach((ev) => {
    const li = document.createElement("li");
    const span = document.createElement("span");
    span.className = "src";
    span.textContent = ev.source || "?";
    li.appendChild(span);
    li.appendChild(document.createTextNode(` ${ev.text || ev.kind || ""}`));
    feed.appendChild(li);
  });
}

function updatePending(pending) {
  const el = $("pending-banner");
  if (!pending) {
    el.classList.add("hidden");
    return;
  }
  el.classList.remove("hidden");
  el.textContent = pending.message || `Confirm ${pending.intent}? (${pending.remaining_s}s left)`;
}

function updateDiversity(diversity) {
  const track = $("diversity-track");
  while (track.firstChild) track.removeChild(track.firstChild);
  if (!diversity) return;

  const covered = new Set(diversity.covered || []);
  FAMILIES.forEach((f) => {
    const pill = document.createElement("span");
    pill.className = `diversity-pill ${covered.has(f) ? "done" : ""}`;
    pill.textContent = f;
    track.appendChild(pill);
  });

  $("diversity-hint").textContent = diversity.hint || "";
}

function updateSmart(smart, fallback) {
  const panel = $("smart-panel");
  while (panel.firstChild) panel.removeChild(panel.firstChild);
  if (!smart) return;

  const addLine = (text, alert = false) => {
    const p = document.createElement("p");
    p.textContent = text;
    if (alert) p.className = "alert";
    panel.appendChild(p);
  };

  addLine(`${smart.session_minutes} min · ${smart.command_count} moves`);
  if (smart.fatigue_slowdown) addLine("Fatigue detected — pace is slowing.", true);
  if (smart.rest_reminder_due) addLine("Time for a hand stretch.", true);
  if (smart.caregiver_mode) addLine("Caregiver mode — extra confirmations on.");

  const pill = $("smart-pill");
  if (smart.rest_reminder_due || smart.fatigue_slowdown) {
    pill.textContent = "Rest";
    pill.className = "pill pill-warn";
  } else {
    pill.textContent = `${smart.command_count} moves`;
    pill.className = "pill pill-ok";
  }

  const tip = $("fallback-tip");
  tip.textContent = (fallback && fallback.last_suggestion) || "";

  if (state.profile) {
    updateComfortSummary({
      ...state.profile,
      rest_reminder_due: smart.rest_reminder_due,
      fatigue_active: smart.fatigue_slowdown,
      session_minutes: smart.session_minutes,
    });
  }

  const health = $("input-health");
  while (health.firstChild) health.removeChild(health.firstChild);
  const channels = (fallback && fallback.channels) || {};
  Object.entries(channels).forEach(([name, stats]) => {
    const row = document.createElement("div");
    row.className = "health-row";
    const label = document.createElement("span");
    label.textContent = name;
    const bar = document.createElement("div");
    bar.className = "health-bar";
    const fill = document.createElement("i");
    const total = (stats.successes || 0) + (stats.failures || 0);
    const pct = total ? Math.round((stats.successes / total) * 100) : 100;
    fill.style.width = `${pct}%`;
    bar.appendChild(fill);
    const pctEl = document.createElement("span");
    pctEl.className = "health-pct";
    pctEl.textContent = `${pct}%`;
    row.appendChild(label);
    row.appendChild(bar);
    row.appendChild(pctEl);
    health.appendChild(row);
  });
}

function updateComfortSummary(profile) {
  const el = $("comfort-summary");
  if (!el) return;
  const speed = profile.default_speed_pct ?? $("speed-pct").value ?? 30;
  const rest = profile.rest_reminder_minutes ?? $("rest-minutes").value ?? 0;
  const confirmations = profile.gentle_reach !== false || profile.caregiver_mode;
  const lines = [
    ["Speed", `${speed}%`],
    ["Rest", rest > 0 ? `${rest} min` : "Off"],
    ["Confirm", confirmations ? "On" : "Standard"],
  ];
  if (profile.fatigue_active) lines.push(["Pace", "Slowing"]);
  el.replaceChildren(...lines.map(([label, value]) => {
    const row = document.createElement("p");
    row.innerHTML = `<span>${label}</span><strong>${value}</strong>`;
    return row;
  }));
}

function updateCapture(capture) {
  if (!capture || !capture.active) return;
  const n = capture.sample_count || 0;
  $("capture-fill").style.width = `${Math.min(100, (n / 24) * 100)}%`;
  $("capture-status").textContent = `Capturing… ${n} samples — hold steady`;
}

let radarCtx = null;
let radarSweepAngle = 0;
let radarObjects = [];
let hoveredObj = null;

function setupHiDpiCanvas(canvas, logicalW, logicalH) {
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = Math.round(logicalW * dpr);
  canvas.height = Math.round(logicalH * dpr);
  canvas.style.width = `${logicalW}px`;
  canvas.style.height = `${logicalH}px`;
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  state.radarLogical = { width: logicalW, height: logicalH };
  return ctx;
}

function startCameraPreview() {
  if (state.cameraStreamOn) return;
  const img = $("camera-preview");
  const placeholder = $("camera-placeholder");
  if (!img) return;
  img.src = `/api/camera/mjpeg?ts=${Date.now()}`;
  img.classList.remove("hidden");
  placeholder.classList.add("hidden");
  state.cameraStreamOn = true;
  img.onerror = () => {
    state.cameraStreamOn = false;
    img.classList.add("hidden");
    placeholder.classList.remove("hidden");
    $("camera-hint").textContent = "Camera unavailable — check permissions or run main.py --web";
    $("camera-hint").className = "warn";
  };
}

function stopCameraPreview() {
  const img = $("camera-preview");
  if (!img || !state.cameraStreamOn) return;
  img.removeAttribute("src");
  img.classList.add("hidden");
  $("camera-placeholder").classList.remove("hidden");
  state.cameraStreamOn = false;
}

function updateDepthStatus(pipeline) {
  const badge = $("depth-status-badge");
  if (!badge || !pipeline) return;
  if (!pipeline.active) {
    badge.textContent = "Depth off";
    badge.className = "badge badge-off";
    return;
  }
  const mm = pipeline.last_median_mm;
  const ready = pipeline.depth_ready;
  badge.textContent = ready && mm ? `Depth ~${mm}mm` : (pipeline.summary || "Depth on");
  badge.className = ready ? "badge badge-ok" : "badge badge-off";
  badge.title = pipeline.summary || "Monocular depth + LiDAR fusion";
}

function updateCameraStatus(camera) {
  if (!camera) return;
  state.cameraActive = !!camera.active;
  const hint = $("camera-hint");
  if (hint) {
    hint.textContent = camera.message || (camera.active ? "Camera live" : "Camera offline");
    hint.className = camera.active ? "ok" : "warn";
  }
  if (camera.available && state.profile?.enable_gesture_input !== false) {
    if (state.wizardStep >= 2) startCameraPreview();
  }
  const gestureOn = state.profile?.enable_gesture_input !== false;
  if (gestureOn) {
    $("gesture-badge").textContent = camera.active ? "Camera live" : "Camera starting";
    $("gesture-badge").className = camera.active ? "badge badge-ok" : "badge badge-off";
  }
}

function initRadar() {
  const canvas = $("radar-canvas");
  if (!canvas) return;
  radarCtx = setupHiDpiCanvas(canvas, 500, 280);

  canvas.addEventListener("mousemove", (e) => {
    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;

    const scaleX = state.radarLogical.width / rect.width;
    const scaleY = state.radarLogical.height / rect.height;
    const lx = x * scaleX;
    const ly = y * scaleY;
    const { width, height } = state.radarLogical;
    const centerX = width / 2;
    const centerY = height - 20;
    const maxRadius = height - 50;
    const maxDistance = 800; // mm

    hoveredObj = null;
    canvas.style.cursor = "default";

    for (const obj of radarObjects) {
      const rad = (180 - obj.base_deg) * Math.PI / 180;
      const r = (Math.min(maxDistance, obj.distance_mm) / maxDistance) * maxRadius;
      const ox = centerX + r * Math.cos(rad);
      const oy = centerY - r * Math.sin(rad);

      const dist = Math.hypot(lx - ox, ly - oy);
      if (dist < 18) {
        hoveredObj = obj;
        canvas.style.cursor = "pointer";
        break;
      }
    }
  });

  canvas.addEventListener("click", () => {
    if (hoveredObj) {
      let adlId = null;
      if (hoveredObj.label === "bottle") adlId = "medication";
      else if (hoveredObj.label === "remote") adlId = "remote";
      else if (hoveredObj.label === "cell phone") adlId = "phone";
      
      const payload = { label: hoveredObj.label };
      if (adlId) payload.adl_id = adlId;

      sendCommand("pick_object", payload, true);
      toast(`Directing arm to pick up ${hoveredObj.label}`);
    }
  });

  requestAnimationFrame(tickRadar);
}

function tickRadar() {
  if (!radarCtx) return;
  drawRadarFrame();
  radarSweepAngle = (radarSweepAngle + 0.012) % Math.PI;
  requestAnimationFrame(tickRadar);
}

function drawRadarFrame() {
  const canvas = $("radar-canvas");
  if (!canvas || !radarCtx) return;
  const width = state.radarLogical.width;
  const height = state.radarLogical.height;
  
  radarCtx.clearRect(0, 0, width, height);

  const centerX = width / 2;
  const centerY = height - 20;
  const maxRadius = height - 50;
  const maxDistance = 800; // mm

  // Draw grid concentric semicircle rings
  radarCtx.strokeStyle = "rgba(88, 214, 183, 0.15)";
  radarCtx.lineWidth = 1;
  [0.25, 0.5, 0.75, 1.0].forEach((pct) => {
    radarCtx.beginPath();
    radarCtx.arc(centerX, centerY, maxRadius * pct, Math.PI, 0);
    radarCtx.stroke();
    
    radarCtx.fillStyle = "rgba(169, 176, 170, 0.55)";
    radarCtx.font = "9px DM Sans";
    radarCtx.fillText(`${Math.round(maxDistance * pct)}mm`, centerX + 6, centerY - maxRadius * pct + 3);
  });

  // Draw polar angular grid lines
  [30, 60, 90, 120, 150].forEach((deg) => {
    const rad = deg * Math.PI / 180;
    const x = centerX + maxRadius * Math.cos(rad);
    const y = centerY - maxRadius * Math.sin(rad);
    radarCtx.beginPath();
    radarCtx.moveTo(centerX, centerY);
    radarCtx.lineTo(x, y);
    radarCtx.stroke();
    
    radarCtx.fillStyle = "rgba(169, 176, 170, 0.55)";
    radarCtx.font = "9px DM Sans";
    const lx = centerX + (maxRadius + 14) * Math.cos(rad);
    const ly = centerY - (maxRadius + 14) * Math.sin(rad);
    radarCtx.textAlign = "center";
    radarCtx.fillText(`${180 - deg}°`, lx, ly + 3);
  });

  // Draw outer border
  radarCtx.strokeStyle = "rgba(88, 214, 183, 0.35)";
  radarCtx.lineWidth = 1.5;
  radarCtx.beginPath();
  radarCtx.arc(centerX, centerY, maxRadius, Math.PI, 0);
  radarCtx.stroke();

  // Draw sweep light cone
  radarCtx.save();
  radarCtx.beginPath();
  radarCtx.moveTo(centerX, centerY);
  radarCtx.arc(centerX, centerY, maxRadius, -Math.PI + radarSweepAngle - 0.22, -Math.PI + radarSweepAngle);
  radarCtx.closePath();
  const grad = radarCtx.createRadialGradient(centerX, centerY, 0, centerX, centerY, maxRadius);
  grad.addColorStop(0, "rgba(88, 214, 183, 0.16)");
  grad.addColorStop(1, "rgba(88, 214, 183, 0.0)");
  radarCtx.fillStyle = grad;
  radarCtx.fill();
  radarCtx.restore();

  // Draw sweep line
  radarCtx.strokeStyle = "rgba(88, 214, 183, 0.45)";
  radarCtx.lineWidth = 1.5;
  radarCtx.beginPath();
  radarCtx.moveTo(centerX, centerY);
  radarCtx.lineTo(centerX + maxRadius * Math.cos(-Math.PI + radarSweepAngle), centerY + maxRadius * Math.sin(-Math.PI + radarSweepAngle));
  radarCtx.stroke();

  // Draw arm heading ray if connected
  if (state.arm && typeof state.arm.base === "number") {
    const armRad = (180 - state.arm.base) * Math.PI / 180;
    radarCtx.strokeStyle = "rgba(183, 166, 255, 0.65)";
    radarCtx.lineWidth = 2.5;
    radarCtx.beginPath();
    radarCtx.moveTo(centerX, centerY);
    radarCtx.lineTo(centerX + maxRadius * 0.9 * Math.cos(armRad), centerY - maxRadius * 0.9 * Math.sin(armRad));
    radarCtx.stroke();
    
    radarCtx.fillStyle = "#b7a6ff";
    radarCtx.beginPath();
    radarCtx.arc(centerX + maxRadius * 0.9 * Math.cos(armRad), centerY - maxRadius * 0.9 * Math.sin(armRad), 4.5, 0, Math.PI * 2);
    radarCtx.fill();
  }

  // Draw center base node
  radarCtx.fillStyle = "#1e2220";
  radarCtx.strokeStyle = "rgba(88, 214, 183, 0.7)";
  radarCtx.lineWidth = 2.5;
  radarCtx.beginPath();
  radarCtx.arc(centerX, centerY, 8, 0, Math.PI * 2);
  radarCtx.fill();
  radarCtx.stroke();

  // Draw mapped objects
  for (const obj of radarObjects) {
    const rad = (180 - obj.base_deg) * Math.PI / 180;
    const r = (Math.min(maxDistance, obj.distance_mm) / maxDistance) * maxRadius;
    const ox = centerX + r * Math.cos(rad);
    const oy = centerY - r * Math.sin(rad);

    // Fade glow on sweep pass
    const sweepDiff = Math.abs(radarSweepAngle - ((180 - obj.base_deg) * Math.PI / 180));
    const isLit = sweepDiff < 0.25;

    if (hoveredObj === obj || isLit) {
      radarCtx.fillStyle = "rgba(88, 214, 183, 0.3)";
      radarCtx.beginPath();
      radarCtx.arc(ox, oy, 15, 0, Math.PI * 2);
      radarCtx.fill();
    }

    // Node core
    radarCtx.fillStyle = hoveredObj === obj ? "#58d6b7" : "rgba(88, 214, 183, 0.85)";
    radarCtx.beginPath();
    radarCtx.arc(ox, oy, 6, 0, Math.PI * 2);
    radarCtx.fill();

    // Node border
    radarCtx.strokeStyle = "rgba(88, 214, 183, 0.95)";
    radarCtx.lineWidth = 1.5;
    radarCtx.beginPath();
    radarCtx.arc(ox, oy, 9, 0, Math.PI * 2);
    radarCtx.stroke();

    // Text labels
    radarCtx.fillStyle = "#f4f0e8";
    radarCtx.font = "bold 10px DM Sans";
    radarCtx.textAlign = "center";
    radarCtx.fillText(obj.label, ox, oy - 15);

    radarCtx.fillStyle = "rgba(169, 176, 170, 0.9)";
    radarCtx.font = "8px DM Sans";
    radarCtx.fillText(`${obj.distance_mm}mm (${Math.round(obj.confidence * 100)}%)`, ox, oy + 18);
  }
}

function applyLivePayload(msg) {
  if (msg.type === "error") {
    $("live-pill").textContent = "Robot offline";
    $("live-pill").className = "pill pill-off";
    return;
  }
  if (msg.type !== "status") return;

  state.arm = msg.arm || {};
  if (msg.environment) {
    radarObjects = msg.environment;
    const badge = $("radar-status-badge");
    if (badge) {
      badge.textContent = radarObjects.length > 0 ? `${radarObjects.length} Mapped` : "Scanning";
      badge.className = radarObjects.length > 0 ? "badge badge-ok" : "badge badge-off";
    }
  }

  updateArm(msg.arm || {});
  updateActivity(msg.activity);
  updatePending(msg.pending);
  updateDiversity(msg.gesture_diversity);
  updateSmart(msg.smart, msg.input_fallback);
  updateCapture(msg.gesture_capture);
  updateCameraStatus(msg.camera);
  updateDepthStatus(msg.vision_pipeline);

  if (msg.profile_summary && state.profile) {
    applyInputModes({ ...state.profile, ...msg.profile_summary });
  }

  const serial = msg.serial || {};
  $("serial-pill").textContent = serial.mode === "live" ? "Arm connected" : `Sim · ${serial.mode || "?"}`;
  $("serial-pill").className = serial.mode === "live" ? "pill pill-ok" : "pill pill-warn";
}

function connectLive() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/live`);
  const live = $("live-pill");

  ws.onopen = () => {
    live.textContent = "Live";
    live.className = "pill pill-ok";
  };
  ws.onclose = () => {
    live.textContent = "Reconnecting…";
    live.className = "pill pill-warn";
    setTimeout(connectLive, 2000);
  };
  ws.onmessage = (ev) => {
    applyLivePayload(JSON.parse(ev.data));
  };
}

async function init() {
  bindTabs();
  bindControls();
  bindVoiceBar();
  bindStudio();
  bindPresets();
  bindProfileDirty();
  setWizardStep(1);
  setActiveTab("control");
  initRadar();

  window.addEventListener("resize", () => {
    const canvas = $("radar-canvas");
    if (canvas) radarCtx = setupHiDpiCanvas(canvas, 500, 280);
  });

  try {
    await loadProfile();
  } catch {
    const cached = localStorage.getItem("arthassist_profile");
    if (cached) {
      try {
        fillProfile(JSON.parse(cached));
      } catch {
        localStorage.removeItem("arthassist_profile");
      }
    }
    toast("Running offline — start main.py --web to connect", "err");
  }
  connectLive();
}

init();
