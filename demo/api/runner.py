"""
Orchestrates HFTApp publisher/subscriber processes and parses JSON benchmark lines from stdout.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Union

logger = logging.getLogger(__name__)


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


@dataclass
class RunCallbacks:
    on_log: Optional[Callable[[str], None]] = None

    def log(self, msg: str) -> None:
        if self.on_log:
            self.on_log(msg)
        else:
            logger.info(msg)


@dataclass
class LatencyRunResult:
    run_id: str
    shm: Optional[dict[str, Any]] = None
    socket: Optional[dict[str, Any]] = None
    errors: list[str] = field(default_factory=list)
    profile: dict[str, Any] = field(default_factory=dict)


def run_latency_comparison(
    run_id: Optional[str] = None,
    hft_bin: Optional[str] = None,
    timeout_sec: float = 900.0,
    profile: bool = False,
    flamegraph: bool = False,
    callbacks: Optional[RunCallbacks] = None,
) -> LatencyRunResult:
    hft = hft_bin or default_hftapp_bin()
    rid = run_id or str(uuid.uuid4())
    cb = callbacks or RunCallbacks()
    errors: list[str] = []

    publisher = subprocess.Popen(
        [hft, "publisher-both"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    cb.log("Started publisher-both")

    time.sleep(0.4)
    if publisher.poll() is not None:
        err = publisher.stderr.read().decode("utf-8", errors="replace") if publisher.stderr else ""
        errors.append(f"publisher exited early: {err}")
        return LatencyRunResult(run_id=rid, errors=errors)

    shm_proc = subprocess.Popen(
        [
            hft,
            "subscriber-shm",
            "--json",
            f"--run-id={rid}",
            "--bench-mode=latency",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    sock_proc = subprocess.Popen(
        [
            hft,
            "subscriber-socket",
            "--json",
            f"--run-id={rid}",
            "--bench-mode=latency",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    cb.log("Started subscriber-shm and subscriber-socket")

    results: dict[str, Optional[dict[str, Any]]] = {"shm": None, "socket": None}

    def wait_one(name: str, proc: subprocess.Popen) -> None:
        try:
            out, err = proc.communicate(timeout=timeout_sec)
            rc = proc.returncode
            if rc != 0:
                errors.append(f"{name} exit code {rc}: {err[:2000]}")
            parsed = parse_subscriber_json(out)
            if not parsed:
                errors.append(f"{name}: no JSON line on stdout: {out[:500]}")
            results[name] = parsed
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            errors.append(f"{name}: timeout after {timeout_sec}s")
        except Exception as e:
            errors.append(f"{name}: {e}")

    t1 = threading.Thread(target=wait_one, args=("shm", shm_proc))
    t2 = threading.Thread(target=wait_one, args=("socket", sock_proc))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    publisher.terminate()
    try:
        publisher.wait(timeout=5)
    except subprocess.TimeoutExpired:
        publisher.kill()
    cb.log("Stopped publisher")

    prof: dict[str, Any] = {}
    if profile and results["shm"] and results["socket"]:
        prof = _run_profile_samples(hft, rid, cb)
    if flamegraph and results["shm"] and results["socket"]:
        svg, err = _try_flamegraph_socket(hft, rid, cb)
        if svg:
            prof["socket_flamegraph_svg"] = svg
        if err:
            prof["socket_flamegraph_error"] = err

    return LatencyRunResult(
        run_id=rid,
        shm=results["shm"],
        socket=results["socket"],
        errors=errors,
        profile=prof,
    )


def _run_profile_samples(hft: str, run_id: str, cb: RunCallbacks) -> dict[str, Any]:
    """
    Optional perf stat for isolated socket path (syscall / scheduler story).
    """
    out: dict[str, Any] = {}
    perf_path = Path(os.environ.get("HFT_TMPDIR", "/tmp")) / f"{run_id}_socket_perf_stat.txt"
    pub = subprocess.Popen(
        [hft, "publisher-socket"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    time.sleep(0.35)
    try:
        cmd = [
            "perf",
            "stat",
            "-o",
            str(perf_path),
            "--",
            hft,
            "subscriber-socket",
            "--json",
            f"--run-id={run_id}",
            "--bench-mode=profile",
        ]
        cb.log("Running perf stat on isolated socket subscriber")
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=900,
        )
        if r.returncode != 0:
            out["socket_perf_stat_error"] = f"exit {r.returncode}: {r.stderr[:2000]}"
        if perf_path.is_file():
            out["socket_perf_stat"] = perf_path.read_text(encoding="utf-8", errors="replace")
    except (FileNotFoundError, OSError) as e:
        if "perf" in str(e).lower() or isinstance(e, FileNotFoundError):
            out["socket_perf_stat_error"] = "perf not installed or not in PATH"
        else:
            out["socket_perf_stat_error"] = str(e)
    except Exception as e:
        out["socket_perf_stat_error"] = str(e)
    finally:
        if pub.poll() is None:
            pub.terminate()
            try:
                pub.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pub.kill()
    return out


def _try_flamegraph_socket(hft: str, run_id: str, cb: RunCallbacks) -> tuple[Optional[str], Optional[str]]:
    fg = os.environ.get("FLAMEGRAPH_HOME", "").strip()
    if not fg:
        return None, "Set FLAMEGRAPH_HOME to a checkout of https://github.com/brendangregg/FlameGraph"
    fg_home = Path(fg)
    stack_collapse = fg_home / "stackcollapse-perf.pl"
    flamegraph_pl = fg_home / "flamegraph.pl"
    if not stack_collapse.is_file() or not flamegraph_pl.is_file():
        return None, "stackcollapse-perf.pl or flamegraph.pl not found in FLAMEGRAPH_HOME"

    tmp = Path(os.environ.get("HFT_TMPDIR", "/tmp"))
    data = tmp / f"{run_id}_socket_flame.perf.data"

    pub = subprocess.Popen(
        [hft, "publisher-socket"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    time.sleep(0.35)
    try:
        cb.log("perf record (isolated socket subscriber) for flamegraph — this may take minutes")
        r = subprocess.run(
            [
                "perf",
                "record",
                "-g",
                "-F",
                "497",
                "-o",
                str(data),
                "--",
                hft,
                "subscriber-socket",
                "--json",
                f"--run-id={run_id}",
                "--bench-mode=profile",
            ],
            capture_output=True,
            timeout=1200,
        )
        if r.returncode != 0:
            err = (r.stderr or b"").decode("utf-8", errors="replace")
            return None, f"perf record failed: {err[:800]}"
        if not data.is_file():
            return None, "perf.data missing"

        script = subprocess.run(
            ["perf", "script", "-i", str(data)],
            capture_output=True,
            timeout=300,
        )
        if script.returncode != 0:
            err = (script.stderr or b"").decode("utf-8", errors="replace")
            return None, f"perf script failed: {err[:400]}"

        perl = shutil.which("perl") or "perl"
        collapse = subprocess.run(
            [perl, str(stack_collapse)],
            input=script.stdout,
            capture_output=True,
            timeout=300,
        )
        if collapse.returncode != 0:
            return None, "stackcollapse-perf.pl failed"

        graph = subprocess.run(
            [perl, str(flamegraph_pl)],
            input=collapse.stdout,
            capture_output=True,
            timeout=300,
        )
        if graph.returncode != 0:
            return None, "flamegraph.pl failed"
        return graph.stdout.decode("utf-8", errors="replace"), None
    except FileNotFoundError:
        return None, "perf not installed"
    except Exception as e:
        return None, str(e)
    finally:
        if pub.poll() is None:
            pub.terminate()
            try:
                pub.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pub.kill()


@dataclass
class ThroughputRunResult:
    run_id: str
    shm: Optional[dict[str, Any]] = None
    socket: Optional[dict[str, Any]] = None
    errors: list[str] = field(default_factory=list)


def run_throughput_comparison(
    run_id: Optional[str] = None,
    hft_bin: Optional[str] = None,
    timeout_sec: float = 900.0,
    callbacks: Optional[RunCallbacks] = None,
) -> ThroughputRunResult:
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
        pub = subprocess.Popen(
            [hft, pub_mode],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        time.sleep(0.35)
        if pub.poll() is not None:
            err = pub.stderr.read().decode("utf-8", errors="replace") if pub.stderr else ""
            errors.append(f"{label} publisher exited early: {err}")
            break
        cb.log(f"Started {pub_mode} for {label} throughput")
        sub = subprocess.run(
            [
                hft,
                sub_mode,
                "--json",
                f"--run-id={rid}",
                "--bench-mode=throughput",
            ],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
        if sub.returncode != 0:
            errors.append(f"{label} subscriber exit {sub.returncode}: {sub.stderr[:2000]}")
        parsed = parse_subscriber_json(sub.stdout)
        if not parsed:
            errors.append(f"{label}: no JSON on stdout")
        if label == "shm":
            shm_json = parsed
        else:
            sock_json = parsed
        if pub.poll() is None:
            pub.terminate()
            try:
                pub.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pub.kill()
        cb.log(f"Finished {label} throughput")

    return ThroughputRunResult(run_id=rid, shm=shm_json, socket=sock_json, errors=errors)


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
