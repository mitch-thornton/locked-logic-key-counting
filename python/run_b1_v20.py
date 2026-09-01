#!/usr/bin/env python3
# Author: Mitchell A. Thornton
# Copyright (c) 2026 Mitchell A. Thornton
"""
B.1 -- Exact versus independence-approximated watermark credibility.

Kahng et al. (TCAD 2001) compute the credibility of an ownership claim as a
binomial tail under the assumption that embedded constraints are satisfied
independently, an assumption they describe as "often untrue."  This experiment
measures the error of that assumption when the constraints are Haar spectral
coefficients, which are coupled by nesting.

Reported quantity:  R = k_exact(C) / k_indep(C).
R = 1 means independence holds.  R > 1 means the exact number of designs
satisfying the constraints is LARGER than the estimate, so the true
probability of coincidence is higher and the independence metric OVERSTATES
the strength of the ownership claim.

Outputs results/b1_results.json and the LaTeX table fragments.

Usage: python3 run_b1_v20.py [--quick]
"""
import itertools, json, random, sys, os, time
from math import comb, log2

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "common"))
import haarcount_v1 as hc

SEED = 20260826


# ------------------------------------------------------ structure sampling

def pick_disjoint(n, rng, t):
    pool = [k for k in hc.all_keys(n) if k != "H0"]
    rng.shuffle(pool)
    sel = []
    for k in pool:
        if all(hc.disjoint_subtrees(k, o) for o in sel):
            sel.append(k)
        if len(sel) == t:
            break
    return sel


def pick_ancestor_closed(n, rng, t):
    sel = ["H0"]
    frontier = [(1, ())]
    while len(sel) < t and frontier:
        k = rng.choice(frontier)
        frontier.remove(k)
        sel.append(k)
        j, c = k
        if j < n:
            frontier += [(j + 1, c + (0,)), (j + 1, c + (1,))]
    return sel


def pick_random(n, rng, t):
    keys = hc.all_keys(n)
    return rng.sample(keys, min(t, len(keys)))


def pick_spine(n, rng, t):
    """The extreme nested case: H0 and a single root-to-leaf path."""
    sel = ["H0"]
    c = ()
    j = 1
    while len(sel) < t and j <= n:
        sel.append((j, c))
        c = c + (rng.randint(0, 1),)
        j += 1
    return sel


def pick_bottom_up(n, rng, t):
    """A coefficient together with BOTH of its children, repeatedly.

    This family has zero rooted coefficients yet a nontrivial lattice index,
    and it is the family that refutes the root-path heuristic.  It is included
    precisely because the other four structures cannot detect that failure.
    """
    sel = []
    j = 2                       # build from the TOP, where blocks are large
    c = (rng.randint(0, 1),)    # cube length is j-1
    while len(sel) < t and j < n:
        parent = (j, c)
        kids = [(j + 1, c + (0,)), (j + 1, c + (1,))]
        for k in [parent] + kids:
            if len(sel) < t and k not in sel:
                sel.append(k)
        c = c + (rng.randint(0, 1),)
        j += 1
    return sel


STRUCTURES = [
    ("disjoint", pick_disjoint),
    ("ancestor-closed", pick_ancestor_closed),
    ("spine", pick_spine),
    ("bottom-up", pick_bottom_up),
    ("random", pick_random),
]


def nesting_count(C):
    """Number of ordered pairs (a,b) in C with a a strict ancestor of b.

    This is the structural predictor of independence failure.
    """
    keys = list(C)
    return sum(1 for a in keys for b in keys if hc.is_ancestor(a, b))


# ------------------------------------------------------------------ run

def measure(n, sel, f, sp):
    C = {k: sp[k] for k in sel}
    ke = hc.k_dp(n, C)
    ki = hc.k_independence(n, C)
    if ke == 0 or ki <= 0:
        return None
    return {
        "t": len(C),
        "k_exact": ke,
        "k_indep": ki,
        "R": ke / ki,
        "nest": nesting_count(C),
        "cred_exact": ke / (1 << (1 << n)),
        "cred_indep": ki / (1 << (1 << n)),
        "lattice_index": hc.gap_index(n, C),
        "rooted": hc.rooted_count(C),
    }


