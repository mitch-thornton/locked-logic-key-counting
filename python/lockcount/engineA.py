#!/usr/bin/env python3
# Author: Mitchell A. Thornton
# Copyright (c) 2026 Mitchell A. Thornton
"""
engineA.py -- exact key version-space counting by bucket elimination.

Given a locked netlist and a set of oracle queries (x_i, y_i), compute

    |V_t| = #{ K : C(x_i, K) = y_i for i = 1..t }

exactly, in integer arithmetic.

Method.  Fix the primary inputs of a query and constant-propagate.  What
survives is a residual circuit over the key bits.  Each surviving gate
contributes an indicator factor over (its output, its symbolic inputs); each
observed primary output pins a net.  Counting satisfying assignments of the
resulting factor set is a sum-product over an elimination order.

Why this is not projected counting.  Given the primary inputs and the key,
every internal net of a combinational netlist takes exactly one value.  The
internal variables are therefore defined rather than existentially quantified,
so the model count over (keys, internals) equals the key count.  Plain model
counting is singly exponential in induced width; projected model counting is
doubly exponential.  The distinction is the difference between a computation
and an impossibility, and it is why the elimination is done over the
gate-level factor graph rather than over the key variables alone.

Two widths are reported, and the gap between them is the point:

  key_moral_width   induced width of the graph over key variables only,
                    obtained by eliminating every internal variable first.
  factor_width      induced width of the gate-level factor graph, internals
                    retained.  This is what the computation pays.
"""
from __future__ import annotations

import itertools

from netlist import OPS

# Above this many symbolic inputs a gate is decomposed into a chain rather
# than tabulated.  Three keeps every factor at scope four or less.
MAX_FANIN = 3


def _bin_table(op2):
    """Indicator table for out = op2(a, b), scope (out, a, b)."""
    tab = {}
    for a in (0, 1):
        for b in (0, 1):
            tab[(OPS[op2]([a, b]), a, b)] = 1
    return tab


def _decompose_wide(factors, ov, op, av, svars, tag):
    """Exact chain decomposition of a wide associative gate.

    AND, OR, XOR and their negations are associative, so a gate over many
    inputs equals a left-nested chain of two-input gates.  Constants already
    absorbed by the caller are folded in as the chain seed.
    """
    base = {"AND": "AND", "NAND": "AND", "OR": "OR", "NOR": "OR",
            "XOR": "XOR", "XNOR": "XOR"}[op]
    negate = op in ("NAND", "NOR", "XNOR")
    consts = [b for t, b in av if t == "c"]
    seed = None
    if consts:
        seed = consts[0]
        for c in consts[1:]:
            seed = OPS[base]([seed, c])

    cur = None
    if seed is not None:
        # fold the constant into the first step rather than making a node
        if base == "AND" and seed == 1:
            seed = None
        elif base == "OR" and seed == 0:
            seed = None
        elif base == "XOR" and seed == 0:
            seed = None
    idx = 0
    for v in svars:
        if cur is None:
            cur = v
            continue
        nxt = "%s~c%d/%s" % (tag, idx, ov)
        idx += 1
        factors.append(((nxt, cur, v), _bin_table(base)))
        cur = nxt
    if seed is not None:
        nxt = "%s~c%d/%s" % (tag, idx, ov)
        idx += 1
        tab = {}
        for a in (0, 1):
            tab[(OPS[base]([a, seed]), a)] = 1
        factors.append(((nxt, cur), tab))
        cur = nxt
    if negate:
        factors.append(((ov, cur), {(0, 1): 1, (1, 0): 1}))
    else:
        factors.append(((ov, cur), {(0, 0): 1, (1, 1): 1}))


# ------------------------------------------------- residual factor set

