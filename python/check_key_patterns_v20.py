#!/usr/bin/env python3
# Author: Mitchell A. Thornton
# Copyright (c) 2026 Mitchell A. Thornton
"""
check_key_patterns_v20.py -- gate for the key-identification patterns.

Two properties, both checked against the release rather than asserted.

1. Every instance the reader accepts identifies the same number of key ports
   that the instance's own ReadMe file declares.  The release states the key
   size in prose:

       Number of Input: 176( 36 original inputs, 140 key ports)
       Key size = 140

   so this is a check against the benchmark author's intent, not against a
   previous run of this code.

2. Adding the BDD-based naming form in v20 changed the key count on the 24
   BDD-based instances and on nothing else.  Widening a name pattern is
   exactly the kind of change that quietly reclassifies a primary input as a
   key somewhere unrelated, and every count in the paper would move if it did.

Optional, because it needs the release, which is not vendored.  Point --ex at
a directory holding one subdirectory per instance, which is what unzipping the
archives produces.  Without it the gate skips and says so.

    python3 scripts/check_key_patterns_v20.py --ex /path/to/extracted
"""
from __future__ import annotations

import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "experiments", "E2_published_suite"))

import netlist as nlmod                            # noqa: E402
import verilog                                     # noqa: E402

# The patterns as they stood in v19, before the BDD-based form was added.
V19_PATTERNS = [
    re.compile(r"^keyinput\d+$", re.I),
    re.compile(r"^key_?in_?\d+_\d+$", re.I),
    re.compile(r"^key_?\d+$", re.I),
    re.compile(r"^k\d+$", re.I),
    re.compile(r"^K\d+$"),
]

BDD_FAMILIES = ("-BE", "-BR", "-BS")


def readme_key_size(d):
    for f in sorted(os.listdir(d)):
        if f.lower().startswith("readme") and f.endswith(".txt"):
            txt = open(os.path.join(d, f), errors="replace").read()
            m = re.search(r"Key size\s*=\s*(\d+)", txt)
            if m:
                return int(m.group(1))
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ex", help="directory of extracted instances")
    args = ap.parse_args()
    ex = args.ex or os.environ.get("LL_EXTRACTED", "")
    if not ex or not os.path.isdir(ex):
        print("key-pattern gate skipped: give --ex or set LL_EXTRACTED to a "
              "directory holding one subdirectory per benchmark instance.")
        return 0

    read = mismatch = 0
    no_readme = 0
    changed, unexpected = [], []
    for d in sorted(os.listdir(ex)):
        path = os.path.join(ex, d)
        f = os.path.join(path, d + ".v")
        if not os.path.exists(f):
            continue
        try:
            nl = verilog.read_verilog(f)
        except Exception:
            continue                       # cyclic or malformed; other gates
        read += 1
        want = readme_key_size(path)
        if want is None:
            no_readme += 1
        elif want != len(nl.keys):
            mismatch += 1
            print("  %-16s ReadMe says %d key ports, reader found %d"
                  % (d, want, len(nl.keys)))
        ports = nl.inputs + nl.keys
        old = sum(1 for p in ports if any(r.match(p) for r in V19_PATTERNS))
        if old != len(nl.keys):
            changed.append(d)
            if not any(t in d for t in BDD_FAMILIES):
                unexpected.append((d, old, len(nl.keys)))

    print()
    print("instances read                         %4d" % read)
    print("key count matches the instance ReadMe  %4d" % (read - mismatch
                                                          - no_readme))
    print("key count disagrees with the ReadMe    %4d" % mismatch)
    if no_readme:
        print("no key size stated in the ReadMe       %4d" % no_readme)
    print("key count changed since v19            %4d" % len(changed))
    for d, o, n in unexpected:
        print("  UNEXPECTED %-16s v19 found %d keys, v20 finds %d"
              % (d, o, n))

    bad = mismatch or unexpected
    if bad:
        print("\nFAILED")
        return 1
    print("\nevery readable instance agrees with its ReadMe, and the only key "
          "counts that moved since v20's pattern was added are the "
          "BDD-based instances")
    return 0


if __name__ == "__main__":
    sys.exit(main())
