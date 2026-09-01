#!/usr/bin/env python3
# Author: Mitchell A. Thornton
# Copyright (c) 2026 Mitchell A. Thornton
"""
certify.py -- prove that a version space has reached its floor.

The problem this solves.  Every |V_T| the campaign reports is an upper bound on
the surviving secret, because queries are drawn at random and a run of
unchanged counts is not a proof that no further query separates the survivors.
That caveat is stated three times in the paper and it weakens every number.

There is a floor, and it is not zero.  Write E(K*) for the keys functionally
equivalent to the correct one,

    E(K*) = { K : C(x,K) = C(x,K*) for every input pattern x }.

|V_t| is a non-increasing sequence of positive integers bounded below by
|E(K*)|, so it converges, in finitely many steps, and under random queries it
converges to exactly |E(K*)|.  A plateau therefore always exists.  The question
is only whether the one the campaign observed is the real one.

The certificate.  V_t has reached the floor exactly when no input pattern
separates two surviving keys.  For one output o, define over the joint
variables (x, K)

    A_o(x) = there is a K in V_t with output o high,
    B_o(x) = there is a K in V_t with output o low,

each obtained by existentially quantifying the key variables out of
f_o(x,K) AND V_t(K).  A pattern x is a distinguishing input pattern exactly
when A_o(x) AND B_o(x) holds for some o.  If that conjunction is unsatisfiable
for every output, then every key in V_t agrees with every other on every input,
so V_t = E(K*) and log2|V_t| is EXACT rather than an upper bound.

This is the same condition a satisfiability-based attack tests when it looks
for its next distinguishing pattern, which is worth noting: the attack's
termination condition and the plateau certificate are the same statement.

The cost, stated honestly.  The certificate needs f_o as a function of the
primary inputs AND the key bits jointly, which is a diagram of the circuit's
function.  For an integer multiplier that object is exponentially large for
every variable order (Bryant 1991), so this check is affordable on some
circuits and not on others, and the node budget decides.  An affordable
certificate is a strong result; an unaffordable one is not a failure of the
count, only of the proof, and the count remains the upper bound it always was.

Usage:

    python3 certify.py --bench c432-RN320.zip --bench-dir $BENCH -t 120

Verilog is read by the built-in reader in verilog.py; nothing external is
needed.  The Trust-Hub archives are third-party and are not vendored; see
DATA.md.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import shutil
import sys
import time

import cudd_bridge
import engineB
import netlist as nlmod
from count import resolve
from engineB import BDD, FALSE, TRUE

SEED = 20260827


def _exists(bdd, node, levels):
    """Existentially quantify the variables at `levels` out of `node`.

    Depth-first with memoisation.  At a quantified level the two cofactors are
    disjoined; at a retained level they are rebuilt.
    """
    memo = {}

    def rec(n):
        if n <= TRUE:
            return n
        r = memo.get(n)
        if r is not None:
            return r
        lvl, lo, hi = bdd.nodes[n]
        a, b = rec(lo), rec(hi)
        r = bdd.apply("or", a, b) if lvl in levels else bdd.mk(lvl, a, b)
        memo[n] = r
        return r

    return rec(node)


def certify(nl, queries, node_cap=4_000_000, count=None):
    """Is V_t at its floor?  See _certify_inner; this wrapper handles the
    one-key case using whichever engine can supply the count.

    The one-key rule needs only |V_t|, not the joint diagram, so it should not
    be gated on the Python diagram being able to build V_t.  Where the C
    engine is available it is asked first, which is what lets the rule fire on
    instances the Python diagram cannot reach.
    """
    if count is None:
        if cudd_bridge.available():
            count = cudd_bridge.version_space(nl, queries,
                                              node_limit=node_cap * 8,
                                              timeout=300).get("count")
        if count is None:
            count = engineB.version_space(nl, queries,
                                          node_cap=node_cap).get("count")
    if count == 0:
        return {"certified": None, "note": "empty version space", "seconds": 0.0}
    if count == 1:
        return {"certified": True, "trivial": True, "seconds": 0.0,
                "note": "the version space holds one key, so it is at its "
                        "floor by definition and the count is exact"}
    return _certify_inner(nl, queries, node_cap)


def _certify_inner(nl, queries, node_cap=4_000_000):
    """Is V_t at its floor?  Returns a dict; `certified` is the answer.

    Builds one diagram over (inputs, keys) jointly.  Key variables come first
    in the order so that quantifying them out is cheap, and the primary inputs
    follow.
    """
    t0 = time.time()
    keys, ins = list(nl.keys), list(nl.inputs)
    bdd = BDD(keys + ins)
    kv = {k: bdd.var(k) for k in keys}
    iv = {i: bdd.var(i) for i in ins}
    klevels = frozenset(range(len(keys)))

    # V_t over the key variables alone
    acc = TRUE
    for x, y in queries:
        val = dict(kv)
        for i, v in x.items():
            val[i] = TRUE if v else FALSE
        outs = _sim(bdd, nl, val, node_cap)
        if outs is None:
            return {"certified": None, "note": "node cap building V_t",
                    "seconds": round(time.time() - t0, 1)}
        for o, want in zip(nl.outputs, y):
            acc = bdd.apply("and", acc, outs[o] if want else bdd.neg(outs[o]))
    if acc == FALSE:
        return {"certified": None, "note": "empty version space",
                "seconds": round(time.time() - t0, 1)}

    # f_o over inputs AND keys jointly.  This is the expensive object.
    val = dict(kv)
    val.update(iv)
    outs = _sim(bdd, nl, val, node_cap)
    if outs is None:
        return {"certified": None,
                "note": "node cap building the joint circuit diagram",
                "seconds": round(time.time() - t0, 1),
                "nodes": bdd.size()}

    for o in nl.outputs:
        hi = bdd.apply("and", outs[o], acc)
        lo = bdd.apply("and", bdd.neg(outs[o]), acc)
        A = _exists(bdd, hi, klevels)
        B = _exists(bdd, lo, klevels)
        if bdd.apply("and", A, B) != FALSE:
            return {"certified": False, "witness_output": o,
                    "note": "a distinguishing input pattern still exists",
                    "seconds": round(time.time() - t0, 1),
                    "nodes": bdd.size()}
        if bdd.size() > node_cap:
            return {"certified": None, "note": "node cap during quantification",
                    "seconds": round(time.time() - t0, 1),
                    "nodes": bdd.size()}

    return {"certified": True,
            "note": "no input pattern separates two surviving keys, so "
                    "V_t = E(K*) and the count is exact",
            "seconds": round(time.time() - t0, 1), "nodes": bdd.size()}


def _sim(bdd, nl, val, node_cap):
    for out, op, args in nl.gates:
        av = [val[a] for a in args]
        if op == "NOT":
            r = bdd.neg(av[0])
        elif op in ("BUF", "BUFF"):
            r = av[0]
        elif op in ("AND", "NAND"):
            r = av[0]
            for z in av[1:]:
                r = bdd.apply("and", r, z)
            if op == "NAND":
                r = bdd.neg(r)
        elif op in ("OR", "NOR"):
            r = av[0]
            for z in av[1:]:
                r = bdd.apply("or", r, z)
            if op == "NOR":
                r = bdd.neg(r)
        elif op in ("XOR", "XNOR"):
            r = av[0]
            for z in av[1:]:
                r = bdd.apply("xor", r, z)
            if op == "XNOR":
                r = bdd.neg(r)
        else:
            raise ValueError("unsupported gate %r" % op)
        val[out] = r
        if bdd.size() > node_cap:
            return None
    return val


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bench", required=True)
    ap.add_argument("--bench-dir")
    ap.add_argument("-t", "--queries", type=int, default=60)
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--node-cap", type=int, default=4_000_000)
    ap.add_argument("--out")
    args = ap.parse_args()

    path, hold = resolve(args.bench, args.bench_dir)
    try:
        nl = nlmod.load(path)
        st = nl.stats()
        name = os.path.basename(path).rsplit(".", 1)[0]
        print("%s: %d gates, %d inputs, %d keys, %d outputs"
              % (name, st["gates"], st["inputs"], st["keys"], st["outputs"]))
        rng = random.Random(args.seed)
        secret = {k: rng.randint(0, 1) for k in nl.keys}
        queries = []
        for _ in range(args.queries):
            x = {i: rng.randint(0, 1) for i in nl.inputs}
            a = dict(x)
            a.update(secret)
            queries.append((x, nl.simulate(a)))

        # ask the same engine certify() will use, so the reported count and
        # the verdict cannot come from different places
        cnt = None
        if cudd_bridge.available():
            cnt = cudd_bridge.version_space(
                nl, queries, node_limit=args.node_cap * 8,
                timeout=300).get("count")
        if cnt is None:
            cnt = engineB.version_space(
                nl, queries, node_cap=args.node_cap).get("count")
        r = certify(nl, queries, node_cap=args.node_cap, count=cnt)
        r.update({"benchmark": name, "queries": len(queries),
                  "k": len(nl.keys), "count": cnt,
                  "log2_V": None if not cnt else round(math.log2(cnt), 3)})
        verdict = {True: "CERTIFIED EXACT", False: "not at the floor",
                   None: "undecided"}[r["certified"]]
        print("  |V_t| = %s   log2 = %s   %s   (%.1fs)  %s"
              % (cnt, r["log2_V"], verdict, r["seconds"], r["note"]))
        if args.out:
            prev = []
            if os.path.exists(args.out):
                prev = json.load(open(args.out)).get("rows", [])
            prev = [x for x in prev if x["benchmark"] != name]
            prev.append(r)
            json.dump({"note": "plateau certificates; certified=true means "
                               "log2_V is exact, not an upper bound",
                       "seed": args.seed, "rows": prev},
                      open(args.out, "w"), indent=1)
        return 0
    finally:
        if hold:
            shutil.rmtree(hold, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
