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

if [ ! -x "$cache/ngu-gen" ] || [ ! -s "$cache/assets/scene.json" ] || [ ! -s "$cache/game-data/Managed/Assembly-CSharp.dll" ]; then
    python web/snapshot.py /game-data tools/game-data.sha256.json "$cache/game-data"
    python tools/nguprep.py --game-data "$cache/game-data" --out "$cache/assets"
    if [ "${NGU_FETCH_MILESTONE_ICONS:-1}" = 1 ]; then
        timeout 90 python tools/fetch-achievement-icons.py "$cache/assets" ||
            echo "Milestone icons were unavailable. The game will show their names."
    fi
    cargo build --locked --release --package ngu-ilgen
    "$CARGO_TARGET_DIR/release/ngu-ilgen" "$cache/game-data/Managed/Assembly-CSharp.dll" crates/ngu-gen/src ALL NumberOutput::suffixFormat
    cargo build --locked --profile game --package ngu-gen
    install "$CARGO_TARGET_DIR/game/ngu-gen" "$cache/ngu-gen.pending"
    mv "$cache/ngu-gen.pending" "$cache/ngu-gen"
fi
exec 9>&-

exec python web/server.py --host 0.0.0.0 --port 8080 \
    --engine "$cache/ngu-gen" --dll "$cache/game-data/Managed/Assembly-CSharp.dll" \
    --assets "$cache/assets" --save /profile
