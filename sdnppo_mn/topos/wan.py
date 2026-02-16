# -*- coding: utf-8 -*-
from mininet.topo import Topo

class MiniWAN(Topo):
    def build(self, bw=20, delay="5ms"):
        r = [self.addSwitch(f"r{i+1}", dpid=f"{(0x200+i+1):016x}") for i in range(6)]
        edges = [(0,1),(1,2),(2,3),(3,4),(4,5),(0,5),(1,4),(2,5)]
        for u, v in edges:
            self.addLink(r[u], r[v], bw=bw, delay=delay)
        for i, sw in enumerate(r):
            h = self.addHost(f"h{i+1}")
            self.addLink(sw, h, bw=bw, delay=delay)
