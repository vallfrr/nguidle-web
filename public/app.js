import { boot } from '/engine.js';
import { initialProfile, saves } from '/saves.js';

const byId = (id) => document.getElementById(id);
const canvas = byId("game");
const surface = byId("surface");
const failure = byId("failure");

const file = byId("save-file");

const gameText = byId("game-text");
const hoverHoldMs = 450;


let pickerSlot;
const pending = [];
let engine;
let saveService;
let previousFrame;
let frameInfo;
let resolution;
let gotFrame = false;
let keyboardFocus = false;
let textOpen = false;
let waitingForTextClose = false;


function enqueue(event) {
  if (event.type === "navigate" || event.type === "scroll") mousePosition = undefined;
  const last = pending.at(-1);
  if (event.type === "move" && last?.type === "move") pending.pop();
  if (event.type === "scroll" && last?.type === "scroll") {
    last.x += event.x;
    last.y += event.y;
    return;
  }
  if (pending.length < 96) pending.push(event);
}

function point(event) {
  const rect = canvas.getBoundingClientRect();
  return { x: (event.clientX - rect.left) / rect.width, y: (event.clientY - rect.top) / rect.height };
}

let touch;
let mousePosition;
let lastBounds;

function moveMouse(event) {
  mousePosition = { clientX: event.clientX, clientY: event.clientY };
  enqueue({ type: "move", ...point(mousePosition) });
}

canvas.addEventListener("pointerdown", (event) => {
  if (!event.isPrimary) {
    if (event.pointerType === "touch" && touch && !touch.dragging) touch.secondary = true;
    return;
  }
  if (!gotFrame) return;
  canvas.focus({ preventScroll: true });
  canvas.setPointerCapture(event.pointerId);
  if (event.pointerType === "touch") {
    mousePosition = undefined;
    touch = { start: point(event), x: event.clientX, y: event.clientY, started: event.timeStamp, dragging: false, secondary: false };
    enqueue({ type: "move", x: -1, y: -1 });
  } else {
    mousePosition = { clientX: event.clientX, clientY: event.clientY };
    enqueue({ type: "button", ...point(event), button: event.button, down: true });
  }
  event.preventDefault();
});
canvas.addEventListener("pointermove", (event) => {
  if (!event.isPrimary || !gotFrame) return;
  if (touch && !touch.dragging && !touch.secondary && Math.hypot(event.clientX - touch.x, event.clientY - touch.y) > 5) {
    touch.dragging = true;
    enqueue({ type: "button", ...touch.start, button: 0, down: true });
  }
  if (event.pointerType !== "touch") moveMouse(event);
  else if (touch?.dragging) enqueue({ type: "move", ...point(event) });
});
canvas.addEventListener("pointerup", (event) => {
  if (event.isPrimary && gotFrame) {
    if (event.pointerType === "touch" && touch) {
      const inspecting = touch.inspecting || event.timeStamp - touch.started >= hoverHoldMs;
      const button = touch.secondary ? 2 : 0;
      if (inspecting && !touch.dragging && !touch.secondary) {
        enqueue({ type: "move", ...touch.start });
        touch = undefined;
        return;
      }
      if (!touch.dragging) enqueue({ type: "button", ...touch.start, button, down: true });
      enqueue({ type: "button", ...point(event), button, down: false });
      touch = undefined;
    } else {
      enqueue({ type: "button", ...point(event), button: event.button, down: false });
    }
    if (event.pointerType !== "mouse") enqueue({ type: "move", x: -1, y: -1 });
  }
});
canvas.addEventListener("pointercancel", (event) => {
  if (event.isPrimary) {
    touch = undefined;
    enqueue({ type: "reset" });
  }
});
surface.addEventListener("pointerleave", (event) => {
  if (event.pointerType !== "touch" && !canvas.hasPointerCapture(event.pointerId)) {
    mousePosition = undefined;
    enqueue({ type: "move", x: -1, y: -1 });
  }
});
canvas.addEventListener("contextmenu", (event) => event.preventDefault());
canvas.addEventListener("wheel", (event) => {
  const unit = event.deltaMode === 1 ? 3 : event.deltaMode === 2 ? 1 : 100;
  enqueue({ type: "wheel", ...point(event), delta: -event.deltaY / unit });
  event.preventDefault();
}, { passive: false });
const heldKeys = new Map();
canvas.addEventListener("blur", () => {
  heldKeys.clear();
  enqueue({ type: "reset" });
});

