#!/usr/bin/env python3
# Author: Mitchell A. Thornton
# Copyright (c) 2026 Mitchell A. Thornton
"""
verilog.py -- a self-contained structural Verilog reader for locked netlists.

WHY THIS EXISTS

Campaign E.2 reads the Trust-Hub obfuscation release, which ships structural
Verilog.  Until now that went through an external front end, which made the
artifact depend on a separate tool: a reader who could not obtain it could not
reproduce E.2.  This module removes that dependency.  Nothing outside it is
needed to read the release.

It also fixes a defect in the previous path.  That reader's expression
tokenizer accepted only `( ) ~ & | ^`, identifiers and constants, so it failed
on the conditional operator with `unparsable expression near '? ... : ...'`.
The forty-three cyclic-locking instances build their key muxes as

    assign muxed0 = keyinput0 ? G296gat : muxed15;

and were therefore rejected by a parse error rather than by anything to do
with their being cyclic.  They ARE cyclic, every one of them, but the tool was
not the thing establishing that.  This reader parses them and then rejects
them on an explicit acyclicity check that names the cycle it found.

WHAT IT ACCEPTS

Surveyed against all 295 unsynthesized files of the release, which between
them contain exactly:

  directives    `timescale and friends, skipped
  comments      // to end of line, and /* ... */
  identifiers   plain, escaped (\\name followed by whitespace), and
                bit-selects name[k], which are flattened to one net each
  declarations  input, output, wire, with an optional vector range [a:b]
  primitives    not buf and nand or nor xor xnor, named instances,
                (output, input...) positional, fan-in up to 234
  assignments   assign lhs = expr, over ~ & | ^ ( ) and the conditional
                operator, twelve distinct shapes in the release
  instances     one submodule, AntiSAT, positionally bound, inlined here

The grammar implemented is wider than the release needs, because a parser
that accepts exactly one corpus is a corpus reader and not a parser.  What it
does NOT accept is anything sequential, anything behavioural, and standard
cell instantiations: the synthesized `synt_*.v` files are mapped to a cell
library and would need that library before they could be read at all.

USAGE

    import netlist
    nl = netlist.load("c432-RN320.v")      # this module is registered for .v

or directly:

    from verilog import read_verilog
    nl = read_verilog(path)

Raises VerilogError with a located message on anything it cannot handle, and
CyclicNetlist, a subclass, when the design has combinational feedback.
"""
from __future__ import annotations

import re

import netlist as nlmod

__all__ = ["read_verilog", "VerilogError", "CyclicNetlist"]


class VerilogError(Exception):
    """Anything this reader cannot parse or elaborate."""


class CyclicNetlist(VerilogError):
    """The design has combinational feedback.

    Carried separately from a parse failure because the two mean different
    things: one is a limitation of this reader, the other is a property of the
    design.  Callers that want to report cyclic locking as out of scope should
    catch this and not the base class.
    """

    def __init__(self, msg, cycle=None):
        super().__init__(msg)
        self.cycle = cycle or []


PRIMITIVES = {"not", "buf", "and", "nand", "or", "nor", "xor", "xnor"}
_OP = {"not": "NOT", "buf": "BUF", "and": "AND", "nand": "NAND",
       "or": "OR", "nor": "NOR", "xor": "XOR", "xnor": "XNOR"}


# ------------------------------------------------------------------ lexer

# Order matters: escaped identifiers first, then multi-character tokens.
_TOK = re.compile(r"""
    (?P<ws>\s+)
  | (?P<line_comment>//[^\n]*)
  | (?P<block_comment>/\*.*?\*/)
  | (?P<directive>`[A-Za-z_]\w*[^\n]*)
  | (?P<escaped>\\\S+)
  | (?P<number>\d+\s*'\s*[bBdDhHoO]\s*[0-9a-fA-FxXzZ_]+)
  | (?P<ident>[A-Za-z_][A-Za-z0-9_$]*)
  | (?P<int>\d+)
  | (?P<punct>[()\[\],;=:?~&|^])
""", re.X | re.S)


