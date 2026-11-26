#!/bin/sh
set -eu

cd /app
until ./ngu-prepare verify /game-data; do
    echo "Waiting for a complete, supported Steam Windows 1.260 installation."
    sleep 10
done

key=$(cat .engine-key)
cache="/work/build-$key"
mkdir -p "$cache"
exec 9>"$cache/build.lock"
flock 9

if [ ! -s "$cache/game.pak" ] || [ ! -s "$cache/game.pak.gz" ]; then
    ./ngu-prepare web /game-data "$cache"
    if [ "${NGU_FETCH_MILESTONE_ICONS:-1}" = 1 ]; then
        timeout 90 python tools/fetch-achievement-icons.py "$cache/assets" ||
            echo "Milestone icons were unavailable. The game will show their names."
    fi
    python web/pack_assets.py "$cache/assets" "$cache/game-data/NGUIdle_Data/Managed/Assembly-CSharp.dll" "$cache/game.pak.pending"
    mv "$cache/game.pak.pending.gz" "$cache/game.pak.gz"
    mv "$cache/game.pak.pending" "$cache/game.pak"
fi
if [ ! -s "$cache/runtime.wasm" ]; then
    cp /app/runtime.wasm "$cache/runtime.wasm.pending"
    mv "$cache/runtime.wasm.pending" "$cache/runtime.wasm"
fi
exec 9>&-

exec python web/server.py --host 0.0.0.0 --port 8080 --published-host "${NGU_BIND_ADDRESS:-0.0.0.0}" \
    --build "$cache" --assets "$cache/assets" --save /profile
