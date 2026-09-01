#!/usr/bin/env python3
# Author: Mitchell A. Thornton
# Copyright (c) 2026 Mitchell A. Thornton
"""
check_order_determinism_v20.py -- the elimination order must not depend on
Python's string hash seed.

WHY THIS EXISTS.  Through v17 the min-fill and min-degree orderers scanned
candidates by iterating a Python set of net names.  Set iteration order over
strings depends on hash randomization, which is chosen per process, so the
tie-break among equal-fill candidates varied between runs of the same code on
the same input.  The induced widths the paper reports varied with it.  On one
generated instance the point-function width sequence came out

    [2, 3, 5, 10, 15, 16, 17, 20]     PYTHONHASHSEED=0
    [2, 3, 5, 10, 12, 18, 22]         PYTHONHASHSEED=1
    [2, 3, 5,  9, 14, 15, 18, 19]     PYTHONHASHSEED=12345

No count was ever affected.  Counts are exact and independent of the
elimination order, which is why two independent campaign runs agreed on all
seventy instances while their widths disagreed.  It was the reported widths,
and only those, that were not reproducible.

v18 fixed it by scanning a precomputed sorted list in every orderer, in
engineA.py and in lockkit_v20.py.  This gate re-runs the width computation in
child processes under several hash seeds and fails if any width moves.

Run from the bundle root:  python3 scripts/check_order_determinism_v20.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEEDS = ("0", "1", "12345", "99")

PROBE = r"""
import json, os, sys
sys.path.insert(0, os.path.join(%(root)r, "experiments", "E1_locking_width"))
sys.path.insert(0, os.path.join(%(root)r, "experiments", "E2_published_suite"))
import random
import lockkit_v20 as lk

out = {}
for scheme in ("rll", "sll", "point"):
    rng = random.Random(20260827)
    m = getattr(lk, "lock_" + scheme)(lk.BENCHES["adder"](8), 16, rng)
    if isinstance(m, tuple):
        m, key = m
    else:
        key = lk.correct_key(m, rng)
    xs, ys, widths = [], [], []
    for _t in range(5):
        x = {i: rng.randint(0, 1) for i in m.inputs}
        a = dict(x); a.update(key)
        xs.append(x); ys.append(m.simulate(a))
        c, fw, kw = lk.version_space(m, xs, ys, cap=1 << 24)
        widths.append((c, fw, kw))
    out[scheme] = widths
print(json.dumps(out))
"""


def main():
    probe = PROBE % {"root": ROOT}
    seen = {}
    for seed in SEEDS:
        env = dict(os.environ, PYTHONHASHSEED=seed)
        p = subprocess.run([sys.executable, "-c", probe], env=env,
                           capture_output=True, text=True, cwd=ROOT)
        if p.returncode:
            print("order determinism: PROBE FAILED under PYTHONHASHSEED=%s"
                  % seed)
            print(p.stderr.strip()[-1500:])
            return 1
        seen[seed] = json.loads(p.stdout.strip().splitlines()[-1])

    ref_seed = SEEDS[0]
    ref = seen[ref_seed]
    bad = []
    for seed in SEEDS[1:]:
        for scheme in ref:
            if seen[seed][scheme] != ref[scheme]:
                bad.append((scheme, seed, ref[scheme], seen[seed][scheme]))
    if bad:
        print("order determinism: FAILED, widths depend on the hash seed")
        for scheme, seed, a, b in bad:
            print("  %-6s seed %-6s %s" % (scheme, ref_seed, a))
            print("  %-6s seed %-6s %s" % ("", seed, b))
        return 1
    n = sum(len(v) for v in ref.values())
    print("elimination order independent of PYTHONHASHSEED "
          "(%d widths over %d seeds): ok" % (n, len(SEEDS)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
