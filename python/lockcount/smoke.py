#!/usr/bin/env python3
# Author: Mitchell A. Thornton
# Copyright (c) 2026 Mitchell A. Thornton
"""
smoke.py -- end-to-end smoke test.

With no arguments, runs on generated netlists so the package can be checked
without any benchmark files present.

With a .bench file, locks it and reports widths and the exact version-space
trajectory:

    python3 smoke.py c432.bench --keys 16 --scheme rll --queries 6

If the file already carries key inputs, as a locked benchmark release does,
pass --prelocked and supply the key with --key FILE.  In that case no locking
is applied and the oracle is defined by the supplied key.
"""
from __future__ import annotations

import argparse
import random
import sys
import time

import engineA
import lockschemes as ls
import netlist as nlmod
import validate


def trajectory(m, key, nq, rng, cap_bits=26, budget=30.0):
    print("  %3s %14s %8s %9s %8s" % ("t", "|V_t|", "factorW", "keyMoralW",
                                      "sec"))
    qs = []
    for t in range(1, nq + 1):
        x = {i: rng.randint(0, 1) for i in m.inputs}
        a = dict(x)
        a.update(key)
        qs.append((x, m.simulate(a)))
        t0 = time.time()
        r = engineA.version_space(m, qs, cap_bits=cap_bits)
        el = time.time() - t0
        print("  %3d %14s %8s %9s %8.2f"
              % (t, r["count"] if r["count"] is not None else "CAP",
                 r["factor_width"], r["key_moral_width"], el))
        sys.stdout.flush()
        if r["count"] is None or el > budget:
            print("  stopping: %s"
                  % ("bucket cap exceeded" if r["count"] is None
                     else "time budget exceeded"))
            break
    return qs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bench", nargs="?", help="a .bench netlist")
    ap.add_argument("--scheme", default="rll", choices=sorted(ls.SCHEMES))
    ap.add_argument("--keys", type=int, default=16)
    ap.add_argument("--queries", type=int, default=6)
    ap.add_argument("--seed", type=int, default=20260827)
    ap.add_argument("--cap-bits", type=int, default=26)
    ap.add_argument("--prelocked", action="store_true",
                    help="the netlist already has key inputs")
    ap.add_argument("--key", help="key file for a pre-locked netlist")
    args = ap.parse_args()
    rng = random.Random(args.seed)

    if args.bench is None:
        print("no netlist given; running on generated circuits\n")
        for name, size in (("adder", 8), ("cmp", 8), ("mult", 4)):
            base = ls.GENERATORS[name](size)
            for scheme in ("rll", "sll", "point"):
                m, key = ls.lock(base, scheme, args.keys, rng)
                print("%s + %s, %s" % (base.name, scheme, m.stats()))
                ok, msg = validate.key_recovery_check(m, base, key, rng, 200)
                if not ok:
                    print("  key recovery FAILED: %s" % msg)
                    continue
                trajectory(m, key, args.queries, rng,
                           cap_bits=args.cap_bits)
                print()
        return 0

    nl = nlmod.load(args.bench)
    print("read %s: %s" % (args.bench, nl.stats()))
    ok, msg = validate.roundtrip_check(args.bench, rng, trials=500)
    print("round trip: %s %s" % ("ok" if ok else "FAIL", "" if ok else msg))
    if not ok:
        return 1

    if args.prelocked:
        if not nl.keys:
            print("--prelocked given but no key inputs were identified; "
                  "check netlist.KEY_PATTERNS against this release")
            return 1
        if not args.key:
            print("--prelocked needs --key FILE to define the oracle")
            return 1
        key = nlmod.read_key_file(args.key)
        missing = [k for k in nl.keys if k not in key]
        if missing:
            print("key file does not cover %d key inputs, first: %s"
                  % (len(missing), missing[:3]))
            return 1
        m = nl
    else:
        if nl.keys:
            print("note: %d key inputs already present; locking on top of "
                  "them" % len(nl.keys))
        m, key = ls.lock(nl, args.scheme, args.keys, rng)
        print("locked with %s: %s" % (args.scheme, m.stats()))

    trajectory(m, key, args.queries, rng, cap_bits=args.cap_bits)
    return 0


if __name__ == "__main__":
    sys.exit(main())
