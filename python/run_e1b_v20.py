#!/usr/bin/env python3
# Author: Mitchell A. Thornton
# Copyright (c) 2026 Mitchell A. Thornton
"""
E.1b -- how far each engine gets on the schemes E.1 builds.

Why this experiment exists.  E.1.2 originally reported that the method becomes
expensive on point-function locking at around the eighth query, because the
factor width grows with the query count.  That is true of the elimination
engine and it is the failure mode Section VII-C predicted.  It is not true of
the method.  The version space of a point-function lock is the full key space
minus the handful of keys the queries have removed, and a decision diagram
represents that in a number of nodes linear in the query count.  So the engine
that pays the width dies at the eighth query and the engine that represents
the version space walks past it.

Reporting the elimination engine's limit as the method's limit would understate
the result, so this driver measures both engines on the same instances and the
same queries, and the paper reports what it finds.

What is measured.  For each scheme, queries are drawn uniformly at random and
applied in sequence.  Engine A (variable elimination over the residual factor
graph, from lockkit) and Engine B (decision diagram over the key bits, from the
E.2 package) each compute |V_t| after every query.  Engine A is run only while
it is affordable; Engine B is run to `--tmax`.  Wherever both produce an
answer they must agree, and a disagreement stops the run rather than being
reported as a discrepancy.

The two engines come from different packages with different netlist classes and
share no code, so the agreement is evidence and not a restatement.  The bridge
between the two representations is `to_lockcount` below and is deliberately
trivial: both are (out, op, fanin) triples in topological order.

Outputs results/e1b_results.json.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# Engine B lives in the E.2 package.  The bundle and the public repository lay
# that out differently, so both are probed rather than assuming one.
for _cand in (os.path.join(HERE, "..", "E2_published_suite"),   # bundle
              os.path.join(HERE, "lockcount"),                   # repo image
              os.path.join(HERE, "..", "lockcount")):
    if os.path.isfile(os.path.join(_cand, "engineB.py")):
        sys.path.insert(0, _cand)
        break
else:
    raise SystemExit("cannot find engineB.py; expected an E2_published_suite "
                     "or lockcount directory next to this script")

import lockkit_v20 as lk          # Engine A, and the netlist generators
import engineB                    # Engine B, from the E.2 package
import netlist as lcnet           # the E.2 netlist class

SEED = 20260827

# Engine A gives up above this many table entries.  Same cap E.1 uses.
CAP = 1 << 26


def to_lockcount(m):
    """Bridge a lockkit netlist into the E.2 package's netlist class.

    Both are (out, op, fanin) triples in topological order over the same gate
    vocabulary, so this is a copy.  It is written out rather than hidden in a
    shared module because the point of running two engines is that they do not
    share one.
    """
    n = lcnet.Netlist("e1b")
    n.inputs = list(m.inputs)
    n.keys = list(m.keys)
    n.outputs = list(m.outputs)
    n.gates = [(o, p, list(a)) for o, p, a in m.gates]
    return n


def run_one(bench, size, scheme, nkeys, tmax, rng, a_budget):
    m = getattr(lk, "lock_" + scheme)(lk.BENCHES[bench](size), nkeys, rng)
    if isinstance(m, tuple):          # some kits return (netlist, key)
        m, key = m
    else:
        key = lk.correct_key(m, rng)
    n = to_lockcount(m)

    xs, ys, rows = [], [], []
    a_alive, a_reason = True, None
    for t in range(1, tmax + 1):
        x = {i: rng.randint(0, 1) for i in m.inputs}
        a = dict(x)
        a.update(key)
        y = m.simulate(a)
        xs.append(x)
        ys.append(y)

        ca = fw = ta = None
        if a_alive:
            t0 = time.time()
            ca, fw, _kw = lk.version_space(m, xs, ys, cap=CAP)
            ta = round(time.time() - t0, 3)
            if ca is None:
                a_alive, a_reason = False, "state cap of 2^%d exceeded" % (
                    CAP.bit_length() - 1)
            elif ta > a_budget:
                a_alive, a_reason = False, "exceeded %g s" % a_budget

        t0 = time.time()
        rb = engineB.version_space(n, list(zip(xs, ys)))
        tb = round(time.time() - t0, 3)
        cb = rb.get("count")

        if ca is not None and cb is not None and ca != cb:
            raise SystemExit("ENGINE DISAGREEMENT %s/%s t=%d: A=%d B=%d"
                             % (bench, scheme, t, ca, cb))

        rows.append({"t": t, "V_t": cb if cb is not None else ca,
                     "A_count": ca, "A_factor_width": fw, "A_seconds": ta,
                     "B_nodes": rb.get("acc_nodes"), "B_seconds": tb})
    stop = next((r["t"] for r in rows if r["A_count"] is None), None)
    return {"bench": bench, "size": size, "scheme": scheme,
            "keys": len(m.keys), "A_stops_at": stop, "A_stop_reason": a_reason,
            "B_final_nodes": rows[-1]["B_nodes"],
            "B_total_seconds": round(sum(r["B_seconds"] for r in rows), 2),
            "rows": rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tmax", type=int, default=300,
                    help="queries per instance for Engine B")
    ap.add_argument("--a-budget", type=float, default=10.0,
                    help="seconds after which Engine A is retired for this "
                         "instance; exceeding it is the datum, not an error")
    ap.add_argument("--keys", type=int, default=24)
    ap.add_argument("--out", default=os.path.join(HERE, "results",
                                                  "e1b_results.json"))
    args = ap.parse_args()

    out = {"seed": SEED, "tmax": args.tmax, "a_budget_seconds": args.a_budget,
           "cap_bits": CAP.bit_length() - 1,
           "note": "Engine A is variable elimination over the residual factor "
                   "graph (lockkit).  Engine B is a decision diagram over the "
                   "key bits (E.2 package).  They share no code and no "
                   "representation.  A_count is null once Engine A has been "
                   "retired for that instance; V_t is then Engine B's.",
           "instances": []}

    for scheme in ("point", "sll", "rll"):
        rng = random.Random(SEED)
        r = run_one("adder", 8, scheme, args.keys, args.tmax, rng,
                    args.a_budget)
        out["instances"].append(r)
        last = r["rows"][-1]
        print("%-6s k=%d: Engine A stops at t=%s (%s); Engine B reaches "
              "t=%d with %s nodes in %.1fs, |V|=%s"
              % (scheme, r["keys"],
                 r["A_stops_at"] if r["A_stops_at"] else "not within tmax",
                 r["A_stop_reason"] or "still running", last["t"],
                 last["B_nodes"], r["B_total_seconds"], last["V_t"]))
        sys.stdout.flush()

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as fh:
        json.dump(out, fh, indent=1)
    print("wrote %s" % args.out)


if __name__ == "__main__":
    main()
