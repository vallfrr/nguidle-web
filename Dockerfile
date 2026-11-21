FROM rust:1.100.0-bookworm

RUN apt-get update \
 && apt-get install -y --no-install-recommends npm python3 python3-venv util-linux \
 && rustup target add wasm32-wasip1 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY Cargo.toml Cargo.lock ./
COPY clr clr
COPY crates crates
COPY tools/requirements-prep.txt tools/verify_game_data.py tools/game-data.sha256.json tools/nguprep.py tools/fetch-achievement-icons.py tools/
COPY nguidle-web/server.py nguidle-web/entrypoint.sh nguidle-web/snapshot.py nguidle-web/pack_assets.py web/
COPY nguidle-web/package.json nguidle-web/package-lock.json web/
COPY nguidle-web/public web/public
RUN python3 -m venv /opt/ngu-python \
 && /opt/ngu-python/bin/pip install --no-cache-dir -r tools/requirements-prep.txt \
 && npm ci --prefix web --omit=dev --ignore-scripts --no-audit --no-fund \
 && cargo fetch --locked --target wasm32-wasip1 \
 && (find clr crates tools -type f -print0; printf '%s\0' Cargo.toml Cargo.lock web/pack_assets.py web/entrypoint.sh) \
    | sort -z | xargs -0 sha256sum | sha256sum | cut -d ' ' -f 1 > /app/.engine-key \
 && useradd --uid 10001 --create-home ngu \
 && mkdir -p /work /profile \
 && chown -R ngu:ngu /app /work /profile "$CARGO_HOME"

ENV PATH="/opt/ngu-python/bin:${PATH}" \
    CARGO_TARGET_DIR=/work/cargo \
    CARGO_BUILD_JOBS=2
USER ngu
EXPOSE 8080
ENTRYPOINT ["sh", "/app/web/entrypoint.sh"]
