#!/usr/bin/env bash
# Juggler installer for Linux: ./install.sh
# Creates .venv, installs packages, downloads Xray/geo data/Tor and adds a
# "Juggler" entry (with icon) to the app menu and the Desktop.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
ROOT="$PWD"
step() { printf '\n\033[36m [%s/4] %s\033[0m\n' "$1" "$2"; }
ok()   { printf '       \033[32m%s\033[0m\n' "$1"; }
fail() { printf '\n\033[31m Setup could not finish: %s\033[0m\n' "$1"; exit 1; }

printf '\n  \033[35mJUGGLER setup\033[0m\n'

step 1 "Python"
PY=$(command -v python3 || true)
[ -n "$PY" ] || fail "python3 not found. Install it first, e.g.: sudo apt install python3 python3-venv"
"$PY" -c 'import sys; sys.exit(sys.version_info < (3, 10))' || fail "Python 3.10+ needed (found $("$PY" -V))"
"$PY" -c 'import venv, ensurepip' 2>/dev/null || fail "the venv module is missing. On Debian/Ubuntu: sudo apt install python3-venv"
ok "$("$PY" -V)"

step 2 "Python packages"
[ -x .venv/bin/python ] || "$PY" -m venv .venv
# release archive: install from the bundled wheels, offline; otherwise from PyPI
{ [ -d wheels ] && .venv/bin/python -m pip install -q --disable-pip-version-check --no-index --find-links wheels -r requirements.txt; } \
  || .venv/bin/python -m pip install -q --disable-pip-version-check -r requirements.txt \
  || fail "pip could not install the packages"
ok "ready"

step 3 "Xray, Iran geo data and Tor (about 70 MB, only once)"
.venv/bin/python -m juggler.setup
[ -x bin/xray ] || fail "Xray could not be downloaded (GitHub blocked?). Use any VPN for this step, or copy bin/ from another machine."
ok "ready"

step 4 "App menu and Desktop shortcut"
chmod +x juggler.sh
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p "$APPS"
cat > "$APPS/juggler.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=Juggler
Comment=Self-hosted VPN
Exec="$ROOT/juggler.sh"
Path=$ROOT
Icon=$ROOT/assets/juggler.png
Terminal=true
Categories=Network;
DESK
chmod +x "$APPS/juggler.desktop"
DESKTOP=$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")
if [ -d "$DESKTOP" ]; then
  cp "$APPS/juggler.desktop" "$DESKTOP/juggler.desktop"
  chmod +x "$DESKTOP/juggler.desktop"
  gio set "$DESKTOP/juggler.desktop" metadata::trusted true 2>/dev/null || true
fi
ok "Juggler is in your app menu${DESKTOP:+ and on the Desktop}"

printf '\n  \033[32mAll set. Start Juggler from the app menu, or run ./juggler.sh\033[0m\n\n'
