# Web architecture

The browser input, render and save adapters belong to this repository.
`src/lib.rs` builds the WASI reactor against the locked `clr` dependency.
JavaScript runs that reactor in the browser and draws its quads using
WebGL. The Python server serves the client and stores saves in SQLite.

The engine revision is controlled in `Cargo.toml` and `Cargo.lock`.
`engine.py` validates the matching tag and stages only data-preparation
tools when requested. An engine upgrade changes the pin and lockfile
together, followed by `just check` and a browser check.

The game is verified and extracted locally from the mounted Steam
installation. The image downloads build dependencies and can compile from
the cached dependencies after validating the installation.

## Relation to the engine's port template
This port follows the layout of the engine's port template (`just new-port` in nguidle-rust): `AGENTS.md`, `engine.tag`,
`justfile` with `check`, the same hygiene check and the same GitHub workflow shape. The justified differences are the platform:
the crate builds a WebAssembly reactor (`src/lib.rs`, a `cdylib`) instead of a native binary and implements the engine's
interface by running `clr::session::Session` behind the browser adapter's event protocol (`src/lib.rs` translates events and adds the browser save and text-field extras through `Session::vm()`); the draw list reaches WebGL through
`platform_api::TriangleList`; saves are owned by the Python server (`profile_store.py`) rather than a local folder; and CI also
runs the JavaScript tests. The adapter was moved onto `Session` in engine `v0.5.0` and verified with `tests/browser_smoke.py`.
