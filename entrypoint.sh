#!/bin/sh
set -eu

cd /app
until python tools/verify_game_data.py /game-data; do
    echo "Waiting for a complete, supported Steam Windows 1.260 installation."
    sleep 10
done

key=$(cat .engine-key)
cache="/work/build-$key"
mkdir -p "$cache"
exec 9>"$cache/build.lock"
flock 9

if [ ! -s "$cache/runtime.wasm" ] || [ ! -s "$cache/game.pak" ]; then
    python web/snapshot.py /game-data tools/game-data.sha256.json "$cache/game-data"
    python tools/nguprep.py --game-data "$cache/game-data" --out "$cache/assets"
    if [ "${NGU_FETCH_MILESTONE_ICONS:-1}" = 1 ]; then
        timeout 90 python tools/fetch-achievement-icons.py "$cache/assets" ||
            echo "Milestone icons were unavailable. The game will show their names."
    fi
    python web/pack_assets.py "$cache/assets" "$cache/game-data/Managed/Assembly-CSharp.dll" "$cache/game.pak"
    cargo build --locked --release --target wasm32-wasip1 --package ngu-browser
    install "$CARGO_TARGET_DIR/wasm32-wasip1/release/ngu_browser.wasm" "$cache/runtime.wasm.pending"
    mv "$cache/runtime.wasm.pending" "$cache/runtime.wasm"
fi
exec 9>&-

exec python web/server.py --host 0.0.0.0 --port 8080 --published-host "${NGU_BIND_ADDRESS:-0.0.0.0}" \
    --build "$cache" --assets "$cache/assets" --save /profile
