# -*- coding: utf-8 -*-
from mininet.topo import Topo

class FatTreeK4(Topo):
    def build(self, bw=20, delay="1ms"):
        k = 4
        pods = k
        core = [self.addSwitch(f"c{i+1}", dpid=f"{(0x300+i+1):016x}") for i in range((k//2)**2)]
        agg, edge = [], []
        for p in range(pods):
            agg_p = [self.addSwitch(f"a{p+1}{i+1}", dpid=f"{(0x400 + p*0x10 + i+1):016x}") for i in range(k//2)]
            edge_p = [self.addSwitch(f"e{p+1}{i+1}", dpid=f"{(0x500 + p*0x10 + i+1):016x}") for i in range(k//2)]
            agg.append(agg_p); edge.append(edge_p)
            for a in agg_p:
                for e in edge_p:
                    self.addLink(a, e, bw=bw, delay=delay)
            for ei, e in enumerate(edge_p):
                for h in range(k//2):
                    host = self.addHost(f"h{p+1}{ei+1}{h+1}")
                    self.addLink(e, host, bw=bw, delay=delay)
        idx = 0
        for g in range(k//2):
            for j in range(k//2):
                c = core[idx]; idx += 1
                for p in range(pods):
                    self.addLink(c, agg[p][g], bw=bw, delay=delay)
