#!/usr/bin/env python3
# Author: Mitchell A. Thornton
# Copyright (c) 2026 Mitchell A. Thornton
"""
satsolve.py -- CNF construction for a locked netlist, and one SAT call.

Two things live here.  `tseitin` turns a Netlist into CNF, which is the only
part that has to know about the gate operators.  `solve` runs a solver, and it
accepts either of two, so a reader who cannot install a Python package can
still reproduce the results:

  1. `python-sat` if it imports.  Fastest to set up:

         pip install python-sat
         pip install python-sat --break-system-packages   # Ubuntu 24.04

  2. any DIMACS solver on PATH, tried in this order: cadical, kissat,
     cryptominisat5, minisat, glucose.  On Ubuntu `sudo apt install minisat`,
     on macOS `brew install minisat`.

`have_solver()` reports which one will be used, so a driver can say so in its
output rather than failing halfway through a campaign.

The CNF is a plain Tseitin encoding.  Every gate contributes clauses that
force its output literal to equal the operator applied to its inputs, so a
satisfying assignment is exactly a consistent simulation of the netlist.  No
optimisation is attempted; the formulas here are small and the solver is not
the bottleneck.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

BINARIES = ("cadical", "kissat", "cryptominisat5", "minisat", "glucose")


class Pool:
    """Integer variable ids for hashable keys, one id per distinct key."""

    def __init__(self):
        self._n = 0
        self._id = {}

    def id(self, key):
        v = self._id.get(key)
        if v is None:
            self._n += 1
            v = self._n
            self._id[key] = v
        return v

    def __len__(self):
        return self._n


def tseitin(nl, pool, tag, xvar):
    """CNF for one copy of `nl`.

    `xvar` maps each primary input to a literal, shared between copies when
    the caller wants the copies to see the same input.  Key literals are
    tagged, so two copies with different tags have independent keys.

    Returns (value_map, clauses) where value_map carries a literal for every
    net, including the primary outputs.
    """
    cls = []
    v = dict(xvar)
    for k in nl.keys:
        v[k] = pool.id(("k", tag, k))
    for out, op, ins in nl.gates:
        o = pool.id(("g", tag, out))
        a = [v[i] for i in ins]
        v[out] = o
        if op in ("BUF", "BUFF"):
            cls += [[-o, a[0]], [o, -a[0]]]
        elif op == "NOT":
            cls += [[-o, -a[0]], [o, a[0]]]
        elif op == "AND":
            cls += [[-o, x] for x in a]
            cls.append([o] + [-x for x in a])
        elif op == "NAND":
            cls += [[o, x] for x in a]
            cls.append([-o] + [-x for x in a])
        elif op == "OR":
            cls += [[o, -x] for x in a]
            cls.append([-o] + a)
        elif op == "NOR":
            cls += [[-o, -x] for x in a]
            cls.append([o] + a)
        elif op in ("XOR", "XNOR"):
            cur = a[0]
            for j, nxt in enumerate(a[1:]):
                t = pool.id(("x", tag, out, j))
                cls += [[-t, cur, nxt], [-t, -cur, -nxt],
                        [t, -cur, nxt], [t, cur, -nxt]]
                cur = t
            if op == "XOR":
                cls += [[-o, cur], [o, -cur]]
            else:
                cls += [[-o, -cur], [o, cur]]
        else:
            raise ValueError("no CNF encoding for gate %r" % op)
    return v, cls


def equal_clauses(pool, p, q, name):
    """Clauses defining a fresh literal as (p XNOR q) is false, i.e. p != q."""
    d = pool.id(("d", name))
    return d, [[-d, p, q], [-d, -p, -q], [d, -p, q], [d, p, -q]]


def have_solver():
    """('pysat', None) or ('binary', path) or (None, None)."""
    try:
        import pysat.solvers  # noqa: F401
        return "pysat", None
    except Exception:
        pass
    for b in BINARIES:
        p = shutil.which(b)
        if p:
            return "binary", p
    return None, None


UNKNOWN = "unknown"


def solve(nvars, clauses, conflict_budget=0):
    """Solve, and distinguish "no solution" from "gave up".

    Returns the set of true variable ids when satisfiable, None when the
    formula is proved unsatisfiable, and the string UNKNOWN when a conflict
    budget ran out before either was established.

    The three outcomes must stay distinct.  An unsatisfiable answer here is
    the tightness certificate, and a solver that merely gave up looks exactly
    like one that proved unsatisfiability if the caller collapses the two.
    A budget of 0 means no budget, in which case UNKNOWN is never returned.
    """
    kind, path = have_solver()
    if kind is None:
        raise RuntimeError(
            "no SAT solver found.  Install one:\n"
            "  pip install python-sat          (add --break-system-packages "
            "on Ubuntu 24.04)\n"
            "  sudo apt install minisat        or  brew install minisat")
    if kind == "pysat":
        from pysat.solvers import Minisat22
        with Minisat22(bootstrap_with=clauses) as s:
            if conflict_budget:
                s.conf_budget(int(conflict_budget))
                r = s.solve_limited()      # True, False, or None for gave up
            else:
                r = s.solve()              # True or False
            if r is None:
                return UNKNOWN
            if r is False:
                return None
            return {x for x in s.get_model() if x > 0}
    if conflict_budget:
        raise RuntimeError(
            "a conflict budget is only supported through python-sat; "
            "either install it or run without --solver-conflicts")
    return _solve_binary(path, nvars, clauses)


def _solve_binary(path, nvars, clauses):
    d = tempfile.mkdtemp(prefix="satsolve_")
    cnf = os.path.join(d, "f.cnf")
    out = os.path.join(d, "f.out")
    with open(cnf, "w") as fh:
        fh.write("p cnf %d %d\n" % (nvars, len(clauses)))
        for c in clauses:
            fh.write(" ".join(str(x) for x in c) + " 0\n")
    name = os.path.basename(path)
    if name == "minisat":
        argv = [path, cnf, out]
    elif name in ("cryptominisat5", "glucose"):
        argv = [path, cnf]
    else:                                   # cadical, kissat
        argv = [path, cnf]
    p = subprocess.run(argv, capture_output=True, text=True)
    text = p.stdout
    if name == "minisat" and os.path.exists(out):
        text = open(out).read()
        if text.split("\n", 1)[0].strip() == "UNSAT":
            return None
        lits = text.split("\n", 1)[1].split()
    else:
        if any(l.startswith("s ") and "UNSATISFIABLE" in l
               for l in text.splitlines()):
            return None
        if not any(l.startswith("s ") and "SATISFIABLE" in l
                   for l in text.splitlines()):
            raise RuntimeError("%s gave no s-line; output was:\n%s"
                               % (name, text[:400]))
        lits = []
        for l in text.splitlines():
            if l.startswith("v "):
                lits += l[2:].split()
    return {int(x) for x in lits if x and int(x) > 0}
