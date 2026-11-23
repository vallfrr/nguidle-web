# syntax=docker/dockerfile:1
FROM rust:1.100.0-bookworm

RUN apt-get update \
 && apt-get install -y --no-install-recommends git npm python3 python3-venv util-linux \
 && rustup target add wasm32-wasip1 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY Cargo.toml Cargo.lock engine.py ./
COPY src src
COPY server.py entrypoint.sh snapshot.py pack_assets.py web/
COPY package.json package-lock.json web/
COPY public web/public
RUN python3 -B engine.py --stage tools \
 && python3 -m venv /opt/ngu-python \
 && /opt/ngu-python/bin/pip install --no-cache-dir -r tools/requirements-prep.txt \
 && npm ci --prefix web --omit=dev --ignore-scripts --no-audit --no-fund \
 && cargo fetch --locked --target wasm32-wasip1 \
 && (find src tools -type f -print0; printf '%s\0' Cargo.toml Cargo.lock engine.py web/pack_assets.py web/entrypoint.sh) \
    | sort -z | xargs -0 sha256sum | sha256sum | cut -d ' ' -f 1 > /app/.engine-key \
 && useradd --uid 10001 --create-home ngu \
 && mkdir -p /work /profile \
 && chown -R ngu:ngu /app /work /profile "$CARGO_HOME"

ENV PATH="/opt/ngu-python/bin:${PATH}" \
    CARGO_TARGET_DIR=/work/cargo \
    CARGO_BUILD_JOBS=2 \
    CARGO_NET_OFFLINE=true
USER ngu
EXPOSE 8080
ENTRYPOINT ["sh", "/app/web/entrypoint.sh"]
