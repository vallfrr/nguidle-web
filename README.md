# nguidle-web

Self-hosted browser host for [nguidle-rust](https://github.com/vallfrr/nguidle-rust).
The server runs the same converted Rust game and responsive UI as the desktop
build. The browser sends input and displays frames rendered by that engine.
The host serves one personal profile.

## Docker Compose

1. Clone the Rust repository. Clone this repository into its `nguidle-web/`
   directory so that `nguidle-web/compose.yaml` and the parent `Cargo.toml`
   are in the same checkout.
2. In `nguidle-web/`, copy `.env.example` to `.env` and set
   `NGU_GAME_DATA` to the full path of your own Steam Windows 1.260
   `NGUIdle_Data` directory. With Docker Desktop, use a path shared with
   Docker such as `D:/Steam/steamapps/common/NGU IDLE/NGUIdle_Data`.
3. Run `docker compose up --build` and open
   [http://localhost:8080](http://localhost:8080) after the service starts.

The container mounts your original directory read-only and verifies every
required file against the Rust repository's SHA-256 manifest before
conversion. If the files are missing or differ, it waits and retries. Once
they match, it verifies a private copy, extracts the game and compiles the
Rust worker. The first compilation can take several minutes and may require
more than 10 GB of RAM. Follow progress with `docker compose logs -f game`.
Set `NGU_FETCH_MILESTONE_ICONS=0` to skip downloading Steam milestone
icons. The game shows milestone names when an icon is unavailable.

The `build-cache` volume holds generated files and compiled code. The
`saves` volume holds your profile separately. Back up the `saves` volume
before changing machines or removing Docker volumes.

## Local Windows preview

The Rust workspace must contain the web worker. Build and prepare your
installation with the instructions in the parent README, then start the host
from PowerShell in this directory.

```powershell
.\start-local.ps1 -GameData 'D:\Steam\steamapps\common\NGU IDLE\NGUIdle_Data' `
  -Engine '..\build\pc\game\ngu-gen.exe' -Assets '..\out\pc'
```

The native preview uses `out/profile` in this repository for its own saves
by default. Point `-Save` at a directory dedicated to the web host.

## Controls and saves

- Mouse and touchscreen use the game canvas for clicks, drags, and scrolling.
  Click the canvas before using the keyboard. **F1** switches between
  game shortcuts and keyboard focus navigation.
- A browser that exposes a standard gamepad uses the D-pad or left stick
  for focus, the south button to activate, the east button to go back,
  the shoulders to change menu, and the right stick to scroll.
- Use the five in-game slots and **Load Autosave** as on desktop. The
  browser toolbar imports a `.txt` save into an empty slot using the file
  picker or drag-and-drop. **Export save** downloads a numbered slot.
- The game runs on the host while a session is active, including when the
  browser tab is hidden. The slots are shared between your devices that use
  the same host. More than one open tab controls the same live game.

## LAN and reverse proxy

The default binding is localhost. For a trusted LAN or Tailscale network,
set `NGU_BIND_ADDRESS` (or `-BindAddress` for the native preview) to the
host address you want to use. The application exposes the personal game and
its saves to clients that can reach this port.

For a personal domain, use your own Nginx TLS and access controls. Keep the
game bound to localhost when Nginx runs on the host, or keep the game on a
private Docker network when Nginx runs in Docker. Preserve the incoming
`Host` header and set `client_max_body_size 16m` for save uploads.

```nginx
location / {
    client_max_body_size 16m;
    proxy_pass http://127.0.0.1:8080;
    proxy_set_header Host $http_host;
    proxy_read_timeout 60s;
}
```

The application has no account system. Use an authenticated reverse proxy
or private network for access from outside your machine.

## First preview verification

Chromium on Windows verified game creation, server autosave, slot import
and export, keyboard focus, a simulated gamepad event, and a landscape
touchscreen viewport. A Linux worker rendered a frame in WSL using freshly
extracted data. The Compose configuration and preparation steps were
checked separately. The full Docker lifecycle and a live Nginx deployment
still need testing on their target hosts.

## License

GPL-3.0. Game data is supplied by the person running the host and stays
outside the repository.
