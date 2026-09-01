#!/usr/bin/env python3
# Author: Mitchell A. Thornton
# Copyright (c) 2026 Mitchell A. Thornton
"""
lockkit -- netlists, locking schemes, and exact key-version-space counting.

The object of interest.  A locked netlist C(X,K) has primary inputs X and key
inputs K.  An attacker with an oracle applies a pattern x, reads the correct
response y, and discards every key that would have produced anything else.
After t queries the surviving set is

    V_t = { k : C(x_i, k) = y_i for i = 1..t },

which the logic-locking literature already calls the version space (AppSAT,
HOST 2017).  That literature proves |V_t| shrinks and then estimates it by
sampling, because computing it was taken to be out of reach.  This module
computes it exactly.

How.  Fix a query x_i.  Constant-propagate the netlist under x_i.  What
survives is a residual circuit over the key variables alone, usually a small
fraction of the original.  Each residual gate contributes one indicator
factor over (its output, its inputs), and the observed response pins the
output.  Counting satisfying assignments of the resulting factor set is a
sum-product over an elimination order, and the cost is set by the induced
width of that order, not by the key length.

Two widths are reported throughout, and the distinction is the point:

  key-moral width   the induced width of the graph on KEY VARIABLES ONLY,
                    obtained after eliminating the internal signals.  This
                    is the naive object, and for a point-function lock it is
                    a clique on all k key bits.

  factor width      the induced width of the gate-level factor graph, with
                    the internal signals kept as variables.  This is what
                    the computation actually pays, and for the same
                    point-function lock it is small, because a comparator
                    AND-tree is a tree.

Nothing here needs projected model counting.  Given x and k every internal
signal is determined, so the internal variables are defined rather than
existentially quantified, and the count is a plain sum-product.  That matters
because projected counting is double exponential in width (Fichte et al.) and
plain counting is single exponential.
"""
import itertools
import random

OPS = {
    "AND":  lambda v: int(all(v)),
    "NAND": lambda v: int(not all(v)),
    "OR":   lambda v: int(any(v)),
    "NOR":  lambda v: int(not any(v)),
    "XOR":  lambda v: int(sum(v) % 2 == 1),
    "XNOR": lambda v: int(sum(v) % 2 == 0),
    "NOT":  lambda v: int(not v[0]),
    "BUF":  lambda v: int(v[0]),
}


# ------------------------------------------------------------ netlists

class Netlist:
    def __init__(self):
        self.inputs = []
        self.keys = []
        self.outputs = []
        self.gates = []          # (out, op, [fanin]) in topological order
        self._n = 0

    def fresh(self, pfx="w"):
        self._n += 1
        return "%s%d" % (pfx, self._n)

    def add(self, op, *args, **kw):
        out = kw.get("out") or self.fresh()
        self.gates.append((out, op, list(args)))
        return out

    def copy(self):
        m = Netlist()
        m.inputs = list(self.inputs)
        m.keys = list(self.keys)
        m.outputs = list(self.outputs)
        m.gates = [(o, p, list(a)) for o, p, a in self.gates]
        m._n = self._n
        return m

    def simulate(self, assign):
        v = dict(assign)
        for out, op, args in self.gates:
            v[out] = OPS[op]([v[a] for a in args])
        return [v[o] for o in self.outputs]


def parse_bench(path):
    nl = Netlist()
    for line in open(path):
        line = line.split("#")[0].strip()
        if not line:
            continue
        if line.startswith("INPUT("):
            nl.inputs.append(line[6:-1].strip())
        elif line.startswith("OUTPUT("):
            nl.outputs.append(line[7:-1].strip())
        elif "=" in line:
            lhs, rhs = line.split("=", 1)
            op, args = rhs.strip().split("(", 1)
            nl.add(op.strip().upper(),
                   *[a.strip() for a in args.rstrip(")").split(",")],
                   out=lhs.strip())
    mx = 0
    for name in [g[0] for g in nl.gates]:
        if name[1:].isdigit():
            mx = max(mx, int(name[1:]))
    nl._n = mx
    return nl


# --------------------------------------------------- benchmark builders

def ripple_adder(w):
    """w-bit ripple-carry adder."""
    nl = Netlist()
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


def array_multiplier(w):
    """w x w array multiplier, the c6288 family."""
    nl = Netlist()
    a = ["a%d" % i for i in range(w)]
    b = ["b%d" % i for i in range(w)]
    nl.inputs = a + b
    rows = [[nl.add("AND", a[i], b[j]) for i in range(w)] for j in range(w)]
    acc = rows[0]
    nl.outputs.append(acc[0])
    for j in range(1, w):
        carry = None
        new = []
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


