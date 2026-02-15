#!/usr/bin/env bash
set -euo pipefail

TOPO="${1:-leafspine}"
DUR="${2:-480}"
STEP="${3:-2}"

shift 3 || true

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

exec "$PY" -m sdnppo_mn.run_experiment --topo "$TOPO" --policy external --duration_s "$DUR" --step_s "$STEP" "$@"