def main(quick=False):
    rng = random.Random(SEED)
    ns = (4, 5, 6) if quick else (4, 5, 6, 7)
    trials = 60 if quick else 400
    ts = (2, 3, 4, 5, 6, 8, 10, 12)
    rows = []
    t0 = time.time()

    for n in ns:
        nkeys = len(hc.all_keys(n))
        for sname, pick in STRUCTURES:
            for t in ts:
                if t > nkeys:
                    continue
                acc = []
                for _ in range(trials):
                    sel = pick(n, rng, t)
                    if len(sel) < 2 or len(set(map(str, sel))) != len(sel):
                        continue
                    f = tuple(rng.getrandbits(1) for _ in range(1 << n))
                    sp = hc.spectrum(f, n)
                    m = measure(n, sel, f, sp)
                    if m:
                        m["n"] = n
                        m["structure"] = sname
                        acc.append(m)
                if not acc:
                    continue
                Rs = sorted(x["R"] for x in acc)
                # per-trial ratio of measurement to prediction: the sampler
                # can vary the structure between trials, so comparing a median
                # R against one trial's index would be meaningless
                per_idx = sorted(x["R"] / x["lattice_index"]
                                 for x in acc if x["lattice_index"])
                per_root = sorted(x["R"] / (2.0 ** x["rooted"]) for x in acc)
                rows.append({
                    "n": n, "structure": sname, "t_requested": t,
                    "t_actual": acc[0]["t"], "trials": len(acc),
                    "R_median": Rs[len(Rs) // 2],
                    "R_min": Rs[0], "R_max": Rs[-1],
                    "R_p10": Rs[int(0.10 * len(Rs))],
                    "R_p90": Rs[int(0.90 * len(Rs))],
                    "nest_mean": sum(x["nest"] for x in acc) / len(acc),
                    "gap_law": 2.0 ** (acc[0]["t"] - 1),
                    "lattice_index_first": acc[0]["lattice_index"],
                    "rooted_pred_first": 2.0 ** acc[0]["rooted"],
                    "R_over_index_median": (per_idx[len(per_idx) // 2]
                                            if per_idx else None),
                    "R_over_rooted_median": per_root[len(per_root) // 2],
                    "index_values": sorted({x["lattice_index"] for x in acc
                                            if x["lattice_index"]}),
                })
        print("  n=%d done (%.1fs)" % (n, time.time() - t0)); sys.stdout.flush()

    # ---- lattice index vs the rooted-count heuristic, head to head
    law = []
    for r in rows:
        li = r.get("lattice_index_first")
        if not li:
            continue
        law.append({
            "n": r["n"], "structure": r["structure"], "t": r["t_actual"],
            "R_median": r["R_median"],
            "lattice_index_first": li,
            "index_values": r["index_values"],
            "rel_err_index": r["R_over_index_median"] - 1.0,
            "rel_err_rooted": r["R_over_rooted_median"] - 1.0,
        })

    # ---- does nesting count predict R for random sets?
    rng2 = random.Random(SEED + 1)
    byn = {}
    for n in ns:
        pts = []
        for _ in range(3000 if not quick else 500):
            t = rng2.randint(2, min(10, len(hc.all_keys(n))))
            sel = pick_random(n, rng2, t)
            f = tuple(rng2.getrandbits(1) for _ in range(1 << n))
            sp = hc.spectrum(f, n)
            m = measure(n, sel, f, sp)
            if m:
                pts.append((m["nest"], m["R"]))
        agg = {}
        for nest, R in pts:
            agg.setdefault(nest, []).append(R)
        byn[n] = {str(k): {"count": len(v),
                           "R_median": sorted(v)[len(v) // 2],
                           "R_mean": sum(v) / len(v)}
                  for k, v in sorted(agg.items())}

    out = {"seed": SEED, "quick": quick, "rows": rows,
           "gap_law_check": law, "R_by_nesting": byn,
           "haarcount_version": hc.__version__,
           "elapsed_sec": round(time.time() - t0, 1)}
    os.makedirs("results", exist_ok=True)
    with open("results/b1_results.json", "w") as fh:
        json.dump(out, fh, indent=1)

    # ---------------------------------------------------------- reporting
    print("\n=== R by structure (median, [p10, p90]) ===")
    print("%-4s %-16s %4s %10s %18s %10s" %
          ("n", "structure", "|C|", "R median", "[p10, p90]", "2^(|C|-1)"))
    for r in rows:
        if r["t_actual"] not in (2, 4, 6, 8, 10):
            continue
        print("%-4d %-16s %4d %10.3f  [%7.3f,%7.3f] %10.1f" %
              (r["n"], r["structure"], r["t_actual"], r["R_median"],
               r["R_p10"], r["R_p90"], r["gap_law"]))

    print("\n=== lattice index vs the rooted-count heuristic ===")
    print("%-4s %-16s %4s %11s %9s %10s %10s" %
          ("n", "structure", "|C|", "R median", "indices", "index err", "rooted err"))
    nbad_root = nbad_idx = ntot = 0
    for L in law:
        if L["t"] > 10:
            continue
        ntot += 1
        if abs(L["rel_err_index"]) > 0.25:
            nbad_idx += 1
        if L["rel_err_rooted"] is not None and abs(L["rel_err_rooted"]) > 0.25:
            nbad_root += 1
        if L["n"] == max(x["n"] for x in law):
            print("%-4d %-16s %4d %11.3f %9s %9.1f%% %9.1f%%" %
                  (L["n"], L["structure"], L["t"], L["R_median"],
                   ",".join(str(v) for v in L["index_values"][:3]),
                   100 * L["rel_err_index"], 100 * L["rel_err_rooted"]))
    print("  cells where the LATTICE INDEX is off by >25%%: %d of %d"
          % (nbad_idx, ntot))
    print("  cells where the ROOTED HEURISTIC is off by >25%%: %d of %d"
          % (nbad_root, ntot))

    print("\n=== R vs nesting count, random sets ===")
    for n in ns:
        d = byn[n]
        cells = ["nest=%s: %.2f (%d)" % (k, v["R_median"], v["count"])
                 for k, v in list(d.items())[:7]]
        print("n=%d  %s" % (n, "  ".join(cells)))

    print("\nwrote results/b1_results.json  (%.1fs)" % out["elapsed_sec"])
    return out


if __name__ == "__main__":
    main(quick="--quick" in sys.argv)
