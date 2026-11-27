# syntax=docker/dockerfile:1
FROM rust:1.100.0-bookworm AS wasm

RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && rustup target add wasm32-wasip1 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY Cargo.toml Cargo.lock engine.py ./
COPY src src
RUN cargo build --locked --release --target wasm32-wasip1 --package ngu-browser

FROM rust:1.100.0-bookworm AS prep

RUN apt-get update  && apt-get install -y --no-install-recommends git python3  && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY Cargo.toml Cargo.lock engine.py engine.tag ./
RUN python3 -B engine.py --stage-source /engine  && cargo build --locked --release --manifest-path /engine/Cargo.toml -p ngu-prep --bin ngu-prepare

FROM debian:bookworm-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates git npm python3 python3-venv util-linux \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=wasm /app/target/wasm32-wasip1/release/ngu_browser.wasm /app/runtime.wasm
COPY --from=prep /engine/target/release/ngu-prepare /app/ngu-prepare
COPY Cargo.toml Cargo.lock engine.py ./
COPY server.py profile_store.py entrypoint.sh pack_assets.py web/
COPY package.json package-lock.json web/
COPY public web/public
RUN python3 -B engine.py --stage tools \
 && python3 -m venv /opt/ngu-python \
 && /opt/ngu-python/bin/pip install --no-cache-dir -r tools/requirements-icons.txt \
 && npm ci --prefix web --omit=dev --ignore-scripts --no-audit --no-fund \
 && (find tools -type f -print0; printf '%s\0' Cargo.toml Cargo.lock engine.py web/pack_assets.py web/entrypoint.sh runtime.wasm ngu-prepare) \
    | sort -z | xargs -0 sha256sum | sha256sum | cut -d ' ' -f 1 > /app/.engine-key \
 && useradd --uid 10001 --create-home ngu \
 && mkdir -p /work /profile \
 && chown -R ngu:ngu /app /work /profile

ENV PATH="/opt/ngu-python/bin:${PATH}"
USER ngu
EXPOSE 8080
ENTRYPOINT ["sh", "/app/web/entrypoint.sh"]
