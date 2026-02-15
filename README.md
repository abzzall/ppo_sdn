# PPO-SDN experiment kit (Ryu + Mininet + iperf3) — Meter-based action (NOT routing)

This kit is designed for PPO control without action-driven routing changes.

Action design:
- Action `u ∈ [0,1]` controls an OpenFlow 1.3 **meter** rate applied to elephant/shock UDP flows.
- Routing is deterministic shortest path (not controlled by the action).

Traffic classes (iperf3 UDP destination port):
- mice: 5201–5202 (NOT metered)
- elephants: 5203 (metered)
- shock: 5204 (metered)

Logs:
- `logs/<run_id>/steps.csv` includes columns: `S1..S5, A, R, Sp1..Sp5` (+ metadata)
- `logs/<run_id>/iperf3/*.json` per-flow iperf3 JSON logs
- `logs/<run_id>/flows.csv` flow catalog
- `logs/<run_id>/ryu_state.jsonl` raw state snapshots

Correct run order (recommended):
1) Unzip and `cd ppo_sdn_meter_exp_fixed2/`
2) One-time:
   - `chmod +x scripts/*.sh`
3) Before every run:
   - `sudo -E ./scripts/00_clean_all.sh`
4) Terminal 1 (Ryu venv, no sudo):
   - uses OpenFlow TCP port 6653 by default (set SDNPPO_OF_PORT to override)
   - `source /path/to/ryu_venv/bin/activate`
   - `export SDNPPO_LINK_CAP_MBPS=20`
   - `export SDNPPO_RATE_MIN_KBPS=2000`
   - `export SDNPPO_RATE_MAX_KBPS=20000`
   - `./scripts/01_run_ryu.sh`
5) Terminal 2 (Mininet venv, sudo):
If your sudo resets PATH and your Mininet venv python is not used, set:
- export SDNPPO_MININET_PY=/full/path/to/mn_venv/bin/python3
and rerun the scripts.

   - `source /path/to/mn_venv/bin/activate`
   - `sudo -E ./scripts/02_run_mininet_baseline.sh leafspine util_guard 480 2`

Topos: `leafspine` (fastest), `wan`, `fattree`.

Policies (baseline logs):
- `util_guard` : lowers u when congestion/loss rises (tightens elephant meter)
- `const50`    : constant u=0.5
- `rr`         : toggles u between 0.3 and 0.8
- `external`   : do not set u from Mininet; use external PPO client

Real-time PPO control (3 terminals):
- Terminal 2:
  - `sudo -E ./scripts/03_run_mininet_external.sh leafspine 480 2`
- Terminal 3 (Torch venv):
  - `python3 -m sdnppo_mn.export_norm --csv logs/<run_id>/steps.csv --out norm.json`
  - `python3 -m sdnppo_mn.ppo_client --actor_state actor_state.pt --norm_json norm.json --duration_s 480 --step_s 2`

Controller REST API:
- `GET  /sdnppo/state`
- `POST /sdnppo/action {"u": 0.5}`
- `POST /sdnppo/reset`
