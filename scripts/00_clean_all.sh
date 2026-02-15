#!/usr/bin/env bash
set -euo pipefail

echo "[clean] kill iperf3 (server+client) ..."
sudo pkill -9 -f "iperf3 -s" >/dev/null 2>&1 || true
sudo pkill -9 -f "iperf3 -c" >/dev/null 2>&1 || true
sudo pkill -9 -f "iperf3" >/dev/null 2>&1 || true

echo "[clean] mininet cleanup ..."
sudo mn -c >/dev/null 2>&1 || true

echo "[clean] delete leftover OVS bridges (common prefixes) ..."
for br in $(sudo ovs-vsctl list-br 2>/dev/null || true); do
  if [[ "$br" == s* || "$br" == l* || "$br" == r* || "$br" == c* || "$br" == a* || "$br" == e* ]]; then
    sudo ovs-vsctl --if-exists del-br "$br" >/dev/null 2>&1 || true
  fi
done

echo "[clean] done."
