# Agent instructions

This is the browser port of NGU Idle on the nguidle-rust engine. Read `README.md`, then the engine's `docs/PORTING.md` (how a
port is structured) and `docs/ENGINE-API.md` (what is public). `ARCHITECTURE.md` says what this repository owns.

- Run `just check` before and after each change. Work on a topic branch.
- The browser adapter (`src/lib.rs`) follows the engine's platform contract: input first, then game time, then the draw,
  then textures, text fields and sound. Call the engine from one thread; copy borrowed texture or draw data before using it elsewhere.
- Never commit the game's files, extracted assets, saves or machine-specific paths. Test with isolated profiles; run the browser
  test (`tests/browser_smoke.py`) against a server started on an empty profile folder.
- Update the engine pin together: `Cargo.toml` and `engine.tag` hold the tag, `Cargo.lock` the exact revision
  (`cargo update -p clr`), then `just check` and a browser check.
- Keep code, comments and documentation in English. Report what was tested and what was not.
