#!/usr/bin/env python3
# Author: Mitchell A. Thornton
# Copyright (c) 2026 Mitchell A. Thornton
"""
E.1 -- exact key version-space counting for locked netlists, and the width
that decides whether it is affordable.

This is the feasibility measurement, run before any downstream claim is built
on it.  Three questions, in order.

E.1.0  Is the counter correct?  Checked against exhaustive enumeration over
       all 2^|K| keys.

E.1.1  How wide is the problem?  Two widths are reported for every instance.
       The KEY-MORAL width is the induced width of the graph over key
       variables alone, after the internal signals have been eliminated.  It
       is the object one would reason about if one thought of the key
       constraints directly, and for an interference-maximizing or
       point-function lock it is a near-clique on all key bits.  The FACTOR
       width is the induced width of the gate-level factor graph with the
       internal signals retained.  It is what the computation actually pays.
       The gap between the two is the reason exact counting is possible.

E.1.2  What does the version space do as queries accumulate?  |V_t| is
       reported exactly, per query, per scheme.

Outputs results/e1_results.json.
"""
import json, os, random, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lockkit_v20 as lk

SEED = 20260827


def validate(rng, trials=(("adder", 3), ("cmp", 4), ("mult", 3))):
    bad = tested = 0
    for bench, size in trials:
        nl = lk.BENCHES[bench](size)
        for scheme in ("rll", "sll", "point"):
            for nk in (3, 4, 5):
                m = lk.LOCKS[scheme](nl, nk, rng)
                if len(m.keys) < 2:
                    continue
                sk = lk.correct_key(m, rng)
                xs, ys = [], []
                for _ in range(3):
                    x = {i: rng.randint(0, 1) for i in m.inputs}
                    a = dict(x)
                    a.update(sk)
                    xs.append(x)
                    ys.append(m.simulate(a))
                    exact, _, _ = lk.version_space(m, xs, ys)
                    tested += 1
                    if exact != lk.count_brute(m, xs, ys):
                        bad += 1
    print("  exact count == brute force (%d cases): %s"
          % (tested, "ok" if bad == 0 else "FAIL (%d)" % bad))
    return {"cases": tested, "mismatches": bad}


def widths(rng, out):
    print("\nE.1.1  width of the key-constraint system")
    print("  %-9s %-6s %4s %3s %10s %9s" %
          ("bench", "lock", "K", "t", "key-moral", "factor"))
    for bench, size in (("mult", 4), ("adder", 8), ("cmp", 8)):
        nl = lk.BENCHES[bench](size)
        for scheme in ("rll", "sll", "point"):
            for nk in (8, 16, 24, 32):
                m = lk.LOCKS[scheme](nl, nk, rng)
                if len(m.keys) < 2:
                    continue
                sk = lk.correct_key(m, rng)
                xs, ys = [], []
                for t in range(1, 5):
                    x = {i: rng.randint(0, 1) for i in m.inputs}
                    a = dict(x)
                    a.update(sk)
                    xs.append(x)
                    ys.append(m.simulate(a))
                cnt, fw, kw = lk.version_space(m, xs, ys, cap=1 << 22)
                row = {"bench": "%s%d" % (bench, size), "lock": scheme,
                       "K": len(m.keys), "t": len(xs),
                       "factor_width": fw, "key_moral_width": kw,
                       "V_t": cnt}
                out["widths"].append(row)
                print("  %-9s %-6s %4d %3d %10d %9d"
                      % (row["bench"], scheme, row["K"], row["t"], kw, fw))
                sys.stdout.flush()


def trajectory(rng, out):
    print("\nE.1.2  version-space trajectory, |V_t| by query count")
    for bench, size in (("adder", 8),):
        nl = lk.BENCHES[bench](size)
        for scheme in ("rll", "sll", "point"):
            m = lk.LOCKS[scheme](nl, 24, rng)
            if len(m.keys) < 2:
                continue
            sk = lk.correct_key(m, rng)
            xs, ys, traj = [], [], []
            for t in range(1, 13):
                x = {i: rng.randint(0, 1) for i in m.inputs}
                a = dict(x)
                a.update(sk)
                xs.append(x)
                ys.append(m.simulate(a))
                t0 = time.time()
                cnt, fw, kw = lk.version_space(m, xs, ys, cap=1 << 22)
                el = time.time() - t0
                traj.append({"t": t, "V_t": cnt, "factor_width": fw,
                             "seconds": round(el, 3)})
                if cnt is None or el > 20:
                    break
            out["trajectory"].append(
                {"bench": "%s%d" % (bench, size), "lock": scheme,
                 "K": len(m.keys), "points": traj})
            shown = ", ".join("%s" % p["V_t"] for p in traj[:8])
            print("  %-6s K=%d  |V_t| = %s ..." % (scheme, len(m.keys), shown))
            sys.stdout.flush()


def main():
    rng = random.Random(SEED)
    out = {"seed": SEED, "widths": [], "trajectory": [],
           "note": "generated benchmark netlists; locking schemes are "
                   "reimplementations in the style of RLL, SLL and the "
                   "point-function family, not the original authors' code"}
    print("E.1.0  validation")
    out["validation"] = validate(rng)
    widths(rng, out)
    trajectory(rng, out)
    os.makedirs("results", exist_ok=True)
    with open("results/e1_results.json", "w") as fh:
        json.dump(out, fh, indent=1)
    print("\nwrote results/e1_results.json")
    return out


if __name__ == "__main__":
    main()
