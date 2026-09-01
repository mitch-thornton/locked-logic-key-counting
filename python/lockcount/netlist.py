#!/usr/bin/env python3
# Author: Mitchell A. Thornton
# Copyright (c) 2026 Mitchell A. Thornton
"""
netlist.py -- the netlist IR, a BENCH reader, and simulation.

The IR is deliberately thin.  It exists so that everything downstream depends
on one small interface rather than on a file format, which is what lets a new
reader be dropped in without touching the engines.

To add a reader for another format, implement one function:

    def load(path, fmt=None) -> Netlist

returning a Netlist with `inputs`, `keys`, `outputs` and `gates` populated and
`gates` in topological order.  Register it in READERS below.  Nothing else in
this package needs to change.  `roundtrip_check` in validate.py is the gate
that any new reader must pass before it is trusted.

Readers here: the ISCAS BENCH format (ISCAS-85, ISCAS-89 combinational cores,
and most locked benchmark releases that ship .bench), the ISCAS .isc dialect
(isc.py), and structural Verilog (verilog.py).  The Verilog reader is
self-contained, so this package reads the Trust-Hub obfuscation release with
nothing external installed.
"""
from __future__ import annotations

import os
import random
import re

# A gate operator maps a list of 0/1 inputs to 0/1.
OPS = {
    "AND":  lambda v: int(all(v)),
    "NAND": lambda v: int(not all(v)),
    "OR":   lambda v: int(any(v)),
    "NOR":  lambda v: int(not any(v)),
    "XOR":  lambda v: int(sum(v) % 2 == 1),
    "XNOR": lambda v: int(sum(v) % 2 == 0),
    "NOT":  lambda v: int(not v[0]),
    "BUF":  lambda v: int(v[0]),
    "BUFF": lambda v: int(v[0]),
}

# Nets whose names match these are treated as key inputs.  Locked benchmark
# releases are not consistent, so the list is deliberately broad and the
# result is always reported so a wrong guess is visible rather than silent.
KEY_PATTERNS = [
    re.compile(r"^keyinput\d+$", re.I),          # TrustHub synthesized form
    re.compile(r"^keyinput\d+_\w+$", re.I),      # TrustHub BDD-based form,
                                                 # documented in those instances'
                                                 # ReadMe files as
                                                 # 'keyinput[KeyNumber]_GateName'
    re.compile(r"^key_?in_?\d+_\d+$", re.I),     # TrustHub unsynthesized form
    re.compile(r"^key_?\d+$", re.I),
    re.compile(r"^k\d+$", re.I),
    re.compile(r"^K\d+$"),
]


class Netlist:
    """A combinational netlist.  `gates` is in topological order."""

    def __init__(self, name="unnamed"):
        self.name = name
        self.inputs = []          # primary inputs, excluding keys
        self.keys = []            # key inputs
        self.outputs = []         # primary output net names
        self.gates = []           # (out, op, [fanin]) topologically ordered
        self._n = 0

    # -- construction ---------------------------------------------------

    def fresh(self, prefix="w"):
        self._n += 1
        return "%s%d" % (prefix, self._n)

    def add(self, op, *args, out=None):
        out = out or self.fresh()
        self.gates.append((out, op.upper(), list(args)))
        return out

    def copy(self):
        m = Netlist(self.name)
        m.inputs = list(self.inputs)
        m.keys = list(self.keys)
        m.outputs = list(self.outputs)
        m.gates = [(o, p, list(a)) for o, p, a in self.gates]
        m._n = self._n
        return m

    # -- queries --------------------------------------------------------

    @property
    def nets(self):
        return self.inputs + self.keys + [g[0] for g in self.gates]

    def stats(self):
        return {"name": self.name, "inputs": len(self.inputs),
                "keys": len(self.keys), "outputs": len(self.outputs),
                "gates": len(self.gates)}

    def simulate(self, assign):
        """assign maps every primary and key input to 0/1."""
        v = dict(assign)
        for out, op, args in self.gates:
            v[out] = OPS[op]([v[a] for a in args])
        return [v[o] for o in self.outputs]

    def random_assignment(self, rng, key=None):
        a = {i: rng.randint(0, 1) for i in self.inputs}
        if key is None:
            a.update({k: rng.randint(0, 1) for k in self.keys})
        else:
            a.update(key)
        return a

    def topo_ok(self):
        """True when every gate's fanin is defined before it is used."""
        seen = set(self.inputs) | set(self.keys)
        for out, _, args in self.gates:
            if any(a not in seen for a in args):
                return False
            seen.add(out)
        return all(o in seen for o in self.outputs)

    def levelize(self):
        lev = {n: 0 for n in self.inputs + self.keys}
        for out, _, args in self.gates:
            lev[out] = 1 + max(lev[a] for a in args)
        return lev


