#!/usr/bin/env bash
set -euo pipefail

if command -v python3.11 >/dev/null 2>&1; then
  python3.11 "$(dirname "${BASH_SOURCE[0]}")/dev-stack.py"
elif command -v python >/dev/null 2>&1; then
  python "$(dirname "${BASH_SOURCE[0]}")/dev-stack.py"
else
  printf 'Python 3.11+ is required to supervise the local stack.\n' >&2
  exit 1
fi