def comparator_tree(w):
    """Equality comparator: one output, a balanced AND-tree over XNORs."""
    nl = Netlist()
    a = ["a%d" % i for i in range(w)]
    b = ["b%d" % i for i in range(w)]
    nl.inputs = a + b
    lvl = [nl.add("XNOR", a[i], b[i]) for i in range(w)]
    while len(lvl) > 1:
        nxt = []
        for i in range(0, len(lvl) - 1, 2):
            nxt.append(nl.add("AND", lvl[i], lvl[i + 1]))
        if len(lvl) % 2:
            nxt.append(lvl[-1])
        lvl = nxt
    nl.outputs.append(lvl[0])
    return nl


BENCHES = {
    "adder": ripple_adder,
    "mult": array_multiplier,
    "cmp": comparator_tree,
}


# ------------------------------------------------------ locking schemes

def _fanout_map(nl):
    fo = {}
    for out, _, args in nl.gates:
        for a in args:
            fo.setdefault(a, []).append(out)
    return fo


def lock_rll(nl, nkeys, rng):
    """Random logic locking: XOR/XNOR key gates on randomly chosen nets."""
    m = nl.copy()
    cand = [g[0] for g in m.gates if g[0] not in m.outputs]
    if not cand:
        cand = [g[0] for g in m.gates]
    picks = rng.sample(cand, min(nkeys, len(cand)))
    ren = {}
    newgates = []
    for idx, net in enumerate(picks):
        k = "k%d" % idx
        m.keys.append(k)
        ren[net] = (k, rng.random() < 0.5)
    for out, op, args in m.gates:
        newgates.append((out, op, args))
        if out in ren:
            k, isxnor = ren[out]
            kg = m.fresh("kg")
            newgates.append((kg, "XNOR" if isxnor else "XOR", [out, k]))
            ren[out] = kg
    remap = {net: kg for net, (kg) in
             [(n, v if isinstance(v, str) else v) for n, v in ren.items()]}
    fixed = []
    done = set()
    for out, op, args in newgates:
        if out in remap and out not in done and not out.startswith("kg"):
            done.add(out)
            fixed.append((out, op, args))
            continue
        na = [remap.get(a, a) if a != out else a for a in args]
        if out.startswith("kg"):
            na = args
        fixed.append((out, op, na))
    m.gates = fixed
    m.outputs = [remap.get(o, o) for o in m.outputs]
    return m


def lock_sll(nl, nkeys, rng):
    """Interference-maximizing placement: key gates concentrated on nets that
    share downstream cones, which is what Strong Logic Locking aims at."""
    m = nl.copy()
    fo = _fanout_map(m)
    cand = [g[0] for g in m.gates if g[0] not in m.outputs]
    if not cand:
        cand = [g[0] for g in m.gates]
    # rank by fanout, then take a contiguous run so the cones converge
    cand.sort(key=lambda n: -len(fo.get(n, [])))
    picks = cand[:nkeys]
    return _insert_keygates(m, picks, rng)


def lock_point(nl, nkeys, rng):
    """Point-function lock of the SARLock/Anti-SAT family: all key bits feed
    one comparator AND-tree whose single output flips one primary output."""
    m = nl.copy()
    ins = m.inputs[:nkeys]
    while len(ins) < nkeys:
        ins.append(m.inputs[len(ins) % len(m.inputs)])
    lvl = []
    for i in range(nkeys):
        k = "k%d" % i
        m.keys.append(k)
        lvl.append(m.add("XNOR", ins[i], k))
    while len(lvl) > 1:
        nxt = []
        for i in range(0, len(lvl) - 1, 2):
            nxt.append(m.add("AND", lvl[i], lvl[i + 1]))
        if len(lvl) % 2:
            nxt.append(lvl[-1])
        lvl = nxt
    flip = m.add("XOR", m.outputs[0], lvl[0])
    m.outputs[0] = flip
    return m


def _insert_keygates(m, picks, rng):
    remap = {}
    out_gates = []
    for out, op, args in m.gates:
        out_gates.append((out, op, [remap.get(a, a) for a in args]))
        if out in picks:
            k = "k%d" % len(m.keys)
            m.keys.append(k)
            kg = m.fresh("kg")
            out_gates.append((kg, "XNOR" if rng.random() < 0.5 else "XOR",
                              [out, k]))
            remap[out] = kg
    m.gates = out_gates
    m.outputs = [remap.get(o, o) for o in m.outputs]
    return m


