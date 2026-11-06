#!/usr/bin/env bash
# Stop everything scripts/serve.sh started (Ollama keeps running if it was already running before).
cd "$(dirname "$0")/.."
if [ -f logs/serve.pids ]; then
  while read -r pid; do pkill -P "$pid" 2>/dev/null; kill "$pid" 2>/dev/null; done < logs/serve.pids
  rm -f logs/serve.pids
fi
pkill -f "streamlit run .*evidex/app.py" 2>/dev/null
pkill -f "ngrok http 8501" 2>/dev/null
echo "Stopped app, tunnel and keep-awake."
