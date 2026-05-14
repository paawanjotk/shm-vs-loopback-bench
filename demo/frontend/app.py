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
            st.info(
                f"Socket **p50** is ~**{ratio:.0f}×** SHM p50 in this replay (illustrative; your hardware may differ)."
            )

    errs = replay.get("errors") or []
    if errs:
        for e in errs:
            st.error(e)


def render_pipeline_story() -> None:
    st.subheader("What you are comparing")
    st.markdown(
        """
Two **local** IPC paths on Linux x86_64:

| Path | Mechanism | Typical cost drivers |
|------|-----------|----------------------|
| **SHM** | POSIX shared memory + **lock-free SPSC ring buffer** | cache misses, queue dwell time, consumer not attached |
| **Socket** | Unix domain **SOCK_STREAM** (`/tmp/market.sock`) | `send`/`recv` syscalls, kernel buffers, scheduling wakeups |

**Latency mode** uses one publisher feeding **both** subscribers so both transports see the same synthetic stream.

**Throughput mode** runs each transport **in isolation** (cleaner max messages/sec).
        """
    )
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("#### SHM lane (conceptual)")
        st.caption("Producer writes into shared ring; consumer spins/polls in userspace.")
        st.progress(0.15)
        st.progress(0.45)
        st.progress(0.85)
    with c2:
        st.markdown("#### Socket lane (conceptual)")
        st.caption("Producer `write()` → kernel buffer → consumer `read()` + wakeups.")
        st.progress(0.25)
        st.progress(0.55)
        st.progress(0.95)


def render_profiling(replay: dict[str, Any]) -> None:
    prof = replay.get("profile") or {}
    if not prof:
        st.markdown(
            """
Run a **live latency** benchmark from the sidebar with **Profile** enabled to capture an extra
`perf stat` pass on an isolated **socket** subscriber (syscall / scheduler counters when `perf` is available).

Optional **flamegraphs**: the API can attach SVG snippets under `profile.shm_flamegraph_svg` /
`profile.socket_flamegraph_svg` when generated offline (e.g. `perf record` + FlameGraph).
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
            st.success("Loaded sample_latency.json")
        st.divider()
        st.markdown("### Live benchmark (calls API)")
        profile = st.checkbox("Enable profiling (`perf stat` on socket after latency run)", value=False)
        flamegraph = st.checkbox(
            "Also generate socket flamegraph (needs `perf`, `perl`, FLAMEGRAPH_HOME; very slow)",
            value=False,
        )
        if st.button("Run latency comparison"):
            try:
                req_timeout = 3600 if flamegraph else 1200
                r = requests.post(
                    f"{api_base}/api/runs/latency",
                    json={"profile": profile, "flamegraph": flamegraph},
                    timeout=req_timeout,
                )
                r.raise_for_status()
                st.session_state["replay"] = r.json()
                st.success("Latency run complete (saved server-side)")
            except Exception as e:
                st.error(f"API error: {e}")
        if st.button("Run throughput comparison"):
            try:
                r = requests.post(
                    f"{api_base}/api/runs/throughput",
                    json={},
                    timeout=1200,
                )
                r.raise_for_status()
                st.session_state["replay"] = r.json()
                st.success("Throughput run complete")
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
