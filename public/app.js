"use strict";

const byId = (id) => document.getElementById(id);
const canvas = byId("game");
const surface = byId("surface");
const ctx = canvas.getContext("2d", { alpha: false });
const status = byId("status");
const connecting = byId("connecting");
const file = byId("save-file");
const textDialog = byId("text-dialog");
const gameText = byId("game-text");
const exportDialog = byId("export-dialog");
const importDialog = byId("import-dialog");
let selectedSave;
const pending = [];
let inFlight = false;
let lastRequest = 0;
let gotFrame = false;
let keyboardFocus = false;
let textOpen = false;
let waitingForTextClose = false;
let dragDepth = 0;

function enqueue(event) {
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

canvas.addEventListener("pointerdown", (event) => {
  if (!event.isPrimary || !gotFrame) return;
  canvas.focus({ preventScroll: true });
  canvas.setPointerCapture(event.pointerId);
  enqueue({ type: "button", ...point(event), button: event.button, down: true });
  event.preventDefault();
});
canvas.addEventListener("pointermove", (event) => {
  if (event.isPrimary && gotFrame) enqueue({ type: "move", ...point(event) });
});
canvas.addEventListener("pointerup", (event) => {
  if (event.isPrimary && gotFrame) {
    enqueue({ type: "button", ...point(event), button: event.button, down: false });
    if (event.pointerType !== "mouse") enqueue({ type: "move", x: -1, y: -1 });
  }
});
canvas.addEventListener("pointercancel", () => enqueue({ type: "reset" }));
canvas.addEventListener("pointerleave", (event) => {
  if (!canvas.hasPointerCapture(event.pointerId)) enqueue({ type: "move", x: -1, y: -1 });
});
canvas.addEventListener("contextmenu", (event) => event.preventDefault());
canvas.addEventListener("wheel", (event) => {
  const unit = event.deltaMode === 1 ? 3 : event.deltaMode === 2 ? 1 : 100;
  enqueue({ type: "wheel", ...point(event), delta: -event.deltaY / unit });
  event.preventDefault();
}, { passive: false });
canvas.addEventListener("blur", () => enqueue({ type: "reset" }));

const directions = { ArrowUp: "up", ArrowDown: "down", ArrowLeft: "left", ArrowRight: "right", Enter: "activate", " ": "activate", Escape: "back" };
const heldKeys = new Set();
canvas.addEventListener("keydown", (event) => {
  if (event.key === "F1" && !event.repeat) {
    keyboardFocus = !keyboardFocus;
    status.textContent = keyboardFocus ? "Keyboard focus navigation enabled. F1 switches back." : "Game keyboard shortcuts enabled.";
    event.preventDefault();
    return;
  }
  if (keyboardFocus && directions[event.key]) {
    enqueue({ type: "navigate", action: directions[event.key] });
    event.preventDefault();
    return;
  }
  if (event.key === "Tab" || event.key.startsWith("F") && event.key.length > 1) return;
  if (event.ctrlKey && event.key !== "Control") return;
  heldKeys.add(event.code);
  enqueue({ type: "key", key: event.key, right: event.location === 2, down: true, repeat: event.repeat });
  if (directions[event.key]) event.preventDefault();
});
canvas.addEventListener("keyup", (event) => {
  if (heldKeys.delete(event.code)) enqueue({ type: "key", key: event.key, right: event.location === 2, down: false, repeat: false });
});
window.addEventListener("blur", () => {
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

function showText(encoded) {
  if (!encoded) {
    waitingForTextClose = false;
    return;
  }
  if (textOpen || waitingForTextClose) return;
  const request = JSON.parse(decodeURIComponent(encoded));
  textOpen = true;
  gameText.value = request.value;
  gameText.maxLength = request.max;
  gameText.inputMode = request.numeric ? "decimal" : "text";
  textDialog.returnValue = "cancel";
  textDialog.showModal();
  gameText.focus();
  gameText.select();
}
textDialog.addEventListener("close", () => {
  enqueue({ type: "text", value: textDialog.returnValue === "apply" ? gameText.value : null });
  textOpen = false;
  waitingForTextClose = true;
  canvas.focus({ preventScroll: true });
});

async function frame(now) {
  requestAnimationFrame(frame);
  pollGamepad(now);
  if (inFlight || document.hidden || now - lastRequest < 1000 / 30) return;
  inFlight = true;
  lastRequest = now;
  const rect = canvas.getBoundingClientRect();
  const scale = Math.min(window.devicePixelRatio || 1, 1.5, 1280 / rect.width, 900 / rect.height);
  const body = {
    width: Math.max(200, Math.round(rect.width * scale)),
    height: Math.max(110, Math.round(rect.height * scale)),
    events: pending.splice(0, 96),
  };
  try {
    const response = await fetch("/api/frame", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!response.ok) throw new Error(await response.text());
    const bitmap = await createImageBitmap(await response.blob());
    if (canvas.width !== bitmap.width) canvas.width = bitmap.width;
    if (canvas.height !== bitmap.height) canvas.height = bitmap.height;
    ctx.drawImage(bitmap, 0, 0);
    bitmap.close();
    connecting.hidden = true;
    gotFrame = true;
    showText(response.headers.get("X-NGU-Text"));
    for (const sound of (response.headers.get("X-NGU-Sounds") || "").split(",")) {
      if (sound === "Fart" || sound === "LongFart") {
        new Audio("/audio/" + sound + ".wav").play().catch(() => {});
      }
    }
  } catch (error) {
    connecting.textContent = "Waiting for the game server…";
    connecting.hidden = false;
    status.textContent = error.message;
    lastRequest += 900;
  } finally {
    inFlight = false;
  }
}

async function chooseImportSlot(save) {
  if (!save) return;
  if (!save.size || save.size > 16 * 1024 * 1024) {
    status.textContent = "Choose a nonempty NGU Idle save smaller than 16 MB.";
    file.value = "";
    return;
  }
  const select = byId("import-slot");
  select.replaceChildren();
  try {
    const response = await fetch("/api/slots");
    if (!response.ok) throw new Error(await response.text());
    for (const slot of await response.json()) {
      if (!slot.occupied) select.add(new Option("Slot " + slot.number, slot.number));
    }
    if (!select.options.length) throw new Error("All slots are occupied. Free a slot in Create New Game before importing.");
    selectedSave = save;
    importDialog.returnValue = "cancel";
    importDialog.showModal();
  } catch (error) {
    status.textContent = error.message;
  } finally {
    file.value = "";
  }
}

importDialog.addEventListener("close", async () => {
  const save = selectedSave;
  selectedSave = undefined;
  if (!save || importDialog.returnValue !== "apply") return;
  const slot = byId("import-slot").value;
  status.textContent = "Importing save…";
  try {
    const headers = { "Content-Type": "application/octet-stream", "X-NGU-Filename": encodeURIComponent(save.name) };
    const response = await fetch("/api/import/" + slot, { method: "POST", headers, body: save });
    if (!response.ok) throw new Error(await response.text());
    status.textContent = "Save imported into Slot " + slot + ". Open Load Game to play it.";
  } catch (error) {
    status.textContent = error.message;
  }
});
byId("import").addEventListener("click", () => file.click());
file.addEventListener("change", () => chooseImportSlot(file.files[0]));
surface.addEventListener("dragenter", (event) => {
  if (!event.dataTransfer.types.includes("Files")) return;
  event.preventDefault();
  dragDepth += 1;
  byId("drop").hidden = false;
});
surface.addEventListener("dragover", (event) => {
  if (event.dataTransfer.types.includes("Files")) event.preventDefault();
});
surface.addEventListener("dragleave", () => {
  dragDepth = Math.max(0, dragDepth - 1);
  byId("drop").hidden = dragDepth === 0;
});
surface.addEventListener("drop", (event) => {
  event.preventDefault();
  dragDepth = 0;
  byId("drop").hidden = true;
  chooseImportSlot(event.dataTransfer.files[0]);
});
byId("export").addEventListener("click", async () => {
  const list = byId("export-list");
  list.replaceChildren();
  try {
    const response = await fetch("/api/slots");
    if (!response.ok) throw new Error(await response.text());
    for (const slot of await response.json()) {
      if (!slot.occupied) continue;
      const entry = document.createElement("li");
      const link = document.createElement("a");
      link.href = "/api/slots/" + slot.number;
      link.download = "NGU-Slot-" + slot.number + ".txt";
      link.textContent = "Download Slot " + slot.number;
      entry.append(link);
      list.append(entry);
    }
    byId("export-help").hidden = list.childElementCount > 0;
    exportDialog.showModal();
  } catch (error) {
    status.textContent = error.message;
  }
});
byId("fullscreen").addEventListener("click", () => {
  if (surface.requestFullscreen) surface.requestFullscreen().catch((error) => { status.textContent = error.message; });
});
if (!document.fullscreenEnabled) byId("fullscreen").hidden = true;
requestAnimationFrame(frame);
