# -*- coding: utf-8 -*-
import math
import random

def const50(state, step_idx, prev_u=0.5):
    return 0.5

def rr(state, step_idx, prev_u=0.5):
    return 0.3 if (step_idx % 2 == 0) else 0.8

def util_guard(state, step_idx, prev_u=0.5):
    max_util = float(state.get("max_util", 0.0))
    drop_rate = float(state.get("drop_rate", 0.0))
    if max_util > 0.80 or drop_rate > 0.02:
        target = 0.15
    else:
        boost = 0.35 * math.tanh(2.0 * (0.75 - max_util))
        target = 0.45 + boost + random.uniform(-0.05, 0.05)
    u = 0.7 * prev_u + 0.3 * target
    return max(0.05, min(0.95, u))
