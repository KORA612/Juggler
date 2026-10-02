#!/usr/bin/env bash
# Start Juggler on Linux/macOS: ./vpn.sh [--no-browser]
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "Creating virtualenv..."
  python3 -m venv .venv
  .venv/bin/pip install -q --disable-pip-version-check -r requirements.txt
fi
exec .venv/bin/python run.py "$@"
