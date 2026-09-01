#!/usr/bin/env python3
# Author: Mitchell A. Thornton
# Copyright (c) 2026 Mitchell A. Thornton
"""
runpar_v20.py -- run the E.2 entropy campaign across cores, one process per
instance, and merge the shards into a single output file.

WHY ONE PROCESS PER INSTANCE

The campaign is embarrassingly parallel at the instance level and nowhere
else.  Each instance has its own netlist, its own query stream from a
per-instance seed, and produces one independent row, so nothing is shared and
there is nothing to synchronise.  Inside one instance there is no parallelism
worth having: a decision-diagram build is pointer-chasing and sequential, and
CUDD is single-threaded.

This launcher therefore does not touch the engines.  It spawns the existing
driver once per instance, N at a time, and merges.  That buys three things a
thread pool would not.  A runaway instance can be killed without taking the
run down.  A CUDD process that grows to tens of gigabytes releases every byte
back to the operating system when it exits.  And the sequential driver stays
the reference implementation, so a parallel run and a serial run execute the
same code on each instance and must agree.

WHAT PARALLELISM BUYS, AND WHEN

It is worth being blunt, because the answer depends on the campaign.  Total
time falls to the makespan, which is the longest single instance, so the gain
is bounded by (total work) / (longest instance).  On a campaign where one
instance dominates a pile of short ones the gain is small no matter how many
cores are available.  On a campaign where many instances run to a wall-clock
limit, which is what running to a plateau produces, the gain approaches the
worker count.  `--plan` prints that bound for the instance list you give it,
using timings from a previous run if it can find them, so the decision is
made on numbers rather than hope.

TWO TIERS

Workers and node caps compete for the same memory.  A hundred-million-node
cap is single-digit gigabytes per worker, so twenty workers wants most of a
128 GB machine, and a cap ten times larger wants a worker count ten times
smaller.  Run wide and shallow over the many instances that finish quickly,
then narrow and deep over the few that are still blocked:

    # wide: everything, small cap, all cores
    python3 runpar_v20.py --only-file all.txt --workers 20 \\
        --node-cap 8000000 --out results/phase6_wide.json

    # deep: the stragglers, huge cap, two at a time
    python3 runpar_v20.py --only c432-RN640,c1908-SL1280 --workers 2 \\
        --node-cap 2000000000 --wall 21600 --out results/phase6_deep2.json

RESUMPTION

Instances already present in --out are skipped, exactly as the sequential
driver skips them, so an interrupted run is continued by repeating the
command.  Shards live in <out>.shards/ and are merged after every instance
finishes, so killing the launcher never loses completed work.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DRIVER = os.path.join(HERE, "run_e2_v20.py")


def say(msg):
    print(msg, flush=True)


def hhmm(s):
    m, s = divmod(int(s), 60)
    h, m = divmod(m, 60)
    return "%d:%02d:%02d" % (h, m, s) if h else "%d:%02d" % (m, s)


def load_rows(path):
    if not os.path.exists(path):
        return []
    try:
        return json.load(open(path)).get("rows", [])
    except Exception:
        return []


def prior_timings(bundle_root):
    """Seconds per instance from any campaign output already on disk.

    Only used for --plan.  A missing instance simply has no estimate.
    """
    out = {}
    res = os.path.join(HERE, "results")
    for f in sorted(os.listdir(res)) if os.path.isdir(res) else []:
        if not f.startswith("phase6") or not f.endswith(".json"):
            continue
        for r in load_rows(os.path.join(res, f)):
            if r.get("seconds"):
                out.setdefault(r["benchmark"], r["seconds"])
    return out


def plan(names, workers, timings):
    known = [(timings[n], n) for n in names if n in timings]
    unknown = [n for n in names if n not in timings]
    say("  %d instance(s), %d with a timing from a previous run"
        % (len(names), len(known)))
    if not known:
        say("  no previous timings, so no estimate; run it and find out")
        return
    total = sum(s for s, _ in known)
    longest, who = max(known)
    bins = [0.0] * max(1, workers)
    for s, _ in sorted(known, reverse=True):
        i = bins.index(min(bins))
        bins[i] += s
    makespan = max(max(bins), longest)
    say("  total work %s, longest single instance %s (%s)"
        % (hhmm(total), hhmm(longest), who))
    say("  serial      %s" % hhmm(total))
    say("  %2d workers  %s   speedup %.1fx" % (workers, hhmm(makespan),
                                               total / makespan))
    if makespan <= longest * 1.001:
        say("  the makespan is one instance, so more workers than %d buy "
            "nothing here" % max(1, int(total // longest)))
    if unknown:
        say("  %d instance(s) have no prior timing and are not in the "
            "estimate" % len(unknown))


def child_argv(args, name, shard):
    a = [sys.executable, DRIVER, "--phase", "entropy",
         "--bench-dir", args.bench_dir, "--only", name, "--out", shard]
    for flag, val in (("--seed", args.seed), ("--tmax", args.tmax),
                      ("--plateau", args.plateau),
                      ("--node-cap", args.node_cap),
                      ("--timeout", args.timeout), ("--wall", args.wall),
                      ("--engine", args.engine)):
        if val is not None:
            a += [flag, str(val)]
    if args.until_plateau:
        a += ["--until-plateau"]
    return a


def merge(out, shard_dir, header):
    rows = {r["benchmark"]: r for r in load_rows(out)}
    for f in sorted(os.listdir(shard_dir)):
        if not f.endswith(".json"):
            continue
        for r in load_rows(os.path.join(shard_dir, f)):
            rows[r["benchmark"]] = r
    doc = dict(header)
    doc["rows"] = [rows[k] for k in sorted(rows)]
    tmp = out + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(doc, fh, indent=1)
    os.replace(tmp, out)
    return len(doc["rows"])


def main():
    ap = argparse.ArgumentParser(
        description="Run the E.2 entropy campaign one process per instance.")
    ap.add_argument("--only", help="comma-separated instance names")
    ap.add_argument("--only-file", help="file with one instance name per line, "
                                        "or a comma-separated list")
    ap.add_argument("--bench-dir", required=True)
    ap.add_argument("--out", help="merged output file; not needed with --plan")
    ap.add_argument("--workers", type=int, default=0,
                    help="processes to run at once; 0 means one per CPU, "
                         "capped at the instance count")
    ap.add_argument("--plan", action="store_true",
                    help="print the schedule and the speedup bound, run "
                         "nothing")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--tmax", type=int)
    ap.add_argument("--until-plateau", action="store_true")
    ap.add_argument("--plateau", type=int)
    ap.add_argument("--node-cap", type=int)
    ap.add_argument("--timeout", type=float)
    ap.add_argument("--wall", type=float)
    ap.add_argument("--engine", choices=("cudd", "bdd-py"))
    ap.add_argument("--deadline", type=float, default=0,
                    help="stop launching new instances after this many "
                         "seconds; those already running are left to finish")
    args = ap.parse_args()

    names = []
    if args.only:
        names += [x.strip() for x in args.only.split(",") if x.strip()]
    if args.only_file:
        txt = open(args.only_file).read()
        names += [x.strip() for x in txt.replace(",", "\n").split("\n")
                  if x.strip()]
    seen, ordered = set(), []
    for n in names:
        if n not in seen:
            seen.add(n)
            ordered.append(n)
    names = ordered
    if not names:
        sys.exit("give --only or --only-file")

    workers = args.workers or (os.cpu_count() or 1)
    workers = max(1, min(workers, len(names)))

    if args.plan:
        say("plan for %d instance(s) at %d worker(s)" % (len(names), workers))
        plan(names, workers, prior_timings(HERE))
        return 0

    if not args.out:
        sys.exit("--out is required unless you pass --plan")
    done = {r["benchmark"] for r in load_rows(args.out)}
    todo = [n for n in names if n not in done]
    if done:
        say("%d instance(s) already in %s, skipping them"
            % (len(done & set(names)), args.out))
    if not todo:
        say("nothing to do")
        return 0

    shard_dir = args.out + ".shards"
    os.makedirs(shard_dir, exist_ok=True)
    header = {"note": "parallel launch; rows are produced by the same "
                      "sequential driver, one process per instance",
              "seed": args.seed, "workers": workers,
              "launcher": "runpar_v20.py"}

    say("%d instance(s), %d worker(s)" % (len(todo), workers))
    t0 = time.time()
    running = {}          # popen -> (name, shard, started)
    queue = list(todo)
    finished = failed = 0

    try:
        while queue or running:
            while (queue and len(running) < workers
                   and (not args.deadline
                        or time.time() - t0 < args.deadline)):
                name = queue.pop(0)
                shard = os.path.join(shard_dir, "%s.json" % name)
                log = os.path.join(shard_dir, "%s.log" % name)
                fh = open(log, "w")
                p = subprocess.Popen(child_argv(args, name, shard), cwd=HERE,
                                     stdout=fh, stderr=subprocess.STDOUT)
                running[p] = (name, shard, time.time(), fh)
                say("  [%2d running] start %s" % (len(running), name))

            if not running:
                break
            time.sleep(0.5)
            for p in [q for q in running if q.poll() is not None]:
                name, shard, started, fh = running.pop(p)
                fh.close()
                dt = time.time() - started
                if p.returncode == 0 and os.path.exists(shard):
                    finished += 1
                    rows = load_rows(shard)
                    r = rows[0] if rows else {}
                    say("  [%2d running] done  %-16s %s  log2V=%s %s"
                        % (len(running), name, hhmm(dt),
                           r.get("log2_V"), r.get("note") or ""))
                else:
                    failed += 1
                    say("  [%2d running] FAIL  %-16s %s  exit %s, see %s.log"
                        % (len(running), name, hhmm(dt), p.returncode,
                           os.path.join(shard_dir, name)))
                merge(args.out, shard_dir, header)
    except KeyboardInterrupt:
        say("interrupted; terminating %d child process(es)" % len(running))
        for p in running:
            p.terminate()
        for p in running:
            try:
                p.wait(timeout=10)
            except Exception:
                p.kill()
        merge(args.out, shard_dir, header)
        say("what finished is in %s; repeat the command to continue"
            % args.out)
        return 130

    n = merge(args.out, shard_dir, header)
    say("%d finished, %d failed, %s wall clock, %d row(s) in %s"
        % (finished, failed, hhmm(time.time() - t0), n, args.out))
    if failed == 0:
        shutil.rmtree(shard_dir, ignore_errors=True)
    else:
        say("shards kept in %s because %d instance(s) failed"
            % (shard_dir, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
