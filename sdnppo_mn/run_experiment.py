# -*- coding: utf-8 -*-
import argparse
import csv
import json
import os
import time
from datetime import datetime
from urllib.request import Request, urlopen

from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.link import TCLink

from .topos.leafspine import LeafSpine
from .topos.wan import MiniWAN
from .topos.fattree import FatTreeK4
from .traffic import start_iperf_servers, run_traffic
from . import policies

def wait_for_ovs_controllers(net, timeout_s: float = 10.0, poll_s: float = 0.5) -> bool:
    """Best-effort: wait until all OVS bridges report controller is_connected=true."""
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        ok = True
        for sw in net.switches:
            out = sw.cmd(f"ovs-vsctl get Controller {sw.name} is_connected").strip()
            if out != "true":
                ok = False
                break
        if ok:
            return True
        time.sleep(poll_s)
    return False

def http_get(url, timeout=2.0):
    req = Request(url, method="GET")
    with urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

def http_post(url, payload, timeout=2.0):
    data = json.dumps(payload).encode("utf-8")
    req = Request(url, data=data, method="POST", headers={"Content-Type":"application/json"})
    with urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

def reward_proxy(st):
    thr = float(st.get("throughput_mbps", 0.0))
    maxu = float(st.get("max_util", 0.0))
    drop = float(st.get("drop_rate", 0.0))
    thr_term = thr / 50.0
    cong_pen = maxu
    drop_pen = min(1.0, drop * 20.0)
    return float(1.0 * thr_term - 1.2 * cong_pen - 1.5 * drop_pen)

def topo(name, bw, delay):
    if name == "leafspine":
        return LeafSpine(bw=bw, delay=delay)
    if name == "wan":
        return MiniWAN(bw=bw, delay=delay)
    if name == "fattree":
        return FatTreeK4(bw=bw, delay=delay)
    raise ValueError(name)

