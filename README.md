# HFTProject

A small single-producer / multi-consumer latency benchmark comparing two IPC transports for market-data style messages on Linux x86_64:

- A lock-free SPSC ring buffer in POSIX shared memory.
- A Unix domain `SOCK_STREAM` socket at `/tmp/market.sock`.

The publisher generates synthetic quotes and can emit them to shared memory, the socket, or both. Two subscribers consume from their respective transports, time each message using `rdtscp`, and print min / p50 / p99 / p99.9 / max latency in nanoseconds after a fixed-size measurement window.

## Repository layout

```
src/
  main.cpp                     Role dispatcher + CPU pinning + TSC calibration
  common/
    quote.h                    MarketMessageData + Instrument enum
    ringbuffer.h / .cpp        SPSCQueue<T, size> and SharedMarketDataRegion
    tsc_clock.h / .cpp         rdtsc_ordered, pin_to_cpu, init_tsc_clock
    latency_stats.h            Percentile summary + pretty print
  publisher/
    publisher.h / .cpp         Publisher with SHM_ONLY / SOCKET_ONLY / BOTH modes
    xorshift.h                 Small PRNG for the synthetic mid-price walk
  subscriber-l/
    subscriber.h / .cpp        Socket (loopback) subscriber
  subscriber-s/
    subscriber.h / .cpp        Shared-memory subscriber
CMakeLists.txt
```

## Build

Requirements:

- Linux x86_64 with an invariant TSC (modern Intel/AMD).
- CMake 3.15+, a C++17 compiler.
- `libfmt` and Boost headers installed.

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build
```

The binary is `build/HFTApp`.

## Running

The first argument selects the role. Each role pins itself to a dedicated CPU and calibrates the TSC before starting:

| Role | CPU | What it does |
|------|-----|--------------|
| `publisher-shm` | 2 | Publishes to SHM only |
| `publisher-socket` | 2 | Publishes to Unix socket only |
| `publisher-both` (alias: `publisher`) | 2 | Publishes to both transports |
| `subscriber-shm` (alias: `subscriber-shared-memory`) | 3 | Consumes from SHM, prints latency summary |
| `subscriber-socket` (alias: `subscriber-loopback`) | 4 | Consumes from socket, prints latency summary |

Example — benchmark SHM in isolation:

```bash
# terminal 1
./build/HFTApp publisher-shm

# terminal 2
./build/HFTApp subscriber-shm
```

Example — benchmark socket in isolation:

```bash
# terminal 1
./build/HFTApp publisher-socket

# terminal 2
./build/HFTApp subscriber-socket
```

Example — both transports driven by the same publisher:

```bash
# terminal 1
./build/HFTApp publisher-both

# terminal 2
./build/HFTApp subscriber-shm

# terminal 3
./build/HFTApp subscriber-socket
```

The publisher runs forever. Each subscriber exits after collecting its measurement window and prints one summary line, for example:

```
[shm] samples=200000 min_ns=74.91 p50_ns=246.48 p99_ns=14838.52 p999_ns=25756.50 max_ns=26826.25
```

## Benchmark protocol

Both subscribers follow the same pattern (see `src/subscriber-s/subscriber.cpp` and `src/subscriber-l/subscriber.cpp`):

1. Calibrate the TSC via `init_tsc_clock()` (500 ms `steady_clock` anchor).
2. Warm up: discard the first `10,000` messages.
3. Measure: record the next `200,000` cycle deltas into a preallocated vector.
4. Sort and compute min / p50 / p99 / p99.9 / max using `summarize_cycles` in `src/common/latency_stats.h`.
5. Print one aggregate summary line tagged with the transport name and exit.

Per-message sample:

```
cycles = rdtsc_ordered_on_consumer - timestamp_stamped_on_publisher
```

For SHM, the publisher stamps `shm_timestamp` right before `push()`. For the socket path, it stamps `send_timestamp` right before `write()`. The consumer stamps `rdtsc_ordered()` right after a successful read.

## Data model

`MarketMessageData` in `src/common/quote.h` is a POD struct:

- `send_timestamp` (u64) — publisher TSC before `write()` to socket.
- `shm_timestamp`  (u64) — publisher TSC before `push()` into SHM queue.
- `ask` (u32), `bid` (u32) — integer prices (cents).
- `instrument` (u16 enum) — currently fixed to `RELIANCE`.

A `static_assert(sizeof(MarketMessageData) == 32)` locks the on-wire layout.

## Shared memory layout

`src/common/ringbuffer.h` defines:

- `SPSCQueue<T, size>` — single-producer / single-consumer lock-free ring buffer with power-of-two `size` and `alignas(64)` head/tail indices using relaxed/acquire/release atomics.
- `SharedMarketDataRegion`:
  - `std::atomic<uint32_t> ready` — publisher-set readiness flag.
  - `SPSCQueue<MarketMessageData, kMarketQueueSize>` — the actual ring.

The publisher sets `ready = 1` after placement-new on the mmap'd region. The SHM subscriber polls `ready` before entering its measurement loop, which replaces the earlier blind sleep.

## Architecture

```
+-----------------+           shm_timestamp          +-----------------------+
|  publisher-shm  |-------------------------->       | subscriber-shm        |
| (CPU 2)         |     SPSCQueue in POSIX SHM       | (CPU 3)               |
+-----------------+                                  +-----------------------+

+--------------------+        send_timestamp         +-----------------------+
| publisher-socket   |-------------------------->    | subscriber-socket     |
| (CPU 2)            |   /tmp/market.sock (UDS)      | (CPU 4)               |
+--------------------+                               +-----------------------+

publisher-both feeds both paths from the same loop.
```

## Caveats and tips for clean numbers

- `min_ns` is usually the best estimate of raw transport cost; p50 and tails include OS jitter, cache effects, and CPU frequency/C-state transitions.
- Starting the subscriber before the publisher lets CPU 3 enter deep idle during the 500 ms retry sleeps. The first post-warmup samples will reflect the CPU ramping back up. To compare runs cleanly, start the publisher first, or increase the warm-up constant, or disable deep C-states.
- The publisher stamps `shm_timestamp` before spinning on `push()` when the queue is full, so a very slow consumer will inflate the measured "latency" by publisher backpressure. Keep the consumer at least as fast as the producer for transport-only numbers.
- For the most stable results: pin CPUs via `isolcpus=`, lock the frequency governor to `performance`, and disable SMT on the chosen cores.
- The Unix socket in `SOCKET_ONLY` publisher mode uses a non-blocking `accept()`; if no subscriber is attached, the publisher still spins and increments `message_count` but sends nothing.

## Troubleshooting

- `shm_open` fails with `EACCES`: stale `/dev/shm/tryhard` from a prior run. Remove it with `rm /dev/shm/tryhard`.
- Socket subscriber cannot connect: stale `/tmp/market.sock`. The publisher already calls `unlink()` on startup, but you can remove it manually if something crashed mid-bind.
- Subscriber hangs on "Attempting to open shared memory": the publisher hasn't created the SHM region yet, or was started in `publisher-socket` mode (no SHM).
- `sched_setaffinity` error: the selected CPU ID doesn't exist on this machine. Adjust the pinning in `src/main.cpp`.