def _tokenize(text, path):
    """(kind, value, line) for everything that is not whitespace or comment."""
    out = []
    pos, line = 0, 1
    n = len(text)
    while pos < n:
        m = _TOK.match(text, pos)
        if not m:
            raise VerilogError("%s:%d: cannot tokenize %r"
                               % (path, line, text[pos:pos + 24]))
        kind = m.lastgroup
        val = m.group()
        line += val.count("\n")
        pos = m.end()
        if kind in ("ws", "line_comment", "block_comment", "directive"):
            continue
        if kind == "escaped":
            # \name is one identifier; the trailing whitespace terminates it
            # and is not part of the name.
            out.append(("ident", val[1:], line))
        else:
            out.append((kind, val, line))
    return out


class _Stream:
    def __init__(self, toks, path):
        self.t, self.i, self.path = toks, 0, path

    def peek(self, k=0):
        j = self.i + k
        return self.t[j] if j < len(self.t) else ("eof", "", -1)

    def next(self):
        tok = self.peek()
        self.i += 1
        return tok

    def at(self, val):
        return self.peek()[1] == val

    def eat(self, val):
        if not self.at(val):
            k, v, ln = self.peek()
            raise VerilogError("%s:%d: expected %r, found %r"
                               % (self.path, ln, val, v))
        return self.next()

    def maybe(self, val):
        if self.at(val):
            self.next()
            return True
        return False


# ----------------------------------------------------------------- parser

class _Module:
    def __init__(self, name):
        self.name = name
        self.ports = []            # declared port order, for positional bind
        self.inputs = []
        self.outputs = []
        self.wires = []
        self.gates = []            # (op, out, [ins])
        self.assigns = []          # (lhs, expr-tree)
        self.instances = []        # (modname, [actual nets])


def _parse_net(s):
    """One net reference: ident, optionally with a [k] bit-select.

    A bit-select is flattened into the net name, so KeyWire_0[3] becomes the
    single net 'KeyWire_0[3]'.  The release never slices a range on the right
    hand side, and treating each bit as its own net is what the IR wants.
    """
    k, v, ln = s.next()
    if k != "ident":
        raise VerilogError("%s:%d: expected a net name, found %r"
                           % (s.path, ln, v))
    if s.at("["):
        s.next()
        _k2, idx, ln2 = s.next()
        if _k2 not in ("int", "ident"):
            raise VerilogError("%s:%d: bad bit-select index %r"
                               % (s.path, ln2, idx))
        s.eat("]")
        return "%s[%s]" % (v, idx)
    return v


def _parse_decl_names(s):
    """The name list of an input/output/wire declaration, expanding vectors."""
    names = []
    lo = hi = None
    if s.at("["):
        s.next()
        _k, a, _ln = s.next()
        s.eat(":")
        _k, b, _ln = s.next()
        s.eat("]")
        lo, hi = int(a), int(b)
    while True:
        base = _parse_net(s)
        if lo is None:
            names.append(base)
        elif "[" in base:
            names.append(base)          # already an explicit bit
        else:
            step = 1 if hi >= lo else -1
            names.extend("%s[%d]" % (base, i)
                         for i in range(lo, hi + step, step))
        if not s.maybe(","):
            break
    s.eat(";")
    return names


# expression trees: ('net', name) | ('not', e) | (op, a, b) | ('mux', s, a, b)

def _parse_expr(s):
    return _parse_cond(s)


def _parse_cond(s):
    c = _parse_or(s)
    if s.maybe("?"):
        a = _parse_cond(s)
        s.eat(":")
        b = _parse_cond(s)
        return ("mux", c, a, b)
    return c


def _parse_or(s):
    e = _parse_xor(s)
    while s.at("|"):
        s.next()
        e = ("or", e, _parse_xor(s))
    return e


