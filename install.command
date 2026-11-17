#!/bin/zsh
set -euo pipefail
cd -- "${0:A:h}"
python_bin="$(command -v python3 || true)"
if [[ -z "$python_bin" ]]; then
  print 'Python 3.10 or newer is required.'
  exit 1
fi
"$python_bin" -c 'import sys; assert sys.version_info >= (3, 10), "Python 3.10+ is required"'
"$python_bin" -m venv .venv
.venv/bin/python -m pip install --upgrade -r requirements.txt
.venv/bin/python native/install.py
