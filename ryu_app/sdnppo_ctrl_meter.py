#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ryu SDN Controller for PPO experiments (OpenFlow 1.3)

Action u in [0,1]:
- u sets the rate of an OpenFlow meter used to police elephant/shock UDP flows.
- Routing is deterministic shortest-path (no action-driven routing changes).

REST:
- GET  /sdnppo/state
- POST /sdnppo/action {"u":0.5}
- POST /sdnppo/reset
"""

import json
import time
from collections import defaultdict, deque
from typing import Dict, List, Optional, Tuple

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import MAIN_DISPATCHER, CONFIG_DISPATCHER, DEAD_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib import hub
from ryu.lib.packet import packet, ethernet, ipv4, udp, tcp
from ryu.topology import event
from ryu.topology.api import get_switch, get_link

from ryu.app.wsgi import WSGIApplication, ControllerBase, route
from webob import Response

REST_APP_NAME = "sdnppo_rest"

MICE_PORTS = {5201, 5202}
ELE_PORTS  = {5203}
SHOCK_PORTS= {5204}
METERED_PORTS = ELE_PORTS | SHOCK_PORTS

def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))

class Rest(ControllerBase):
    def __init__(self, req, link, data, **config):
        super().__init__(req, link, data, **config)
        self.app = data[REST_APP_NAME]

    @route("sdnppo", "/sdnppo/state", methods=["GET"])
    def get_state(self, req, **kwargs):
        body = json.dumps(self.app.get_state())
        return Response(content_type="application/json", body=body)

    @route("sdnppo", "/sdnppo/action", methods=["POST"])
    def set_action(self, req, **kwargs):
        try:
            payload = req.json if req.body else {}
        except Exception:
            payload = {}
        u = float(payload.get("u", 0.5))
        self.app.set_u(u)
        body = json.dumps({"ok": True, "u": self.app.u, "meter_kbps": self.app.meter_kbps})
        return Response(content_type="application/json", body=body)

    @route("sdnppo", "/sdnppo/reset", methods=["POST"])
    def reset(self, req, **kwargs):
        self.app.reset_metrics()
        body = json.dumps({"ok": True})
        return Response(content_type="application/json", body=body)

class SdnPpoController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    _CONTEXTS = {"wsgi": WSGIApplication}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        wsgi = kwargs["wsgi"]
        wsgi.register(Rest, {REST_APP_NAME: self})

        self.u = 0.5

        self.link_cap_mbps = float(self.env("SDNPPO_LINK_CAP_MBPS", "20"))
        self.stats_interval_s = float(self.env("SDNPPO_STATS_INTERVAL_S", "1.0"))

        self.rate_min_kbps = int(self.env("SDNPPO_RATE_MIN_KBPS", "2000"))
        self.rate_max_kbps = int(self.env("SDNPPO_RATE_MAX_KBPS", "20000"))
        self.meter_id = 1
        self.meter_kbps = self.u_to_kbps(self.u)

        self.datapaths: Dict[int, object] = {}
        self.adj = defaultdict(set)
        self.port_map = defaultdict(dict)
        self.host_loc: Dict[str, Tuple[int, int]] = {}

        self._meter_installed = set()
        self._last_port = {}
        self.flow_last_seen = {}
        self.cookie_ctr = 1

        self.latest = {
            "ts": time.time(),
            "u": self.u,
            "meter_kbps": self.meter_kbps,
            "mean_util": 0.0,
            "max_util": 0.0,
            "drop_rate": 0.0,
            "throughput_mbps": 0.0,
            "active_flows": 0,
        }

        self._stats_thread = hub.spawn(self.stats_loop)
        self._cleanup_thread = hub.spawn(self.cleanup_loop)

    def env(self, key: str, default: str) -> str:
        import os
        return os.environ.get(key, default)

    def u_to_kbps(self, u: float) -> int:
        u = clamp(u, 0.0, 1.0)
        return int(self.rate_min_kbps + u * (self.rate_max_kbps - self.rate_min_kbps))

    def set_u(self, u: float):
        self.u = clamp(u, 0.0, 1.0)
        self.meter_kbps = self.u_to_kbps(self.u)
        self.latest["u"] = self.u
        self.latest["meter_kbps"] = self.meter_kbps
        for dp in list(self.datapaths.values()):
            self.ensure_meter(dp, force_modify=True)

    def reset_metrics(self):
        self._last_port = {}
        self.flow_last_seen = {}
        self.latest.update({
            "ts": time.time(),
            "u": self.u,
            "meter_kbps": self.meter_kbps,
            "mean_util": 0.0,
            "max_util": 0.0,
            "drop_rate": 0.0,
            "throughput_mbps": 0.0,
            "active_flows": 0,
        })

    def get_state(self):
        d = dict(self.latest)
        d["ts"] = time.time()
        return d

    @set_ev_cls(event.EventSwitchEnter)
    def on_switch_enter(self, ev):
        self.rebuild_topology()

    @set_ev_cls(event.EventLinkAdd)
    def on_link_add(self, ev):
        self.rebuild_topology()

    def rebuild_topology(self):
        switch_list = get_switch(self, None)
        link_list = get_link(self, None)
        self.adj.clear()
        self.port_map.clear()
        for sw in switch_list:
            self.adj[sw.dp.id]
        for lk in link_list:
            s = lk.src.dpid
            d = lk.dst.dpid
            self.adj[s].add(d)
            self.port_map[s][d] = lk.src.port_no
        self.logger.info("Topology updated: switches=%d links=%d", len(switch_list), len(link_list))

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features(self, ev):
        dp = ev.msg.datapath
        ofp = dp.ofproto
        parser = dp.ofproto_parser

        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofp.OFPP_CONTROLLER, ofp.OFPCML_NO_BUFFER)]
        inst = [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]
        dp.send_msg(parser.OFPFlowMod(datapath=dp, priority=0, match=match, instructions=inst))

        self.ensure_meter(dp, force_modify=False)

    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def dp_state_change(self, ev):
        dp = ev.datapath
        if ev.state == MAIN_DISPATCHER:
            self.datapaths[dp.id] = dp
            self.ensure_meter(dp, force_modify=False)
        elif ev.state == DEAD_DISPATCHER:
            self.datapaths.pop(dp.id, None)
            self._meter_installed.discard(dp.id)

    def ensure_meter(self, dp, force_modify: bool):
        ofp = dp.ofproto
        parser = dp.ofproto_parser
        band = parser.OFPMeterBandDrop(rate=self.meter_kbps, burst_size=max(1, self.meter_kbps // 10))
        flags = ofp.OFPMF_KBPS
        cmd = ofp.OFPMC_ADD if dp.id not in self._meter_installed else ofp.OFPMC_MODIFY
        if not force_modify and dp.id in self._meter_installed:
            cmd = ofp.OFPMC_MODIFY
        req = parser.OFPMeterMod(datapath=dp, command=cmd, flags=flags, meter_id=self.meter_id, bands=[band])
        try:
            dp.send_msg(req)
            self._meter_installed.add(dp.id)
        except Exception as e:
            self.logger.warning("meter op failed on dpid=%s: %s", dp.id, e)

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in(self, ev):
        msg = ev.msg
        dp = msg.datapath
        ofp = dp.ofproto
        parser = dp.ofproto_parser
        in_port = msg.match["in_port"]

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)
        if eth is None:
            return
        src = eth.src
        dst = eth.dst
        self.host_loc[src] = (dp.id, in_port)

        if eth.ethertype == 0x0806:
            self.flood(dp, msg)
            return

        ip4 = pkt.get_protocol(ipv4.ipv4)
        if ip4 is None:
            self.flood(dp, msg)
            return
        if dst not in self.host_loc:
            self.flood(dp, msg)
            return

        src_sw, _ = self.host_loc[src]
        dst_sw, dst_port = self.host_loc[dst]

        path = self.shortest_path(src_sw, dst_sw)
        if not path:
            self.flood(dp, msg)
            return

        match, metered = self.flow_match(parser, ip4, pkt)
        if metered:
            src_dp = self.datapaths.get(src_sw)
            if src_dp is not None:
                self.ensure_meter(src_dp, force_modify=False)

        self.install_path(path, match, dst_port, metered_src_switch=src_sw if metered else None)

        out_port = self.next_hop_out_port(path, dp.id, dst_port)
        if out_port is None:
            self.flood(dp, msg)
            return
        actions = [parser.OFPActionOutput(out_port)]
        data = None if msg.buffer_id != ofp.OFPP_NO_BUFFER else msg.data
        dp.send_msg(parser.OFPPacketOut(datapath=dp, buffer_id=msg.buffer_id, in_port=in_port, actions=actions, data=data))

    def flow_match(self, parser, ip4, pkt):
        ip_proto = ip4.proto
        m = {"eth_type": 0x0800, "ipv4_src": ip4.src, "ipv4_dst": ip4.dst, "ip_proto": ip_proto}
        metered = False
        if ip_proto == 17:
            u = pkt.get_protocol(udp.udp)
            if u:
                m["udp_src"] = int(u.src_port)
                m["udp_dst"] = int(u.dst_port)
                if int(u.dst_port) in METERED_PORTS:
                    metered = True
        elif ip_proto == 6:
            t = pkt.get_protocol(tcp.tcp)
            if t:
                m["tcp_src"] = int(t.src_port)
                m["tcp_dst"] = int(t.dst_port)
        return parser.OFPMatch(**m), metered

    def flood(self, dp, msg):
        ofp = dp.ofproto
        parser = dp.ofproto_parser
        actions = [parser.OFPActionOutput(ofp.OFPP_FLOOD)]
        dp.send_msg(parser.OFPPacketOut(datapath=dp, buffer_id=msg.buffer_id,
                                       in_port=msg.match["in_port"], actions=actions,
                                       data=None if msg.buffer_id != ofp.OFPP_NO_BUFFER else msg.data))

    def install_path(self, path: List[int], match, dst_port: int, metered_src_switch: Optional[int]):
        for idx, sw in enumerate(path):
            dp = self.datapaths.get(sw)
            if dp is None:
                continue
            parser = dp.ofproto_parser
            ofp = dp.ofproto

            if sw == path[-1]:
                out_port = dst_port
            else:
                nxt = path[idx + 1]
                out_port = self.port_map.get(sw, {}).get(nxt)
            if out_port is None:
                continue

            actions = [parser.OFPActionOutput(out_port)]
            inst = []
            if metered_src_switch is not None and sw == metered_src_switch:
                inst.append(parser.OFPInstructionMeter(self.meter_id))
            inst.append(parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions))

            cookie = self.cookie_ctr
            self.cookie_ctr += 1
            self.flow_last_seen[cookie] = time.time()

            dp.send_msg(parser.OFPFlowMod(datapath=dp, cookie=cookie, priority=100, match=match,
                                          instructions=inst, idle_timeout=60, hard_timeout=0))

    def next_hop_out_port(self, path: List[int], current_sw: int, dst_port: int) -> Optional[int]:
        if current_sw not in path:
            return None
        i = path.index(current_sw)
        if i == len(path) - 1:
            return dst_port
        nxt = path[i + 1]
        return self.port_map.get(current_sw, {}).get(nxt)

    def shortest_path(self, src: int, dst: int) -> List[int]:
        if src == dst:
            return [src]
        prev = {src: None}
        q = deque([src])
        while q:
            u = q.popleft()
            for v in self.adj.get(u, []):
                if v not in prev:
                    prev[v] = u
                    q.append(v)
        if dst not in prev:
            return []
        path = []
        cur = dst
        while cur is not None:
            path.append(cur)
            cur = prev[cur]
        path.reverse()
        return path

    def stats_loop(self):
        while True:
            try:
                for dp in list(self.datapaths.values()):
                    self.request_port_stats(dp)
            except Exception as e:
                self.logger.warning("stats_loop error: %s", e)
            hub.sleep(self.stats_interval_s)

    def cleanup_loop(self):
        while True:
            now = time.time()
            expired = [c for c, ts in self.flow_last_seen.items() if now - ts > 120]
            for c in expired:
                self.flow_last_seen.pop(c, None)
            self.latest["active_flows"] = len(self.flow_last_seen)
            hub.sleep(5.0)

    def request_port_stats(self, dp):
        parser = dp.ofproto_parser
        dp.send_msg(parser.OFPPortStatsRequest(dp, 0, dp.ofproto.OFPP_ANY))

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def port_stats_reply(self, ev):
        msg = ev.msg
        dp = msg.datapath
        now = time.time()

        cap_bps = self.link_cap_mbps * 1e6
        utils = []
        total_tx_bytes = 0
        total_tx_pkts = 0
        total_tx_drop = 0

        for st in msg.body:
            port_no = int(st.port_no)
            if port_no <= 0 or port_no >= dp.ofproto.OFPP_MAX:
                continue
            key = (dp.id, port_no)
            prev = self._last_port.get(key)

            tx_bytes = int(st.tx_bytes)
            tx_pkts = int(st.tx_packets)
            tx_drop = int(st.tx_dropped)

            if prev is not None:
                dt = max(1e-3, now - prev["ts"])
                dbytes = max(0, tx_bytes - prev["tx_bytes"])
                dpkts = max(0, tx_pkts - prev["tx_pkts"])
                ddrop = max(0, tx_drop - prev["tx_drop"])

                utils.append((dbytes * 8.0 / dt) / cap_bps)
                total_tx_bytes += dbytes
                total_tx_pkts += dpkts
                total_tx_drop += ddrop

            self._last_port[key] = {"tx_bytes": tx_bytes, "tx_pkts": tx_pkts, "tx_drop": tx_drop, "ts": now}

        mean_util = sum(utils) / len(utils) if utils else 0.0
        max_util = max(utils) if utils else 0.0
        drop_rate = (total_tx_drop / (total_tx_pkts + 1.0)) if total_tx_pkts > 0 else 0.0
        thr_mbps = (total_tx_bytes * 8.0 / max(1e-3, self.stats_interval_s)) / 1e6

        a = 0.5
        self.latest["mean_util"] = a * self.latest["mean_util"] + (1 - a) * float(mean_util)
        self.latest["max_util"] = a * self.latest["max_util"] + (1 - a) * float(max_util)
        self.latest["drop_rate"] = a * self.latest["drop_rate"] + (1 - a) * float(drop_rate)
        self.latest["throughput_mbps"] = a * self.latest["throughput_mbps"] + (1 - a) * float(thr_mbps)
        self.latest["u"] = self.u
        self.latest["meter_kbps"] = self.meter_kbps
        self.latest["ts"] = now