def _parse_xor(s):
    e = _parse_and(s)
    while s.at("^"):
        s.next()
        e = ("xor", e, _parse_and(s))
    return e


def _parse_and(s):
    e = _parse_unary(s)
    while s.at("&"):
        s.next()
        e = ("and", e, _parse_unary(s))
    return e


def _parse_unary(s):
    if s.maybe("~"):
        return ("not", _parse_unary(s))
    if s.maybe("("):
        e = _parse_expr(s)
        s.eat(")")
        return e
    k, v, ln = s.peek()
    if k == "number":
        raise VerilogError("%s:%d: constant %r in an expression is not "
                           "supported; no file in the release uses one"
                           % (s.path, ln, v))
    return ("net", _parse_net(s))


def _parse_module(s):
    s.eat("module")
    name = _parse_net(s)
    m = _Module(name)
    if s.maybe("("):
        if not s.at(")"):
            while True:
                m.ports.append(_parse_net(s))
                if not s.maybe(","):
                    break
        s.eat(")")
    s.eat(";")

    while not s.at("endmodule"):
        k, v, ln = s.peek()
        if k == "eof":
            raise VerilogError("%s: module %s is not terminated by endmodule"
                               % (s.path, name))
        if v in ("input", "output", "wire"):
            s.next()
            names = _parse_decl_names(s)
            getattr(m, {"input": "inputs", "output": "outputs",
                        "wire": "wires"}[v]).extend(names)
        elif v == "assign":
            s.next()
            while True:
                lhs = _parse_net(s)
                s.eat("=")
                m.assigns.append((lhs, _parse_expr(s)))
                if not s.maybe(","):
                    break
            s.eat(";")
        elif v in PRIMITIVES:
            op = s.next()[1]
            if s.peek()[0] == "ident":       # optional instance name
                s.next()
            s.eat("(")
            nets = []
            while True:
                nets.append(_parse_net(s))
                if not s.maybe(","):
                    break
            s.eat(")")
            s.eat(";")
            if len(nets) < 2:
                raise VerilogError("%s:%d: %s needs an output and at least "
                                   "one input" % (s.path, ln, op))
            m.gates.append((op, nets[0], nets[1:]))
        elif k == "ident":
            # module instantiation: MODNAME inst ( a, b, ... );
            modname = s.next()[1]
            if s.peek()[0] == "ident":
                s.next()
            s.eat("(")
            actuals = []
            if not s.at(")"):
                while True:
                    if s.at("."):
                        raise VerilogError(
                            "%s:%d: named port binding in an instance of %r "
                            "is not supported; the release binds positionally"
                            % (s.path, ln, modname))
                    actuals.append(_parse_net(s))
                    if not s.maybe(","):
                        break
            s.eat(")")
            s.eat(";")
            m.instances.append((modname, actuals))
        else:
            raise VerilogError("%s:%d: unexpected %r inside module %s"
                               % (s.path, ln, v, name))
    s.eat("endmodule")
    return m


def _parse_file(text, path):
    s = _Stream(_tokenize(text, path), path)
    mods = []
    while s.peek()[0] != "eof":
        if not s.at("module"):
            k, v, ln = s.peek()
            raise VerilogError("%s:%d: expected 'module', found %r"
                               % (path, ln, v))
        mods.append(_parse_module(s))
    if not mods:
        raise VerilogError("%s: no module found" % path)
    return mods


# ------------------------------------------------------------- elaboration

