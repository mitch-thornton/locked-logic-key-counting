#!/usr/bin/env python3
# Author: Mitchell A. Thornton
# Copyright (c) 2026 Mitchell A. Thornton
"""
cudd_bridge.py -- run the CUDD engine on a lockcount netlist.

The front end stays in Python because its readers are gated; the C side reads
a flat problem file so it has nothing to guess.  This module writes that file,
invokes the binary, and returns the same dict shape as the Python engines.

Exactness: the binary uses Cudd_ApaCountMinterm, the arbitrary-precision
count, because Cudd_CountMinterm returns a double and loses precision above
2^53 -- which is inside the range of interest, since a 64-bit key space is
2^64.  The count comes back as a decimal string and is parsed to a Python int.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))

_CANDIDATES = [
    os.environ.get("LOCKCOUNT_CUDD", ""),
    os.path.join(HERE, "..", "..", "c", "engineB_cudd"),   # bundle or repo root c/
    os.path.join(HERE, "..", "c", "engineB_cudd"),         # experiments/c/
    os.path.join(HERE, "engineB_cudd"),                    # beside this file
]


def _find():
    for p in _CANDIDATES:
        if p and os.path.exists(p) and os.access(p, os.X_OK):
            return os.path.abspath(p)
    return os.path.abspath(_CANDIDATES[1])


BINARY = _find()


def available():
    return os.path.exists(BINARY) and os.access(BINARY, os.X_OK)


def write_problem(nl, queries, path):
    with open(path, "w") as fh:
        fh.write("INPUTS %s\n" % " ".join(nl.inputs))
        fh.write("KEYS %s\n" % " ".join(nl.keys))
        fh.write("OUTPUTS %s\n" % " ".join(nl.outputs))
        for out, op, args in nl.gates:
            fh.write("GATE %s %s %s\n" % (out, op, " ".join(args)))
        for x, y in queries:
            xs = "".join(str(x[i]) for i in nl.inputs)
            ys = "".join(str(b) for b in y)
            fh.write("QUERY %s %s\n" % (xs, ys))


def trajectory(nl, queries, node_limit=0, reorder="none", timeout=3600):
    """Exact |V_t| after EVERY prefix of the query list, in one invocation.

    Counting after each query by re-invoking on a growing query list rebuilds
    the whole set every time and is quadratic in the query count.  The C
    engine already holds the correct diagram after each conjunction, so it
    reports the count there and the whole trajectory costs one pass.

    Returns the same dict as version_space, with an extra "traj" list of
    {"t", "count", "acc_nodes"} whose counts are Python ints.
    """
    r = _run(nl, queries, node_limit, reorder, timeout, trajectory=True)
    for row in r.get("traj", []):
        row["count"] = int(row["count"])
    if r.get("count") is not None:
        r["count"] = int(r["count"])
    return r


def version_space(nl, queries, node_limit=0, reorder="none", timeout=600):
    """Exact |V_t| via CUDD.  Returns a dict like the Python engines."""
    r = _run(nl, queries, node_limit, reorder, timeout)
    if r.get("count") is not None:
        r["count"] = int(r["count"])
    return r


def _run(nl, queries, node_limit, reorder, timeout, trajectory=False):
    if not available():
        raise RuntimeError("CUDD engine not built at %s" % BINARY)
    tmp = tempfile.mktemp(suffix=".prob")
    try:
        write_problem(nl, queries, tmp)
        cmd = [BINARY, tmp, "--json", "--reorder", reorder]
        if trajectory:
            cmd += ["--trajectory"]
        if node_limit:
            cmd += ["--node-limit", str(node_limit)]
        p = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout)
        if p.returncode == 3:
            try:
                return json.loads(p.stdout)
            except Exception:
                return {"count": None, "note": "aborted"}
        if p.returncode != 0:
            return {"count": None,
                    "note": "exit %d: %s" % (p.returncode,
                                             p.stderr.strip()[:200])}
        return json.loads(p.stdout)
    except subprocess.TimeoutExpired as e:
        # A trajectory run prints each query's count as it goes, so a run cut
        # off by the timeout still carries every query it did finish.  Closing
        # the open array salvages that rather than throwing the pass away.
        part = e.stdout or ""
        if isinstance(part, bytes):
            part = part.decode("utf-8", "replace")
        if trajectory and part.startswith("{\"traj\": ["):
            try:
                cut = part.rstrip().rstrip(",")
                if cut.endswith("}"):
                    return dict(json.loads(cut + "], \"count\": null}"),
                                note="timeout after %d s" % timeout)
            except Exception:
                pass
        return {"count": None, "note": "timeout"}
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
