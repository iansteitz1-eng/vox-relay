#!/usr/bin/env bash
# Vox Relay — pre-build dev run ON THE MAC:  ./run_dev.sh
# Runs `python3 -m voxrelay` from this folder (menu-bar app appears; Ctrl-C or Quit to stop).
# Needs: macOS 13+, python3, `pip install rumps`, and Full Disk Access for the terminal you run this from.
set -euo pipefail
cd "$(dirname "$0")"
if ! python3 -c "import rumps" 2>/dev/null; then
  echo "rumps missing — installing into ./.venv-dev"
  python3 -m venv .venv-dev
  # shellcheck disable=SC1091
  source .venv-dev/bin/activate
  pip install --quiet --upgrade pip rumps
elif [ -d .venv-dev ]; then
  # shellcheck disable=SC1091
  source .venv-dev/bin/activate
fi
echo "log: ~/Library/Logs/VoxRelay.log · output: ~/Library/Application Support/VoxRelay/relay.jsonl"
exec python3 -m voxrelay
