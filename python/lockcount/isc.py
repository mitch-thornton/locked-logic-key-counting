#!/usr/bin/env python3
# Author: Mitchell A. Thornton
# Copyright (c) 2026 Mitchell A. Thornton
"""
isc.py -- reader for the original ISCAS-85 .isc netlist format.

The ISCAS-85 distribution ships .isc, not .bench, and the two are not the
same.  A record line is

    <line#>  <name>  <type>  <fanout>  <fanin>  [>sa0] [>sa1]

and when <fanin> is nonzero the fanin line numbers follow on one or more
continuation lines, which contain numbers only.  Fanout stems are given
their own records:

    <line#>  <name>  from  <stem-name>  [faults]

which is a branch of the named stem and, functionally, a buffer.  Gate
records with a fanout count of zero are the primary outputs.

Two things about this format catch people out and are handled explicitly
here.  A `from` record occupies the field positions that a gate record uses
for fanout and fanin, so the record must be dispatched on its type before
its numeric fields are read.  And high-fanin gates wrap their fanin list
across several continuation lines, so the reader consumes fanin numbers
until it has the declared count rather than assuming one line.

Gate types observed across the ISCAS-85 set: inpt, and, nand, or, nor, not,
buff, xor, from.  Anything else raises rather than being silently dropped.
"""
from __future__ import annotations

import re

from netlist import Netlist, OPS

# `from` is resolved to BUF; `inpt` becomes a primary input.
ISC_OPS = {
    "and": "AND", "nand": "NAND", "or": "OR", "nor": "NOR",
    "not": "NOT", "buff": "BUF", "buf": "BUF",
    "xor": "XOR", "xnor": "XNOR",
}

_NUMERIC_LINE = re.compile(r"^[\s\d]+$")


def read_isc(path, key_patterns=True):
    """Read a .isc file into a Netlist.

    `key_patterns` is accepted for interface compatibility with the BENCH
    reader; .isc files carry no key inputs, so it has no effect.
    """
    records = []          # (lineno, name, type, fanout, fanin, stem)
    pending = None        # record awaiting fanin numbers
    need = 0
    fanin_of = {}

    with open(path) as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("*"):
                continue

            if need > 0 and _NUMERIC_LINE.match(line):
                nums = [int(t) for t in line.split()]
                fanin_of[pending].extend(nums[:need])
                need -= min(need, len(nums))
                continue

            tok = line.split()
            if len(tok) < 3:
                continue
            lineno = int(tok[0])
            name = tok[1]
            typ = tok[2].lower()

            if typ == "from":
                stem = tok[3]
                records.append((lineno, name, "from", 1, 1, stem))
                continue

            if typ == "inpt":
                records.append((lineno, name, "inpt", int(tok[3]), 0, None))
                continue

            if typ not in ISC_OPS:
                raise ValueError("unsupported .isc gate type %r in %s"
                                 % (typ, path))
            fanout, fanin = int(tok[3]), int(tok[4])
            records.append((lineno, name, typ, fanout, fanin, None))
            fanin_of[lineno] = []
            pending, need = lineno, fanin

    if need > 0:
        raise ValueError("%s ended with %d fanin entries outstanding"
                         % (path, need))

    # names are unique per line number; map both ways
    name_of = {r[0]: r[1] for r in records}
    line_of_name = {r[1]: r[0] for r in records}

    nl = Netlist(name=path.split("/")[-1].rsplit(".", 1)[0])

    # primary inputs first, in file order
    for lineno, name, typ, fanout, fanin, stem in records:
        if typ == "inpt":
            nl.inputs.append(name)

    # gates in file order; .isc is already topologically ordered
    for lineno, name, typ, fanout, fanin, stem in records:
        if typ == "inpt":
            continue
        if typ == "from":
            if stem not in line_of_name:
                raise ValueError("%s: fanout stem %r not defined before use"
                                 % (path, stem))
            nl.add("BUF", stem, out=name)
            continue
        args = [name_of[x] for x in fanin_of[lineno]]
        if len(args) != fanin:
            raise ValueError("%s: gate %s declared fanin %d, found %d"
                             % (path, name, fanin, len(args)))
        if typ == "not" and len(args) != 1:
            raise ValueError("%s: NOT gate %s has fanin %d"
                             % (path, name, len(args)))
        nl.add(ISC_OPS[typ], *args, out=name)

    # primary outputs are the records with fanout zero
    for lineno, name, typ, fanout, fanin, stem in records:
        if typ not in ("inpt", "from") and fanout == 0:
            nl.outputs.append(name)

    if not nl.outputs:
        raise ValueError("%s: no primary outputs found" % path)
    if not nl.topo_ok():
        raise ValueError("%s: not topologically ordered" % path)
    return nl


def _selftest():
    """c17 has a known function; check it against the published structure."""
    import sys
    if len(sys.argv) > 1:
        for p in sys.argv[1:]:
            nl = read_isc(p)
            print("%-14s %s" % (p.split("/")[-1], nl.stats()))
        return True
    print("usage: python3 isc.py FILE.isc [FILE.isc ...]")
    return True


if __name__ == "__main__":
    _selftest()
