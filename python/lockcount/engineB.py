#!/usr/bin/env python3
# Author: Mitchell A. Thornton
# Copyright (c) 2026 Mitchell A. Thornton
"""
engineB.py -- exact key version-space counting by decision diagram.

Same integer as engineA, computed a different way, so the two check each
other.  Where engineA eliminates variables over a factor graph and pays the
induced width, this builds a reduced ordered BDD over the key variables and
pays the diagram size.

Method.  Fix the primary inputs of a query.  Symbolically simulate the netlist
so that every net becomes a BDD over the key variables alone.  Conjoin
`net XNOR y` over the observed outputs.  Conjoin across queries.  Then count
the satisfying assignments of the accumulated constraint.

Why this matters beyond parity.  The two engines have opposite cost trends in
the number of queries.  Each query adds another copy of the residual circuit
to engineA's factor graph, so its induced width GROWS with t.  Each query
further constrains engineB's accumulated BDD, and the diagram tracks the
version space, which SHRINKS with t.  Measured on the TrustHub obfuscation
suite, engineA's width crosses the practical limit around three to four
queries, which is roughly where the version space has already collapsed by
orders of magnitude.  Neither engine dominates; they fail at opposite ends.

The BDD here is a small self-contained ROBDD: unique table, memoized apply,
complement-free.  It is not CUDD.  It exists so that parity can be checked
without a C toolchain, and so that the crossover between the two engines can
be measured at all.  A CUDD implementation is the production path.
"""
from __future__ import annotations

from netlist import OPS

# A node is an int.  0 and 1 are the terminals.  Everything else indexes
# `self.nodes`, holding (var_level, low, high).
FALSE, TRUE = 0, 1


class BDD:
    def __init__(self, order):
        """`order` is the list of variable names, outermost first."""
        self.order = list(order)
        self.level = {v: i for i, v in enumerate(self.order)}
        self.nodes = [None, None]          # terminals occupy 0 and 1
        self.unique = {}
        self._ite = {}
        self._count = {}

    # -- construction ---------------------------------------------------

    def mk(self, lvl, lo, hi):
        if lo == hi:
            return lo
        key = (lvl, lo, hi)
        n = self.unique.get(key)
        if n is None:
            n = len(self.nodes)
            self.nodes.append(key)
            self.unique[key] = n
        return n

    def var(self, name):
        return self.mk(self.level[name], FALSE, TRUE)

    def size(self):
        return len(self.nodes) - 2

    # -- operations ------------------------------------------------------

    def _top(self, *ns):
        best = None
        for n in ns:
            if n > TRUE:
                lvl = self.nodes[n][0]
                if best is None or lvl < best:
                    best = lvl
        return best

    def _cof(self, n, lvl, val):
        if n <= TRUE or self.nodes[n][0] != lvl:
            return n
        return self.nodes[n][2] if val else self.nodes[n][1]

    def apply(self, op, a, b):
        """op in {'and','or','xor','xnor'}."""
        key = (op, a, b) if a <= b or op == "xnor" else (op, b, a)
        r = self._ite.get(key)
        if r is not None:
            return r
        t = self._terminal(op, a, b)
        if t is not None:
            self._ite[key] = t
            return t
        lvl = self._top(a, b)
        lo = self.apply(op, self._cof(a, lvl, 0), self._cof(b, lvl, 0))
        hi = self.apply(op, self._cof(a, lvl, 1), self._cof(b, lvl, 1))
        r = self.mk(lvl, lo, hi)
        self._ite[key] = r
        return r

    def _terminal(self, op, a, b):
        if a > TRUE or b > TRUE:
            if op == "and":
                if a == FALSE or b == FALSE:
                    return FALSE
                if a == TRUE:
                    return b
                if b == TRUE:
                    return a
            elif op == "or":
                if a == TRUE or b == TRUE:
                    return TRUE
                if a == FALSE:
                    return b
                if b == FALSE:
                    return a
            elif op in ("xor", "xnor"):
                if a == b:
                    return TRUE if op == "xnor" else FALSE
            return None
        # both terminal
        va, vb = bool(a), bool(b)
        if op == "and":
            return int(va and vb)
        if op == "or":
            return int(va or vb)
        if op == "xor":
            return int(va != vb)
        return int(va == vb)

    def neg(self, a):
        return self.apply("xor", a, TRUE)

    # -- counting --------------------------------------------------------

    def count(self, n, nvars=None):
        """Satisfying assignments over the full variable order."""
        nvars = len(self.order) if nvars is None else nvars
        c = self._count_from(n)
        top = nvars if n <= TRUE else self.nodes[n][0]
        return c * (1 << top)

    def _count_from(self, n):
        """Assignments over variables at or below this node's level."""
        if n == FALSE:
            return 0
        if n == TRUE:
            return 1
        r = self._count.get(n)
        if r is not None:
            return r
        lvl, lo, hi = self.nodes[n]
        tot = 0
        for child in (lo, hi):
            clvl = len(self.order) if child <= TRUE else self.nodes[child][0]
            tot += self._count_from(child) * (1 << (clvl - lvl - 1))
        self._count[n] = tot
        return tot


# --------------------------------------------------------- symbolic sim

def _key_order(nl, queries):
    """Variable order for the key bits.

    Ordering matters a great deal for diagram size.  Keys are ordered by the
    topological level of the gate they first reach, so key bits that interact
    early sit adjacent.  This is a cheap heuristic, not an optimum; CUDD's
    reordering would do better.
    """
    lev = nl.levelize()
    return sorted(nl.keys, key=lambda k: (lev.get(k, 0), k))


def version_space(nl, queries, node_cap=2_000_000, order=None):
    """Exact |V_t| by BDD.  Returns a dict shaped like engineA's."""
    order = order or _key_order(nl, queries)
    bdd = BDD(order)
    kvar = {k: bdd.var(k) for k in order}
    acc = TRUE
    peak = 0
    for x, y in queries:
        val = {}
        for i, v in x.items():
            val[i] = TRUE if v else FALSE
        for k in nl.keys:
            val[k] = kvar[k]
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
                return {"count": None, "bdd_nodes": bdd.size(),
                        "peak_nodes": max(peak, bdd.size()),
                        "note": "node cap exceeded"}
        for o, want in zip(nl.outputs, y):
            got = val[o]
            acc = bdd.apply("and", acc,
                            got if want else bdd.neg(got))
            if acc == FALSE:
                return {"count": 0, "bdd_nodes": bdd.size(),
                        "peak_nodes": max(peak, bdd.size())}
        peak = max(peak, bdd.size())
        if bdd.size() > node_cap:
            return {"count": None, "bdd_nodes": bdd.size(),
                    "peak_nodes": peak, "note": "node cap exceeded"}
    return {"count": bdd.count(acc), "bdd_nodes": bdd.size(),
            "peak_nodes": peak, "acc_nodes": _sub_size(bdd, acc)}


def _sub_size(bdd, n):
    seen, stack = set(), [n]
    while stack:
        m = stack.pop()
        if m <= TRUE or m in seen:
            continue
        seen.add(m)
        _l, lo, hi = bdd.nodes[m]
        stack.append(lo)
        stack.append(hi)
    return len(seen)
