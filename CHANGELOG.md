# Changelog

## [Unreleased]

- Prepare the game data with the engine's Rust program `ngu-prepare` instead of the Python tools. `NGU_GAME_DATA` is now the `NGU IDLE` game folder (the sounds need `UnityPlayer.dll`), not `NGUIdle_Data`. The image builds `ngu-prepare` from the pinned engine revision.

- Use engine `v0.9.0` and its public `Session` methods for saves, text entry and the start-up resolution; the port, including its `audit` build, no longer reaches into the engine through `Session::vm()`.
- Run the browser adapter on the engine's `Session` instead of its own host loop (fixed-step time, input queue, focus pointer).
- Use engine `v0.5.0`; add `AGENTS.md` and `engine.tag`, and document how this port relates to the engine's port template.

- Use engine `v0.3.0`, including the reference-tested random sequence corrections.
- Keep valid browser events when an adjacent event is invalid, release held keys during text entry, and reject unacknowledged save locks.
- Release decoded texture pixels after the WebGL upload and use engine tag `v0.2.0`.

- Add local and GitHub checks for the WASM build and save-client behavior.

## [0.1.0]

- Own the WebAssembly adapter and lock the engine to `v0.1.0`.
- Fetch the matching data preparation tools from the locked engine revision.
- Build the browser runtime from an independent web checkout.
