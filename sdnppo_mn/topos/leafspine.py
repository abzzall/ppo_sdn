# -*- coding: utf-8 -*-
from mininet.topo import Topo

class LeafSpine(Topo):
    def build(self, bw=20, delay="1ms", spines=2, leaves=4, hosts_per_leaf=2):
        sp = [self.addSwitch(f"s{i+1}") for i in range(spines)]
        lf = [self.addSwitch(f"l{i+1}") for i in range(leaves)]
        for s in sp:
            for l in lf:
                self.addLink(s, l, bw=bw, delay=delay)
        hid = 1
        for l in lf:
            for _ in range(hosts_per_leaf):
                h = self.addHost(f"h{hid}")
                hid += 1
                self.addLink(l, h, bw=bw, delay=delay)
