#!/usr/bin/env python3
# Author: Mitchell A. Thornton
# Copyright (c) 2026 Mitchell A. Thornton
"""
screen.py -- reachability screen over the TrustHub obfuscation suite.

The screen answers, cheaply and before any counting is attempted, whether an
instance is within reach.  It reports two answers, because the two engines are
reachable in different places and a single answer would be wrong for one of
them.

ENGINE A: residual width.  Constant-propagate each oracle query, build the
residual factor graph over the key bits and surviving internal nets, and decide
whether a greedy elimination order keeps the induced width at or below a
threshold.  Engine A's cost is governed by that width, so the width alone
classifies the instance.  Computing it takes seconds; computing the count can
take hours.

ENGINE B: diagram size.  Build the accumulated decision diagram over the key
bits for the same queries and report its size, or that it exceeded the node
budget.  Engine B's cost is governed by the size of the version space it
represents, which is a different quantity entirely, and it is not bounded by
the width.

Why both.  An earlier version of this screen reported only the width, and the
campaign then produced results for instances the screen had called
unreachable, because the campaign ran on Engine B.  A screen that classifies
by one engine's cost model cannot speak for the other.  Reporting both is what
makes the screen usable as a campaign plan rather than as a claim about the
method.

Usage:

    python3 screen.py --bench-dir /path/to/OBFUSCATION/benchmarks \\
                      --index /path/to/index.json \\
                      --out results/screen.json \\
                      [--only c1355,c432] [--methods RN,SL,NR] [--limit 40]

The suite ships each benchmark as a zip holding an unsynthesized `.v` and a
`synt_*.v`.  The screen uses the UNSYNTHESIZED file: it is a flat module over
standard Verilog primitives and parses directly, whereas the synthesized form
is mapped to SAED90nm cells (OA21X1, AO22X1, MUX21X1 and so on) that would
need a cell library first.  Gate and port counts from the unsynthesized file
match the published ReadMe values.

Results are written one record per benchmark, and the run is resumable: an
instance already present in the output file is skipped.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time

import cudd_bridge
import engineA
import engineB
import netlist as nlmod


# The screen exists to be cheap.  Its Engine B budget is deliberately smaller
# than the campaign's: the question here is "is this instance worth spending a
# campaign slot on", not "what is the count".
args_b_time = [45.0]
args_b_cap = [8_000_000]


# The release marks synthesized netlists two different ways: 252 files use a
# `synt_NAME.v` prefix and 43 use a `NAME_synt.v` suffix.  Checking only the
# prefix, and taking whatever os.walk yields first, made the choice of netlist
# depend on filesystem enumeration order, which differs between machines.  A
# wrong pick is loud rather than silent, because the synthesized files are
# mapped to standard cells with named port binding and no reader here accepts
# them, but "loud on some machines and not others" is not good enough.
# The one definition lives in netlist.py so the three readers cannot drift
# apart again.
_is_synthesized = nlmod.is_synthesized


def unpack(zip_path, dest):
    """Unpack one benchmark archive and return its unsynthesized netlist.

    Deterministic: the walk is sorted, and both synthesized-file naming
    conventions are recognised.
    """
    subprocess.run(["unzip", "-q", "-o", zip_path, "-d", dest], check=True)
    found = []
    for root, dirs, files in os.walk(dest):
        dirs.sort()
        for f in sorted(files):
            if f.endswith(".v") and not _is_synthesized(f):
                found.append(os.path.join(root, f))
    return found[0] if found else None


def diagram_reach(nl, queries, node_cap=8_000_000, timeout=45.0):
    """Does Engine B reach this instance at these queries, and at what size?

    Returns (acc_nodes, note).  `acc_nodes` is the size of the accumulated
    constraint, which is the version space itself; None means the engine gave
    up and the note says why.
    """
    if cudd_bridge.available():
        r = cudd_bridge.version_space(nl, queries, node_limit=node_cap,
                                      timeout=timeout)
    else:
        r = engineB.version_space(nl, queries, node_cap=node_cap)
    if r.get("count") is None:
        return None, (r.get("note") or "engine gave up")
    return r.get("acc_nodes"), None


def residual_width(nl, queries, limit=25, budget=60.0):
    """Is the residual system within reach?  Decision, not exact width.

    Returns (width_or_bound, n_vars, note).  `width` is exact when it is at or
    below `limit`; above it the value is `limit + 1` and the note says so.
    """
    factors, varset = [], set()
    for i, (x, y) in enumerate(queries):
        f, vs = engineA.residual_factors(nl, x, y, tag="q%d/" % i)
        if f is None:
            return None, None, "inconsistent"
        factors.extend(f)
        varset |= vs
    varset.update(nl.keys)
    adj = engineA.primal_graph(factors, varset)
    w, ok = engineA.width_at_most_fast(adj, limit, time_budget=budget)
    if w is None:
        return None, len(varset), "ordering timed out"
    return w, len(varset), None if ok else "width exceeds %d" % limit


def screen_one(path, tmax, rng, limit=25, budget=60.0, meta=None,
               want_b=True):
    t0 = time.time()
    nl = nlmod.load(path)
    st = nl.stats()
    rec = {"file": os.path.basename(path), "stats": st}
    if meta:
        rec["meta"] = meta
        want = meta.get("key")
        if want and str(want).isdigit() and int(want) != st["keys"]:
            rec["key_count_mismatch"] = {"declared": int(want),
                                         "parsed": st["keys"]}
    if not nl.keys:
        rec["error"] = "no key inputs identified"
        return rec
    secret = {k: rng.randint(0, 1) for k in nl.keys}
    queries, widths = [], []
    for t in range(1, tmax + 1):
        x = {i: rng.randint(0, 1) for i in nl.inputs}
        a = dict(x)
        a.update(secret)
        queries.append((x, nl.simulate(a)))
        w, nv, err = residual_width(nl, queries, limit=limit, budget=budget)
        row = {"t": t, "width": w, "resid_vars": nv,
               "over_limit": bool(err and "exceeds" in err)}
        if want_b:
            row["B_nodes"], row["B_note"] = diagram_reach(
                nl, queries, node_cap=args_b_cap[0], timeout=args_b_time[0])
            row["B_reached"] = row["B_nodes"] is not None
        widths.append(row)
        if err:
            rec["note"] = err
            if not want_b:
                break
            # Engine A being out of reach says nothing about Engine B, so the
            # sweep continues when both are being measured.
            for tt in range(t + 1, tmax + 1):
                x = {i: rng.randint(0, 1) for i in nl.inputs}
                a = dict(x)
                a.update(secret)
                queries.append((x, nl.simulate(a)))
                bn, bnote = diagram_reach(nl, queries,
                                          node_cap=args_b_cap[0],
                                          timeout=args_b_time[0])
                widths.append({"t": tt, "width": None, "resid_vars": None,
                               "over_limit": True, "B_nodes": bn,
                               "B_note": bnote, "B_reached": bn is not None})
            break
    rec["widths"] = widths
    rec["seconds"] = round(time.time() - t0, 2)
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bench-dir", required=True)
    ap.add_argument("--index")
    ap.add_argument("--out", default="results/screen.json")
    ap.add_argument("--tmax", type=int, default=3)
    ap.add_argument("--only", help="comma-separated circuit prefixes or full "
                                  "instance names")
    ap.add_argument("--methods", help="comma-separated codes, e.g. RN,SL,NR")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--seed", type=int, default=20260827)
    ap.add_argument("--limit-width", type=int, default=25,
                    help="feasibility threshold; the screen decides whether "
                         "the width is at or below this, and does not compute "
                         "it exactly above")
    ap.add_argument("--budget", type=float, default=60.0,
                    help="seconds per ordering attempt")
    ap.add_argument("--b-timeout", type=float, default=45.0,
                    help="seconds allowed to Engine B per query count in the "
                         "screen; smaller than the campaign's on purpose")
    ap.add_argument("--b-node-cap", type=int, default=8_000_000)
    ap.add_argument("--no-engine-b", action="store_true",
                    help="report only the Engine A width, as the original "
                         "screen did; the campaign runs on Engine B, so this "
                         "gives an incomplete picture of reachability")
    args = ap.parse_args()

    args_b_time[0] = args.b_timeout
    args_b_cap[0] = args.b_node_cap

    rng = random.Random(args.seed)

    index = {}
    if args.index and os.path.exists(args.index):
        for r in json.load(open(args.index)):
            index[r["name"]] = r

    done = {}
    if os.path.exists(args.out):
        done = {r["benchmark"]: r for r in json.load(open(args.out))}
        print("resuming: %d already done" % len(done))

    zips = sorted(f for f in os.listdir(args.bench_dir) if f.endswith(".zip"))
    if args.only:
        # Accepts either a circuit prefix (c432) or a full instance name
        # (c432-BE280).  It used to accept only the prefix, which made
        # screening one instance impossible without also screening its
        # whole family.
        want = tuple(x.strip() for x in args.only.split(",") if x.strip())
        zips = [z for z in zips
                if z.split("-")[0] in want or z[:-4] in want]
    if args.methods:
        codes = tuple(args.methods.split(","))
        zips = [z for z in zips
                if re.match(r"^c?\d*-?([A-Za-z_]+)", z.split(".")[0])
                and re.match(r"^c?\d*-?([A-Za-z_]+)",
                             z.split(".")[0]).group(1) in codes]
    if args.limit:
        zips = zips[:args.limit]

    print("screening %d benchmarks, t=1..%d, threshold=%d"
          % (len(zips), args.tmax, args.limit_width))
    print("  %-22s %6s %6s %7s %9s  %s"
          % ("benchmark", "gates", "keys", "A width", "B nodes", "note"))
    out = list(done.values())
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    for z in zips:
        name = z[:-4]
        if name in done:
            continue
        tmp = tempfile.mkdtemp(prefix="obf_")
        try:
            v = unpack(os.path.join(args.bench_dir, z), tmp)
            if not v:
                rec = {"benchmark": name, "error": "no unsynthesized .v"}
            else:
                rec = screen_one(v, args.tmax, rng, limit=args.limit_width,
                                 budget=args.budget, meta=index.get(name),
                                 want_b=not args.no_engine_b)
                rec["benchmark"] = name
        except Exception as e:
            rec = {"benchmark": name,
                   "error": "%s: %s" % (type(e).__name__, str(e)[:160])}
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        out.append(rec)
        w = rec.get("widths")
        st = rec.get("stats", {})
        bcell = "-"
        if w:
            reached = [r for r in w if r.get("B_reached")]
            if reached:
                bcell = str(reached[-1]["B_nodes"])
            elif any("B_reached" in r for r in w):
                bcell = "over"
        print("  %-22s %6s %6s %7s %9s  %s"
              % (name, st.get("gates", "-"), st.get("keys", "-"),
                 ((">%d" % args.limit_width) if (w and w[-1].get("over_limit"))
                  else (w[-1]["width"] if w else "-")),
                 bcell,
                 (rec.get("error") or rec.get("note") or "")[:38]))
        sys.stdout.flush()
        with open(args.out, "w") as fh:
            json.dump(out, fh, indent=1)
    print("\nwrote %s (%d records)" % (args.out, len(out)))


if __name__ == "__main__":
    main()