def residual_factors(nl, x, y, tag=""):
    """Constant-propagate under primary-input pattern `x`, return factors.

    Returns (factors, varset) or (None, None) if the observation is
    inconsistent with every key, in which case |V| is zero.

    A factor is (scope_tuple, table) with table mapping an assignment tuple
    over the scope to 0 or 1.
    """
    val = {}                     # net -> constant where known
    sym = {}                     # net -> variable name where symbolic
    val.update(x)
    for k in nl.keys:
        sym[k] = k
    factors = []

    def var(net):
        return net if net in nl.keys else tag + net

    for out, op, args in nl.gates:
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

        consts = [b for t, b in av if t == "c"]
        svars = [b for t, b in av if t == "v"]

        # controlling values
        if op == "AND" and 0 in consts:
            val[out] = 0
            continue
        if op == "NAND" and 0 in consts:
            val[out] = 1
            continue
        if op == "OR" and 1 in consts:
            val[out] = 1
            continue
        if op == "NOR" and 1 in consts:
            val[out] = 0
            continue

        if len(svars) == 1:
            # one symbolic input: the gate is a buffer, an inverter, or const
            v = svars[0]
            outs = []
            for probe in (0, 1):
                vals = [b if t == "c" else probe for t, b in av]
                outs.append(OPS[op](vals))
            if outs[0] == outs[1]:
                val[out] = outs[0]
                continue
            if outs == [0, 1]:
                sym[out] = v            # buffer: alias, no factor needed
                continue
            ov = var(out)               # inverter
            sym[out] = ov
            factors.append(((ov, v), {(0, 1): 1, (1, 0): 1,
                                      (0, 0): 0, (1, 1): 0}))
            continue

        ov = var(out)
        sym[out] = ov
        if len(svars) > MAX_FANIN:
            # A wide gate would need a table of size 2^fanin, and would make
            # its inputs a clique in the primal graph.  Associative operators
            # decompose exactly into a chain of two-input gates with fresh
            # auxiliary variables, which is both cheaper to build and NARROWER:
            # a chain has width 2 where the clique has width fanin.  AntiSAT
            # comparator blocks are the case that forces this; they carry AND
            # gates over hundreds of key bits.
            _decompose_wide(factors, ov, op, av, svars, tag)
            continue
        scope = tuple([ov] + svars)
        tab = {}
        for bits in itertools.product((0, 1), repeat=len(svars)):
            sub = dict(zip(svars, bits))
            res = OPS[op]([b if t == "c" else sub[b] for t, b in av])
            tab[(res,) + bits] = 1
        factors.append((scope, tab))

    for o, want in zip(nl.outputs, y):
        if o in val:
            if val[o] != want:
                return None, None
            continue
        v = sym[o]
        factors.append(((v,), {(want,): 1}))

    vs = {z for sc, _ in factors for z in sc}
    vs.update(nl.keys)
    return factors, vs


# --------------------------------------------------- graphs and orders

def primal_graph(factors, varset):
    adj = {v: set() for v in varset}
    for sc, _ in factors:
        for a in sc:
            adj[a].update(z for z in sc if z != a)
    return adj


def _eliminate(adj, v, remaining):
    nb = adj[v] & remaining
    nbl = list(nb)
    for i in range(len(nbl)):
        for j in range(i + 1, len(nbl)):
            adj[nbl[i]].add(nbl[j])
            adj[nbl[j]].add(nbl[i])
    remaining.discard(v)
    return len(nb)


def order_min_fill(adj):
    # DETERMINISM.  Candidates are visited in sorted order, not in set order.
    # Set iteration order over strings depends on hash randomization, which is
    # per process, so scanning `remaining` directly made the tie-break, and
    # therefore the induced width, vary between runs of the same code on the
    # same input.  The counts were never affected; the reported widths were.
    # Scanning a precomputed sorted list keeps the cost the same and makes the
    # choice a function of the graph alone.
    adj = {v: set(s) for v, s in adj.items()}
    allv = sorted(adj)
    remaining = set(adj)
    order, width = [], 0
    while remaining:
        best, bestfill, bestdeg = None, None, None
        for v in allv:
            if v not in remaining:
                continue
            nb = adj[v] & remaining
            nbl = list(nb)
            fill = 0
            for i in range(len(nbl)):
                for j in range(i + 1, len(nbl)):
                    if nbl[j] not in adj[nbl[i]]:
                        fill += 1
            if (bestfill is None or fill < bestfill
                    or (fill == bestfill and len(nb) < bestdeg)):
                best, bestfill, bestdeg = v, fill, len(nb)
        width = max(width, _eliminate(adj, best, remaining))
        order.append(best)
    return order, width


