import { WASI, File as MemoryFile, Directory, PreopenDirectory, OpenFile, ConsoleStdout } from "/vendor/index.js";

const encode = new TextEncoder();
const decode = new TextDecoder();

export const files = [
  ...Array.from({ length: 5 }, (_, i) => `slots/slot-${i + 1}.txt`),
  "NGUSaveSteam.txt", "NGUSave.txt", "autosave-slot.txt", "prefs.json", "achievements.json",
];

// The browser disk is in memory. Keep file timestamps for the save list and the
// game's double-confirmation check, including files created by Rust itself.
const stat = MemoryFile.prototype.stat;
MemoryFile.prototype.stat = function () {
  const result = stat.call(this);
  this.modified ??= Date.now();
  result.mtim = BigInt(this.modified) * 1_000_000n;
  return result;
};
const writeAt = OpenFile.prototype.fd_pwrite;
OpenFile.prototype.fd_pwrite = function (data, offset) {
  const result = writeAt.call(this, data, offset);
  if (!result.ret && result.nwritten) this.file.modified = Date.now();
  return result;
};

export async function boot(canvas, profile, modified = {}) {
  const [wasm, data] = await Promise.all([
    WebAssembly.compileStreaming(fetch("/runtime.wasm")),
    fetch("/game.pak").then(response => {
      if (!response.ok) throw new Error("Game data could not be loaded.");
      return response.arrayBuffer();
    }),
  ]);
  const assets = new PreopenDirectory("/game", new Map([["ngu.pak", new MemoryFile(data, { readonly: true })]]));
  const save = new PreopenDirectory("/save", new Map([
    ["slots", new Directory([])], ["import", new Directory([])],
  ]));
  const wasi = new WASI(["ngu-browser"], ["NGU_CULTURE=en"], [
    new OpenFile(new MemoryFile([])), ConsoleStdout.lineBuffered(console.info),
    ConsoleStdout.lineBuffered(console.warn), assets, save,
  ]);

  function entry(path) {
    const [dir, name] = path.split("/");
    return name ? [save.dir.contents.get(dir).contents, name] : [save.dir.contents, dir];
  }
  function set(path, value, modified) {
    const [dir, name] = entry(path);
    if (value == null) dir.delete(name);
    else {
      const file = new MemoryFile(encode.encode(value));
      file.modified = modified;
      dir.set(name, file);
    }
  }
  function get(path) {
    const [dir, name] = entry(path);
    const file = dir.get(name);
    return file ? decode.decode(file.data) : null;
  }
  for (const name of files) set(name, profile[name], modified[name]);
  const instance = await WebAssembly.instantiate(wasm, { wasi_snapshot_preview1: wasi.wasiImport });
  wasi.initialize(instance);
  const exports = instance.exports;
  const ready = exports.web_boot();
  if (ready) throw new Error(`Game startup failed (${ready}).`);
  let draw = renderer(canvas, exports);
  canvas.addEventListener("webglcontextlost", event => { event.preventDefault(); draw = null; });
  canvas.addEventListener("webglcontextrestored", () => { draw = renderer(canvas, exports); });

  function output(length) {
    return JSON.parse(decode.decode(new Uint8Array(exports.memory.buffer, exports.web_output(), length)));
  }
  return {
    set, get,
    command(events) {
      if (!events.length) return [];
      const data = encode.encode(JSON.stringify(events));
      const address = exports.web_input(data.length);
      new Uint8Array(exports.memory.buffer, address, data.length).set(data);
      const length = exports.web_command();
      if (!length) throw new Error("The browser engine returned no command response.");
      return output(length);
    },
    frame(width, height, dt) {
      if (canvas.width !== width) canvas.width = width;
      if (canvas.height !== height) canvas.height = height;
      const info = output(exports.web_frame(width, height, dt));
      draw?.(width, height);
      return info;
    },
  };
}

