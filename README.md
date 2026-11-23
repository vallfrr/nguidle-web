# nguidle-web

Self-hosted browser port of [nguidle-rust](https://github.com/vallfrr/nguidle-rust).
Each browser tab runs the shared Unity-compatible Rust engine and CIL
interpreter in WebAssembly, with the desktop scene, UI and WebGL rendering.
The server delivers the build and stores a personal save profile.

## Docker Compose

1. Clone this repository. Cargo fetches engine tag `v0.2.0` at the revision
   committed in `Cargo.lock`. The image build fetches and checks the matching
   engine data-preparation scripts.
2. Copy `.env.example` to `.env` in `nguidle-web/`. Set `NGU_GAME_DATA` to
   the absolute path of your Steam Windows 1.260 `NGUIdle_Data` directory.
3. Run `docker compose up --build` and open
   [http://localhost:8080](http://localhost:8080) when the service is healthy.

The container checks 115 SHA-256 hashes on the read-only game mount,
validates a private snapshot, extracts the assets and builds the WebAssembly
module. It waits if the installation is incomplete or unsupported. Follow
progress with `docker compose logs -f game`.

The `build-cache` volume contains generated files. The `saves` volume
contains the SQLite profile. Back up the saves volume while the container is
stopped, or export individual saves in the game. Set
`NGU_FETCH_MILESTONE_ICONS=0` to skip the optional icon downloads. Achievement
names and conditions remain available.

## Local Windows preview

Install Python 3.11 or later, npm, Git and Rustup. The launcher prepares the
assets from the game installation. Use `-Assets` to reuse compatible
prepared data if you already have them. The toolchain file selects the
required WebAssembly target.

```powershell
$gameData = Read-Host 'Path to NGUIdle_Data'
.\start-local.ps1 -GameData $gameData
```

The launcher builds to `build/wasm` and packages to `work/prepared/web`
in the workspace. New profiles go under `work/saves/web`. The launcher
automatically reuses an existing `out/profile`. Use `-Runtime` for an
existing WASM build and `-Save` to choose a different
profile. To migrate numbered saves from an earlier web profile, point
`-Save` there. On its first start SQLite imports those saves and preserves
the original files.

## Play and manage saves

- Play in landscape on phones. Touch uses the same scene and pointer path
  for taps and drags. A second finger during a tap sends a secondary click.
  Hold a finger in place to read the game's tooltip. Lift to keep it open,
  then tap elsewhere to dismiss it.
  The resolution choices limit rendering resolution in the browser viewport.
  On a computer, click the game to give it keyboard
  focus. F1 switches between game shortcuts and focus navigation.
- The D-pad or left stick moves focus on standard gamepads. The south
  button activates, the east button goes back, the shoulders change menus,
  and the right stick scrolls.
- **Create New Game** asks for a numbered slot. **Load Autosave** opens the
  last server autosave. In **Load Game**, select an empty slot to use
  **Import Save File** or drop a save onto the game. Select a filled slot
  to use **Export Save**.
- **Save Game → Export Current Game** downloads live progress during
  gameplay, including during a connection outage. During an outage the
  game keeps running in the browser and shows **Save server unavailable**
  in the autosave area. Opening a saved game requires the save server.
- The active game autosaves about every 30 seconds. A slot in use is
  disabled on other devices. Save before returning to the main menu or
  closing the page. An unreleased lock becomes available after two minutes.
  If another device takes the slot during an outage, export current progress
  before reloading.
  The shared engine calculates offline progress when opening a save.

## LAN and personal reverse proxy

For trusted LAN or Tailscale access, set `NGU_BIND_ADDRESS` (or
`-BindAddress` in the launcher) to the interface address. Clients that can
reach the service share the personal profile. For a personal domain,
configure TLS and access control in Nginx and preserve the incoming Host.

```nginx
location / {
    client_max_body_size 33m;
    proxy_pass http://127.0.0.1:8080;
    proxy_set_header Host $http_host;
}
```

## Verification

Install Just 1.43.0, Python 3.11 or later, Node.js 22 and Rustup. Install
Ruff with `python -m pip install ruff==0.11.13`. Run `just check` to
verify the engine pin, build WebAssembly and test both the save server
and the client lease behavior. The tests use synthetic data and do not
need the Steam installation. The service tests use temporary profiles in
`out/`. For the separate browser check, install Playwright and Chromium, start a
separate host with an empty profile, and supply a compatible save fixture.

```sh
python tests/browser_smoke.py --url http://127.0.0.1:8081 --fixture /path/to/save.txt
```

The acceptance test exercises named creation, in-game import, offline
export, server writes, timed autosave, and a handoff to a landscape touch
viewport. It writes screenshots under `out/verify-browser/`.
Run `tests/browser_hover.py` against an isolated server using a runtime
compiled with `-p ngu-browser --features audit` and an unlocked save fixture.

The browser's in-memory save files use timestamp hooks in `public/engine.js`.
They wrap `stat()` and `fd_pwrite()` from the pinned `@bjorn3/browser_wasi_shim`
dependency. When changing that version, rerun the browser save flow and
verify that both existing timestamps and newly written ones reach the game.

## License

GPL-3.0. Supply the game data from your own installation. Game data and
saves stay outside the repository.