LOCKS = {"rll": lock_rll, "sll": lock_sll, "point": lock_point}


def correct_key(m, rng):
    """A key is correct for XOR-style locking when every key gate is a buffer.
    Rather than derive it, pick one and define the oracle by it: the oracle is
    the locked circuit under the secret key, which is what an attacker sees."""
    return {k: rng.randint(0, 1) for k in m.keys}


# ------------------------------------- residual key constraints per query

def residual_factors(m, x, y):
    """Constant-propagate under primary input pattern x, return factors.

    Returns (factors, varset).  A factor is (scope_tuple, table_dict) where
    table_dict maps an assignment tuple to 0/1.  Variables are key bits and
    the internal signals that survive propagation.
    """
    val = dict(x)                      # net -> constant, where known
    sym = {}                           # net -> variable name, where symbolic
    for k in m.keys:
        sym[k] = k
    factors = []
    for out, op, args in m.gates:
        av = []
        allconst = True
        for a in args:
            if a in val:
                av.append(("c", val[a]))
            else:
                av.append(("v", sym[a]))
                allconst = False
        if allconst:
            val[out] = OPS[op]([b for _, b in av])
            continue
        # try constant absorption
        consts = [b for t, b in av if t == "c"]
        svars = [b for t, b in av if t == "v"]
        if op in ("AND", "NAND") and 0 in consts:
            val[out] = 0 if op == "AND" else 1
            continue
        if op in ("OR", "NOR") and 1 in consts:
            val[out] = 1 if op == "OR" else 0
            continue
        if len(svars) == 1 and op in ("AND", "OR", "XOR", "XNOR",
                                      "NAND", "NOR"):
            # single symbolic input with absorbing-free constants: the gate is
            # a buffer or an inverter of that variable
            v = svars[0]
            t0 = OPS[op]([0 if s == "v" else b for s, b in
                          [(t, 0 if t == "v" else b) for t, b in av]])
            t1 = OPS[op]([1 if s == "v" else b for s, b in
                          [(t, 1 if t == "v" else b) for t, b in av]])
            if t0 == t1:
                val[out] = t0
                continue
            if t0 == 0 and t1 == 1:
                sym[out] = v
                continue
            # inverter: keep as a one-input factor
            ov = out
            sym[out] = ov
            factors.append(((ov, v), {(0, 1): 1, (1, 0): 1,
                                      (0, 0): 0, (1, 1): 0}))
            continue
        # general symbolic gate
        ov = out
        sym[out] = ov
        scope = tuple([ov] + svars)
        tab = {}
        for bits in itertools.product((0, 1), repeat=len(svars)):
            sub = dict(zip(svars, bits))
            res = OPS[op]([b if t == "c" else sub[b] for t, b in av])
            for o in (0, 1):
                tab[(o,) + bits] = int(o == res)
        factors.append((scope, tab))

    # pin the observed outputs
    for o, want in zip(m.outputs, y):
        if o in val:
            if val[o] != want:
                return None, None          # inconsistent: no key works
            continue
        v = sym[o]
        factors.append(((v,), {(want,): 1, (1 - want,): 0}))

    vs = set()
    for sc, _ in factors:
        vs.update(sc)
    # key bits with no surviving factor are free; record them
    for k in m.keys:
        vs.add(k)
    return factors, vs


# ----------------------------------------- elimination order and width

def primal_graph(factors, varset):
    adj = {v: set() for v in varset}
    for sc, _ in factors:
        for a in sc:
            for b in sc:
                if a != b:
                    adj[a].add(b)
    return adj


def min_fill_order(adj):
    """Greedy min-fill elimination order; returns (order, induced_width).

    DETERMINISM.  Candidates are visited in sorted order.  Set iteration order
    over strings depends on hash randomization, which is per process, so
    scanning `remaining` directly made the tie-break, and therefore the
    induced width, vary between runs of the same code on the same input.  The
    counts were never affected; the reported widths were.  Scanning a
    precomputed sorted list costs the same and makes the choice a function of
    the graph alone.
    """
    adj = {v: set(s) for v, s in adj.items()}
    allv = sorted(adj)
    order, width = [], 0
    remaining = set(adj)
    while remaining:
        best, bestfill = None, None
        for v in allv:
            if v not in remaining:
                continue
            nb = adj[v] & remaining
            fill = 0
            nbl = list(nb)
            for i in range(len(nbl)):
                for j in range(i + 1, len(nbl)):
                    if nbl[j] not in adj[nbl[i]]:
                        fill += 1
            if bestfill is None or fill < bestfill or \
               (fill == bestfill and len(nb) < len(adj[best] & remaining)):
                best, bestfill = v, fill
        nb = adj[best] & remaining
        width = max(width, len(nb))
        nbl = list(nb)
        for i in range(len(nbl)):
            for j in range(i + 1, len(nbl)):
                adj[nbl[i]].add(nbl[j])
                adj[nbl[j]].add(nbl[i])
        remaining.discard(best)
        order.append(best)
    return order, width


