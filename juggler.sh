#!/usr/bin/env bash
# Start Juggler: ./juggler.sh [--no-browser]   (runs the installer first if needed)
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
if [ ! -x .venv/bin/python ] || [ ! -x bin/xray ]; then
  ./install.sh
fi
exec .venv/bin/python run.py "$@"
