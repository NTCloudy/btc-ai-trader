#!/usr/bin/env bash
# Starts the BTC engine in its own systemd user unit (falls back to nohup). Safe to call from cron.
set -euo pipefail
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
cd "$(dirname "$0")/.."
mkdir -p logs
if pgrep -f "^python3 -m btc_ai run$" >/dev/null; then echo "already running"; exit 0; fi
if command -v systemd-run >/dev/null && systemctl --user show-environment >/dev/null 2>&1; then
  systemd-run --user --collect --quiet --unit="btc-ai-engine-$(date +%s)" -p WorkingDirectory="$PWD" \
    --setenv=PATH="$PATH" --setenv=HOME="$HOME" bash -c 'exec python3 -m btc_ai run >> logs/engine.log 2>&1 < /dev/null'
  sleep 1; echo "started pid $(pgrep -f '^python3 -m btc_ai run$' || echo '?')"
else
  nohup setsid python3 -m btc_ai run >> logs/engine.log 2>&1 < /dev/null &
  echo "started pid $!"
fi
