# HFT IPC benchmark + Streamlit demo (Linux x86_64)
# syntax=docker/dockerfile:1

FROM ubuntu:24.04 AS cpp-build
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    cmake \
    g++ \
    make \
    pkg-config \
    libfmt-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY CMakeLists.txt ./
COPY src ./src
RUN cmake -S . -B build -DCMAKE_BUILD_TYPE=Release \
    && cmake --build build -j"$(nproc)" \
    && install -D build/HFTApp /out/HFTApp

FROM ubuntu:24.04 AS runtime
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-venv \
    ca-certificates \
    curl \
    perl \
    libfmt9 \
    linux-tools-generic \
    linux-tools-common \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

COPY demo/requirements.txt /app/demo/requirements.txt
RUN pip install --no-cache-dir -r /app/demo/requirements.txt

COPY demo /app/demo
COPY scripts/docker-entrypoint.sh /entrypoint.sh
COPY --from=cpp-build /out/HFTApp /opt/hft/bin/HFTApp

RUN chmod +x /opt/hft/bin/HFTApp /entrypoint.sh \
    && mkdir -p /data/replays

ENV PYTHONPATH=/app
ENV HFTAPP_BIN=/opt/hft/bin/HFTApp
ENV HFT_REPLAY_DIR=/data/replays
ENV HFT_API_URL=http://127.0.0.1:8000

EXPOSE 8000 8501
ENTRYPOINT ["/entrypoint.sh"]
