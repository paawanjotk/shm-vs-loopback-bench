# fast inter-process communication project - shared memory & loopback socket

Benchmark for two local IPC paths on Linux x86_64:

- POSIX shared memory + lock-free SPSC ring buffer
- Unix domain `SOCK_STREAM` socket at `/tmp/market.sock`

The publisher generates synthetic market-data messages. The subscribers timestamp each message with `rdtscp` and report `min`, `p50`, `p99`, `p99.9`, and `max` latency.

## Build

Requirements:

- Linux x86_64
- CMake 3.15+
- C++17 compiler
- `fmt`
- Boost headers

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build
```

Binary: `build/HFTApp`

## Machine-readable results (demo / automation)

Subscribers can emit a single JSON object on **stdout** (logs on **stderr**):

```bash
./build/HFTApp subscriber-shm --json --run-id=myrun --bench-mode=latency
./build/HFTApp subscriber-socket --json --run-id=myrun --bench-mode=latency
```

Fields include latency percentiles (`latency_ns`), wall-clock over the measured window, throughput estimate, queue capacity, effective `SO_RCVBUF` for the socket path, and **`shm_handoff_latency`** (`true` when SHM used pause+drain for `bench_mode=benchmark`).

## Web demo (Streamlit + FastAPI) and Docker

Hybrid **replay-first** UI with optional **live** benchmark via **`POST /api/runs/benchmark`** (isolated topology only; same subscriber JSON fields as the CLI).

### What the Streamlit demo supports today

The Streamlit app is an audience-facing dashboard for the current IPC comparison. It currently supports:

- Loading the built-in sample replay so visitors can see representative SHM-vs-socket results immediately, without running a benchmark.
- Pointing the UI at a FastAPI backend by editing the **API base URL** in the sidebar.
- Triggering one live benchmark from the browser: isolated **`publisher-shm` + `subscriber-shm`**, then isolated **`publisher-socket` + `subscriber-socket`**.
- Showing latency percentiles (`min`, `p50`, `p99`, `p99.9`) for SHM and socket side by side in a table.
- Rendering separate latency charts for SHM and socket, so nanosecond-scale SHM values are not visually flattened by socket tail latency.
- Rendering measured throughput in messages/sec for both transports from the same subscriber measurement window.
- Displaying run metadata such as run id, mode, topology-backed result shape, and any benchmark errors returned by the API.
- Explaining, inside the UI, how the SHM path differs from the Unix domain socket path and how to interpret queue dwell, scheduler effects, buffering, and near-1x p50 results.


```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r demo/requirements.txt
export PYTHONPATH="$(pwd)"
export HFTAPP_BIN="$(pwd)/build/HFTApp"
uvicorn demo.api.main:app --host 127.0.0.1 --port 8000 &
streamlit run demo/frontend/app.py --server.port 8501
```

Docker (expects **Linux x86_64**; compose runs **two services**: the API container holds `ipc: shareable` and shared memory for `HFTApp`, while Streamlit runs separately so throughput benchmarks are not competing with the UI in the same cgroup.)

```bash
docker compose build
docker compose up
```

- API: `http://localhost:8001` (`GET /health`, `POST /api/runs/benchmark`, `GET /api/replays`, …). `POST /api/runs/latency` and `POST /api/runs/throughput` return **410 Gone**; use **`/api/runs/benchmark`** (isolated SHM phase, then isolated socket). For API-only (no UI): `docker compose up hft-ipc-api`.
- UI: `http://localhost:8501` (defaults to calling the API at `http://hft-ipc-api:8000` inside the compose network; override in the sidebar if needed.)

Example (API on host port 8001):

```bash
curl -sS -X POST "http://127.0.0.1:8001/api/runs/benchmark" -H "Content-Type: application/json" -d '{}'
```

## Roles

| Role | CPU | Purpose |
|------|-----|---------|
| `publisher-shm` | 2 | SHM publisher only |
| `publisher-socket` | 2 | Socket publisher only |
| `publisher-both` / `publisher` | 2 | Publishes to both paths |
| `subscriber-shm` | 3 | SHM consumer |
| `subscriber-socket` / `subscriber-loopback` | 4 | Socket consumer |

All roles pin themselves to fixed CPUs and calibrate TSC before running.

## Recommended usage

### Latency comparison

Use one publisher feeding both subscribers:

```bash
# terminal 1
./build/HFTApp publisher-both

# terminal 2
./build/HFTApp subscriber-shm

# terminal 3
./build/HFTApp subscriber-socket
```

This keeps both subscribers on the same message stream, so the latency comparison is cleaner.

### Throughput comparison

Run each transport in isolation:

```bash
# SHM
./build/HFTApp publisher-shm
/usr/bin/time -f '%e s wall' ./build/HFTApp subscriber-shm

# Socket
./build/HFTApp publisher-socket
/usr/bin/time -f '%e s wall' ./build/HFTApp subscriber-socket
```

This removes cross-transport interaction and gives the cleanest throughput numbers.

## Current benchmark settings

