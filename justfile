python := if os() == "windows" { "python" } else { "python3" }

check:
    {{python}} -B engine.py
    cargo check --locked --target wasm32-wasip1
    {{python}} -B -m unittest discover -s tests
