#!/usr/bin/env bash
set -euo pipefail
export PYTHONPATH="${PYTHONPATH:-/app}"
exec streamlit run demo/frontend/app.py --server.address 0.0.0.0 --server.port 8501 --browser.gatherUsageStats false
