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

Fields include latency percentiles (`latency_ns`), wall-clock over the measured window, throughput estimate, queue capacity, and effective `SO_RCVBUF` for the socket path.

## Web demo (Streamlit + FastAPI) and Docker

Hybrid **replay-first** UI with optional **live** benchmarks (same roles as CLI), plus optional **`perf stat`** / **FlameGraph** profiling hooks from the API.

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r demo/requirements.txt
export PYTHONPATH="$(pwd)"
export HFTAPP_BIN="$(pwd)/build/HFTApp"
uvicorn demo.api.main:app --host 127.0.0.1 --port 8000 &
streamlit run demo/frontend/app.py --server.port 8501
```

Docker (expects **Linux x86_64**; compose uses `ipc: shareable` for POSIX shared memory between processes in the container):

```bash
docker compose build
docker compose up
```

- API: `http://localhost:8000` (`GET /health`, `POST /api/runs/latency`, `POST /api/runs/throughput`, `GET /api/replays`, …)
- UI: `http://localhost:8501`

For **`perf record`** / flamegraphs inside Docker you may need extra privileges (see comments in `docker-compose.yml`). Set **`FLAMEGRAPH_HOME`** to a [FlameGraph](https://github.com/brendangregg/FlameGraph) checkout when using the flamegraph option.

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

### Latency (`publisher-both`)

These are the useful transport-latency numbers.

| Metric | SHM | Socket |
|--------|----:|-------:|
| min | ~80 ns | ~1.0 us |
| p50 | ~175 ns | ~174 us |
| p99 | ~250 ns | ~420 us |
| p99.9 | ~3.7-4.2 us | ~0.71-0.78 ms |

Interpretation:

- SHM best-case latency is about **12x lower** than socket best-case latency.
- SHM typical latency is in the **hundreds of ns**.
- Socket typical latency is in the **hundreds of us**.
- Socket tail latency is much larger because it includes syscall, kernel buffering, wakeup, and scheduling effects.

### Throughput (isolated modes)

Throughput is:

```text
throughput = 1,100,000 / wall_clock_seconds
```

Measure it with:

```bash
/usr/bin/time -f '%e s wall' ./build/HFTApp subscriber-shm
/usr/bin/time -f '%e s wall' ./build/HFTApp subscriber-socket
```

Recent isolated runs on this machine:

| Transport | Mode pairing | Wall clock | Throughput |
|-----------|--------------|-----------:|-----------:|
| SHM | `publisher-shm` + `subscriber-shm` | ~1.08 s | ~1.02 M msg/s |
| Socket | `publisher-socket` + `subscriber-socket` | ~1.71-1.86 s | ~0.59-0.64 M msg/s |

Interpretation:

- SHM throughput is about **1.6x** socket throughput on this setup.
- SHM isolated mode is around **1 million messages/sec**.
- Socket isolated mode is around **0.6 million messages/sec**.

## Important caveats

- `min_ns` is the most direct indicator of raw messaging or handoff performance—a lower bound on the actual transport cost, without queuing or scheduling artifacts. It represents the best observed path under ideal conditions.
- Percentiles like `p50` and `p99` incorporate effects of transient contention, scheduler interrupts, or jitter; they are valuable for understanding expected and tail behavior, but always expect some spread.
- `publisher-both` is good for **latency comparison** and soak testing. Isolated publisher modes are better for **throughput**.
- In SHM isolated mode, latency can become much larger and vary a lot if the queue stays non-empty. In that case you are measuring **queue dwell time + handoff cost**, not just raw shared-memory handoff.
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




