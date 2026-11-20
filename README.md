# nguidle-web

Web version of **NGU Idle**, **self-hosted** as a Docker container, built on the engine and conversion of [`nguidle-rust`](https://github.com/vallfrr/nguidle-rust).

**Not started yet: this is the documented design.**

## How it will work
1. You install Docker and clone this repository.
2. You put the files of **your own copy of the game** in a folder next to it (for example `./game/`, the `NGUIdle_Data` folder of your installation).
3. `docker compose up --build` mounts that folder read-only into the container, which then:
   - extracts the game data and converts the game's code with the `nguidle-rust` tooling,
   - builds the web target,
   - serves the game in the browser (default `http://localhost:8080`).
4. Saves live in the browser's storage, with file export/import.

## Rules
- **Self-hosting only.** The container is built on your machine from your files. A public site serving the converted game would distribute the game's code, so no hosted instance is provided or supported.
- No game file is provided or published in this repository.
- License: GPL-3.0.
