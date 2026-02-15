#!/usr/bin/env bash
set -euo pipefail
OF_PORT=${SDNPPO_OF_PORT:-6653}
ryu-manager --ofp-tcp-listen-port ${OF_PORT} --observe-links ryu_app/sdnppo_ctrl_meter.py --verbose
