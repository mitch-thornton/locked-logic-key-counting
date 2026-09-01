#!/usr/bin/env python3
# Author: Mitchell A. Thornton
# Copyright (c) 2026 Mitchell A. Thornton
"""
validate.py -- the parity harness.

Nothing in this package is trusted until it agrees with something computed a
different way.  Three checks live here.

  roundtrip_check   a reader is correct when writing what it read and reading
                    that back reproduces the same simulation on random
                    vectors.  Any new reader must pass this before anything
                    depends on it.

  key_recovery_check  a locked benchmark that ships a key is correct when the
                    locked netlist under that key matches the unlocked
                    reference on random vectors.  An instance that fails is
                    set aside and reported, never worked around.

  parity_check      Engine A against exhaustive enumeration, and against any
                    other engine registered.  Engines fail differently, so
                    agreement is evidence and disagreement halts work.

Run directly for a self-test on generated netlists.
"""
from __future__ import annotations

import itertools
import os
import random
import tempfile

import cudd_bridge
import engineA
import engineB
import netlist as nlmod


# --------------------------------------------------------- ground truth

def brute_version_space(nl, queries):
    """Exhaustive |V_t| over all 2^|K| keys.  For validation only."""
    ks = nl.keys
    if len(ks) > 24:
        raise ValueError("refusing to enumerate %d keys" % len(ks))
    n = 0
    for bits in itertools.product((0, 1), repeat=len(ks)):
        asg = dict(zip(ks, bits))
        ok = True
        for x, y in queries:
            a = dict(asg)
            a.update(x)
            if nl.simulate(a) != y:
                ok = False
                break
        if ok:
            n += 1
    return n


# ------------------------------------------------------------- checks

def roundtrip_check(path, rng, trials=1000, fmt=None):
    """Read, write, read back, and compare simulation."""
    a = nlmod.load(path, fmt=fmt)
    tmp = tempfile.mktemp(suffix=".bench")
    try:
        nlmod.write_bench(a, tmp)
        b = nlmod.read_bench(tmp)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    sa, sb = dict(a.stats()), dict(b.stats())
    sa.pop("name", None)
    sb.pop("name", None)
    if sa != sb:
        return False, "stats differ: %s vs %s" % (sa, sb)
    for _ in range(trials):
        asg = a.random_assignment(rng)
        if a.simulate(asg) != b.simulate(asg):
            return False, "simulation differs"
    return True, "ok (%d vectors)" % trials


def key_recovery_check(locked, reference, key, rng, trials=1000):
    """The locked netlist under its published key must equal the reference."""
    missing = [k for k in locked.keys if k not in key]
    if missing:
        return False, "key file does not cover %d key inputs" % len(missing)
    for _ in range(trials):
        x = {i: rng.randint(0, 1) for i in locked.inputs}
        a = dict(x)
        a.update({k: key[k] for k in locked.keys})
        if locked.simulate(a) != reference.simulate(x):
            return False, "locked netlist under the published key differs " \
                          "from the reference"
    return True, "ok (%d vectors)" % trials


def parity_check(nl, queries, cap_bits=26, engines=()):
    """Engine A against brute force and against any extra engines.

    `engines` is a sequence of (name, callable) where the callable takes
    (netlist, queries) and returns an integer or None.
    """
    res = engineA.version_space(nl, queries, cap_bits=cap_bits)
    out = {"A": res["count"], "factor_width": res["factor_width"],
           "key_moral_width": res["key_moral_width"]}
    if len(nl.keys) <= 20:
        out["brute"] = brute_version_space(nl, queries)
    for name, fn in engines:
        out[name] = fn(nl, queries)
    vals = [v for k, v in out.items()
            if k not in ("factor_width", "key_moral_width") and v is not None]
    out["agree"] = len(set(vals)) <= 1
    return out


# ---------------------------------------------------------- self-test

