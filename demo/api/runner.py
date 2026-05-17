"""
Orchestrates HFTApp publisher/subscriber processes and parses JSON benchmark lines from stdout.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Union

logger = logging.getLogger(__name__)
_BENCHMARK_LOCK = threading.Lock()
_SHM_PATH = "/dev/shm/tryhard"


def default_hftapp_bin() -> str:
    return os.environ.get("HFTAPP_BIN", "/opt/hft/bin/HFTApp")


def parse_subscriber_json(stdout: Union[str, bytes, None]) -> Optional[dict[str, Any]]:
    if stdout is None:
        return None
    if isinstance(stdout, bytes):
        stdout = stdout.decode("utf-8", errors="replace")
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return None


def cleanup_stale_shm() -> None:
    """Remove stale POSIX SHM name before subscriber-first SHM runs."""
    try:
        os.unlink(_SHM_PATH)
    except FileNotFoundError:
        return
    except OSError as e:
        logger.warning("failed to unlink stale shm %s: %s", _SHM_PATH, e)


@dataclass
class RunCallbacks:
    on_log: Optional[Callable[[str], None]] = None

    def log(self, msg: str) -> None:
        if self.on_log:
            self.on_log(msg)
        else:
            logger.info(msg)


@dataclass
class BenchmarkRunResult:
    run_id: str
    shm: Optional[dict[str, Any]] = None
    socket: Optional[dict[str, Any]] = None
    errors: list[str] = field(default_factory=list)


def _run_isolated_pair(
    hft: str,
    rid: str,
    label: str,
    pub_mode: str,
    sub_mode: str,
    bench_mode: str,
    timeout_sec: float,
    cb: RunCallbacks,
    subscriber_first: bool = False,
) -> tuple[Optional[dict[str, Any]], list[str]]:
    errors: list[str] = []
    if subscriber_first:
        cleanup_stale_shm()
        sub = subprocess.Popen(
            [
                hft,
                sub_mode,
                "--json",
                f"--run-id={rid}",
                f"--bench-mode={bench_mode}",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        time.sleep(0.25)
        pub = subprocess.Popen(
            [hft, pub_mode],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        time.sleep(0.2)
        if pub.poll() is not None:
            err = pub.stderr.read().decode("utf-8", errors="replace") if pub.stderr else ""
            errors.append(f"{label} publisher exited early: {err}")
            sub.kill()
            try:
                sub.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                sub.kill()
            return None, errors
        cb.log(f"Started {sub_mode} then {pub_mode} for {label} benchmark")
        try:
            out, err = sub.communicate(timeout=timeout_sec)
        except subprocess.TimeoutExpired:
            sub.kill()
            out, err = sub.communicate()
            errors.append(f"{label} subscriber timeout after {timeout_sec}s")
        if pub.poll() is None:
            pub.terminate()
            try:
                pub.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pub.kill()
        cb.log(f"Finished {label} benchmark")
        if sub.returncode is not None and sub.returncode != 0:
            errors.append(f"{label} subscriber exit {sub.returncode}: {err[:2000]}")
        parsed = parse_subscriber_json(out)
        if not parsed:
            errors.append(f"{label}: no JSON on stdout")
        return parsed, errors

    pub = subprocess.Popen(
        [hft, pub_mode],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    time.sleep(0.35)
    if pub.poll() is not None:
        err = pub.stderr.read().decode("utf-8", errors="replace") if pub.stderr else ""
        errors.append(f"{label} publisher exited early: {err}")
        return None, errors
    cb.log(f"Started {pub_mode} for {label} benchmark")
    sub = subprocess.run(
        [
            hft,
            sub_mode,
            "--json",
            f"--run-id={rid}",
            f"--bench-mode={bench_mode}",
        ],
        capture_output=True,
        text=True,
        timeout=timeout_sec,
    )
    if pub.poll() is None:
        pub.terminate()
        try:
            pub.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pub.kill()
    cb.log(f"Finished {label} benchmark")
    if sub.returncode != 0:
        errors.append(f"{label} subscriber exit {sub.returncode}: {sub.stderr[:2000]}")
    parsed = parse_subscriber_json(sub.stdout)
    if not parsed:
        errors.append(f"{label}: no JSON on stdout")
    return parsed, errors


def run_benchmark(
    run_id: Optional[str] = None,
    hft_bin: Optional[str] = None,
    timeout_sec: Optional[float] = None,
    callbacks: Optional[RunCallbacks] = None,
) -> BenchmarkRunResult:
    """
    Isolated topology only: publisher-shm + subscriber-shm, then publisher-socket + subscriber-socket.
    SHM phase: subscriber starts first; SHM latency uses pause+drain handoff (bench_mode=benchmark).
    Socket phase: publisher first (listener must exist before connect). Socket latency unchanged (not handoff-only).
    Each phase reports latency_ns and throughput_messages_per_sec for the measured window.
    """
    with _BENCHMARK_LOCK:
        timeout_sec = timeout_sec if timeout_sec is not None else float(
            os.environ.get("HFT_BENCHMARK_TIMEOUT_SEC", "2400")
        )
        hft = hft_bin or default_hftapp_bin()
        rid = run_id or str(uuid.uuid4())
        cb = callbacks or RunCallbacks()
        errors: list[str] = []
        shm_json: Optional[dict[str, Any]] = None
        sock_json: Optional[dict[str, Any]] = None

        for label, pub_mode, sub_mode in (
            ("shm", "publisher-shm", "subscriber-shm"),
            ("socket", "publisher-socket", "subscriber-socket"),
        ):
            parsed, pair_errors = _run_isolated_pair(
                hft,
                rid,
                label,
                pub_mode,
                sub_mode,
                "benchmark",
                timeout_sec,
                cb,
                subscriber_first=(label == "shm"),
            )
            errors.extend(pair_errors)
            if label == "shm":
                shm_json = parsed
            else:
                sock_json = parsed
            if any("publisher exited early" in e for e in pair_errors):
                break

        return BenchmarkRunResult(run_id=rid, shm=shm_json, socket=sock_json, errors=errors)


def build_replay_payload(
    mode: str,
    run_id: str,
    shm: Optional[dict[str, Any]],
    socket: Optional[dict[str, Any]],
    profile: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "replay_kind": "ipc_comparison",
        "run_id": run_id,
        "mode": mode,
        "topology": "isolated",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "shm": shm,
        "socket": socket,
        "profile": profile or {},
    }


def save_replay(replay_dir: Path, payload: dict[str, Any]) -> Path:
    replay_dir.mkdir(parents=True, exist_ok=True)
    rid = payload.get("run_id", "unknown")
    path = replay_dir / f"{rid}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path
