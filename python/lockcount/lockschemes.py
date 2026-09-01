#!/usr/bin/env python3
# Author: Mitchell A. Thornton
# Copyright (c) 2026 Mitchell A. Thornton
"""
lockschemes.py -- benchmark generators and locking schemes.

The generators build combinational netlists whose function is known, so that
correctness of everything downstream can be checked without a reference tool.
The locking schemes are reimplementations in the style of random insertion,
interference-maximizing placement, and the point-function family.  They are
NOT the original authors' code, and they are not a substitute for the
published locked benchmark releases.  Where a measurement on a
reimplementation disagrees with the same scheme from TrustHub, the published
instance wins and the reimplementation is the bug.

Every `lock` call returns (locked_netlist, correct_key) so that an oracle can
be defined without a separate key file.
"""
from __future__ import annotations

import random

from netlist import Netlist, OPS



# ------------------------------------------------------------ generators

def gen_adder(w):
    """w-bit ripple-carry adder."""
    nl = Netlist("adder%d" % w)
    a = ["a%d" % i for i in range(w)]
    b = ["b%d" % i for i in range(w)]
    nl.inputs = a + b
    c = None
    for i in range(w):
        s1 = nl.add("XOR", a[i], b[i])
        if c is None:
            s, c = s1, nl.add("AND", a[i], b[i])
        else:
            s = nl.add("XOR", s1, c)
            t1 = nl.add("AND", s1, c)
            t2 = nl.add("AND", a[i], b[i])
            c = nl.add("OR", t1, t2)
        nl.outputs.append(s)
    nl.outputs.append(c)
    return nl


def gen_mult(w):
    """w x w array multiplier, the c6288 family."""
    nl = Netlist("mult%d" % w)
    a = ["a%d" % i for i in range(w)]
    b = ["b%d" % i for i in range(w)]
    nl.inputs = a + b
    rows = [[nl.add("AND", a[i], b[j]) for i in range(w)] for j in range(w)]
    acc = rows[0]
    nl.outputs.append(acc[0])
    for j in range(1, w):
        carry, new = None, []
        prev = acc[1:] + [None]
        for i in range(w):
            x, y = rows[j][i], prev[i]
            if y is None:
                if carry is None:
                    new.append(x)
                    continue
                s = nl.add("XOR", x, carry)
                carry = nl.add("AND", x, carry)
                new.append(s)
                continue
            s1 = nl.add("XOR", x, y)
            if carry is None:
                s, carry = s1, nl.add("AND", x, y)
            else:
                s = nl.add("XOR", s1, carry)
                t1 = nl.add("AND", s1, carry)
                t2 = nl.add("AND", x, y)
                carry = nl.add("OR", t1, t2)
            new.append(s)
        acc = new
        nl.outputs.append(acc[0])
    for t in acc[1:]:
        nl.outputs.append(t)
    return nl


def gen_cmp(w):
    """w-bit equality comparator: a balanced AND-tree over XNORs."""
    nl = Netlist("cmp%d" % w)
    a = ["a%d" % i for i in range(w)]
    b = ["b%d" % i for i in range(w)]
    nl.inputs = a + b
    lvl = [nl.add("XNOR", a[i], b[i]) for i in range(w)]
    while len(lvl) > 1:
        nxt = [nl.add("AND", lvl[i], lvl[i + 1])
               for i in range(0, len(lvl) - 1, 2)]
        if len(lvl) % 2:
            nxt.append(lvl[-1])
        lvl = nxt
    nl.outputs.append(lvl[0])
    return nl


GENERATORS = {"adder": gen_adder, "mult": gen_mult, "cmp": gen_cmp}


# -------------------------------------------------------------- locking

def _fanout(nl):
    fo = {}
    for out, _, args in nl.gates:
        for a in args:
            fo.setdefault(a, []).append(out)
    return fo


def _insert_keygates(m, picks, rng):
    """Insert an XOR or XNOR key gate on each chosen net.

    The correct key bit is the one that makes the gate a buffer: 0 for XOR,
    1 for XNOR.
    """
    key = {}
    remap = {}
    out_gates = []
    for out, op, args in m.gates:
        out_gates.append((out, op, [remap.get(a, a) for a in args]))
        if out in picks:
            kname = "keyinput%d" % len(m.keys)
            m.keys.append(kname)
            isxnor = rng.random() < 0.5
            key[kname] = 1 if isxnor else 0
            kg = m.fresh("kg")
            out_gates.append((kg, "XNOR" if isxnor else "XOR", [out, kname]))
            remap[out] = kg
    m.gates = out_gates
    m.outputs = [remap.get(o, o) for o in m.outputs]
    return m, key


def lock_rll(nl, nkeys, rng):
    """Random logic locking: key gates on randomly chosen nets."""
    m = nl.copy()
    m.name = nl.name + "_rll%d" % nkeys
    cand = [g[0] for g in m.gates if g[0] not in m.outputs] or \
           [g[0] for g in m.gates]
    picks = set(rng.sample(cand, min(nkeys, len(cand))))
    return _insert_keygates(m, picks, rng)


def lock_sll(nl, nkeys, rng):
    """Interference-maximizing placement, in the style of Strong Logic
    Locking: key gates on high-fanout nets whose cones converge."""
    m = nl.copy()
    m.name = nl.name + "_sll%d" % nkeys
    fo = _fanout(m)
    cand = [g[0] for g in m.gates if g[0] not in m.outputs] or \
           [g[0] for g in m.gates]
    cand.sort(key=lambda n: -len(fo.get(n, [])))
    return _insert_keygates(m, set(cand[:nkeys]), rng)


def lock_point(nl, nkeys, rng):
    """Point-function lock in the style of SARLock and Anti-SAT: all key bits
    feed one comparator AND-tree that flips a primary output on a single
    input pattern per wrong key."""
    m = nl.copy()
    m.name = nl.name + "_point%d" % nkeys
    key = {}
    ins = [m.inputs[i % len(m.inputs)] for i in range(nkeys)]
    lvl = []
    for i in range(nkeys):
        kname = "keyinput%d" % i
        m.keys.append(kname)
        key[kname] = rng.randint(0, 1)
        lvl.append(m.add("XNOR", ins[i], kname))
    while len(lvl) > 1:
        nxt = [m.add("AND", lvl[i], lvl[i + 1])
               for i in range(0, len(lvl) - 1, 2)]
        if len(lvl) % 2:
            nxt.append(lvl[-1])
        lvl = nxt
    m.outputs[0] = m.add("XOR", m.outputs[0], lvl[0])
    return m, key


SCHEMES = {"rll": lock_rll, "sll": lock_sll, "point": lock_point}


def lock(nl, scheme, nkeys, rng):
    """Lock `nl` and return (locked_netlist, correct_key)."""
    if scheme not in SCHEMES:
        raise ValueError("unknown scheme %r; have %s"
                         % (scheme, sorted(SCHEMES)))
    return SCHEMES[scheme](nl, nkeys, rng)


def oracle_queries(locked, key, n, rng):
    """n random queries answered by the oracle under the correct key."""
    qs = []
    for _ in range(n):
        x = {i: rng.randint(0, 1) for i in locked.inputs}
        a = dict(x)
        a.update(key)
        qs.append((x, locked.simulate(a)))
    return qs