def _selftest(quick=False):
    import lockschemes as ls

    rng = random.Random(20260827)
    print("validate.py self-test")

    # round trip on a generated netlist
    nl = ls.gen_adder(8)
    tmp = tempfile.mktemp(suffix=".bench")
    nlmod.write_bench(nl, tmp)
    ok, msg = roundtrip_check(tmp, rng)
    os.remove(tmp)
    print("  round trip, generated adder: %s %s" % ("ok" if ok else "FAIL",
                                                    "" if ok else msg))

    # key recovery on a locked netlist
    ref = ls.gen_adder(6)
    locked, key = ls.lock(ref, "rll", 8, rng)
    ok, msg = key_recovery_check(locked, ref, key, rng, trials=300)
    print("  key recovery, RLL: %s %s" % ("ok" if ok else "FAIL",
                                          "" if ok else msg))

    # parity, engine A against brute force
    bad = tested = 0
    for bench, size in (("adder", 4), ("cmp", 4), ("mult", 3)):
        base = ls.GENERATORS[bench](size)
        for scheme in ("rll", "sll", "point"):
            for nk in (4, 6, 8):
                m, key = ls.lock(base, scheme, nk, rng)
                if len(m.keys) < 2:
                    continue
                queries = []
                for _ in range(3):
                    x = {i: rng.randint(0, 1) for i in m.inputs}
                    a = dict(x)
                    a.update(key)
                    queries.append((x, m.simulate(a)))
                    r = parity_check(m, queries)
                    tested += 1
                    if not r["agree"]:
                        bad += 1
                        print("    MISMATCH %s/%s k=%d: %s"
                              % (bench, scheme, len(m.keys), r))
    print("  engine A == brute force (%d cases): %s"
          % (tested, "ok" if bad == 0 else "FAIL (%d)" % bad))

    ok2 = _wide_gate_check(rng, trials=40 if quick else 200)
    ok4 = _trajectory_check(rng, trials=8 if quick else 30)
    ok5 = _certificate_check(rng, trials=8 if quick else 30)
    ok3 = _engine_parity_check(rng, trials=20 if quick else 120)
    return bad == 0 and ok2 and ok3 and ok4 and ok5


def _engine_parity_check(rng, trials=120):
    """Engine A against Engine B against brute force.

    The two engines share no intermediate representation: A eliminates
    variables over a factor graph and pays induced width, B builds a decision
    diagram over the key variables and pays diagram size.  Agreement is
    therefore evidence rather than a restatement.
    """
    import lockschemes as ls
    bad = tested = 0
    for _ in range(trials):
        bench = rng.choice(["adder", "cmp", "mult"])
        size = rng.randint(3, 5)
        base = ls.GENERATORS[bench](size)
        scheme = rng.choice(["rll", "sll", "point"])
        m, key = ls.lock(base, scheme, rng.randint(4, 10), rng)
        if len(m.keys) < 2:
            continue
        qs = []
        for _t in range(2):
            x = {i: rng.randint(0, 1) for i in m.inputs}
            a = dict(x)
            a.update(key)
            qs.append((x, m.simulate(a)))
            vals = [engineA.version_space(m, qs)["count"],
                    engineB.version_space(m, qs)["count"]]
            if cudd_bridge.available():
                vals.append(cudd_bridge.version_space(m, qs)["count"])
            if len(m.keys) <= 16:
                vals.append(brute_version_space(m, qs))
            tested += 1
            if len(set(v for v in vals if v is not None)) > 1:
                bad += 1
    label = "engineA == engineB == brute force"
    if cudd_bridge.available():
        label = "engineA == engineB(py) == engineB(CUDD) == brute force"
    print("  %s (%d cases): %s"
          % (label, tested, "ok" if bad == 0 else "FAIL (%d)" % bad))
    return bad == 0