class _Builder:
    """Flattens modules into (op, out, ins) triples over unique net names."""

    def __init__(self, path):
        self.path = path
        self.gates = []
        self.k = 0

    def tmp(self, tag):
        self.k += 1
        return "__t%d_%s" % (self.k, tag)

    def emit(self, op, out, ins):
        self.gates.append((op, out, list(ins)))

    def expr(self, e, sub, tag, into=None):
        """Emit gates for expression `e`, return the net carrying its value.

        `sub` renames nets, which is how an inlined instance binds its formals
        to the caller's actuals.  `into` names the net the result must land
        on, which lets the outermost operation of an assignment write straight
        to the left hand side instead of to a temporary that a buffer then
        copies.  That one buffer per assignment is not free: it is another
        variable in the factor graph and another node in the diagram, on a
        release that contains 387,169 assignments.
        """
        kind = e[0]
        if kind == "net":
            src = sub(e[1])
            if into is None:
                return src
            self.emit("buf", into, [src])       # a rename needs a real gate
            return into
        if kind == "not":
            a = self.expr(e[1], sub, tag)
            o = into or self.tmp(tag)
            self.emit("not", o, [a])
            return o
        if kind in ("and", "or", "xor"):
            a = self.expr(e[1], sub, tag)
            b = self.expr(e[2], sub, tag)
            o = into or self.tmp(tag)
            self.emit(kind, o, [a, b])
            return o
        if kind == "mux":
            # s ? a : b  ==  (s & a) | (~s & b)
            sn = self.expr(e[1], sub, tag)
            a = self.expr(e[2], sub, tag)
            b = self.expr(e[3], sub, tag)
            ns, t1, t2 = self.tmp(tag), self.tmp(tag), self.tmp(tag)
            o = into or self.tmp(tag)
            self.emit("not", ns, [sn])
            self.emit("and", t1, [sn, a])
            self.emit("and", t2, [ns, b])
            self.emit("or", o, [t1, t2])
            return o
        raise VerilogError("%s: internal: unknown expression node %r"
                           % (self.path, kind))


def _elaborate(mods, path, top=None):
    by_name = {m.name: m for m in mods}
    if top is None:
        instantiated = {n for m in mods for n, _ in m.instances}
        roots = [m for m in mods if m.name not in instantiated]
        if len(roots) != 1:
            raise VerilogError("%s: cannot pick a top module among %s"
                               % (path, sorted(by_name)))
        topmod = roots[0]
    else:
        topmod = by_name[top]

    b = _Builder(path)

    def do(m, sub, depth, tag):
        if depth > 16:
            raise VerilogError("%s: instance nesting deeper than 16 in %s"
                               % (path, m.name))
        for op, out, ins in m.gates:
            b.emit(op, sub(out), [sub(i) for i in ins])
        for lhs, e in m.assigns:
            b.expr(e, sub, tag, into=sub(lhs))
        for k, (modname, actuals) in enumerate(m.instances):
            if modname not in by_name:
                raise VerilogError("%s: instance of unknown module %r"
                                   % (path, modname))
            child = by_name[modname]
            if len(actuals) != len(child.ports):
                raise VerilogError(
                    "%s: instance of %s binds %d nets to %d ports"
                    % (path, modname, len(actuals), len(child.ports)))
            bind = {f: sub(a) for f, a in zip(child.ports, actuals)}
            ctag = "%s_%d" % (modname, k)
            local = set(child.inputs) | set(child.outputs) | set(child.wires)

            def csub(n, bind=bind, ctag=ctag, local=local):
                if n in bind:
                    return bind[n]
                if n in local or True:
                    return "%s/%s" % (ctag, n)

            do(child, csub, depth + 1, ctag)

    do(topmod, lambda n: n, 0, "top")
    return topmod, b.gates


# -------------------------------------------------------- topological sort