const directions = { ArrowUp: "up", ArrowDown: "down", ArrowLeft: "left", ArrowRight: "right", Enter: "activate", " ": "activate", Escape: "back" };
canvas.addEventListener("keydown", (event) => {
  if (event.key === "F1" && !event.repeat) {
    keyboardFocus = !keyboardFocus;
    event.preventDefault();
    return;
  }
  if ((keyboardFocus || frameInfo?.home) && directions[event.key]) {
    enqueue({ type: "navigate", action: directions[event.key] });
    event.preventDefault();
    return;
  }
  if (event.key === "Tab" || event.key.startsWith("F") && event.key.length > 1) return;
  if (event.ctrlKey && event.key !== "Control") return;
  const key = heldKeys.get(event.code) ?? { key: event.key, right: event.location === 2 };
  heldKeys.set(event.code, key);
  enqueue({ type: "key", ...key, down: true, repeat: event.repeat });
  if (directions[event.key]) event.preventDefault();
});
window.addEventListener("keyup", (event) => {
  const key = heldKeys.get(event.code);
  if (heldKeys.delete(event.code)) enqueue({ type: "key", ...key, down: false, repeat: false });
});
window.addEventListener("blur", () => {
  touch = undefined;
  mousePosition = undefined;
  heldKeys.clear();
  enqueue({ type: "reset" });
});

let gamepadButtons = new Set();
let lastDirection = "";
let repeatAfter = 0;
let lastPad = 0;
const stick = (axis) => {
  const v = axis || 0;
  return Math.sign(v) * Math.max(0, (Math.abs(v) - .22) / .78);
};
function pollGamepad(now) {
  const dt = Math.min(.05, (now - lastPad) / 1000);
  lastPad = now;
  const pad = Array.from(navigator.getGamepads?.() ?? []).find(Boolean);
  if (!pad || textOpen || document.hidden) {
    gamepadButtons.clear();
    lastDirection = "";
    return;
  }
  const pressed = (index) => pad.buttons[index]?.pressed;
  const current = new Set();
  for (const [index, action] of [[0, "activate"], [1, "back"], [4, "prev"], [5, "next"]]) {
    if (pressed(index)) {
      current.add(index);
      if (!gamepadButtons.has(index)) enqueue({ type: "navigate", action });
    }
  }
  gamepadButtons = current;
  const direction = pressed(12) || pad.axes[1] < -.5 ? "up"
    : pressed(13) || pad.axes[1] > .5 ? "down"
    : pressed(14) || pad.axes[0] < -.5 ? "left"
    : pressed(15) || pad.axes[0] > .5 ? "right" : "";
  if (direction && (direction !== lastDirection || now >= repeatAfter)) {
    enqueue({ type: "navigate", action: direction });
    repeatAfter = now + (direction === lastDirection ? 120 : 400);
  }
  lastDirection = direction;
  const x = -stick(pad.axes[2]) * 390 * dt;
  const y = stick(pad.axes[3]) * 390 * dt;
  if (x || y) enqueue({ type: "scroll", x, y });
}

function place(element, rect) {
  const [left, top, right, bottom] = rect;
  Object.assign(element.style, {
    left: left * 100 + "%", top: top * 100 + "%",
    width: (right - left) * 100 + "%", height: (bottom - top) * 100 + "%",
  });
}

function endText(apply) {
  if (!textOpen) return;
  textOpen = false;
  waitingForTextClose = true;
  enqueue({ type: "text", value: apply ? gameText.value : null });
  gameText.hidden = true;
}

function showText(request) {
  if (!request) {
    waitingForTextClose = false;
    if (textOpen) endText(false);
    return;
  }
  place(gameText, request.rect);
  if (textOpen || waitingForTextClose) return;
  gameText.value = request.value;
  gameText.maxLength = request.max || 1024;
  gameText.inputMode = request.numeric ? "decimal" : "text";
  gameText.hidden = false;
  textOpen = true;
  gameText.focus({ preventScroll: true });
  gameText.select();
}

gameText.addEventListener("keydown", event => {
  if (event.key === "Enter" || event.key === "Escape") {
    event.preventDefault();
    endText(event.key === "Enter");
    canvas.focus({ preventScroll: true });
  }
});
gameText.addEventListener("blur", () => endText(true));