def order_min_degree(adj):
    # Deterministic tie-break; see the note in order_min_fill.
    adj = {v: set(s) for v, s in adj.items()}
    allv = sorted(adj)
    remaining = set(adj)
    order, width = [], 0
    while remaining:
        best = min((v for v in allv if v in remaining),
                   key=lambda v: len(adj[v] & remaining))
        width = max(width, _eliminate(adj, best, remaining))
        order.append(best)
    return order, width


ORDERERS = {"min-fill": order_min_fill, "min-degree": order_min_degree}


def best_order(adj, methods=("min-fill", "min-degree")):
    """Try several heuristics, keep the narrowest."""
    best = None
    for m in methods:
        o, w = ORDERERS[m](adj)
        if best is None or w < best[1]:
            best = (o, w, m)
    return best


def key_moral_width(factors, varset, keys):
    """Induced width over key variables only, internals eliminated first."""
    adj = {v: set(s) for v, s in primal_graph(factors, varset).items()}
    remaining = set(adj)
    for v in [z for z in varset if z not in keys]:
        if v in remaining:
            _eliminate(adj, v, remaining)
    sub = {v: (adj[v] & remaining) for v in remaining}
    if not sub:
        return 0
    return best_order(sub)[1]


# ------------------------------------------------------ bucket elimination

def count_models(factors, varset, order, cap_bits=26):
    """Exact model count over `varset`.  None if a bucket exceeds the cap."""
    pos = {v: i for i, v in enumerate(order)}
    buckets = {v: [] for v in order}
    for sc, tab in factors:
        buckets[min(sc, key=pos.__getitem__)].append((tuple(sc), tab))
    total = 1
    for v in order:
        fl = buckets[v]
        if not fl:
            total *= 2                      # unconstrained variable
            continue
        scope = []
        for sc, _ in fl:
            for z in sc:
                if z not in scope:
                    scope.append(z)
        if len(scope) > cap_bits:
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
                    if not p:
                        break
                s += p
            if s:
                newtab[bits] = s
        if not rest:
            total *= newtab.get((), 0)
            if total == 0:
                return 0
            continue
        if not newtab:
            return 0
        buckets[min(rest, key=pos.__getitem__)].append((tuple(rest), newtab))
    return total


# ------------------------------------------------------------ public API

def version_space(nl, queries, cap_bits=26, want_key_moral=True):
    """Exact |V_t| for a query list [(x, y), ...].

    Returns a dict with the count, both widths, and the order used.  `count`
    is None when the cap was exceeded, which is a datum and not an error.
    """
    factors, varset = [], set()
    for i, (x, y) in enumerate(queries):
        f, vs = residual_factors(nl, x, y, tag="q%d/" % i)
        if f is None:
            return {"count": 0, "factor_width": 0, "key_moral_width": 0,
                    "order": "n/a", "inconsistent": True}
        factors.extend(f)
        varset |= vs
    varset.update(nl.keys)
    adj = primal_graph(factors, varset)
    order, fw, method = best_order(adj)
    kw = key_moral_width(factors, varset, set(nl.keys)) if want_key_moral \
        else None
    cnt = count_models(factors, varset, order, cap_bits=cap_bits)
    return {"count": cnt, "factor_width": fw, "key_moral_width": kw,
            "order": method, "n_factors": len(factors),
            "n_vars": len(varset), "inconsistent": False}


# ------------------------------------------- incremental (per-query) mode

