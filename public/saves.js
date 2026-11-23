import { files } from "/engine.js";

const owner = Array.from(crypto.getRandomValues(new Uint8Array(24)), byte => byte.toString(16).padStart(2, "0")).join("");
const manualFiles = [...files.slice(0, 5), "prefs.json", "achievements.json"];
const slotFile = slot => `slots/slot-${slot}.txt`;

async function api(action, body) {
  const options = body === undefined ? { headers: { "X-NGU-Owner": owner } } : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...body, owner }),
  };
  const response = await fetch("/api/" + action, { ...options, signal: AbortSignal.timeout(5000) });
  if (!response.ok) {
    const error = new Error(await response.text());
    error.status = response.status;
    throw error;
  }
  return response.json();
}

export async function initialProfile() {
  try { return await api("profile"); }
  catch { return null; }
}

export function saves(engine, initial, indicate, stop) {
  let profile = initial ?? { files: {}, locked: [], revisions: {} };
  let online = initial != null;
  let busy = false;
  let held = null;
  let active = null;
  let previousPoll = 0;
  let autosaveAt = Infinity;
  let renewAt = Infinity;
  let label;
  let lostLease = false;
  let latestFrame;
  let background;
  const blocked = () => profile.locked.reduce((mask, slot) => mask | 1 << slot, 0);
  const available = () => online && !lostLease;

  function signal(reload = false) {
    engine.command([{ type: "profile", online: available(), locked: blocked(), reload }]);
  }

  function result(next, acceptFiles = false) {
    const changed = !online || blocked() !== next.locked.reduce((mask, slot) => mask | 1 << slot, 0);
    online = true;
    if (acceptFiles) {
      const hadChanges = files.some(key => profile.files[key] !== next.files[key]);
      for (const key of files) {
        if (profile.files[key] !== next.files[key] || engine.get(key) !== (next.files[key] ?? null)) {
          engine.set(key, next.files[key], next.modified[key]);
        }
      }
      profile = next;
      signal(changed || hadChanges);
    } else {
      profile.locked = next.locked;
      if (changed) signal();
    }
  }

  function fail(error) {
    if (!error.status || error.status >= 500) {
      const interrupted = online;
      online = false;
      signal();
      if (interrupted) indicate("Save server unavailable. You can keep playing and export your current game.");
    } else {
      indicate(error.message);
    }
  }

  function snapshot(slot = null) {
    const [output] = engine.command([{ type: "export", slot }]);
    if (!output?.save) throw new Error(output?.error ?? "No game is active.");
    return output.save;
  }

  function diff() {
    const changes = {};
    for (const key of manualFiles) {
      const value = engine.get(key);
      if (value !== (profile.files[key] ?? null)) {
        changes[key] = { value, revision: profile.revisions[key] ?? null };
      }
    }
    return changes;
  }

  async function write(changes) {
    if (!Object.keys(changes).length) return;
    const next = await api("write", { slot: held, changes });
    // A new in-game change can arrive while a request is in flight. Keep that local change.
    for (const key of files) {
      const baseline = Object.hasOwn(changes, key) ? changes[key].value : profile.files[key] ?? null;
      if (engine.get(key) === baseline && (next.files[key] !== profile.files[key] || Object.hasOwn(changes, key))) {
        engine.set(key, next.files[key], next.modified[key]);
      }
    }
    profile = next;
    online = true;
    signal();
    const written = Object.entries(changes).find(([key, change]) => key.startsWith("slots/") && change.value != null);
    if (written && active != null) indicate("Saved to Slot " + written[0][11] + ".");
  }

  async function claim(slot) {
    const before = held;
    let claimed = false;
    try {
      const next = await api("claim", { slot });
      claimed = true;
      result(next, true);
      const [reply] = engine.command([{ type: "lease", granted: true }]);
      if (!reply || reply.error) throw new Error(reply?.error ?? "The browser engine did not acknowledge the save slot.");
      held = slot;
      active = slot;
      renewAt = performance.now() + 20_000;
      autosaveAt = performance.now() + 30_000;
      await write(diff());
      if (before != null && before !== slot) await api("release", { slot: before });
    } catch (error) {
      if (claimed && held !== slot) await api("release", { slot }).catch(() => {});
      if (error instanceof WebAssembly.RuntimeError) throw error;
      engine.command([{ type: "lease", granted: false }]);
      if (!error.status && !(error instanceof TypeError) && error.name !== "TimeoutError") indicate(error.message);
      else fail(error);
    }
  }

  async function poll(meta, now, refresh) {
    if (busy) return;
    busy = true;
    try {
      if (meta.request != null && available()) {
        await claim(meta.request);
        return;
      }
      active = meta.active;
      if (held != null && active == null && meta.home) {
        if (!lostLease) {
          try { await write(diff()); }
          catch (error) {
            if (error.status !== 423 && error.status !== 409) throw error;
            result(await api("profile"), true);
            indicate(error.message);
          }
        }
        await api("release", { slot: held });
        held = null;
        lostLease = false;
        signal();
      }
      if (held != null && !lostLease && now >= renewAt) {
        try {
          result(await api("renew", { slot: held }));
          renewAt = now + 20_000;
        } catch (error) {
          if (error.status === 423) {
            lostLease = true;
            signal();
          }
          throw error;
        }
      }
      const changes = online && !lostLease ? diff() : {};
      if (Object.keys(changes).length) {
        try { await write(changes); }
        catch (error) {
          if (error.status === 409 || error.status === 423) result(await api("profile"), true);
          throw error;
        }
      }
      if (!document.hidden && held != null && active === held && available() && now >= autosaveAt) {
        result(await api("autosave", { slot: held, save: snapshot() }));
        autosaveAt = now + 30_000;
      }
      if (refresh) {
        const next = await api("status");
        if (held == null && next.version !== profile.version) result(await api("profile"), true);
        else result(next);
      }
    } catch (error) {
      if (error instanceof WebAssembly.RuntimeError) stop();
      else fail(error);
    }
    finally { busy = false; }
  }

  window.addEventListener("pagehide", event => {
    if (held !== null && !event.persisted) {
      navigator.sendBeacon("/api/release", JSON.stringify({ owner, slot: held }));
    }
  });
  window.addEventListener("beforeunload", event => {
    if (held !== null && !lostLease && Object.keys(diff()).length) {
      event.preventDefault();
      event.returnValue = true;
    }
  });
  window.setInterval(() => {
    if (document.hidden && held != null && latestFrame && !busy && !lostLease) {
      background = poll(latestFrame, performance.now(), false);
    }
  }, 15_000);
  signal();
  return {
    update(meta, now) {
      latestFrame = meta;
      const refresh = now >= previousPoll;
      if (!busy && (refresh || meta.active !== active || meta.request != null)) {
        if (refresh) previousPoll = now + 1700;
        background = poll(meta, now, refresh);
      }
      const message = lostLease ? "Save in use on another device"
        : !online ? "Save server unavailable"
        : `Autosave in ${Math.max(0, Math.ceil((Number.isFinite(autosaveAt) ? autosaveAt - now : 30_000) / 1000))}s`;
      if (message !== label) {
        label = message;
        engine.command([{ type: "save_label", message }]);
      }
    },
    snapshot,
    async importSave(slot, data) {
      await background;
      if (!available()) throw new Error("Save server unavailable.");
      if (busy) throw new Error("A save operation is in progress. Try again.");
      busy = true;
      try {
        await write(diff());
        result(await api("profile"), true);
        if (profile.locked.includes(slot) || profile.files[slotFile(slot)] != null) throw new Error("Choose an empty, available slot.");
        engine.set("import/upload.txt", data);
        const [reply] = engine.command([{ type: "import", slot }]);
        engine.set("import/upload.txt", null);
        if (reply?.error || !reply) throw new Error(reply?.error ?? "The game rejected this save.");
        await write(diff());
      } catch (error) {
        const key = slotFile(slot);
        engine.set(key, profile.files[key], profile.modified?.[key]);
        if (error.status) fail(error);
        throw error;
      } finally {
        busy = false;
      }
    },
  };
}
