#!/usr/bin/env bash
# Start Evidex for evaluators: Ollama + app + ngrok tunnel, auto-restarting, Mac kept awake.
#   scripts/serve.sh        start (returns; everything keeps running in the background)
#   scripts/stop.sh         stop
# Logs: logs/app.log, logs/ngrok.log, logs/ollama.log
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
mkdir -p logs

env_get() { grep -E "^$1=" .env | tail -1 | cut -d= -f2-; }
NGROK_AUTHTOKEN="$(env_get NGROK_AUTHTOKEN || true)"
NGROK_DOMAIN="$(env_get NGROK_DOMAIN || true)"
NGROK_DOMAIN="${NGROK_DOMAIN#https://}"; NGROK_DOMAIN="${NGROK_DOMAIN#http://}"; NGROK_DOMAIN="${NGROK_DOMAIN%/}"
MODEL="$(env_get LLM_MODEL || true)"; MODEL="${MODEL:-qwen3:8b}"
PORT=8501

[ -n "$NGROK_AUTHTOKEN" ] || { echo "Set NGROK_AUTHTOKEN in .env first"; exit 1; }
[ -n "$NGROK_DOMAIN" ] || { echo "Set NGROK_DOMAIN in .env first (e.g. something.ngrok-free.app)"; exit 1; }
if [ -f logs/serve.pids ]; then echo "Already running (logs/serve.pids). Run scripts/stop.sh first."; exit 1; fi

# 1. Ollama (native, so it can use the Mac's GPU), model loaded and kept in memory
if ! curl -s localhost:11434/api/tags >/dev/null; then
  nohup ollama serve >> logs/ollama.log 2>&1 &
  echo $! >> logs/serve.pids
  for _ in $(seq 30); do curl -s localhost:11434/api/tags >/dev/null && break; sleep 1; done
fi
echo "Loading $MODEL into memory..."
curl -s localhost:11434/api/generate -d "{\"model\":\"$MODEL\",\"keep_alive\":-1}" >/dev/null

# 2. App on localhost only (the tunnel is the only way in), restarted if it exits
nohup bash -c "while true; do
  '$ROOT/.venv/bin/streamlit' run '$ROOT/app.py' --server.port $PORT --server.address 127.0.0.1
  echo \"[\$(date)] app exited; restarting in 3 s\"; sleep 3
done" >> logs/app.log 2>&1 &
echo $! >> logs/serve.pids

# 3. Tunnel on the fixed domain, restarted if it drops
nohup bash -c "while true; do
  NGROK_AUTHTOKEN='$NGROK_AUTHTOKEN' ngrok http $PORT --url 'https://$NGROK_DOMAIN' --log stdout --log-format logfmt
  echo \"[\$(date)] tunnel exited; restarting in 5 s\"; sleep 5
done" >> logs/ngrok.log 2>&1 &
echo $! >> logs/serve.pids

# 4. Keep the Mac awake (display may sleep; system, disk and network stay up)
nohup caffeinate -imsu >/dev/null 2>&1 &
echo $! >> logs/serve.pids

for _ in $(seq 30); do curl -s "localhost:$PORT/_stcore/health" | grep -q ok && break; sleep 1; done
echo "App:    http://localhost:$PORT  ($(curl -s localhost:$PORT/_stcore/health || echo 'not up yet'))"
echo "Public: https://$NGROK_DOMAIN"
echo "Stop with scripts/stop.sh. Logs in logs/."
