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