- `kWarmupMessages = 100000`
- `kMeasureMessages = 1000000`
- Total messages consumed per run: `1100000`
- SHM queue size: `8192` messages
- Requested socket buffer size: `256 KB`
- Effective `SO_SNDBUF` / `SO_RCVBUF` on this machine: `425984` bytes

## Current numbers

Showcase figures below are the **arithmetic mean of five consecutive** isolated runs (same pairing as `POST /api/runs/benchmark`: `publisher-shm` + `subscriber-shm`, then `publisher-socket` + `subscriber-socket`). Recorded on **Linux x86_64** in **May 2026** on one reference host; **your machine will differ** (CPU frequency, load, C-state behavior, TSC calibration).

### Latency (isolated, same window as throughput)

| Metric | SHM | Socket |
|--------|----:|-------:|
| min | ~66 ns | ~1.0 µs |
| p50 | ~1.6 µs | ~180 µs |
| p99 | ~228 µs | ~689 µs |
| p99.9 | ~266 µs | ~816 µs |
| max | ~270 µs | ~0.93 ms |

Interpretation:

- **`min` is noisy** (outliers, TSC, reordering); prefer **p50** / **p99** for comparisons.
- On this aggregate, **p50** is on the order of **~100×** lower on SHM than on the socket path (about **~1.6 µs** vs **~180 µs**).
- Socket tails are dominated by syscall, kernel buffering, wakeups, and scheduling.

### Throughput (same five runs)

Throughput is still:

```text
throughput_messages_per_sec ≈ 1,000,000 / wall_seconds
```

over the subscriber’s measured window (after warmup).

| Transport | Mode pairing | Mean wall clock | Mean throughput |
|-----------|--------------|----------------:|----------------:|
| SHM | `publisher-shm` + `subscriber-shm` | ~0.045 s | ~22.5 M msg/s |
| Socket | `publisher-socket` + `subscriber-socket` | ~0.645 s | ~1.56 M msg/s |

Interpretation:

- Mean SHM throughput is about **14×** mean socket throughput on this host for these runs.
- Effective **`SO_RCVBUF`** reported in JSON for the socket path was **425984** bytes (unchanged across runs).

To refresh these numbers, run five consecutive **`run_benchmark()`** calls from **`demo.api.runner`** (same orchestration as **`POST /api/runs/benchmark`**) with **`HFTAPP_BIN`** set to your **`build/HFTApp`**, then average the **`latency_ns`** and **`throughput_messages_per_sec`** / **`wall_seconds`** fields from each result.

## Important caveats

- `min_ns` is the most direct indicator of raw messaging or handoff performance—a lower bound on the actual transport cost, without queuing or scheduling artifacts. It represents the best observed path under ideal conditions.
- Percentiles like `p50` and `p99` incorporate effects of transient contention, scheduler interrupts, or jitter; they are valuable for understanding expected and tail behavior, but always expect some spread.
- The **HTTP API** uses **isolated** publishers only (`POST /api/runs/benchmark`). For a **same-instant** head-to-head under one combined feed, run **`publisher-both`** plus both subscribers manually in separate terminals.
- **`SharedMarketDataRegion` layout** includes `pause_publish` for SHM handoff benchmarks; always run **matching** publisher and subscriber binaries from the same build.
- For **`--bench-mode=benchmark`**, the SHM subscriber performs a **pause + drain** (shared `pause_publish` flag) after warmup so the measured window starts with an **empty ring**; JSON includes **`shm_handoff_latency": true`** when that path completed. Latency is **handoff-focused** (stamp-to-pop), not queue backlog. **Without** `benchmark` mode, SHM behavior is unchanged (no drain).
- Manual **`subscriber-shm`** without **`benchmark`** mode: if the queue stays non-empty, latency reflects **queue dwell + handoff**, not handoff alone.
- The publisher re-stamps `shm_timestamp` immediately before a successful SHM push, so publisher-side backpressure does not get counted as transport latency.
- The SHM path is lossless while a consumer is attached and lossy otherwise. If no SHM consumer is attached, full-queue pushes are dropped and counted in `shm_dropped`.
- Larger socket buffers reduce drops / blocking pressure but increase typical socket latency because messages sit longer in the kernel buffer.
- `max` is not stable. Use `p99` or `p99.9` when comparing runs.

## Code map

Start here if you want to inspect the implementation:

- `src/main.cpp` - role dispatch + CPU pinning
- `src/publisher/publisher.cpp` - publisher loop and transport setup
- `src/subscriber-s/subscriber.cpp` - SHM consumer and latency sampling
- `src/subscriber-l/subscriber.cpp` - socket consumer and latency sampling
- `src/common/ringbuffer.h` - SPSC queue + shared region
- `src/common/latency_stats.h` - percentile summary logic
- `src/common/benchmark_json.h` - JSON line for subscriber results
- `src/common/benchmark_options.h` - `--json` / `--run-id` / `--bench-mode` parsing
- `demo/` - FastAPI runner, Streamlit UI, sample replay




