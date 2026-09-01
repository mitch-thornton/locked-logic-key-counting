#!/usr/bin/env python3
# Author: Mitchell A. Thornton
# Copyright (c) 2026 Mitchell A. Thornton
"""
count.py -- compute |V_t| for one locked netlist with one named engine.

The campaign drivers choose an engine for you and manage budgets.  This is the
one-instance, one-engine tool: it exists so that each engine can be exercised
on its own, so that a new installation can be checked engine by engine, and so
that the engines can be timed against each other on a single instance without
running a campaign.

Engines:

  A         variable elimination over the residual factor graph.  Pays the
            induced width of the residual system.  Exact.
  B-py      decision diagram over the key bits, self-contained Python.  Pays
            the size of the diagram, which is the version space itself.  Exact.
  B-cudd    the same engine in C on CUDD, counting with
            Cudd_ApaCountMinterm so the count stays exact past 2^53.
            Requires c/engineB_cudd to have been built.
  brute     exhaustive enumeration over all 2^k keys.  Ground truth, and
            refuses above 24 key bits because it would not finish.
  all       every engine that can run on this instance, with a comparison.
            Any disagreement is reported as a failure, because it would be one.

Input may be a locked benchmark zip from the Trust-Hub obfuscation release, a
directory holding one, or a netlist file (.bench, .isc, .v) directly.  All
three formats are read by the built-in readers; Verilog uses verilog.py and
needs nothing external installed.  The Trust-Hub archives are third-party and
are not vendored; see DATA.md for where to obtain them.

Examples:

  python3 count.py --bench /path/to/benchmarks/c880-RN640.zip \\
      --engine all -t 5

  python3 count.py --bench c1908-SL320.zip --bench-dir /path/to/benchmarks \\
      --engine B-cudd -t 20
"""
from __future__ import annotations

import argparse
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
ENGINES = ("A", "B-py", "B-cudd", "brute", "all")


def resolve(path, bench_dir):
    """Accept a zip, a directory, or a netlist file, and return a netlist path.

    Returns (netlist_path, tempdir_to_clean_or_None).
    """
    if bench_dir and not os.path.exists(path):
        path = os.path.join(bench_dir, path)
    if not os.path.exists(path):
        # Say which of the two things is wrong.  A --bench-dir that does not
        # exist, or that holds no archives, produces the same "no such
        # benchmark" line as a typo in the instance name, and the two have
        # very different fixes.  The placeholder path from the install notes
        # lands here.
        if bench_dir and not os.path.isdir(bench_dir):
            raise SystemExit(
                "--bench-dir does not exist: %s\n"
                "  This is where the Trust-Hub archives live, one zip per\n"
                "  instance.  Find it with:\n"
                "    find ~ -maxdepth 6 -name 'c432-RN320.zip' 2>/dev/null"
                % bench_dir)
        if bench_dir and not any(f.endswith(".zip")
                                 for f in os.listdir(bench_dir)):
            raise SystemExit(
                "--bench-dir holds no .zip archives: %s\n"
                "  Expected one zip per benchmark instance." % bench_dir)
        raise SystemExit("no such benchmark: %s" % path)

    if path.endswith(".zip"):
        tmp = tempfile.mkdtemp(prefix="count_")
        subprocess.run(["unzip", "-q", "-o", path, "-d", tmp], check=True)
        path, hold = _find_netlist(tmp), tmp
        if not path:
            shutil.rmtree(hold, ignore_errors=True)
            raise SystemExit("no unsynthesized netlist inside that archive")
        return path, hold
    if os.path.isdir(path):
        found = _find_netlist(path)
        if not found:
            raise SystemExit("no unsynthesized netlist under %s" % path)
        return found, None
    return path, None


def _find_netlist(root):
    """The unsynthesized file, preferred over any synth_* sibling.

    The obfuscation release ships an unsynthesized .v and a synt_*.v per
    instance.  The unsynthesized one is flat Verilog over primitives and parses
    directly; the synthesized one is mapped to standard cells and would need a
    cell library first.
    """
    for ext in (".bench", ".isc", ".v"):
        hits = []
        for dirpath, dirs, files in os.walk(root):
            dirs.sort()
            for f in sorted(files):
                if f.endswith(ext) and not _is_synthesized(f):
                    hits.append(os.path.join(dirpath, f))
        if hits:
            return hits[0]
    return None

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



