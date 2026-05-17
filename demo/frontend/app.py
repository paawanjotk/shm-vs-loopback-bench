"""
Streamlit UI: single-page IPC comparison (SHM vs Unix socket) with optional live runs via API.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import requests
import streamlit as st
import streamlit.components.v1 as components

# Project root (parent of `demo/`) on PYTHONPATH in Docker; fallback for local `streamlit run demo/frontend/app.py`
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

DEFAULT_API = os.environ.get("HFT_API_URL", "http://127.0.0.1:8000")
SAMPLE_REPLAY = _ROOT / "demo" / "replays" / "sample_latency.json"


def load_replay_from_path(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def latency_percentile_values(lat: dict[str, Any]) -> tuple[list[str], list[float]]:
    keys = ["min", "p50", "p99", "p999"]
    return keys, [float(lat.get(k, 0) or 0) for k in keys]


def render_comparison(replay: dict[str, Any]) -> None:
    st.subheader("Metrics dashboard")
    mode = replay.get("mode", "?")
    st.caption(f"Run `{replay.get('run_id', '?')}` — mode: **{mode}**")

    shm = replay.get("shm") or {}
    sock = replay.get("socket") or {}
    lat_shm = (shm.get("latency_ns") or {}) if isinstance(shm, dict) else {}
    lat_sock = (sock.get("latency_ns") or {}) if isinstance(sock, dict) else {}

    if lat_shm and lat_sock:
        import pandas as pd

        st.markdown(
            "**Latency (ns)** — each transport uses **its own chart scale** so nanosecond SHM is not "
            "flattened by microsecond–millisecond socket tails. See the table for exact numbers side by side."
        )
        if isinstance(shm, dict) and shm.get("shm_handoff_latency"):
            st.caption(
                "SHM row: **handoff latency** (`bench_mode=benchmark`: publisher pauses, ring drained after warmup). "
                "Socket row: path latency (unchanged; not drain-synchronized)."
            )
        labels, shm_vals = latency_percentile_values(lat_shm)
        _, sock_vals = latency_percentile_values(lat_sock)
        tbl = pd.DataFrame({"shm_ns": shm_vals, "socket_ns": sock_vals}, index=labels)
        st.dataframe(
            tbl.style.format("{:.2f}"),
            use_container_width=True,
        )

        col_shm, col_sock = st.columns(2)
        with col_shm:
            st.caption("SHM (nanoseconds)")
            st.bar_chart(pd.DataFrame({"latency_ns": shm_vals}, index=labels))
        with col_sock:
            st.caption("Socket (nanoseconds)")
            st.bar_chart(pd.DataFrame({"latency_ns": sock_vals}, index=labels))

        t_shm = shm.get("throughput_messages_per_sec", 0) or 0
        t_sock = sock.get("throughput_messages_per_sec", 0) or 0
        st.markdown("**Throughput (messages/sec)** from measured window")
        st.bar_chart(
            pd.DataFrame(
                {"msgs_per_sec": [t_shm, t_sock]},
                index=["shm", "socket"],
            )
        )

        if lat_shm.get("p50") and lat_sock.get("p50"):
            ratio = float(lat_sock["p50"]) / max(float(lat_shm["p50"]), 1e-9)
            if ratio < 1.0:
                st.info(
                    f"Socket **p50** is **{(1.0 / ratio):.1f}× lower** than SHM p50 in this replay. "
                    "That can happen in isolated runs when the SHM ring refills faster than the consumer can drain it, "
                    "so the SHM percentile includes queue dwell time rather than pure handoff cost."
                )
            elif ratio < 1.5:
                st.info(
                    f"Socket **p50** is about **{ratio:.1f}×** SHM p50 in this replay. "
                    "A near-1× result usually means the run is dominated by scheduling, buffering, or SHM queue dwell, "
                    "not the theoretical raw cost of the IPC primitive alone."
                )
            else:
                st.info(
                    f"Socket **p50** is about **{ratio:.1f}×** SHM p50 in this replay. "
                    "Treat this as an observed result for this machine and load, not a universal constant."
                )

    errs = replay.get("errors") or []
    if errs:
        for e in errs:
            st.error(e)


def render_pipeline_story() -> None:
    st.subheader("What you are comparing")
    st.markdown(
        """
This demo compares two **local** IPC paths on Linux x86_64. Both move the same synthetic market-data
message shape, but they take very different routes through the machine:

| Path | Mechanism | Typical cost drivers |
|------|-----------|----------------------|
| **SHM** | POSIX shared memory + **lock-free SPSC ring buffer** | cache-line movement, consumer polling, queue dwell if producer outruns consumer |
| **Socket** | Unix domain **SOCK_STREAM** (`/tmp/market.sock`) | `send`/`recv` syscalls, kernel buffering, wakeups, scheduler latency |

