#!/usr/bin/env bash
set -euo pipefail

TOPO="${1:-leafspine}"
POL="${2:-util_guard}"   # util_guard|const50|rr
DUR="${3:-480}"
STEP="${4:-2}"

shift 4 || true

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="$ROOT:${PYTHONPATH:-}"

# Most robust: allow explicit python path under sudo.
if [[ -n "${SDNPPO_MININET_PY:-}" && -x "${SDNPPO_MININET_PY}" ]]; then
  PY="${SDNPPO_MININET_PY}"
elif [[ -n "${VIRTUAL_ENV:-}" && -x "${VIRTUAL_ENV}/bin/python3" ]]; then
  PY="${VIRTUAL_ENV}/bin/python3"
else
  PY="python3"
fi

exec "$PY" -m sdnppo_mn.run_experiment --topo "$TOPO" --policy "$POL" --duration_s "$DUR" --step_s "$STEP" "$@"
