# -*- coding: utf-8 -*-
import os
import random
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

MICE_PORTS = [5201, 5202]
ELE_PORTS  = [5203]
SHOCK_PORTS= [5204]

@dataclass
class FlowSpec:
    flow_id: str
    src: str
    dst: str
    dst_ip: str
    dst_port: int
    proto: str
    rate_mbps: float
    duration_s: int
    flow_type: str
    start_ts: float

class PortPool:
    def __init__(self, ports: List[int]):
        self.ports = ports
        self.free = set(ports)

    def acquire(self) -> Optional[int]:
        if not self.free:
            return None
        p = random.choice(list(self.free))
        self.free.remove(p)
        return p

    def release(self, port: int):
        if port in self.ports:
            self.free.add(port)

def start_iperf_servers(net, outdir="logs/iperf3_servers"):
    os.makedirs(outdir, exist_ok=True)
    for h in net.hosts:
        h.cmd('pkill -f "iperf3 -s" >/dev/null 2>&1 || true')
        for p in set(MICE_PORTS + ELE_PORTS + SHOCK_PORTS):
            logfile = os.path.join(outdir, f"{h.name}_p{p}.log")
            h.cmd(f"iperf3 -s -p {p} -D --logfile {logfile}")
    time.sleep(0.5)

def choose_pair(hosts) -> Tuple:
    src = random.choice(hosts)
    dst = random.choice(hosts)
    while dst == src:
        dst = random.choice(hosts)
    return src, dst

def schedule(now_ts, next_mice, next_ele, next_shock, mice_int, ele_int, shock_int):
    do_m = now_ts >= next_mice
    do_e = now_ts >= next_ele
    do_s = now_ts >= next_shock
    if do_m: next_mice = now_ts + random.uniform(*mice_int)
    if do_e: next_ele  = now_ts + random.uniform(*ele_int)
    if do_s: next_shock= now_ts + random.uniform(*shock_int)
    return do_m, do_e, do_s, next_mice, next_ele, next_shock

def launch(src_host, dst_ip, dst_port, rate_mbps, duration_s, json_path):
    os.makedirs(os.path.dirname(json_path), exist_ok=True)
    cmd = ["iperf3","-c",dst_ip,"-p",str(dst_port),"-u","-t",str(duration_s),
           "-b",f"{rate_mbps}M","-J","--logfile",json_path]
    return src_host.popen(cmd)

def run_traffic(net, duration_s: int, outdir: str,
                mice_dur=(2,5), ele_dur=(20,60),
                mice_rate=(1,2), ele_rate=(2,6),
                mice_interval=(5,15), ele_interval=(30,120),
                shock_interval=(90,180),
                seed: int = 1):
    random.seed(seed)
    os.makedirs(outdir, exist_ok=True)

    pools = {}
    for h in net.hosts:
        pools[h.name] = {
            "mice": PortPool(MICE_PORTS),
            "elephant": PortPool(ELE_PORTS),
            "shock": PortPool(SHOCK_PORTS),
        }

    active = {}
    specs: List[FlowSpec] = []

    start = time.time()
    next_m = start + random.uniform(*mice_interval)
    next_e = start + random.uniform(*ele_interval)
    next_s = start + random.uniform(*shock_interval)

    fid = 0
    while time.time() - start < duration_s:
        now = time.time()
        do_m, do_e, do_s, next_m, next_e, next_s = schedule(
            now, next_m, next_e, next_s, mice_interval, ele_interval, shock_interval
        )

        def make(ftype: str):
            nonlocal fid
            src, dst = choose_pair(net.hosts)
            key = "mice" if ftype == "mice" else ("elephant" if ftype == "elephant" else "shock")
            p = pools[dst.name][key].acquire()
            if p is None:
                return
            fid += 1
            if ftype == "mice":
                dur = random.randint(*mice_dur); rate = random.uniform(*mice_rate)
            elif ftype == "elephant":
                dur = random.randint(*ele_dur); rate = random.uniform(*ele_rate)
            else:
                dur = random.randint(10, 20); rate = random.uniform(max(ele_rate[1], 6), 10)

            spec = FlowSpec(f"f{fid:06d}", src.name, dst.name, dst.IP(), p, "udp",
                            float(rate), int(dur), ftype, now)
            jpath = os.path.join(outdir, "iperf3", f"{spec.flow_id}.json")
            proc = launch(src, spec.dst_ip, spec.dst_port, spec.rate_mbps, spec.duration_s, jpath)
            active[spec.flow_id] = (proc, spec)
            specs.append(spec)

        if do_m: make("mice")
        if do_e: make("elephant")
        if do_s:
            make("shock"); make("shock")

        done = []
        for fid_, (proc, spec) in active.items():
            if proc.poll() is not None:
                key = "mice" if spec.flow_type == "mice" else ("elephant" if spec.flow_type == "elephant" else "shock")
                pools[spec.dst][key].release(spec.dst_port)
                done.append(fid_)
        for fid_ in done:
            active.pop(fid_, None)

        time.sleep(0.2)

    # grace
    grace = time.time()
    while active and time.time() - grace < 5.0:
        done = []
        for fid_, (proc, spec) in active.items():
            if proc.poll() is not None:
                key = "mice" if spec.flow_type == "mice" else ("elephant" if spec.flow_type == "elephant" else "shock")
                pools[spec.dst][key].release(spec.dst_port)
                done.append(fid_)
        for fid_ in done:
            active.pop(fid_, None)
        time.sleep(0.2)

    return specs
