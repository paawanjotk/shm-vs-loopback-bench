"""
FastAPI service for orchestrating HFTApp benchmarks and replay files.
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .runner import (
    build_replay_payload,
    default_hftapp_bin,
    run_latency_comparison,
    run_throughput_comparison,
    save_replay,
)

app = FastAPI(title="HFT IPC Demo API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def replay_dir() -> Path:
    root = Path(__file__).resolve().parents[2]
    return Path(os.environ.get("HFT_REPLAY_DIR", str(root / "demo" / "replays")))


class LatencyRunRequest(BaseModel):
    run_id: Optional[str] = None
    profile: bool = False
    flamegraph: bool = False


class ThroughputRunRequest(BaseModel):
    run_id: Optional[str] = None


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "hftapp": default_hftapp_bin()}


@app.post("/api/runs/latency")
def post_latency_run(body: LatencyRunRequest) -> dict[str, Any]:
    rid = body.run_id or str(uuid.uuid4())
    result = run_latency_comparison(run_id=rid, profile=body.profile, flamegraph=body.flamegraph)
    payload = build_replay_payload(
        "latency",
        result.run_id,
        result.shm,
        result.socket,
        profile=result.profile,
    )
    payload["errors"] = result.errors
    path = save_replay(replay_dir(), payload)
    payload["saved_replay"] = str(path)
    return payload


@app.post("/api/runs/throughput")
def post_throughput_run(body: ThroughputRunRequest) -> dict[str, Any]:
    rid = body.run_id or str(uuid.uuid4())
    result = run_throughput_comparison(run_id=rid)
    payload = build_replay_payload(
        "throughput",
        result.run_id,
        result.shm,
        result.socket,
        profile={},
    )
    payload["errors"] = result.errors
    path = save_replay(replay_dir(), payload)
    payload["saved_replay"] = str(path)
    return payload


@app.get("/api/replays")
def list_replays() -> dict[str, Any]:
    d = replay_dir()
    if not d.is_dir():
        return {"replays": []}
    files = sorted(
        [p.name for p in d.iterdir() if p.suffix == ".json"],
        reverse=True,
    )
    return {"replays": files}


@app.get("/api/replays/{name}")
def get_replay(name: str) -> dict[str, Any]:
    if ".." in name or "/" in name or "\\" in name:
        raise HTTPException(400, "invalid name")
    path = replay_dir() / name
    if not path.is_file():
        raise HTTPException(404, "not found")
    return json.loads(path.read_text(encoding="utf-8"))


@app.post("/api/replays/upload")
async def upload_replay(
    file: UploadFile = File(...),
    run_id: Optional[str] = None,
) -> dict[str, Any]:
    raw = await file.read()
    try:
        data = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as e:
        raise HTTPException(400, f"invalid json: {e}") from e
    rid = run_id or data.get("run_id") or str(uuid.uuid4())
    data["run_id"] = rid
    path = save_replay(replay_dir(), data)
    return {"saved_replay": str(path), "run_id": rid}