function renderer(canvas, engine) {
  const gl = canvas.getContext("webgl", { alpha: false, antialias: false, depth: false, stencil: false });
  if (!gl) throw new Error("Enable WebGL to play NGU Idle.");
  const program = gl.createProgram();
  for (const [type, source] of [
    [gl.VERTEX_SHADER, `
      attribute vec2 position;
      attribute vec2 uv;
      attribute vec4 color;
      uniform vec2 screen;
      varying vec2 pixel;
      varying vec4 tint;
      void main() {
        gl_Position = vec4(position / screen * vec2(2., -2.) + vec2(-1., 1.), 0., 1.);
        pixel = uv;
        tint = color;
      }`],
    [gl.FRAGMENT_SHADER, `
      #ifdef GL_FRAGMENT_PRECISION_HIGH
      precision highp float;
      #else
      precision mediump float;
      #endif
      uniform sampler2D image;
      varying vec2 pixel;
      varying vec4 tint;
      void main() { gl_FragColor = texture2D(image, pixel) * tint; }`],
  ]) {
    const shader = gl.createShader(type);
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(shader));
    gl.attachShader(program, shader);
    gl.deleteShader(shader);
  }
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program));
  gl.useProgram(program);
  const screen = gl.getUniformLocation(program, "screen");
  const buffer = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
  for (const [name, count, type, normalized, offset] of [
    ["position", 2, gl.FLOAT, false, 0],
    ["uv", 2, gl.FLOAT, false, 8],
    ["color", 4, gl.UNSIGNED_BYTE, true, 16],
  ]) {
    const attribute = gl.getAttribLocation(program, name);
    gl.enableVertexAttribArray(attribute);
    gl.vertexAttribPointer(attribute, count, type, normalized, 20, offset);
  }
  const textures = new Map();
  let frame = 0;
  gl.enable(gl.BLEND);
  gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
  gl.clearColor(0, 0, 0, 1);

  function texture(id, nearest, atlasDirty) {
    const item = textures.get(id) ?? { object: gl.createTexture() };
    gl.bindTexture(gl.TEXTURE_2D, item.object);
    if (item.width == null || atlasDirty && id === 1) {
      const address = id ? engine.web_texture(id) : 0;
      const dims = new Uint32Array(engine.memory.buffer, engine.web_texture_size(), 2);
      const [width, height] = address ? dims : [1, 1];
      const pixels = address ? new Uint8Array(engine.memory.buffer, address, width * height * 4) : new Uint8Array([255,255,255,255]);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, width, height, 0, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
      if (address && id > 1) engine.web_texture_release?.(id);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      item.width = width;
      textures.set(id, item);
    }
    if (nearest !== item.nearest) {
      const filter = nearest ? gl.NEAREST : gl.LINEAR;
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, filter);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, filter);
      item.nearest = nearest;
    }
  }

  return (width, height) => {
    gl.viewport(0, 0, width, height);
    gl.disable(gl.SCISSOR_TEST);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.enable(gl.SCISSOR_TEST);
    gl.uniform2f(screen, width, height);
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    gl.bufferData(gl.ARRAY_BUFFER, new Uint8Array(engine.memory.buffer, engine.web_vertices(), engine.web_vertex_count() * 20), gl.STREAM_DRAW);
    const count = engine.web_batch_count();
    const atlasDirty = engine.web_atlas_dirty();
    for (let i = 0; i < count; i++) {
      const batch = new Int32Array(engine.memory.buffer, engine.web_batches() + i * 32, 8);
      const [id, nearest, x0, y0, x1, y1, first, size] = batch;
      if (x1 <= x0 || y1 <= y0) continue;
      texture(id, nearest, atlasDirty && id === 1 && textures.get(1)?.frame !== frame);
      if (id === 1) textures.get(id).frame = frame;
      gl.scissor(x0, height - y1, x1 - x0, y1 - y0);
      gl.drawArrays(gl.TRIANGLES, first, size);
    }
    frame++;
  };
}