# ---------------------------------------------------------------- readers

# One place decides which file in an unpacked benchmark archive is the design.
# It used to be decided in three, and they drifted: two excluded `synt_*.v` and
# `*_synt.v`, one excluded only the first form, and none of them walked the
# tree in a fixed order.  On the instances whose synthesized copy is named
# `<name>_synt.v` that produced a different netlist depending on which script
# read the archive and, within one script, on the order the filesystem
# returned.  Both are defects, so the selection now lives here.

def is_synthesized(name):
    """True for the standard-cell copy shipped alongside the design."""
    base = name.rsplit("/", 1)[-1]
    stem = base.rsplit(".", 1)[0]
    return base.startswith("synt_") or stem.endswith("_synt")


def find_source(root, exts=(".v",)):
    """The design file under `root`, or None.

    Deterministic: directories and files are visited in sorted order, so the
    same archive always yields the same file.
    """
    for d, dirs, files in os.walk(root):
        dirs.sort()
        for f in sorted(files):
            if f.endswith(tuple(exts)) and not is_synthesized(f):
                return os.path.join(d, f)
    return None


def _looks_like_key(net):
    return any(p.match(net) for p in KEY_PATTERNS)


def read_bench(path, key_patterns=True):
    """Read an ISCAS BENCH file.

    Key inputs are separated from primary inputs by name when
    `key_patterns` is true.  The split is reported by `Netlist.stats` so a
    wrong guess shows up immediately rather than silently mislabelling.
    """
    nl = Netlist(name=path.split("/")[-1].rsplit(".", 1)[0])
    with open(path) as fh:
        for raw in fh:
            line = raw.split("#")[0].strip()
            if not line:
                continue
            low = line.upper()
            if low.startswith("INPUT(") and line.endswith(")"):
                net = line[line.index("(") + 1:-1].strip()
                if key_patterns and _looks_like_key(net):
                    nl.keys.append(net)
                else:
                    nl.inputs.append(net)
            elif low.startswith("OUTPUT(") and line.endswith(")"):
                nl.outputs.append(line[line.index("(") + 1:-1].strip())
            elif "=" in line:
                lhs, rhs = line.split("=", 1)
                op, args = rhs.strip().split("(", 1)
                op = op.strip().upper()
                if op not in OPS:
                    raise ValueError("unsupported gate %r in %s" % (op, path))
                nl.add(op, *[a.strip() for a in args.rstrip(")").split(",")],
                       out=lhs.strip())
            elif low.startswith(("DFF", "LATCH")):
                raise ValueError(
                    "sequential element in %s; this package is combinational"
                    % path)
    if not nl.topo_ok():
        raise ValueError("%s is not in topological order or has a cycle"
                         % path)
    return nl


def write_bench(nl, path):
    """Emit BENCH.  Used by the round-trip gate."""
    with open(path, "w") as fh:
        fh.write("# %s\n" % nl.name)
        for i in nl.inputs:
            fh.write("INPUT(%s)\n" % i)
        for k in nl.keys:
            fh.write("INPUT(%s)\n" % k)
        fh.write("\n")
        for o in nl.outputs:
            fh.write("OUTPUT(%s)\n" % o)
        fh.write("\n")
        for out, op, args in nl.gates:
            fh.write("%s = %s(%s)\n" % (out, op, ", ".join(args)))


def _read_isc_lazy(path, **kw):
    from isc import read_isc
    return read_isc(path, **kw)


def _read_verilog_lazy(path, **kw):
    from verilog import read_verilog
    return read_verilog(path, **kw)


# Register additional readers here.  Structural Verilog is read by verilog.py,
# which is self-contained: this package needs no external front end to read the
# published benchmark release.
READERS = {"bench": read_bench, "isc": _read_isc_lazy,
           "v": _read_verilog_lazy}


def load(path, fmt=None):
    fmt = fmt or path.rsplit(".", 1)[-1].lower()
    if fmt not in READERS:
        raise ValueError("no reader registered for %r; have %s"
                         % (fmt, sorted(READERS)))
    return READERS[fmt](path)


def read_key_file(path):
    """Read a key.  Accepts 'keyinput0 1' per line, or a bare bit string."""
    txt = open(path).read().strip()
    if re.fullmatch(r"[01]+", txt):
        return {"keyinput%d" % i: int(b) for i, b in enumerate(txt)}
    key = {}
    for line in txt.splitlines():
        line = line.split("#")[0].strip()
        if not line:
            continue
        parts = line.replace("=", " ").replace(",", " ").split()
        if len(parts) >= 2:
            key[parts[0]] = int(parts[1])
    return key