def _certificate_check(rng, trials=30):
    """The plateau certificate against an exhaustive search for a
    distinguishing input pattern.

    certify() decides whether any input pattern separates two surviving keys,
    by quantifying the key variables out of a joint diagram.  Ground truth
    here enumerates the version space and then tries every input pattern.  The
    two must agree: a certificate that can be wrong is worse than none.
    """
    import itertools
    import lockschemes as ls
    from certify import certify
    bad = tested = 0
    for _ in range(trials):
        bench = rng.choice(["adder", "cmp", "mult"])
        base = ls.GENERATORS[bench](rng.randint(3, 4))
        m, key = ls.lock(base, rng.choice(["rll", "sll", "point"]),
                         rng.randint(3, 8), rng)
        if len(m.keys) < 2 or len(m.inputs) > 12:
            continue
        qs = []
        for _t in range(rng.randint(1, 6)):
            x = {i: rng.randint(0, 1) for i in m.inputs}
            a = dict(x)
            a.update(key)
            qs.append((x, m.simulate(a)))
        r = certify(m, qs)
        if r["certified"] is None:
            continue
        V = []
        for bits in itertools.product((0, 1), repeat=len(m.keys)):
            asg = dict(zip(m.keys, bits))
            if all(m.simulate(dict(x, **asg)) == y for x, y in qs):
                V.append(asg)
        at_floor = True
        for xb in itertools.product((0, 1), repeat=len(m.inputs)):
            x = dict(zip(m.inputs, xb))
            if len({tuple(m.simulate(dict(x, **a))) for a in V}) > 1:
                at_floor = False
                break
        tested += 1
        if r["certified"] != at_floor:
            bad += 1
    print("  plateau certificate == exhaustive DIP search (%d cases): %s"
          % (tested, "ok" if bad == 0 else "FAIL (%d)" % bad))
    return bad == 0


def _trajectory_check(rng, trials=30):
    """The C engine's one-pass trajectory against per-prefix counting.

    The campaign reports |V_t| after every query.  Doing that by re-invoking
    the engine on a growing query list is quadratic; the C engine reports the
    count after each conjunction in a single pass instead.  The two must give
    the same sequence, and this is the gate that says so.
    """
    import lockschemes as ls
    if not cudd_bridge.available():
        print("  trajectory parity: skipped, CUDD engine not built")
        return True
    bad = tested = 0
    for _ in range(trials):
        bench = rng.choice(["adder", "cmp", "mult"])
        base = ls.GENERATORS[bench](rng.randint(3, 5))
        m, key = ls.lock(base, rng.choice(["rll", "sll", "point"]),
                         rng.randint(4, 12), rng)
        if len(m.keys) < 2:
            continue
        qs = []
        for _t in range(rng.randint(2, 5)):
            x = {i: rng.randint(0, 1) for i in m.inputs}
            a = dict(x)
            a.update(key)
            qs.append((x, m.simulate(a)))
        one_pass = [row["count"]
                    for row in cudd_bridge.trajectory(m, qs).get("traj", [])]
        prefix = [engineB.version_space(m, qs[:i + 1])["count"]
                  for i in range(len(qs))]
        tested += 1
        if one_pass != prefix:
            bad += 1
    print("  CUDD one-pass trajectory == per-prefix counting (%d cases): %s"
          % (tested, "ok" if bad == 0 else "FAIL (%d)" % bad))
    return bad == 0


def _wide_gate_check(rng, trials=200):
    """Wide gates are decomposed into chains rather than tabulated; that path
    is not reached by two-input generators, so it is exercised here.

    AntiSAT comparator blocks carry AND gates over hundreds of key bits, which
    is what forces the decomposition.  It must be exact.
    """
    from netlist import Netlist
    bad = tested = 0
    for _ in range(trials):
        nk = rng.randint(3, 7)
        op = rng.choice(["AND", "NAND", "OR", "NOR", "XOR", "XNOR"])
        nl = Netlist("wide")
        nl.inputs = ["x%d" % i for i in range(rng.randint(1, 3))]
        nl.keys = ["k%d" % i for i in range(nk)]
        args = list(nl.keys) + (["x0"] if rng.random() < 0.5 else [])
        rng.shuffle(args)
        o = nl.add(op, *args)
        if rng.random() < 0.5:
            o2 = nl.add(rng.choice(["AND", "OR", "XOR"]),
                        *(nl.keys[:max(2, nk - 1)]))
            o = nl.add("XOR", o, o2)
        nl.outputs = [o]
        secret = {k: rng.randint(0, 1) for k in nl.keys}
        qs = []
        for _t in range(2):
            x = {i: rng.randint(0, 1) for i in nl.inputs}
            a = dict(x)
            a.update(secret)
            qs.append((x, nl.simulate(a)))
            tested += 1
            if engineA.version_space(nl, qs)["count"] != \
                    brute_version_space(nl, qs):
                bad += 1
    print("  wide-gate decomposition == brute force (%d cases): %s"
          % (tested, "ok" if bad == 0 else "FAIL (%d)" % bad))
    return bad == 0


if __name__ == "__main__":
    import sys
    sys.exit(0 if _selftest(quick="--quick" in sys.argv) else 1)
