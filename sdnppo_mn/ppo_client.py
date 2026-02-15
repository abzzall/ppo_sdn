# -*- coding: utf-8 -*-
import argparse
import json
import time

def sigmoid(x: float) -> float:
    import math
    return 1.0 / (1.0 + math.exp(-x))

def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))

def http_get(url: str, timeout=2.0):
    from urllib.request import Request, urlopen
    req = Request(url, method="GET")
    with urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

def http_post(url: str, payload: dict, timeout=2.0):
    from urllib.request import Request, urlopen
    data = json.dumps(payload).encode("utf-8")
    req = Request(url, data=data, method="POST", headers={"Content-Type":"application/json"})
    with urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--controller_ip", default="127.0.0.1")
    ap.add_argument("--rest_port", type=int, default=8080)
    ap.add_argument("--step_s", type=float, default=2.0)
    ap.add_argument("--duration_s", type=int, default=480)
    ap.add_argument("--actor_state", required=True)
    ap.add_argument("--norm_json", required=True)
    args = ap.parse_args()

    import torch
    import torch.nn as nn

    class Actor(nn.Module):
        def __init__(self, obs_dim, act_dim=1, hidden=128):
            super().__init__()
            self.net = nn.Sequential(
                nn.Linear(obs_dim, hidden), nn.Tanh(),
                nn.Linear(hidden, hidden), nn.Tanh(),
                nn.Linear(hidden, act_dim),
            )
        def forward(self, x):
            return self.net(x)

    with open(args.norm_json, "r") as f:
        norm = json.load(f)
    mean = norm["mean"]
    var = norm["var"]
    eps = float(norm.get("eps", 1e-8))
    obs_dim = len(mean)

    actor = Actor(obs_dim, 1)
    actor.load_state_dict(torch.load(args.actor_state, map_location="cpu"))
    actor.eval()

    rest = f"http://{args.controller_ip}:{args.rest_port}"
    end_ts = time.time() + args.duration_s

    while time.time() < end_ts:
        st = http_get(rest + "/sdnppo/state")
        s = [
            float(st.get("mean_util", 0.0)),
            float(st.get("max_util", 0.0)),
            float(st.get("drop_rate", 0.0)),
            float(st.get("throughput_mbps", 0.0)),
            float(st.get("active_flows", 0.0)),
        ]
        s_norm = [(s[i] - mean[i]) / ((var[i] + eps) ** 0.5) for i in range(obs_dim)]
        with torch.no_grad():
            a_raw = float(actor(torch.tensor([s_norm], dtype=torch.float32)).item())
        u = clamp(sigmoid(a_raw), 0.05, 0.95)
        http_post(rest + "/sdnppo/action", {"u": u})
        time.sleep(args.step_s)

if __name__ == "__main__":
    main()
