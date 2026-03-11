#!/usr/bin/env bash

set -euo pipefail

python3 -m pytest -m "not container" "$@"

if command -v docker >/dev/null 2>&1 && docker version >/dev/null 2>&1; then
  python3 -m pytest -m container "$@"
else
  echo "=== container tests: SKIPPED (docker unavailable) ==="
fi
