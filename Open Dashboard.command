#!/usr/bin/env bash
# Double-click launcher for the Tempa dashboard (macOS).
# Finder opens .command files in Terminal; keep that window open while you use the
# dashboard - closing it stops the server.
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR" || exit 1

if ! command -v python3 >/dev/null 2>&1; then
  echo
  echo "Python 3 was not found on this machine."
  echo "Install it with 'xcode-select --install' or from https://www.python.org/downloads/,"
  echo "then double-click this file again."
  echo
  read -r -p "Press Return to close this window."
  exit 1
fi

python3 "$DIR/tempa.py" dashboard || {
  echo
  echo "The dashboard stopped with an error - see the messages above."
  echo
  read -r -p "Press Return to close this window."
}