def _order(gates, primary, path):
    """Return gates topologically sorted, or raise CyclicNetlist.

    Iterative Kahn ordering, then an explicit cycle walk on whatever is left
    so the message can name the loop rather than just assert one exists.
    """
    driver = {}
    for i, (_op, out, _ins) in enumerate(gates):
        if out in driver:
            raise VerilogError("%s: net %r is driven by more than one gate"
                               % (path, out))
        driver[out] = i

    known = set(primary)
    indeg = [0] * len(gates)
    users = {}
    for i, (_op, _out, ins) in enumerate(gates):
        for a in ins:
            if a in driver:
                indeg[i] += 1
                users.setdefault(a, []).append(i)
            elif a not in known:
                # Nineteen files of the release read a net that differs from a
                # declared one only in case, such as keyWire_0_2 against the
                # declared KeyWire_0_2.  Verilog is case-sensitive, so the net
                # is genuinely undriven and the file is malformed.  Say so,
                # rather than leaving it to look like a reader limitation.
                near = [n for n in list(known) + list(driver)
                        if n.lower() == a.lower() and n != a]
                hint = ""
                if near:
                    hint = ("; the design declares %r, which differs only in "
                            "case, so this looks like a defect in the "
                            "benchmark file rather than in this reader"
                            % near[0])
                raise VerilogError("%s: net %r is read but never driven or "
                                   "declared as an input%s" % (path, a, hint))

    ready = [i for i in range(len(gates)) if indeg[i] == 0]
    order = []
    while ready:
        i = ready.pop()
        order.append(i)
        for j in users.get(gates[i][1], ()):
            indeg[j] -= 1
            if indeg[j] == 0:
                ready.append(j)

    if len(order) != len(gates):
        stuck = [i for i in range(len(gates)) if indeg[i] > 0]
        cyc = _find_cycle(gates, driver, set(stuck))
        raise CyclicNetlist(
            "%s: combinational feedback, %d gates lie on or below a cycle; "
            "this package assumes an acyclic netlist.  Cycle: %s"
            % (path, len(stuck), " -> ".join(cyc) if cyc else "(not located)"),
            cycle=cyc)
    return [gates[i] for i in order]


def _find_cycle(gates, driver, stuck):
    """One concrete cycle among the gates that never became ready."""
    colour = {}
    stack = []

    def walk(i):
        colour[i] = 1
        stack.append(i)
        for a in gates[i][2]:
            j = driver.get(a)
            if j is None or j not in stuck:
                continue
            c = colour.get(j, 0)
            if c == 1:
                k = stack.index(j)
                return [gates[x][1] for x in stack[k:]] + [gates[j][1]]
            if c == 0:
                found = walk(j)
                if found:
                    return found
        stack.pop()
        colour[i] = 2
        return None

    import sys
    lim = sys.getrecursionlimit()
    sys.setrecursionlimit(max(lim, 20000))
    try:
        for i in sorted(stuck):
            if colour.get(i, 0) == 0:
                found = walk(i)
                if found:
                    return found
    except RecursionError:
        return []
    finally:
        sys.setrecursionlimit(lim)
    return []


# ------------------------------------------------------------------- entry

def read_verilog(path, key_patterns=True, top=None):
    """Read structural Verilog into a netlist.Netlist in topological order."""
    with open(path, "r", errors="replace") as fh:
        text = fh.read()
    mods = _parse_file(text, path)
    topmod, gates = _elaborate(mods, path, top)

    ins = list(topmod.inputs)
    outs = list(topmod.outputs)
    if not outs:
        raise VerilogError("%s: module %s declares no outputs"
                           % (path, topmod.name))

    nl = nlmod.Netlist(topmod.name)
    if key_patterns:
        # netlist.py owns the naming conventions; this reader does not invent
        # its own, so a change there applies to every format at once.
        keyset = [n for n in ins if nlmod._looks_like_key(n)]
    else:
        keyset = []
    kk = set(keyset)
    nl.inputs = [n for n in ins if n not in kk]
    nl.keys = keyset
    nl.outputs = outs

    ordered = _order(gates, set(ins), path)
    nl.gates = [(out, _OP[op], list(a)) for op, out, a in ordered]

    missing = [o for o in outs if o not in set(nl.inputs) | set(nl.keys)
               | {g[0] for g in nl.gates}]
    if missing:
        raise VerilogError("%s: output(s) %s are never driven"
                           % (path, ", ".join(missing[:4])))
    if not nl.topo_ok():
        raise VerilogError("%s: internal: produced a netlist that is not in "
                           "topological order" % path)
    return nl

