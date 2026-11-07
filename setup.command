#!/bin/zsh
set -eu
cd -- "$(dirname -- "$0")"
if [[ -x /opt/homebrew/bin/python3.12 ]]; then
  LAB_PYTHON=/opt/homebrew/bin/python3.12
elif command -v python3.12 >/dev/null 2>&1; then
  LAB_PYTHON=$(command -v python3.12)
else
  print "Install Python 3.12: brew install python@3.12"
  read "?Press Enter to exit..."
  exit 1
fi
"$LAB_PYTHON" -m venv .venv
export PIP_CACHE_DIR="$PWD/.cache/pip"
if [[ -d vendor/wheels ]]; then
  .venv/bin/python -m pip install --disable-pip-version-check --no-index --find-links vendor/wheels -r requirements.txt
else
  .venv/bin/python -m pip install --disable-pip-version-check -r requirements.txt
fi
print "Ready. Run ./start.command"
