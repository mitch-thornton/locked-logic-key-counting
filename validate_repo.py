#!/usr/bin/env python3
# Author: Mitchell A. Thornton
# Copyright (c) 2026 Mitchell A. Thornton
"""
validate_repo.py -- check that a fresh clone of this repository is intact and
that its results reproduce.

Run this first, right after cloning:

    python3 validate_repo.py

It needs nothing external: no CUDD, no benchmark download, standard library
only, Python 3.8+. It runs the checks that must pass before anything else in
the repository is worth believing, and exits non-zero if any of them fail.

What it runs
------------
  1. presence check     every source file the repository is supposed to ship
  2. import check        the lockcount package imports cleanly
  3. closed forms        python/j2_verify_v20.py  -- every closed form in the
                         paper against brute force
  4. counting kernel     python/haarcount_v1.py   -- DP vs brute force, the
                         closed forms, marginals, Parseval, the lattice index
  5. parity gates        python/lockcount/validate.py -- engine A vs the Python
                         diagram engine vs brute force, plus reader round-trip,
                         key recovery, and the plateau certificate
  6. built-in reader     read a small structural-Verilog netlist through
                         verilog.py and count it with two engines and brute
                         force, confirming the standalone (no-Renesis) path

The CUDD engine is optional and is not exercised here; build it with
`cd c && make CUDD=/path/to/cudd-3.0.0` and re-run `python/lockcount/validate.py`
to bring it into the parity gate. See README.md and DATA.md.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable

GREEN = "\033[32m" if sys.stdout.isatty() else ""
RED = "\033[31m" if sys.stdout.isatty() else ""
BOLD = "\033[1m" if sys.stdout.isatty() else ""
OFF = "\033[0m" if sys.stdout.isatty() else ""

REQUIRED = [
    "README.md", "LICENSE", "PATENTS.md", "CITATION.cff", "DATA.md",
    "CODEBASE.md",
    "data/benchmark_manifest.json",
    "c/minibdd_v20.c", "c/engineB_cudd.c", "c/Makefile",
    "python/requirements.txt",
    "python/haarcount_v1.py", "python/j2_verify_v20.py",
    "python/run_b1_v20.py", "python/run_b23_v20.py", "python/run_c1_v20.py",
    "python/run_c2_v20.py", "python/run_d1_v20.py", "python/run_e1_v20.py",
    "python/run_e1b_v20.py", "python/lockkit_v20.py",
    "python/mk_mult_bench_v20.py", "python/make_e1_figures_v20.py",
    "benchmarks/e1/mult_4x4.bench", "benchmarks/e1/mult_6x6.bench",
    "benchmarks/e1/mult_8x8.bench", "benchmarks/e1/mult_10x10.bench",
    "benchmarks/e1/mult_12x12.bench", "benchmarks/e1/c6288_like_16x16.bench",
    "benchmarks/e1/bdd_growth_results.md",
    "data/e1/e1_results.json", "data/e1/e1b_results.json",
    "python/lockcount/netlist.py", "python/lockcount/isc.py",
    "python/lockcount/verilog.py", "python/lockcount/engineA.py",
    "python/lockcount/engineB.py", "python/lockcount/cudd_bridge.py",
    "python/lockcount/lockschemes.py", "python/lockcount/screen.py",
    "python/lockcount/smoke.py", "python/lockcount/count.py",
    "python/lockcount/validate.py", "python/lockcount/certify.py",
    "python/lockcount/run_e2_v20.py", "python/lockcount/runpar_v20.py",
    "python/e3/run_e3_v20.py", "python/e3/satsolve.py",
]

TINY_V = textwrap.dedent("""\
    module tiny (a, b, c, keyinput0, keyinput1, y);
      input a, b, c, keyinput0, keyinput1;
      output y;
      wire w0, w1, w2;
      xor (w0, a, keyinput0);
      xnor(w1, b, keyinput1);
      and (w2, w0, w1);
      or  (y, w2, c);
    endmodule
    """)


def hr():
    print("-" * 70)


def run_step(title, argv, cwd=None):
    """Run a subprocess step, stream nothing, report pass/fail on its exit."""
    print("%s* %s%s" % (BOLD, title, OFF))
    t0 = time.time()
    p = subprocess.run(argv, cwd=cwd, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, text=True)
    dt = time.time() - t0
    ok = (p.returncode == 0)
    tag = "%sPASS%s" % (GREEN, OFF) if ok else "%sFAIL%s" % (RED, OFF)
    print("  %s  (%.1fs)" % (tag, dt))
    if not ok:
        # show the output so a failure is diagnosable without re-running
        for line in p.stdout.rstrip().splitlines():
            print("    | " + line)
    return ok


def check_presence():
    print("%s* file presence%s" % (BOLD, OFF))
    missing = [f for f in REQUIRED if not os.path.exists(os.path.join(ROOT, f))]
    if missing:
        print("  %sFAIL%s  %d file(s) missing:" % (RED, OFF, len(missing)))
        for f in missing:
            print("    - " + f)
        return False
    print("  %sPASS%s  (%d files present)" % (GREEN, OFF, len(REQUIRED)))
    return True


def check_import():
    print("%s* package import%s" % (BOLD, OFF))
    lc = os.path.join(ROOT, "python", "lockcount")
    code = ("import importlib\n"
            "for m in ('netlist','isc','verilog','engineA','engineB',"
            "'cudd_bridge','count','screen','validate','certify'):\n"
            "    importlib.import_module(m)\n"
            "print('ok')\n")
    p = subprocess.run([PY, "-c", code], cwd=lc, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, text=True)
    ok = (p.returncode == 0)
    tag = "%sPASS%s" % (GREEN, OFF) if ok else "%sFAIL%s" % (RED, OFF)
    print("  %s" % tag)
    if not ok:
        for line in p.stdout.rstrip().splitlines():
            print("    | " + line)
    return ok


def check_builtin_reader():
    """The standalone path: read structural Verilog with no external front end."""
    print("%s* built-in Verilog reader (standalone, no Renesis)%s" % (BOLD, OFF))
    lc = os.path.join(ROOT, "python", "lockcount")
    tmp = tempfile.mkdtemp(prefix="valrepo_")
    vpath = os.path.join(tmp, "tiny.v")
    open(vpath, "w").write(TINY_V)
    p = subprocess.run([PY, "count.py", "--bench", vpath, "--engine", "all",
                        "-t", "6"], cwd=lc, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, text=True)
    out = p.stdout
    ok = (p.returncode == 0 and
          ("all engines that completed agree" in out or
           "DISAGREEMENT" not in out and "agree" in out))
    tag = "%sPASS%s" % (GREEN, OFF) if ok else "%sFAIL%s" % (RED, OFF)
    print("  %s" % tag)
    if not ok:
        for line in out.rstrip().splitlines():
            print("    | " + line)
    return ok


def main():
    print("%s%s validate_repo.py -- checking a fresh clone %s"
          % (BOLD, "=" * 8, OFF))
    print("repository: %s" % ROOT)
    print("python:     %s" % sys.version.split()[0])
    if sys.version_info < (3, 8):
        print("%sFAIL%s  Python 3.8 or later is required." % (RED, OFF))
        return 2
    hr()

    results = []
    results.append(("file presence", check_presence()))
    results.append(("package import", check_import()))
    results.append(("closed forms (j2_verify)", run_step(
        "closed forms  (python/j2_verify_v20.py)",
        [PY, os.path.join("python", "j2_verify_v20.py")], cwd=ROOT)))
    results.append(("counting kernel (haarcount)", run_step(
        "counting kernel  (python/haarcount_v1.py)",
        [PY, os.path.join("python", "haarcount_v1.py")], cwd=ROOT)))
    results.append(("parity gates (validate)", run_step(
        "parity gates  (python/lockcount/validate.py)",
        [PY, "validate.py"], cwd=os.path.join(ROOT, "python", "lockcount"))))
    results.append(("built-in reader", check_builtin_reader()))

    hr()
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    for name, ok in results:
        tag = "%sPASS%s" % (GREEN, OFF) if ok else "%sFAIL%s" % (RED, OFF)
        print("  %-32s %s" % (name, tag))
    hr()
    if passed == total:
        print("%s%s ALL %d CHECKS PASSED %s" % (BOLD, GREEN, total, OFF))
        print("The repository is intact and its core results reproduce.")
        return 0
    print("%s%s %d of %d checks FAILED %s"
          % (BOLD, RED, total - passed, total, OFF))
    print("See the output above each failing step for the cause.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
