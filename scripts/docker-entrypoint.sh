#!/usr/bin/env bash
set -euo pipefail
export PYTHONPATH="${PYTHONPATH:-/app}"
export HFTAPP_BIN="${HFTAPP_BIN:-/opt/hft/bin/HFTApp}"
export HFT_REPLAY_DIR="${HFT_REPLAY_DIR:-/data/replays}"
mkdir -p "${HFT_REPLAY_DIR}"
if [[ ! -f "${HFT_REPLAY_DIR}/sample_latency.json" && -f /app/demo/replays/sample_latency.json ]]; then
  cp /app/demo/replays/sample_latency.json "${HFT_REPLAY_DIR}/" || true
fi
uvicorn demo.api.main:app --host 0.0.0.0 --port 8000 &
exec streamlit run demo/frontend/app.py --server.address 0.0.0.0 --server.port 8501 --browser.gatherUsageStats false
