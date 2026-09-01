#!/usr/bin/env python3
# Author: Mitchell A. Thornton
# Copyright (c) 2026 Mitchell A. Thornton
"""
Emit a gate-level 16x16 array multiplier in ISCAS .bench format, the same
function as ISCAS85 c6288, and verify it by simulation against integer
multiplication before writing.

c6288 is a 16x16 carry-save array multiplier: 32 inputs (a0..a15, b0..b15),
32 outputs (p0..p31).  The original netlist realizes each adder cell in NOR
logic; the function is identical to the AND/XOR/OR construction used here,
and it is the *function* that determines BDD size, which is what this
experiment measures.

Usage:  python3 mk_mult_bench_v20.py [WIDTH] [OUTFILE]
"""
import sys
import random


class Netlist:
    def __init__(self):
        self.inputs = []
        self.outputs = []
        self.gates = []          # (name, op, [fanin])
        self.n = 0

    def inp(self, name):
        self.inputs.append(name)
        return name

    def g(self, op, *args):
        self.n += 1
        name = "n%d" % self.n
        self.gates.append((name, op, list(args)))
        return name

    def AND(self, a, b): return self.g("AND", a, b)
    def OR(self, a, b):  return self.g("OR", a, b)
    def XOR(self, a, b): return self.g("XOR", a, b)

    def full_adder(self, x, y, cin):
        """Returns (sum, cout)."""
        t = self.XOR(x, y)
        s = self.XOR(t, cin)
        c1 = self.AND(x, y)
        c2 = self.AND(t, cin)
        cout = self.OR(c1, c2)
        return s, cout

    def half_adder(self, x, y):
        return self.XOR(x, y), self.AND(x, y)

    def write(self, path, name):
        with open(path, "w") as f:
            f.write("# %s\n" % name)
            f.write("# %d inputs, %d outputs, %d gates\n"
                    % (len(self.inputs), len(self.outputs), len(self.gates)))
            for i in self.inputs:
                f.write("INPUT(%s)\n" % i)
            f.write("\n")
            for o in self.outputs:
                f.write("OUTPUT(%s)\n" % o)
            f.write("\n")
            for (nm, op, fan) in self.gates:
                f.write("%s = %s(%s)\n" % (nm, op, ", ".join(fan)))

    def simulate(self, assign):
        """assign: dict input-name -> 0/1.  Returns dict of all net values."""
        v = dict(assign)
        for (nm, op, fan) in self.gates:
            a = v[fan[0]]
            if op == "BUF":
                v[nm] = a
            elif op == "NOT":
                v[nm] = 1 - a
            else:
                b = v[fan[1]]
                if op == "AND":
                    v[nm] = a & b
                elif op == "OR":
                    v[nm] = a | b
                elif op == "XOR":
                    v[nm] = a ^ b
                else:
                    raise ValueError(op)
        return v


def build_array_multiplier(W=16):
    """Classic carry-save array multiplier, W x W -> 2W bits."""
    nl = Netlist()
    a = [nl.inp("a%d" % j) for j in range(W)]
    b = [nl.inp("b%d" % i) for i in range(W)]

    # Partial products pp[i][j] = a[j] & b[i]
    pp = [[nl.AND(a[j], b[i]) for j in range(W)] for i in range(W)]

    prod = [None] * (2 * W)
    prod[0] = pp[0][0]

    # running row: after absorbing row i, row[j] holds weight (i + j + 1)
    row = [pp[0][j] for j in range(1, W)] + [None]

    for i in range(1, W):
        carry = None
        newrow = [None] * W
        for j in range(W):
            x = row[j]
            y = pp[i][j]
            if x is None:
                # nothing accumulated at this weight yet
                if carry is None:
                    s, c = y, None
                else:
                    s, c = nl.half_adder(y, carry)
            else:
                if carry is None:
                    s, c = nl.half_adder(x, y)
                else:
                    s, c = nl.full_adder(x, y, carry)
            newrow[j] = s
            carry = c
        prod[i] = newrow[0]
        row = newrow[1:] + [carry]

    # drain the final row
    for j in range(W - 1):
        prod[W + j] = row[j]
    prod[2 * W - 1] = row[W - 1]

    for k in range(2 * W):
        if prod[k] is None:
            prod[k] = nl.g("AND", a[0], nl.g("NOT", a[0]))  # constant 0
    for k in range(2 * W):
        out = nl.g("BUF", prod[k])
        nl.outputs.append(out)
        nl.gates[-1] = (out, "BUF", [prod[k]])
    return nl, a, b


def verify(nl, a, b, W, trials=300, seed=12345):
    rng = random.Random(seed)
    cases = [(0, 0), (1, 1), ((1 << W) - 1, (1 << W) - 1),
             ((1 << W) - 1, 1), (0, (1 << W) - 1), (0xDEAD & ((1 << W) - 1),
                                                    0xBEEF & ((1 << W) - 1))]
    for _ in range(trials):
        cases.append((rng.getrandbits(W), rng.getrandbits(W)))
    bad = 0
    for (av, bv) in cases:
        assign = {}
        for j in range(W):
            assign[a[j]] = (av >> j) & 1
        for i in range(W):
            assign[b[i]] = (bv >> i) & 1
        v = nl.simulate(assign)
        got = 0
        for k, o in enumerate(nl.outputs):
            got |= v[o] << k
        if got != av * bv:
            bad += 1
            if bad <= 3:
                print("  MISMATCH %d * %d = %d, got %d" % (av, bv, av * bv, got))
    return len(cases), bad


if __name__ == "__main__":
    W = int(sys.argv[1]) if len(sys.argv) > 1 else 16
    out = sys.argv[2] if len(sys.argv) > 2 else "c6288_like_%dx%d.bench" % (W, W)
    nl, a, b = build_array_multiplier(W)
    total, bad = verify(nl, a, b, W)
    print("verification: %d cases, %d mismatches" % (total, bad))
    if bad:
        print("NETLIST IS WRONG, not writing")
        sys.exit(1)
    nl.write(out, "%dx%d array multiplier (c6288-equivalent function)" % (W, W))
    print("wrote %s: %d inputs, %d outputs, %d gates"
          % (out, len(nl.inputs), len(nl.outputs), len(nl.gates)))
