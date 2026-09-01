#!/usr/bin/env python3
# Author: Mitchell A. Thornton
# Copyright (c) 2026 Mitchell A. Thornton
"""
check_verilog_reader_v20.py -- gate for the self-contained Verilog reader.

Hermetic: it writes its own Verilog, so it runs anywhere and does not need the
benchmark release.  It covers every construct the release actually contains,
which was established by surveying all 295 unsynthesized files:

  vector declarations and bit-selects       wire [0:3] W;  and W[2]
  escaped identifiers                       \\IN-G339
  gate primitives, including wide fan-in    and A (o, a, b, c, d, e);
  every assignment shape in the release     ~a & ~b, a & ~b, a | b, ~a, a
  the conditional operator                  s ? a : b
  a positionally bound submodule            inlined at elaboration
  combinational feedback                    must be rejected, and located

The functional check is against a reference computed independently in Python
from the same truth table, not against another reader, so this gate does not
depend on anything outside the bundle.

Run from the bundle root:  python3 scripts/check_verilog_reader_v20.py
"""
from __future__ import annotations

import itertools
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "experiments", "E2_published_suite"))

import netlist as nlmod          # noqa: E402
import verilog                   # noqa: E402

FAIL = []


def check(label, ok, detail=""):
    print("  %-52s %s%s" % (label, "ok" if ok else "FAIL",
                            "" if ok else "  " + detail))
    if not ok:
        FAIL.append(label)


ACYCLIC = r"""
// every construct the release uses, in one module
`timescale 1ns / 1ps
module top (a, b, c, keyinput0, keyinput1, o_gate, o_expr, o_mux, o_esc,
            o_wide, o_sub);
  input a, b, c, keyinput0, keyinput1;
  output o_gate, o_expr, o_mux, o_esc, o_wide, o_sub;
  wire [0:3] W;
  wire n1, n2, n3, \IN-esc ;

  not  G1 (W[0], a);
  and  G2 (W[1], a, b);
  nand G3 (W[2], a, b, c);
  xor  G4 (W[3], a, b);
  buf  G5 (o_gate, W[1]);

  and  WIDE (o_wide, a, b, c, W[0], W[1], W[3]);

  assign n1 = ~a & ~b;
  assign n2 = a | ~c;
  assign n3 = n1 ^ n2;
  assign o_expr = n3;

  assign o_mux = keyinput0 ? W[2] : n2;

  assign \IN-esc  = ~c;
  assign o_esc = \IN-esc ;

  sub S0 (o_sub, a, b, keyinput1);
endmodule

module sub (y, p, q, k);
  input p, q, k;
  output y;
  wire t;
  and A1 (t, p, q);
  assign y = k ? t : p;
endmodule
"""

CYCLIC = r"""
module loop (a, o);
  input a;
  output o;
  wire m0, m1, m2;
  assign m0 = a ? m2 : a;
  assign m1 = a ? m0 : a;
  assign m2 = a ? m1 : a;
  buf B (o, m2);
endmodule
"""


def reference(a, b, c, k0, k1):
    """The same functions, written out independently of the reader."""
    w0 = 1 - a
    w1 = a & b
    w2 = 1 - (a & b & c)
    w3 = a ^ b
    o_gate = w1
    o_wide = a & b & c & w0 & w1 & w3
    n1 = (1 - a) & (1 - b)
    n2 = a | (1 - c)
    n3 = n1 ^ n2
    o_expr = n3
    o_mux = w2 if k0 else n2
    o_esc = 1 - c
    t = a & b
    o_sub = t if k1 else a
    return [o_gate, o_expr, o_mux, o_esc, o_wide, o_sub]


def main():
    print("verilog reader self-test")
    tmp = tempfile.mkdtemp(prefix="vrdr_")
    ok_path = os.path.join(tmp, "top.v")
    cy_path = os.path.join(tmp, "loop.v")
    open(ok_path, "w").write(ACYCLIC)
    open(cy_path, "w").write(CYCLIC)

    try:
        nl = verilog.read_verilog(ok_path)
    except Exception as e:
        check("parses every construct in the release", False, repr(e))
        print("\nFAILED"); return 1
    check("parses every construct in the release", True)

    check("splits primary inputs from key inputs",
          nl.inputs == ["a", "b", "c"] and nl.keys == ["keyinput0",
                                                       "keyinput1"],
          "inputs=%s keys=%s" % (nl.inputs, nl.keys))
    check("keeps the declared output order",
          nl.outputs == ["o_gate", "o_expr", "o_mux", "o_esc", "o_wide",
                         "o_sub"], str(nl.outputs))
    check("emits gates in topological order", nl.topo_ok())
    check("expands a vector declaration into one net per bit",
          all(("W[%d]" % i) in set(g[0] for g in nl.gates) for i in range(4)))
    check("reads an escaped identifier as one net",
          any("IN-esc" in g[0] for g in nl.gates))
    check("keeps wide fan-in as a single gate",
          any(op == "AND" and len(ins) == 6 for _o, op, ins in nl.gates))

    bad = None
    for bits in itertools.product((0, 1), repeat=5):
        a, b, c, k0, k1 = bits
        got = nl.simulate({"a": a, "b": b, "c": c,
                           "keyinput0": k0, "keyinput1": k1})
        want = reference(a, b, c, k0, k1)
        if got != want:
            bad = (bits, got, want)
            break
    check("exhaustive simulation over all 32 input combinations, "
          "against an independent reference", bad is None, str(bad))

    try:
        verilog.read_verilog(cy_path)
        check("rejects combinational feedback", False, "no exception raised")
    except verilog.CyclicNetlist as e:
        check("rejects combinational feedback", True)
        check("names the cycle it found", len(e.cycle) >= 3,
              "cycle=%s" % e.cycle)
    except Exception as e:
        check("rejects combinational feedback", False,
              "raised %s, not CyclicNetlist" % type(e).__name__)

    check("registered for .v in netlist.READERS",
          nlmod.READERS.get("v") is not None)
    try:
        nl2 = nlmod.load(ok_path)
        check("netlist.load dispatches on the extension",
              nl2.stats() == nl.stats())
    except Exception as e:
        check("netlist.load dispatches on the extension", False, repr(e))

    if FAIL:
        print("\nFAILED: %d of the reader checks" % len(FAIL))
        return 1
    print("all reader checks pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