def key_moral_width(factors, varset, keys):
    """Induced width of the graph over KEY VARIABLES ONLY, after eliminating
    every internal signal first.  This is the naive object."""
    adj = primal_graph(factors, varset)
    # sorted, so the internal elimination order does not depend on set order
    internal = sorted(v for v in varset if v not in keys)
    adj = {v: set(s) for v, s in adj.items()}
    remaining = set(adj)
    for v in internal:
        if v not in remaining:
            continue
        nb = adj[v] & remaining
        nbl = list(nb)
        for i in range(len(nbl)):
            for j in range(i + 1, len(nbl)):
                adj[nbl[i]].add(nbl[j])
                adj[nbl[j]].add(nbl[i])
        remaining.discard(v)
    sub = {v: (adj[v] & remaining) for v in remaining}
    if not sub:
        return 0
    _, w = min_fill_order(sub)
    return w


# ------------------------------------------------------ exact counting

def count_models(factors, varset, order=None, cap=1 << 26):
    """Exact number of assignments to varset satisfying all factors, by
    bucket elimination.  Returns None if a bucket exceeds `cap` entries."""
    if order is None:
        order, _ = min_fill_order(primal_graph(factors, varset))
    buckets = {v: [] for v in order}
    pos = {v: i for i, v in enumerate(order)}
    for sc, tab in factors:
        v = min(sc, key=lambda z: pos[z])
        buckets[v].append((tuple(sc), tab))
    total = 1
    for v in order:
        fl = buckets[v]
        if not fl:
            total *= 2               # free variable
            continue
        scope = []
        for sc, _ in fl:
            for z in sc:
                if z not in scope:
                    scope.append(z)
        if (1 << len(scope)) > cap:
            return None
        rest = [z for z in scope if z != v]
        newtab = {}
        for bits in itertools.product((0, 1), repeat=len(rest)):
            asg = dict(zip(rest, bits))
            s = 0
            for vv in (0, 1):
                asg[v] = vv
                p = 1
                for sc, tab in fl:
                    p *= tab.get(tuple(asg[z] for z in sc), 0)
                    if p == 0:
                        break
                s += p
            if s:
                newtab[bits] = s
        if not rest:
            total *= newtab.get((), 0)
            continue
        if not newtab:
            return 0
        nv = min(rest, key=lambda z: pos[z])
        buckets[nv].append((tuple(rest), newtab))
    return total


def count_brute(m, x_list, y_list):
    """Exhaustive |V_t| over all 2^|K| keys.  Only for validation."""
    K = m.keys
    n = 0
    for bits in itertools.product((0, 1), repeat=len(K)):
        asg = dict(zip(K, bits))
        ok = True
        for x, y in zip(x_list, y_list):
            a = dict(asg)
            a.update(x)
            if m.simulate(a) != y:
                ok = False
                break
        if ok:
            n += 1
    return n


def version_space(m, x_list, y_list, cap=1 << 26):
    """Exact |V_t| for the whole query set, plus both widths."""
    allf, allv = [], set()
    for x, y in zip(x_list, y_list):
        f, vs = residual_factors(m, x, y)
        if f is None:
            return 0, 0, 0
        tag = "q%d_" % len(allf)
        rf = []
        for sc, tab in f:
            nsc = tuple(z if z in m.keys else tag + z for z in sc)
            rf.append((nsc, tab))
        allf.extend(rf)
        allv.update(z for sc, _ in rf for z in sc)
    allv.update(m.keys)
    adj = primal_graph(allf, allv)
    order, fw = min_fill_order(adj)
    kw = key_moral_width(allf, allv, set(m.keys))
    cnt = count_models(allf, allv, order, cap=cap)
    if cnt is None:
        return None, fw, kw
    # internal signals are determined, so the model count over (keys,
    # internals) equals the key count
    return cnt, fw, kw
