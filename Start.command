#!/bin/zsh
set -euo pipefail
cd "${0:A:h}"
if [[ -x .venv/bin/python ]]; then
  exec .venv/bin/python scripts/launch.py
fi
if command -v python3 >/dev/null 2>&1; then
  exec python3 scripts/launch.py
fi
echo "Python 3.11 or newer is needed. Install it from https://www.python.org/downloads/ and start again."
read -r "?Press Enter to close."
