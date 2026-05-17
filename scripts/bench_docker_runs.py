#!/usr/bin/env python3
"""Run N POST /api/runs/benchmark calls and print latency spread (for Docker smoke)."""
from __future__ import annotations

import json
import os
import statistics
import sys
import time
from urllib.request import Request, urlopen

URL = os.environ.get("BENCH_URL", "http://127.0.0.1:8001/api/runs/benchmark")
TIMEOUT_SEC = float(os.environ.get("BENCH_TIMEOUT_SEC", "3600"))
N_RUNS = int(os.environ.get("BENCH_N_RUNS", "3"))


def main() -> None:
    body = json.dumps({"timeout_sec": TIMEOUT_SEC}).encode()
    runs: list[dict] = []
    t0 = time.time()
    for i in range(N_RUNS):
        print(f"\n=== Run {i + 1}/{N_RUNS} ===", flush=True)
        req = Request(
            URL,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        t_r = time.time()
        with urlopen(req, timeout=None) as r:
            raw = r.read().decode()
        d = json.loads(raw)
        runs.append(d)
        elapsed = time.time() - t_r
        errs = d.get("errors") or []
        shm = d.get("shm")
        sk = d.get("socket")
        lsh = (shm or {}).get("latency_ns") or {}
        lsk = (sk or {}).get("latency_ns") or {}
        print(f"  wall_elapsed_request_s={elapsed:.1f}", flush=True)
        print(f"  errors={errs}", flush=True)
        print(f"  shm_handoff={(shm or {}).get('shm_handoff_latency')}", flush=True)
        print(f"  shm p50={lsh.get('p50')} p99={lsh.get('p99')}", flush=True)
        print(f"  sock p50={lsk.get('p50')} p99={lsk.get('p99')}", flush=True)
        if shm:
            print(f"  shm tput={(shm or {}).get('throughput_messages_per_sec')}", flush=True)
        if sk:
            print(f"  sock tput={(sk or {}).get('throughput_messages_per_sec')}", flush=True)

    def collect(transport: str, pct: str) -> list[float]:
        vals: list[float] = []
        for r in runs:
            t = r.get(transport) or {}
            l = t.get("latency_ns") or {}
            v = l.get(pct)
            if v is not None:
                vals.append(float(v))
        return vals

    print("\n--- Summary ---", flush=True)
    ok = [r for r in runs if not (r.get("errors") or [])]
    print(f"  completed_ok={len(ok)}/{N_RUNS}  total_wall_s={time.time() - t0:.1f}", flush=True)
    for name, transport in [("shm", "shm"), ("socket", "socket")]:
        for pct in ("p50", "p99"):
            v = collect(transport, pct)
            if len(v) == 0:
                print(f"  {name} {pct}: (no data)", flush=True)
            elif len(v) == 1:
                print(f"  {name} {pct}: {v[0]:.1f}", flush=True)
            else:
                print(
                    f"  {name} {pct}: mean={statistics.mean(v):.1f} "
                    f"stdev={statistics.stdev(v):.1f} min={min(v):.1f} max={max(v):.1f} (n={len(v)})",
                    flush=True,
                )

    if len(ok) < N_RUNS:
        sys.exit(1)


if __name__ == "__main__":
    main()
