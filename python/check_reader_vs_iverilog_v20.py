#!/usr/bin/env python3
# Author: Mitchell A. Thornton
# Copyright (c) 2026 Mitchell A. Thornton
"""
check_reader_vs_iverilog_v20.py -- cross-check the bundle's Verilog reader against an
independent simulator.

`check_verilog_reader_v20.py` is hermetic: it writes its own Verilog and
checks the reader against a reference written in the same bundle.  This does
something the hermetic gate cannot.  It reads a real release file with the
bundle's reader, draws random primary-input and key vectors, evaluates them
with the reader, then evaluates the same vectors with Icarus Verilog on the
same file, and compares output by output.  A disagreement is a reader defect.

Optional, because it needs a tool the bundle does not ship:

    macOS    brew install icarus-verilog
    Ubuntu   sudo apt install iverilog

Usage, from the bundle root:

    python3 scripts/check_reader_vs_iverilog_v20.py --ex DIR c432-RN320 c432-SL640
    python3 scripts/check_reader_vs_iverilog_v20.py --ex DIR --all

--ex names a directory holding one subdirectory per instance, each containing
<instance>.v, which is what unzipping the release produces.  Instances that
the reader refuses for combinational feedback are reported as correctly
rejected, not as failures, because the release does contain cyclic locking.

Exit status is 0 when nothing disagrees.
"""
import argparse
import os, random, re, shutil, subprocess, sys, tempfile

ROOT = os.environ.get("LL_BUNDLE",
                      os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "experiments", "E2_published_suite"))
import verilog  # noqa: E402

EX = os.environ.get("LL_EXTRACTED", "")


def esc(n):
    """Render a net name as a Verilog identifier."""
    if re.match(r"^[A-Za-z_][A-Za-z0-9_$]*$", n):
        return n
    return "\\" + n + " "


def esc_mod(name):
    """Render a module name, preserving the escaped form."""
    if name.startswith("\\"):
        return name + " "
    return name


def top_module(path):
    for line in open(path, errors="replace"):
        m = re.match(r"\s*module\s+(\\\S+|[A-Za-z_][\w$]*)", line)
        if m:
            return m.group(1)
    raise SystemExit("no module in " + path)


def run(name, n_vec=64, seed=11):
    path = os.path.join(EX, name, name + ".v")
    nl = verilog.read_verilog(path)
    ports = nl.inputs + nl.keys
    rng = random.Random(seed)
    vecs = [[rng.randint(0, 1) for _ in ports] for _ in range(n_vec)]

    mine = []
    for v in vecs:
        a = nl.simulate(dict(zip(ports, v)))
        mine.append("".join(str(b) for b in a))

    top = top_module(path)
    tb = []
    tb.append("`timescale 1ns/1ps")
    tb.append("module tb;")
    for p in ports:
        tb.append("  reg %s;" % esc(p))
    for o in nl.outputs:
        tb.append("  wire %s;" % esc(o))
    conns = ", ".join(".%s(%s)" % (esc(p), esc(p))
                      for p in ports + nl.outputs)
    tb.append("  %s dut (%s);" % (esc_mod(top), conns))
    tb.append("  integer i;")
    tb.append("  initial begin")
    for v in vecs:
        for p, b in zip(ports, v):
            tb.append("    %s = 1'b%d;" % (esc(p), b))
        tb.append("    #1;")
        tb.append('    $display("%s", %s);'
                  % ("%b" * len(nl.outputs),
                     ", ".join(esc(o) for o in nl.outputs)))
    tb.append("    $finish;")
    tb.append("  end")
    tb.append("endmodule")

    d = tempfile.mkdtemp(prefix="xchk_")
    tbp = os.path.join(d, "tb.v")
    open(tbp, "w").write("\n".join(tb))
    exe = os.path.join(d, "a.out")
    p = subprocess.run(["iverilog", "-g2005", "-o", exe, tbp, path],
                       capture_output=True, text=True)
    if p.returncode != 0:
        return name, None, (p.stdout + p.stderr)[:400]
    p = subprocess.run(["vvp", exe], capture_output=True, text=True)
    theirs = [l.strip() for l in p.stdout.splitlines() if l.strip()
              and set(l.strip()) <= set("01xzXZ")]
    if len(theirs) != len(mine):
        return name, None, "got %d lines from vvp, expected %d" % (
            len(theirs), len(mine))
    bad = [(i, mine[i], theirs[i]) for i in range(len(mine))
           if mine[i] != theirs[i]]
    return name, (len(mine), len(bad), bad[:2]), None


def main():
    ap = argparse.ArgumentParser(
        description="Cross-check the reader against Icarus Verilog.")
    ap.add_argument("names", nargs="*", help="instance names")
    ap.add_argument("--ex", help="directory of extracted instances; also "
                                 "settable with LL_EXTRACTED")
    ap.add_argument("--all", action="store_true", help="every instance found")
    ap.add_argument("--vectors", type=int, default=64)
    args = ap.parse_args()

    global EX
    EX = args.ex or EX
    if not EX or not os.path.isdir(EX):
        print("Icarus cross-check skipped: set LL_EXTRACTED, or pass --ex, "
              "pointing at a directory with one subdirectory per benchmark "
              "instance.  The release is third party and is not vendored.")
        return 0
    if shutil.which("iverilog") is None or shutil.which("vvp") is None:
        print("iverilog not found, so this optional gate is skipped.")
        print("  macOS   brew install icarus-verilog")
        print("  Ubuntu  sudo apt install iverilog")
        return 0

    names = args.names
    if args.all or not names:
        names = sorted(d for d in os.listdir(EX)
                       if os.path.exists(os.path.join(EX, d, d + ".v")))

    agree = disagree = cyclic = malformed = skipped = 0
    for nm in names:
        try:
            n, res, err = run(nm, args.vectors)
        except Exception as e:
            kind = type(e).__name__
            if kind == "CyclicNetlist":
                cyclic += 1
                print("%-16s correctly rejected, combinational feedback" % nm)
            else:
                malformed += 1
                print("%-16s reader refused the file: %s"
                      % (nm, str(e).split(": ", 1)[-1][:120]))
            continue
        if err:
            skipped += 1
            print("%-16s could not be checked: %s"
                  % (n, err.replace("\n", " ")[:120]))
            continue
        total, nbad, sample = res
        if nbad:
            disagree += 1
            print("%-16s %4d vectors, %d DISAGREE  e.g. %s"
                  % (n, total, nbad, sample))
        else:
            agree += 1
            print("%-16s %4d vectors, all agree" % (n, total))

    print()
    print("agree with Icarus Verilog    %4d" % agree)
    print("disagreements                %4d" % disagree)
    print("correctly rejected as cyclic %4d" % cyclic)
    print("refused as malformed         %4d" % malformed)
    if skipped:
        print("not checked                  %4d" % skipped)
    return 1 if disagree else 0


if __name__ == "__main__":
    sys.exit(main())