function notice(message) {
  enqueue({ type: "notice", message });
}

async function importFile(save, slot) {
  if (!save || !slot) return;
  if (!save.size || save.size > 16 * 1024 * 1024) {
    notice("Choose a nonempty NGU Idle save smaller than 16 MB.");
    return;
  }
  try {
    await saveService.importSave(slot, await save.text());
    notice("Save imported into Slot " + slot + ".");
  } catch (error) { notice(error.message); }
}

file.addEventListener("click", () => { pickerSlot = frameInfo?.import?.[0]; });
file.addEventListener("change", () => {
  void importFile(file.files[0], pickerSlot);
  file.value = "";
});
file.addEventListener("pointermove", event => {
  if (event.pointerType === "mouse") moveMouse(event);
});
surface.addEventListener("dragover", event => {
  if (event.dataTransfer.types.includes("Files")) event.preventDefault();
});
surface.addEventListener("drop", event => {
  event.preventDefault();
  const target = frameInfo?.import?.[0];
  if (target) void importFile(event.dataTransfer.files[0], target);
  else notice("Open Load Game and select an empty slot before importing.");
});

function download(slot) {
  try {
    const data = saveService.snapshot(slot);
    const url = URL.createObjectURL(new Blob([data], { type: "text/plain;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = slot == null ? "NGU-Current.txt" : slot === 0 ? "NGU-Autosave.txt" : "NGU-Slot-" + slot + ".txt";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 30_000);
    notice("Save exported.");
  } catch (error) { notice(error.message); }
}

function fatal(message) {
  console.error(message);
  failure.textContent = message;
  failure.hidden = false;
  engine = null;
  file.hidden = true;
  gameText.hidden = true;
}

function applyResolution(width, height, fullscreen) {
  resolution = fullscreen ? null : [width, height];
  if (fullscreen && !document.fullscreenElement) {
    surface.requestFullscreen?.().catch(() => {});
  } else if (!fullscreen && document.fullscreenElement === surface) {
    document.exitFullscreen?.().catch(() => {});
  }
}

function frame(now) {
  if (!engine) return;
  const dt = previousFrame == null ? 0 : (now - previousFrame) / 1000;
  previousFrame = now;
  pollGamepad(now);
  const rect = canvas.getBoundingClientRect();
  const bounds = [rect.left, rect.top, rect.width, rect.height];
  if (mousePosition && bounds.some((value, i) => value !== lastBounds?.[i])) {
    enqueue({ type: "move", ...point(mousePosition) });
  }
  lastBounds = bounds;
  if (touch && !touch.inspecting && !touch.dragging && !touch.secondary && now - touch.started >= hoverHoldMs) {
    touch.inspecting = true;
    enqueue({ type: "move", ...touch.start });
  }
  const [maxWidth, maxHeight] = resolution ?? [4096, 2160];
  const scale = Math.min(window.devicePixelRatio || 1, maxWidth / rect.width, maxHeight / rect.height);
  try {
    const rejected = engine.command(pending.splice(0, 96)).filter(reply => reply.error);
    if (rejected.length) {
      console.warn("Browser input rejected", rejected);
      notice(rejected[0].error);
    }
    const info = engine.frame(Math.max(1, Math.round(rect.width * scale)), Math.max(1, Math.round(rect.height * scale)), dt);
    frameInfo = info;
    saveService.update(info, now);
    gotFrame = true;
    showText(info.text);
    file.hidden = !info.import;
    if (info.import) place(file, info.import[1]);
    if (info.file?.[0] === "import") file.click();
    if (info.file?.[0] === "export") download(info.file[1]);
    if (info.resolution) applyResolution(...info.resolution);
    for (const sound of info.sounds) {
      if (sound === "Fart" || sound === "LongFart") {
        new Audio("/audio/" + sound + ".wav").play().catch(() => {});
      }
    }
    requestAnimationFrame(frame);
  } catch (error) {
    fatal("The game stopped. Reload this page to continue.");
    console.error(error);
  }
}

try {
  const profile = await initialProfile();
  engine = await boot(canvas, profile?.files ?? {}, profile?.modified);
  saveService = saves(engine, profile, notice, () => fatal("The game stopped. Reload this page to continue."));
  if (!profile) notice("Save server unavailable.");
  canvas.focus({ preventScroll: true });
  requestAnimationFrame(frame);
} catch (error) {
  fatal(error.message);
}