def policy(name):
    if name == "util_guard":
        return policies.util_guard
    if name == "const50":
        return policies.const50
    if name == "rr":
        return policies.rr
    if name == "external":
        return None
    raise ValueError(name)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--topo", choices=["leafspine", "wan", "fattree"], default="leafspine")
    ap.add_argument("--policy", choices=["util_guard", "const50", "rr", "external"], default="util_guard")
    ap.add_argument("--controller_ip", default="127.0.0.1")
    ap.add_argument("--of_port", type=int, default=6653)
    ap.add_argument("--rest_port", type=int, default=8080)
    ap.add_argument("--duration_s", type=int, default=480)
    ap.add_argument("--step_s", type=float, default=2.0)
    ap.add_argument("--bw", type=int, default=20)
    ap.add_argument("--delay", default="1ms")
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()

    run_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S") + f"_{args.topo}_{args.policy}_seed{args.seed}"
    outdir = os.path.join("logs", run_id)
    os.makedirs(outdir, exist_ok=True)
    os.makedirs(os.path.join(outdir, "iperf3"), exist_ok=True)

    net = Mininet(
        topo=topo(args.topo, args.bw, args.delay),
        controller=None,
        switch=OVSSwitch,
        link=TCLink,
        autoSetMacs=True,
        autoStaticArp=False,
        build=True,
    )
    net.addController(RemoteController("c0", ip=args.controller_ip, port=args.of_port))
    net.start()
    for sw in net.switches:
        sw.cmd(f"ovs-vsctl set bridge {sw.name} protocols=OpenFlow13")
        # Force reconnect (important if the initial handshake happened before protocols were restricted)
        sw.cmd(f"ovs-vsctl del-controller {sw.name}")
        sw.cmd(f"ovs-vsctl set-controller {sw.name} tcp:{args.controller_ip}:{args.of_port}")

    time.sleep(1.0)
    if not wait_for_ovs_controllers(net, timeout_s=10.0, poll_s=0.5):
        print("[error] OVS->controller connection failed; aborting run (check SDNPPO_OF_PORT / controller process).")
        for sw in net.switches:
            tgt = sw.cmd(f"ovs-vsctl get Controller {sw.name} target").strip()
            isc = sw.cmd(f"ovs-vsctl get Controller {sw.name} is_connected").strip()
            print(f"  {sw.name}: target={tgt} is_connected={isc}")
        net.stop()
        raise SystemExit(2)

    loss = net.pingAll(timeout=1)
    if loss > 0:
        print(f"[error] pingAll loss={loss}% at startup; aborting.")
        net.stop()
        raise SystemExit(3)

    for sw in net.switches:
        sw.cmd("ovs-vsctl set bridge %s protocols=OpenFlow13" % sw.name)

    # Give OVS a moment to (re)connect after enforcing OF1.3, then warm up ARP/host learning.
    time.sleep(1.0)
    if not wait_for_ovs_controllers(net, timeout_s=10.0, poll_s=0.5):
        print("[warn] some switches report controller is_connected=false (check OF port / firewall)")
    try:
        net.pingAll(timeout=1)
    except Exception:
        pass

    start_iperf_servers(net, outdir=os.path.join(outdir, "iperf3_servers"))

    rest = f"http://{args.controller_ip}:{args.rest_port}"
    try:
        http_post(rest + "/sdnppo/reset", {})
    except Exception:
        pass

    pol = policy(args.policy)
    external = (args.policy == "external")

    import threading
    flow_specs = []
    def traffic_job():
        nonlocal flow_specs
        flow_specs = run_traffic(net, duration_s=args.duration_s, outdir=outdir, seed=args.seed)
    threading.Thread(target=traffic_job, daemon=True).start()

    steps_path = os.path.join(outdir, "steps.csv")
    state_path = os.path.join(outdir, "ryu_state.jsonl")
    flows_path = os.path.join(outdir, "flows.csv")

    fields = ["run_id","topo","policy","step_idx","ts","S1","S2","S3","S4","S5","A","R","Sp1","Sp2","Sp3","Sp4","Sp5"]
    f_steps = open(steps_path, "w", newline="")
    w = csv.DictWriter(f_steps, fieldnames=fields)
    w.writeheader()
    f_state = open(state_path, "w")

    prev_s = prev_a = prev_r = prev_ts = None
    u_prev = 0.5
    n_steps = int(args.duration_s / args.step_s)

    for step_idx in range(n_steps + 1):
        ts = time.time()
        try:
            st = http_get(rest + "/sdnppo/state")
        except Exception:
            st = {"mean_util":0.0,"max_util":0.0,"drop_rate":0.0,"throughput_mbps":0.0,"active_flows":0,"u":u_prev}

        f_state.write(json.dumps({"ts": ts, **st}) + "\n")
        f_state.flush()

        s = {
            "S1": float(st.get("mean_util", 0.0)),
            "S2": float(st.get("max_util", 0.0)),
            "S3": float(st.get("drop_rate", 0.0)),
            "S4": float(st.get("throughput_mbps", 0.0)),
            "S5": float(st.get("active_flows", 0.0)),
        }

        if prev_s is not None:
            row = {
                "run_id": run_id,
                "topo": args.topo,
                "policy": args.policy,
                "step_idx": step_idx - 1,
                "ts": prev_ts,
                **{k: prev_s[k] for k in ["S1","S2","S3","S4","S5"]},
                "A": float(prev_a),
                "R": float(prev_r),
                "Sp1": s["S1"], "Sp2": s["S2"], "Sp3": s["S3"], "Sp4": s["S4"], "Sp5": s["S5"],
            }
            w.writerow(row)
            f_steps.flush()

        if external:
            u = float(st.get("u", u_prev))
            u_prev = u
        else:
            u = float(pol(st, step_idx, prev_u=u_prev))
            u_prev = u
            try:
                http_post(rest + "/sdnppo/action", {"u": u})
            except Exception:
                pass

        r = reward_proxy(st)
        prev_s, prev_a, prev_r, prev_ts = s, u, r, ts
        time.sleep(args.step_s)

    f_steps.close()
    f_state.close()

    with open(flows_path, "w", newline="") as f:
        wf = csv.writer(f)
        wf.writerow(["flow_id","src","dst","dst_ip","dst_port","proto","rate_mbps","duration_s","flow_type","start_ts"])
        for fs in flow_specs:
            wf.writerow([fs.flow_id, fs.src, fs.dst, fs.dst_ip, fs.dst_port, fs.proto,
                         fs.rate_mbps, fs.duration_s, fs.flow_type, fs.start_ts])

    net.stop()
    print("DONE")
    print("steps.csv:", steps_path)

if __name__ == "__main__":
    main()