def version_space_incremental(nl, queries, cap_bits=26, want_key_moral=False):
    """Exact |V_t|, processing one query at a time.

    The monolithic path above builds one factor set over all t queries.  Each
    query contributes its own copy of the residual internal nets, so the
    induced width of that set grows with t even when each query on its own is
    narrow.  Measured on locked ISCAS-85, t rather than key length is the
    binding parameter.

    This routine eliminates each query's internal variables as soon as that
    query has been processed, leaving a factor over key variables only.  The
    internal nets of query i never coexist with those of query j.  Cost is
    then governed by two quantities that do not grow with t:

      per-query width   induced width of one query's residual factor graph
      key-factor width  induced width of the accumulated key-only factors

    The result is identical to the monolithic path; only the schedule
    differs.  That equality is checked in validate.py.

    MEASURED OUTCOME, and it is negative.  On locked ISCAS-85 c432 with 32
    key bits this schedule is WORSE than the monolithic one, not better.
    Eliminating a query's internal variables first leaves a factor whose
    scope is every key bit that query touches, which for these instances is
    28 to 32 of the 32 key bits.  That single factor is larger than anything
    the monolithic order ever forms.  Measured per-query widths were 28 for
    random locking and 32 for both interference-maximizing and
    point-function locking, against monolithic widths of 7 to 41 that grow
    with t but start far lower.

    The reason is worth stating because it is a fact about the problem and
    not about this code.  Any schedule that summarizes one query before
    reading the next must carry a message over the key variables that query
    constrains.  The key-moral width is therefore a LOWER BOUND on the
    message size of every query-incremental algorithm, while the factor
    width bounds the monolithic one.  Neither dominates the other, and the
    right schedule interleaves rather than choosing between them.

    Kept in the tree because the bound above is the useful part, and because
    a negative result that has been measured is worth more than an idea that
    has not.
    """
    key_factors = []
    per_query_widths = []
    for i, (x, y) in enumerate(queries):
        f, vs = residual_factors(nl, x, y, tag="q%d/" % i)
        if f is None:
            return {"count": 0, "factor_width": 0, "key_factor_width": 0,
                    "per_query_width": 0, "inconsistent": True}
        internals = [v for v in vs if v not in nl.keys]
        if not internals:
            key_factors.extend(f)
            per_query_widths.append(0)
            continue
        adj = primal_graph(f, vs)
        # eliminate internals first, keys last, so what survives is key-only
        sub = {v: set(adj[v]) for v in vs}
        order_int, w_int = _order_subset(sub, internals)
        per_query_widths.append(w_int)
        residual_key = _eliminate_to(f, vs, order_int, cap_bits=cap_bits)
        if residual_key is None:
            return {"count": None, "factor_width": None,
                    "key_factor_width": None,
                    "per_query_width": max(per_query_widths),
                    "inconsistent": False}
        key_factors.extend(residual_key)

    keyset = set(nl.keys)
    for sc, _ in key_factors:
        keyset.update(sc)
    adj = primal_graph(key_factors, keyset)
    order, kfw, _m = best_order(adj)
    cnt = count_models(key_factors, keyset, order, cap_bits=cap_bits)
    return {"count": cnt, "key_factor_width": kfw,
            "per_query_width": max(per_query_widths) if per_query_widths
            else 0,
            "factor_width": max(kfw, max(per_query_widths)
                                if per_query_widths else 0),
            "inconsistent": False}


def _order_subset(adj, subset):
    """Min-fill order restricted to `subset`, eliminating only those."""
    adj = {v: set(s) for v, s in adj.items()}
    remaining = set(adj)
    todo = set(subset)
    allsub = sorted(subset)            # deterministic tie-break, as above
    order, width = [], 0
    while todo:
        best, bestfill = None, None
        for v in allsub:
            if v not in todo:
                continue
            nb = adj[v] & remaining
            nbl = list(nb)
            fill = 0
            for i in range(len(nbl)):
                for j in range(i + 1, len(nbl)):
                    if nbl[j] not in adj[nbl[i]]:
                        fill += 1
            if bestfill is None or fill < bestfill:
                best, bestfill = v, fill
        width = max(width, _eliminate(adj, best, remaining))
        order.append(best)
        todo.discard(best)
    return order, width


