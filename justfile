set windows-shell := ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command"]

python := if os() == "windows" { "python" } else { "python3" }

check:
    {{python}} -B .github/repo-check.py
    {{python}} -B engine.py
    cargo clippy --locked --target wasm32-wasip1 --no-deps -- --cap-lints warn
    cargo build --locked --release --target wasm32-wasip1
    cargo test --locked --lib
    {{python}} -B -m ruff check --select E9,F63,F7,F82 .github/repo-check.py engine.py pack_assets.py server.py snapshot.py tests
    {{python}} -B -m unittest discover -s tests
    node --check public/app.js
    node --check public/engine.js
    node --check public/saves.js
    node --experimental-vm-modules --test tests/saves.test.mjs