def brute(nl, queries):
    import itertools
    if len(nl.keys) > 24:
        return None, "refusing to enumerate %d keys" % len(nl.keys)
    n = 0
    for bits in itertools.product((0, 1), repeat=len(nl.keys)):
        asg = dict(zip(nl.keys, bits))
        ok = True
        for x, y in queries:
            a = dict(asg)
            a.update(x)
            if nl.simulate(a) != y:
                ok = False
                break
        if ok:
            n += 1
    return n, None


def run(engine, nl, queries, node_cap, timeout, cap_bits):
    t0 = time.time()
    if engine == "A":
        r = engineA.version_space(nl, queries, cap_bits=cap_bits,
                                  want_key_moral=False)
        return (r.get("count"), time.time() - t0,
                "width %s" % r.get("factor_width"),
                None if r.get("count") is not None
                else "state cap of 2^%d exceeded" % cap_bits)
    if engine == "B-py":
        r = engineB.version_space(nl, queries, node_cap=node_cap)
        return (r.get("count"), time.time() - t0,
                "%s nodes" % r.get("acc_nodes"), r.get("note"))
    if engine == "B-cudd":
        if not cudd_bridge.available():
            return None, 0.0, "", ("not built at %s; see c/Makefile"
                                   % cudd_bridge.BINARY)
        r = cudd_bridge.version_space(nl, queries, node_limit=node_cap,
                                      timeout=timeout)
        return (r.get("count"), time.time() - t0,
                "%s nodes" % r.get("acc_nodes"), r.get("note"))
    c, note = brute(nl, queries)
    return c, time.time() - t0, "", note


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bench", required=True,
                    help="benchmark zip, directory, or netlist file")
    ap.add_argument("--bench-dir", help="directory to resolve --bench against")
    ap.add_argument("--engine", default="all", choices=ENGINES)
    ap.add_argument("-t", "--queries", type=int, default=5)
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--node-cap", type=int, default=8_000_000)
    ap.add_argument("--cap-bits", type=int, default=30)
    ap.add_argument("--timeout", type=float, default=600.0)
    args = ap.parse_args()

    path, hold = resolve(args.bench, args.bench_dir)
    try:
        nl = nlmod.load(path)
        st = nl.stats()
        print("%s: %d gates, %d inputs, %d keys, %d outputs"
              % (os.path.basename(path), st["gates"], st["inputs"],
                 st["keys"], st["outputs"]))
        if not nl.keys:
            raise SystemExit("no key inputs identified in this netlist")

        rng = random.Random(args.seed)
        secret = {k: rng.randint(0, 1) for k in nl.keys}
        queries = []
        for _ in range(args.queries):
            x = {i: rng.randint(0, 1) for i in nl.inputs}
            a = dict(x)
            a.update(secret)
            queries.append((x, nl.simulate(a)))
        print("%d random queries, seed %d\n" % (len(queries), args.seed))

        want = (["A", "B-py", "B-cudd", "brute"] if args.engine == "all"
                else [args.engine])
        results = {}
        for e in want:
            c, secs, detail, note = run(e, nl, queries, args.node_cap,
                                        args.timeout, args.cap_bits)
            results[e] = c
            shown = "-" if c is None else "%d" % c
            bits = "" if not c else "  log2 %.3f" % math.log2(c)
            print("  %-7s %-24s %8.2fs  %-14s %s"
                  % (e, shown, secs, detail, note or ""))
            sys.stdout.flush()

        vals = {v for v in results.values() if v is not None}
        if len(vals) > 1:
            print("\nDISAGREEMENT: %s" % results)
            return 1
        if len(want) > 1 and len(vals) == 1:
            print("\nall engines that completed agree")
        return 0
    finally:
        if hold:
            shutil.rmtree(hold, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