def _eliminate_to(factors, varset, order, cap_bits=26):
    """Sum out the variables in `order`, return the surviving factors."""
    pos = {v: i for i, v in enumerate(order)}
    live = list(factors)
    for v in order:
        touching = [(sc, t) for sc, t in live if v in sc]
        if not touching:
            continue
        live = [(sc, t) for sc, t in live if v not in sc]
        scope = []
        for sc, _ in touching:
            for z in sc:
                if z not in scope:
                    scope.append(z)
        if len(scope) > cap_bits:
            return None
        rest = [z for z in scope if z != v]
        newtab = {}
        for bits in itertools.product((0, 1), repeat=len(rest)):
            asg = dict(zip(rest, bits))
            s = 0
            for vv in (0, 1):
                asg[v] = vv
                p = 1
                for sc, tab in touching:
                    p *= tab.get(tuple(asg[z] for z in sc), 0)
                    if not p:
                        break
                s += p
            if s:
                newtab[bits] = s
        if not rest:
            if newtab.get((), 0) == 0:
                return []
            continue
        if not newtab:
            return []
        live.append((tuple(rest), newtab))
    return live


def width_at_most(adj, limit, time_budget=None):
    """Decide whether a greedy min-degree order keeps the induced width at or
    below `limit`, without computing the width when it does not.

    Returns (width, True) when an order was found with width <= limit, or
    (limit + 1, False) as soon as the bound is exceeded, or (None, False) if
    the time budget ran out.

    The screen only needs the decision.  Computing the exact induced width of
    a wide instance is expensive for no benefit: eliminating a vertex of
    degree d inserts up to d(d-1)/2 fill edges, and on an AntiSAT block with
    278 key bits in one clique that is tens of thousands of set operations per
    step, repeated.  Bailing at the threshold turns a screen that did not
    finish one instance in 400 s into one that answers in seconds.
    """
    import time as _time
    t0 = _time.time()
    adj = {v: set(s) for v, s in adj.items()}
    allv = sorted(adj)                 # deterministic tie-break, as above
    remaining = set(adj)
    width = 0
    while remaining:
        if time_budget is not None and _time.time() - t0 > time_budget:
            return None, False
        best, bestdeg = None, None
        for v in allv:
            if v not in remaining:
                continue
            d = len(adj[v] & remaining)
            if bestdeg is None or d < bestdeg:
                best, bestdeg = v, d
                if d == 0:
                    break
        if bestdeg > limit:
            return limit + 1, False
        width = max(width, bestdeg)
        _eliminate(adj, best, remaining)
    return width, True


def width_at_most_fast(adj, limit, time_budget=None):
    """Same decision as width_at_most, with a lazy heap instead of a scan.

    The scanning version picks the minimum-degree vertex by looking at every
    remaining vertex, which is O(V) per elimination and O(V^2) overall.  On the
    residual systems here V runs to several thousand, and the bail-out does not
    help because min-degree eliminates all the cheap vertices first and only
    then reaches the dense region.  This version keeps a heap of (degree,
    vertex) with stale entries discarded on pop, so the cost is closer to
    O(E log V).

    Checked against width_at_most for identical results in validate.py.
    """
    import heapq
    import time as _time
    t0 = _time.time()
    adj = {v: set(s) for v, s in adj.items()}
    remaining = set(adj)
    heap = [(len(adj[v]), v) for v in adj]
    heapq.heapify(heap)
    width = 0
    while remaining:
        if time_budget is not None and _time.time() - t0 > time_budget:
            return None, False
        best = None
        while heap:
            d, v = heapq.heappop(heap)
            if v not in remaining:
                continue
            dv = len(adj[v] & remaining)
            if dv != d:                       # stale entry, reinsert
                heapq.heappush(heap, (dv, v))
                continue
            best, bestdeg = v, d
            break
        if best is None:
            break
        if bestdeg > limit:
            return limit + 1, False
        width = max(width, bestdeg)
        nb = adj[best] & remaining
        _eliminate(adj, best, remaining)
        for u in nb:                          # degrees changed
            heapq.heappush(heap, (len(adj[u] & remaining), u))
    return width, True
