#!/usr/bin/env python3
# Author: Mitchell A. Thornton
# Copyright (c) 2026 Mitchell A. Thornton
"""
run_e3_v20.py -- the E.3 experiment: random queries against chosen queries.

WHY THIS EXPERIMENT EXISTS

Every entropy number elsewhere in this paper is measured under queries drawn
uniformly at random, and every one of them is reported as an upper bound on
the surviving secret for that reason.  On most instances the bound is close,
because a random input pattern separates surviving keys often enough that the
version space collapses quickly.  On the BDD-based instances it is not close
at all: a hundred random patterns can leave the version space at the full key
space while a distinguishing input exists and a solver finds it in under a
second.

This driver measures both columns on the same instance, so the size of the gap
is a number rather than a caveat.

  random        draw `--random` input patterns uniformly, label them with the
                secret key, and count.

  adversarial   repeatedly ask a solver for an input on which two keys still
                consistent with everything asked so far disagree, use it as
                the next query, and count after each one.  That input is the
                distinguishing input of the SAT attack, so this column is what
                the attack itself extracts.

  certificate   when no such input remains, the version space equals the
                equivalence class of the secret key and the run stops with
                `exhausted` set.  That is a proof of tightness for the
                instance, not a plateau under random querying, and it is
                recorded as such.

WHAT IT DOES NOT CLAIM

The adversarial column is a lower bound on what an attacker who chooses
queries optimally leaves standing, in the same sense that the random column is
an upper bound.  Neither is the true attacker cost.  The gap between them is
the honest statement of how much the random-query instrument understates.

REQUIREMENTS

A SAT solver, either `python-sat` or a DIMACS binary on PATH.  See
satsolve.py.  Everything else is standard library plus the bundle.

USAGE

    python3 run_e3_v20.py --bench-dir /path/to/OBFUSCATION/benchmarks \\
        --only c432-BS320,c432-BE280 --random 100 --adversarial 8 \\
        --out results/e3_v20.json
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
E2 = os.path.join(os.path.dirname(HERE), "E2_published_suite")
sys.path.insert(0, E2)
sys.path.insert(0, HERE)

import engineB                                    # noqa: E402
import netlist as nlmod                           # noqa: E402
import satsolve                                   # noqa: E402

try:
    import cudd_bridge                            # noqa: E402
except Exception:                                 # pragma: no cover
    cudd_bridge = None

SEED = 20260827


def say(m):
    print(m, flush=True)


def log2_or_none(n):
    if n is None:
        return None
    if n <= 0:
        return float("-inf")
    return round(math.log2(n), 6)


def load_instance(bench_dir, name, tmp):
    subprocess.run(["unzip", "-q", "-o",
                    os.path.join(bench_dir, name + ".zip"), "-d", tmp],
                   check=True)
    v = nlmod.find_source(tmp)
    if not v:
        raise RuntimeError("no unsynthesized netlist in %s" % name)
    nl = nlmod.load(v)
    if not nl.keys:
        raise RuntimeError("no key inputs identified in %s" % name)
    return nl


def count(nl, queries, node_cap, timeout):
    if cudd_bridge is not None and cudd_bridge.available():
        r = cudd_bridge.version_space(nl, queries, node_limit=node_cap,
                                      timeout=timeout)
        return r.get("count"), "cudd"
    r = engineB.version_space(nl, queries, node_cap=node_cap)
    return r.get("count"), "bdd-py"


def distinguishing_input(nl, queries, conflict_budget=0):
    """An input on which two keys consistent with `queries` disagree.

    Two copies of the netlist share the primary inputs of the candidate query
    and have independent key vectors.  For every query already asked, both key
    vectors are required to reproduce the observed output.  The objective
    asserts that the two copies differ on the candidate input.  A model gives
    the input; UNSAT means no query separates the survivors, so the version
    space is already the equivalence class.
    """
    pool = satsolve.Pool()
    xvar = {i: pool.id(("x", i)) for i in nl.inputs}
    va, cls = satsolve.tseitin(nl, pool, "a", xvar)
    vb, cb = satsolve.tseitin(nl, pool, "b", xvar)
    cls = cls + cb
    for qi, (qx, qy) in enumerate(queries):
        for tag, _ in (("a", va), ("b", vb)):
            qv = {i: pool.id(("q", qi, tag, i)) for i in nl.inputs}
            vq, cq = satsolve.tseitin(nl, pool, ("q", qi, tag), qv)
            cls += cq
            for i, b in qx.items():
                cls.append([qv[i]] if b else [-qv[i]])
            for k in nl.keys:
                p = pool.id(("k", tag, k))
                q = pool.id(("k", ("q", qi, tag), k))
                cls += [[-p, q], [p, -q]]
            for o, want in zip(nl.outputs, qy):
                cls.append([vq[o]] if want else [-vq[o]])
    diffs = []
    for o in nl.outputs:
        d, dc = satsolve.equal_clauses(pool, va[o], vb[o], o)
        cls += dc
        diffs.append(d)
    cls.append(diffs)
    model = satsolve.solve(len(pool), cls, conflict_budget)
    if model is None:
        return None                      # proved: no distinguishing input
    if model is satsolve.UNKNOWN:
        return satsolve.UNKNOWN          # gave up: says nothing either way
    return {i: int(pool.id(("x", i)) in model) for i in nl.inputs}


def one_instance(name, args, tmp):
    nl = load_instance(args.bench_dir, name, tmp)
    rng = random.Random(args.seed ^ (hash(name) & 0xFFFF))
    secret = {k: rng.randint(0, 1) for k in nl.keys}
    rec = {"benchmark": name, "keys": len(nl.keys), "inputs": len(nl.inputs),
           "outputs": len(nl.outputs), "gates": len(nl.gates),
           "seed": args.seed}
    say("  %-14s %d keys, %d gates" % (name, len(nl.keys), len(nl.gates)))

    # The random column is measured twice.  Once at the same query counts the
    # adversarial column will reach, which is the only comparison where the
    # two are drawing the same number of queries and the difference is the
    # choice of query alone.  And once at the full --random budget, which is
    # what the rest of the paper's campaign does and is the number that says
    # whether random querying gets there eventually.
    t0 = time.time()
    q, matched = [], []
    for j in range(max(args.random, args.adversarial)):
        x = {i: rng.randint(0, 1) for i in nl.inputs}
        q.append((x, nl.simulate({**x, **secret})))
        if j + 1 <= args.adversarial:
            c, eng = count(nl, q, args.node_cap, args.timeout)
            matched.append({"t": j + 1, "count": None if c is None else str(c),
                            "log2": log2_or_none(c)})
        if j + 1 == args.random:
            break
    c, eng = count(nl, q[:args.random], args.node_cap, args.timeout)
    rec["random_matched"] = matched
    rec["random_queries"] = args.random
    rec["random_count"] = None if c is None else str(c)
    rec["random_log2"] = log2_or_none(c)
    rec["random_bits_lost"] = (None if c is None
                               else round(len(nl.keys) - math.log2(c), 6))
    rec["random_seconds"] = round(time.time() - t0, 2)
    rec["engine"] = eng
    say("    %4d random      log2|V| = %s" % (args.random, rec["random_log2"]))

    traj, q, exhausted, gave_up = [], [], False, False
    t0 = time.time()
    for _ in range(args.adversarial):
        if args.wall and time.time() - t0 > args.wall:
            break
        ts = time.time()
        x = distinguishing_input(nl, q, args.solver_conflicts)
        if x is satsolve.UNKNOWN:
            # The solver ran out of budget.  That is not a certificate and it
            # must not be recorded as one; the run simply stops here.
            gave_up = True
            break
        if x is None:
            exhausted = True
            break
        sat_s = time.time() - ts
        q.append((x, nl.simulate({**x, **secret})))
        c, _e = count(nl, q, args.node_cap, args.timeout)
        traj.append({"t": len(q), "count": None if c is None else str(c),
                     "log2": log2_or_none(c), "sat_seconds": round(sat_s, 3)})
        say("    %4d adversarial log2|V| = %s   (solver %.2fs)"
            % (len(q), traj[-1]["log2"], sat_s))
        # Deliberately no early exit at |V| = 1.  The run stops when the
        # solver reports that no distinguishing input remains, because that
        # UNSAT is the certificate.  Stopping one query early on a count
        # would give the same trajectory and no proof.
    rec["adversarial"] = traj
    rec["adversarial_exhausted"] = exhausted
    rec["adversarial_solver_gave_up"] = gave_up
    rec["adversarial_seconds"] = round(time.time() - t0, 2)
    if traj and traj[-1]["log2"] is not None:
        rec["adversarial_bits_lost"] = round(len(nl.keys) - traj[-1]["log2"], 6)
    if exhausted:
        say("    no distinguishing input remains: the version space is the "
            "equivalence class of the secret")
    if gave_up:
        say("    the solver ran out of its conflict budget, which settles "
            "nothing; this instance carries no certificate")
    return rec


def main():
    ap = argparse.ArgumentParser(
        description="Random queries against solver-chosen queries.")
    ap.add_argument("--bench-dir", required=True)
    ap.add_argument("--only", help="comma-separated instance names")
    ap.add_argument("--only-file")
    ap.add_argument("--out", required=True)
    ap.add_argument("--random", type=int, default=100)
    ap.add_argument("--adversarial", type=int, default=8)
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--node-cap", type=int, default=8_000_000)
    ap.add_argument("--timeout", type=float, default=600.0)
    ap.add_argument("--solver-conflicts", type=int, default=0,
                    help="conflict budget per solver call; 0 means none.  A "
                         "call that exhausts its budget stops the adversarial "
                         "column for that instance and records no "
                         "certificate, because giving up is not a proof.")
    ap.add_argument("--wall", type=float, default=0,
                    help="seconds to spend on the adversarial column per "
                         "instance; 0 means no limit")
    args = ap.parse_args()

    kind, path = satsolve.have_solver()
    if kind is None:
        sys.exit("no SAT solver found.  pip install python-sat, or "
                 "apt/brew install minisat.")
    say("solver: %s" % (path if kind == "binary" else "python-sat"))

    names = []
    if args.only:
        names += [x.strip() for x in args.only.split(",") if x.strip()]
    if args.only_file:
        txt = open(args.only_file).read()
        names += [x.strip() for x in txt.replace(",", "\n").split("\n")
                  if x.strip()]
    if not names:
        sys.exit("give --only or --only-file")

    rows = []
    if os.path.exists(args.out):
        try:
            rows = json.load(open(args.out)).get("rows", [])
        except Exception:
            rows = []
    done = {r["benchmark"] for r in rows}
    say("%d instance(s), %d already recorded" % (len(names), len(done)))

    for n in names:
        if n in done:
            continue
        tmp = tempfile.mkdtemp(prefix="e3_")
        try:
            rows.append(one_instance(n, args, tmp))
        except Exception as e:
            say("  %-14s FAILED: %s: %s" % (n, type(e).__name__, e))
            rows.append({"benchmark": n,
                         "error": "%s: %s" % (type(e).__name__, e)})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        doc = {"experiment": "E3 random against adversarial queries",
               "driver": "run_e3_v20.py", "seed": args.seed,
               "random_queries": args.random,
               "adversarial_queries": args.adversarial,
               "solver": path if kind == "binary" else "python-sat",
               "rows": sorted(rows, key=lambda r: r["benchmark"])}
        tmpf = args.out + ".tmp"
        json.dump(doc, open(tmpf, "w"), indent=1)
        os.replace(tmpf, args.out)
    say("wrote %s (%d rows)" % (args.out, len(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
