import assert from "node:assert/strict";
import { webcrypto } from "node:crypto";
import { readFile } from "node:fs/promises";
import { setImmediate } from "node:timers/promises";
import test from "node:test";
import { createContext, SourceTextModule, SyntheticModule } from "node:vm";

const source = await readFile(new URL("../public/saves.js", import.meta.url), "utf8");
const slots = Array.from({ length: 5 }, (_, i) => "slots/slot-" + (i + 1) + ".txt");
const files = [...slots, "NGUSaveSteam.txt", "NGUSave.txt", "autosave-slot.txt", "prefs.json", "achievements.json"];
const profile = (overrides = {}) => ({
  files: {}, locked: [], modified: {}, revisions: {}, version: 1, ...overrides,
});

async function fixture(routes = {}, initial = profile(), createOnGrant = false) {
  const requests = [];
  const routeErrors = [];
  const commands = [];
  const events = new Map();
  const disk = new Map();
  let now = 0;
  const context = createContext({
    AbortSignal, crypto: webcrypto, TypeError, WebAssembly,
    document: { hidden: false },
    window: {
      addEventListener: (name, handler) => events.set(name, handler),
      setInterval: () => {},
    },
    navigator: {
      sendBeacon: (path, data) => requests.push([path.slice(5), JSON.parse(data)]),
    },
    performance: { now: () => now },
    fetch: async (path, options) => {
      const action = path.slice(5);
      const body = options.body ? JSON.parse(options.body) : options.headers;
      requests.push([action, body]);
      try {
        assert.ok(Object.hasOwn(routes, action), "unexpected request " + action);
        const response = routes[action](body);
        return response instanceof Response ? response : Response.json(response);
      } catch (error) {
        routeErrors.push(error);
        throw error;
      }
    },
  });
  const module = new SourceTextModule(source, { context });
  const engineModule = new SyntheticModule(["files"], function () {
    this.setExport("files", files);
  }, { context });
  await module.link(specifier => {
    assert.equal(specifier, "/engine.js");
    return engineModule;
  });
  await module.evaluate();
  const engine = {
    get: name => disk.get(name) ?? null,
    set: (name, value) => disk.set(name, value ?? null),
    command: ([command]) => {
      commands.push(command);
      if (createOnGrant && command.type === "lease" && command.granted) {
        disk.set(slots[0], "new save");
      }
      if (command.type === "export") return [{ save: "live save" }];
      return [{}];
    },
  };
  const client = module.namespace.saves(engine, initial, () => {}, () => assert.fail("game stopped"));
  return {
    client, requests, commands, events, initialProfile: module.namespace.initialProfile,
    async frame(meta, time) {
      now = time;
      client.update(meta, time);
      await setImmediate();
      if (routeErrors.length) throw routeErrors[0];
    },
  };
}

test("claims an empty slot before writing it and releases it on page exit", async () => {
  const scenario = await fixture({
    claim: () => profile({ locked: [1] }),
    write: ({ changes }) => {
      assert.deepEqual(changes[slots[0]], { value: "new save", revision: null });
      return profile({ locked: [1], files: { [slots[0]]: "new save" }, revisions: { [slots[0]]: 1 } });
    },
  }, profile(), true);
  await scenario.frame({ request: 1, active: null, home: true }, 0);
  scenario.events.get("pagehide")({ persisted: false });
  assert.deepEqual(scenario.requests.map(([action]) => action), ["claim", "write", "release"]);
  assert.match(scenario.requests[0][1].owner, /^[0-9a-f]{48}$/);
  for (const [, request] of scenario.requests) {
    assert.equal(request.slot, 1);
    assert.equal(request.owner, scenario.requests[0][1].owner);
  }
});

test("renews the lease before sending an autosave", async () => {
  const scenario = await fixture({
    claim: () => profile({ locked: [1] }),
    renew: () => profile({ locked: [1] }),
    autosave: body => {
      assert.equal(body.slot, 1);
      assert.equal(body.save, "live save");
      return profile({ locked: [1] });
    },
    status: () => profile({ locked: [1] }),
  });
  await scenario.frame({ request: 1, active: null }, 0);
  await scenario.frame({ request: null, active: 1 }, 30_000);
  assert.deepEqual(scenario.requests.map(([action]) => action), ["claim", "renew", "autosave", "status"]);
});

test("an unavailable server blocks import but still allows exporting", async () => {
  const unavailable = () => new Response("offline", { status: 503 });
  const scenario = await fixture({ profile: unavailable }, null);
  assert.equal(await scenario.initialProfile(), null);
  await assert.rejects(scenario.client.importSave(1, "new save"), /Save server unavailable/);
  assert.equal(scenario.client.snapshot(), "live save");
  assert.equal(scenario.commands[0].online, false);
});

test("a revoked lease blocks further writes and displays the lock", async () => {
  const scenario = await fixture({
    claim: () => profile({ locked: [1] }),
    renew: () => new Response("Slot in use", { status: 423 }),
    status: () => profile({ locked: [1] }),
  });
  await scenario.frame({ request: 1, active: null }, 0);
  await scenario.frame({ request: null, active: 1 }, 30_000);
  await scenario.frame({ request: null, active: 1 }, 30_001);
  assert.deepEqual(scenario.requests.map(([action]) => action), ["claim", "renew"]);
  assert.ok(scenario.commands.some(command => command.message === "Save in use on another device"));
  await assert.rejects(scenario.client.importSave(1, "new save"), /Save server unavailable/);
});
