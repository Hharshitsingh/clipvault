#!/usr/bin/env bash
# Start ClipVault (foreground) or pass --toggle.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="${ROOT}${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m clipvault "$@"