The **API** runs **`publisher-shm` + `subscriber-shm`**, then **`publisher-socket` + `subscriber-socket`**, so each transport is measured **in isolation**. Latency percentiles and throughput for a path come from the **same** subscriber window.

For SHM in `benchmark` mode, the publisher briefly pauses after warmup and the subscriber drains the
ring before taking measured samples. That makes the start of the measured window handoff-focused. During
the 1M-message window, however, a very fast publisher can still refill the ring; if that happens, SHM
percentiles include some **queue dwell time** again.

That is why you may occasionally see a message such as **"Socket p50 is about 1× SHM p50"**, or even a
run where socket p50 appears lower. It does **not** mean sockets became fundamentally faster than shared
memory. It usually means the measurement was dominated by queue depth, kernel buffering, CPU scheduling,
or Docker/container contention. The clean interpretation is:

- **SHM** shows the low-overhead user-space path, but it is sensitive to whether the ring stays shallow.
- **Socket** shows the kernel-mediated path, where buffering and wakeups tend to raise typical and tail latency.
- **Throughput** is measured in the same run, so a transport can have high throughput while still showing worse latency if messages wait in a buffer.
        """
    )
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("#### SHM path")
        st.markdown(
            """
1. Publisher writes the message into a **shared ring buffer**.
2. Consumer polls the same memory region and pops the next slot.
3. No kernel handoff is needed for each message; the cost is mostly cache movement and any time spent waiting in the ring.
            """
        )
    with c2:
        st.markdown("#### Socket path")
        st.markdown(
            """
1. Publisher calls **`write()`**, entering the kernel.
2. The kernel copies/buffers the bytes and wakes the receiver when data is available.
3. Consumer calls **`read()`** and returns to user space with the message; latency includes syscalls, buffering, and scheduling.
            """
        )


def render_profiling(replay: dict[str, Any]) -> None:
    prof = replay.get("profile") or {}
    if not prof:
        st.markdown(
            """
The live API no longer runs `perf` from the server. You can still attach profiling artifacts under
`profile` (e.g. `socket_perf_stat` text or `socket_flamegraph_svg`) when you generate them offline.
            """
        )
        return
    if prof.get("socket_perf_stat"):
        st.markdown("#### `perf stat` (isolated socket subscriber)")
        st.text_area("perf stat output", prof.get("socket_perf_stat", ""), height=320)
    if prof.get("socket_flamegraph_error"):
        st.warning(str(prof["socket_flamegraph_error"]))
    if prof.get("socket_perf_stat_error"):
        st.warning(str(prof["socket_perf_stat_error"]))
    if prof.get("shm_flamegraph_svg"):
        st.markdown("#### SHM flamegraph (SVG)")
        components.html(prof["shm_flamegraph_svg"], height=600, scrolling=True)
    if prof.get("socket_flamegraph_svg"):
        st.markdown("#### Socket flamegraph (SVG)")
        components.html(prof["socket_flamegraph_svg"], height=600, scrolling=True)


def main() -> None:
    st.set_page_config(page_title="IPC: SHM vs Socket", layout="wide")
    st.title("IPC comparison: shared memory vs Unix domain socket")
    st.caption("Replay-first demo for the HFTProject benchmark (Linux x86_64).")

    if "replay" not in st.session_state:
        st.session_state["replay"] = load_replay_from_path(SAMPLE_REPLAY)

    with st.sidebar:
        api_base = st.text_input("API base URL", value=DEFAULT_API, key="api_url").rstrip("/")
        st.divider()
        st.markdown("### Sample replay")
        if st.button("Load built-in sample (README-scale)"):
            st.session_state["replay"] = load_replay_from_path(SAMPLE_REPLAY)
            st.success("Loaded built-in sample replay")
        st.divider()
        st.markdown("### Live benchmark (calls API)")
        if st.button("Run benchmark (isolated SHM, then socket)"):
            try:
                r = requests.post(
                    f"{api_base}/api/runs/benchmark",
                    json={},
                    timeout=1200,
                )
                r.raise_for_status()
                st.session_state["replay"] = r.json()
                st.success("Benchmark complete (saved server-side)")
            except Exception as e:
                st.error(f"API error: {e}")

    replay: dict[str, Any] = st.session_state["replay"]
    prof = replay.get("profile") or {}
    profiling_expanded = bool(prof)

    render_comparison(replay)

    with st.expander("How SHM vs socket differs (read me)", expanded=False):
        render_pipeline_story()

    with st.expander("Profiling (perf / flamegraph)", expanded=profiling_expanded):
        render_profiling(replay)


if __name__ == "__main__":
    main()
