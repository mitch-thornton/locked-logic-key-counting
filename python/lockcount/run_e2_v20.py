#!/usr/bin/env python3
# Author: Mitchell A. Thornton
# Copyright (c) 2026 Mitchell A. Thornton
"""
run_e2_v20.py -- the E.2 campaign driver.

Produces every number in Section VIII of the paper that comes from published
benchmarks: the surviving-entropy table, the two figures, the engine
comparison, and the per-query information measurement.

Four phases, selected with --phase:

  entropy   Draw random queries against a locked benchmark until |V_t| is
            unchanged for `--plateau` consecutive queries or the budget runs
            out, and record log2|V_t| after each one.  This is the headline
            measurement and it produces the trajectories.

  engines   Run Engine A and Engine B on the same instance at the same query
            counts and record width, diagram size and wall time for each.
            The engines share no representation, so a disagreement halts the
            run rather than being averaged away.

  worth     Measure what one random query is worth.  For a sample of random
            input patterns, report the fraction of a random key sample that
            the query eliminates.  A scheme whose queries are usually
            worthless is invisible to iteration counting, which is the point.

  all       entropy, then engines, then worth.

Engine selection.  --engine cudd uses the C engine when it is built and falls
back to the Python diagram engine otherwise, saying so in the record.  The
counts are identical either way; only the speed differs.

Reported entropies are UPPER BOUNDS on the surviving secret.  Queries are
drawn uniformly at random, an attacker choosing them arrives sooner, and a
plateau under random queries is not a proof that no further query separates
the survivors.  See the paper.

Usage:

    python3 run_e2_v20.py --phase all \\
        --bench-dir /path/to/OBFUSCATION/benchmarks \\
        --only c880-RN640,c880-RN320 \\
        --out results/phase6.json

Verilog is read by the built-in reader in verilog.py; nothing external is
needed.  The benchmark archives are third-party and are not vendored; see
DATA.md for where to obtain them.
`results/benchmark_manifest.json` carries the SHA-256 of every archive used,
so a reader can confirm they have the same files before reproducing.
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

import cudd_bridge
import engineA
import engineB
import netlist as nlmod

SEED = 20260827


# ------------------------------------------------------------ benchmark io

def unpack(zip_path, dest):
    """Return the unsynthesized .v inside the archive.

    The suite ships each benchmark as a zip holding an unsynthesized `.v` and
    a `synt_*.v`.  The unsynthesized file is flat Verilog over primitives and
    parses directly; the synthesized form is mapped to SAED90nm cells and
    would need a cell library first.
    """
    subprocess.run(["unzip", "-q", "-o", zip_path, "-d", dest], check=True)
    return nlmod.find_source(dest)


def load_instance(bench_dir, name, tmp):
    v = unpack(os.path.join(bench_dir, name + ".zip"), tmp)
    if not v:
        raise RuntimeError("no unsynthesized .v in %s" % name)
    nl = nlmod.load(v)
    if not nl.keys:
        raise RuntimeError("no key inputs identified in %s" % name)
    return nl


def draw(nl, secret, rng):
    x = {i: rng.randint(0, 1) for i in nl.inputs}
    a = dict(x)
    a.update(secret)
    return (x, nl.simulate(a))


# --------------------------------------------------------------- counting

def count(nl, queries, engine, node_cap, timeout):
    """|V_t| by the requested engine.  Returns (count, extra_dict)."""
    if engine == "cudd" and cudd_bridge.available():
        r = cudd_bridge.version_space(nl, queries, node_limit=node_cap,
                                      timeout=timeout)
        return r.get("count"), {"engine": "cudd",
                                "acc_nodes": r.get("acc_nodes"),
                                "note": r.get("note")}
    r = engineB.version_space(nl, queries, node_cap=node_cap)
    return r.get("count"), {"engine": "bdd-py",
                            "acc_nodes": r.get("acc_nodes"),
                            "note": r.get("note")}


def log2_or_none(n):
    if n is None:
        return None
    if n <= 0:
        return float("-inf")
    return round(math.log2(n), 3)


# ---------------------------------------------------------------- phases

def phase_entropy(nl, name, rng, args):
    """Random queries to plateau; log2|V_t| after each.

    Queries are drawn uniformly at random and their selection does not depend
    on any count, so drawing a batch and truncating at the plateau gives the
    identical sequence that drawing one at a time would give.  That matters
    because the C engine reports the count after every query in a single pass,
    while counting after each query by re-invoking on a growing list is
    quadratic in the query count.  The batch is extended until a plateau
    appears or a budget is reached.
    """
    t0 = time.time()
    secret = {k: rng.randint(0, 1) for k in nl.keys}
    k = len(nl.keys)
    queries = []
    counts = []
    note = None
    extra = {"engine": None, "acc_nodes": None}

    use_cudd = args.engine == "cudd" and cudd_bridge.available()

    stop_reason = None
    while len(counts) < args.tmax:
        if time.time() - t0 > args.wall:
            stop_reason = "wall clock"
            note = ("wall-clock limit of %g s reached before a plateau"
                    % args.wall)
            break
        if use_cudd:
            want = min(args.tmax, max(args.chunk, len(counts) + args.chunk))
            while len(queries) < want:
                queries.append(draw(nl, secret, rng))
            r = cudd_bridge.trajectory(nl, queries, node_limit=args.node_cap,
                                       timeout=args.timeout)
            got = [row["count"] for row in r.get("traj", [])]
            # A pass that gave up partway still counted every query it
            # finished.  Keep the longer prefix rather than discarding work.
            if len(got) >= len(counts):
                counts = got
                extra = {"engine": "cudd",
                         "acc_nodes": (r["traj"][-1]["acc_nodes"]
                                       if r.get("traj") else None)}
            if not counts:
                note = r.get("note") or "engine gave up"
                break
            if len(got) < len(queries):
                note = r.get("note") or "engine gave up mid-trajectory"
        else:
            queries.append(draw(nl, secret, rng))
            c, extra = count(nl, queries, args.engine, args.node_cap,
                             args.timeout)
            if c is None:
                note = extra.get("note") or "engine gave up"
                break
            counts.append(c)

        cut = _plateau_at(counts, args.plateau)
        if cut is not None:
            counts = counts[:cut]
            stop_reason = "plateau"
            break
        if note:
            stop_reason = "engine gave up"
            break
        if time.time() - t0 > args.wall:
            stop_reason = "wall clock"
            note = ("wall-clock limit of %g s reached before a plateau"
                    % args.wall)
            break

    if stop_reason is None and len(counts) >= args.tmax:
        stop_reason = "query budget"
        note = ("query budget of %d reached without a plateau; the count was "
                "still falling, so this figure is a weaker upper bound than "
                "the plateaued rows" % args.tmax)

    traj = [log2_or_none(c) for c in counts]
    rec = {"benchmark": name, "k": k, "queries": len(traj),
           "node_cap": args.node_cap, "plateau_rule": args.plateau,
           "stop_reason": stop_reason,
           "wall_limit": args.wall,
           "query_limit": None if getattr(args, "unlimited", False)
           else args.tmax,
           "plateaued": stop_reason == "plateau",
           "log2_V": traj[-1] if traj else None,
           "bits_lost": (round(k - traj[-1], 2) if traj else None),
           "engine": extra.get("engine"), "acc_nodes": extra.get("acc_nodes"),
           "traj": traj, "seconds": round(time.time() - t0, 1)}
    if note:
        rec["note"] = note
    return rec


def _plateau_at(counts, plateau):
    """Index one past the first run of `plateau` consecutive equal counts.

    Returns None when no such run has appeared yet.  This is the same
    stopping rule applied whether the counts arrived one at a time or in a
    batch, which is what makes the two equivalent.
    """
    run = 0
    for i in range(1, len(counts)):
        run = run + 1 if counts[i] == counts[i - 1] else 0
        if run >= plateau:
            return i + 1
    return None


def _engineA_worker(conn, nl, queries, cap_bits):
    try:
        conn.send(engineA.version_space(nl, queries, cap_bits=cap_bits,
                                        want_key_moral=False))
    except Exception as e:
        conn.send({"count": None, "note": "%s: %s" % (type(e).__name__, e)})
    finally:
        conn.close()


def engineA_bounded(nl, queries, cap_bits, seconds):
    """Engine A with a wall-clock guard.

    Engine A has a state cap but no time bound: a hard elimination order can
    keep it working long past the point where the answer would be useful.
    Running it in a child process and killing the child is the only honest way
    to report "did not finish in N seconds" instead of hanging the campaign.
    """
    import multiprocessing as mp
    parent, child = mp.Pipe(False)
    p = mp.Process(target=_engineA_worker, args=(child, nl, queries, cap_bits))
    p.start()
    child.close()
    got = parent.poll(seconds)
    r = parent.recv() if got else None
    p.join(1)
    if p.is_alive():
        p.terminate()
        p.join()
    if r is None:
        return {"count": None, "factor_width": None,
                "note": "did not finish within %g s" % seconds}
    return r


def phase_engines(nl, name, rng, args):
    """Engine A against Engine B at matched query counts."""
    secret = {k: rng.randint(0, 1) for k in nl.keys}
    queries, rows = [], []
    for t in range(1, args.engine_tmax + 1):
        queries.append(draw(nl, secret, rng))

        ta = time.time()
        ra = engineA_bounded(nl, queries, args.cap_bits, args.engine_budget)
        ta = time.time() - ta

        tb = time.time()
        cb, extra = count(nl, queries, args.engine, args.node_cap,
                          args.timeout)
        tb = time.time() - tb

        ca = ra.get("count")
        if ca is not None and cb is not None and ca != cb:
            raise SystemExit("ENGINE DISAGREEMENT on %s at t=%d: A=%d B=%d"
                             % (name, t, ca, cb))
        rows.append({"benchmark": name, "t": t,
                     "A_width": ra.get("factor_width"),
                     "A_seconds": round(ta, 1),
                     "A_note": ra.get("note"),
                     "B_acc_nodes": extra.get("acc_nodes"),
                     "B_seconds": round(tb, 1),
                     "count": str(ca if ca is not None else cb)})
        print("    t=%-3d A width %-6s %7.1fs   B nodes %-6s %7.1fs  %s"
              % (t, ra.get("factor_width"), ta, extra.get("acc_nodes"), tb,
                 ra.get("note") or ""))
        sys.stdout.flush()
    return rows


def phase_worth(nl, name, rng, args):
    """What one random query is worth.

    For each of `--worth-patterns` random input patterns, the fraction of a
    random key sample that the query eliminates.  Sampling rather than exact
    counting, because the question is about the query and not about |V|.
    """
    secret = {k: rng.randint(0, 1) for k in nl.keys}
    sample = [{k: rng.randint(0, 1) for k in nl.keys}
              for _ in range(args.worth_keys)]
    fracs = []
    zero = 0
    for _p in range(args.worth_patterns):
        x, y = draw(nl, secret, rng)
        killed = 0
        for cand in sample:
            a = dict(x)
            a.update(cand)
            if nl.simulate(a) != y:
                killed += 1
        f = killed / float(len(sample))
        fracs.append(round(f, 4))
        if killed == 0:
            zero += 1
    fracs_sorted = sorted(fracs)
    med = fracs_sorted[len(fracs_sorted) // 2]
    return {"benchmark": name, "patterns": args.worth_patterns,
            "key_sample": args.worth_keys, "median_eliminated": med,
            "patterns_eliminating_none": zero, "fractions": fracs}


# ------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", default="entropy",
                    choices=["entropy", "engines", "worth", "all"])
    ap.add_argument("--bench-dir", required=True)
    ap.add_argument("--only", required=True,
                    help="comma-separated instance names, e.g. c880-RN640")
    ap.add_argument("--out", default="results/phase6.json")
    ap.add_argument("--engine", default="cudd", choices=["cudd", "bdd-py"])
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--tmax", type=int, default=120,
                    help="query budget per instance.  0 means no query limit: "
                         "run until the plateau rule fires or a clock stops "
                         "it, which is what --until-plateau selects.")
    ap.add_argument("--until-plateau", action="store_true",
                    help="run each instance until its count plateaus, bounded "
                         "only by --wall and --deadline.  Equivalent to "
                         "--tmax 0.  Proposition 3 guarantees a plateau "
                         "exists, so the only question is whether the clock "
                         "reaches it.")
    ap.add_argument("--plateau", type=int, default=8,
                    help="stop after this many consecutive unchanged counts")
    ap.add_argument("--node-cap", type=int, default=8_000_000)
    ap.add_argument("--chunk", type=int, default=24,
                    help="queries per trajectory pass; only "
                         "affects how often the pass restarts")
    ap.add_argument("--timeout", type=float, default=600.0)
    ap.add_argument("--wall", type=float, default=3600.0,
                    help="seconds per instance before giving up")
    ap.add_argument("--deadline", type=float, default=0.0,
                    help="total seconds for the whole run; when it passes, the "
                         "campaign stops cleanly between instances rather than "
                         "being killed mid-write.  0 disables.")
    ap.add_argument("--engine-tmax", type=int, default=4)
    ap.add_argument("--engine-budget", type=float, default=300.0,
                    help="wall-clock seconds allowed to Engine A per query "
                         "count; exceeding it is reported, not hidden")
    ap.add_argument("--cap-bits", type=int, default=30,
                    help="Engine A gives up above 2^cap_bits table entries; "
                         "that is a datum, not an error")
    ap.add_argument("--worth-patterns", type=int, default=20)
    ap.add_argument("--worth-keys", type=int, default=2000)
    args = ap.parse_args()
    if args.until_plateau:
        args.tmax = 0
    # 0 means unlimited; internally that is a very large ceiling so the loop
    # conditions stay simple, and the wall clock is what actually stops it
    tmax = args.tmax if args.tmax > 0 else 10 ** 9
    args.tmax = tmax
    args.unlimited = (args.tmax >= 10 ** 9)

    if args.engine == "cudd" and not cudd_bridge.available():
        print("note: CUDD engine not built at %s; using the Python diagram "
              "engine.  Counts are identical, speed is not."
              % cudd_bridge.BINARY)

    phases = (["entropy", "engines", "worth"] if args.phase == "all"
              else [args.phase])
    names = args.only.split(",")

    done = {}
    if os.path.exists(args.out):
        prev = json.load(open(args.out))
        done = {r["benchmark"]: r for r in prev.get("rows", [])}
        print("resuming: %d instances already done" % len(done))

    rows, engine_rows, worth_rows = list(done.values()), [], []
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)

    t_start = time.time()
    for name in names:
        if args.deadline and time.time() - t_start > args.deadline:
            print("deadline of %g s reached; %d instances not attempted"
                  % (args.deadline, len(names) - names.index(name)))
            break
        rng = random.Random(args.seed)
        tmp = tempfile.mkdtemp(prefix="obf_")
        try:
            nl = load_instance(args.bench_dir, name, tmp)
            st = nl.stats()
            print("%s: %d gates, %d inputs, %d keys, %d outputs"
                  % (name, st["gates"], st["inputs"], st["keys"],
                     st["outputs"]))
            sys.stdout.flush()
            for ph in phases:
                if ph == "entropy":
                    if name in done:
                        print("  entropy: already done, skipping")
                        continue
                    r = phase_entropy(nl, name, rng, args)
                    rows.append(r)
                    print("  entropy: k=%d, %d queries, log2|V|=%s, lost %s "
                          "[%s]"
                          % (r["k"], r["queries"], r["log2_V"],
                             r["bits_lost"], r["stop_reason"]))
                elif ph == "engines":
                    engine_rows.extend(phase_engines(nl, name, rng, args))
                else:
                    r = phase_worth(nl, name, rng, args)
                    worth_rows.append(r)
                    print("  worth: median %.3f eliminated, %d of %d patterns "
                          "eliminate none"
                          % (r["median_eliminated"],
                             r["patterns_eliminating_none"], r["patterns"]))
                sys.stdout.flush()
                # incremental write: a run killed by a scheduler keeps what it
                # has, which is how the k=64 campaign was lost once already
                _write(args, rows, engine_rows, worth_rows)
        except Exception as e:
            print("  ERROR %s: %s" % (type(e).__name__, str(e)[:200]))
            rows.append({"benchmark": name,
                         "error": "%s: %s" % (type(e).__name__, str(e)[:200])})
            _write(args, rows, engine_rows, worth_rows)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    _write(args, rows, engine_rows, worth_rows)
    print("\nwrote %s (%d entropy rows)" % (args.out, len(rows)))


def _write(args, rows, engine_rows, worth_rows):
    out = {"note": "Reported log2|V_T| are UPPER BOUNDS on the surviving "
                   "secret: queries are uniformly random, and a plateau under "
                   "random queries is not a proof that no further query "
                   "separates the survivors.",
           "seed": args.seed, "rows": rows}
    if engine_rows:
        out["engines"] = engine_rows
    if worth_rows:
        out["worth"] = worth_rows
    with open(args.out, "w") as fh:
        json.dump(out, fh, indent=1)


if __name__ == "__main__":
    main()
