#!/usr/bin/env python3
# Author: Mitchell A. Thornton
# Copyright (c) 2026 Mitchell A. Thornton
"""
B.2 -- credibility per revealed constraint.
B.3 -- robustness under adversarial constraint removal.

Setting.  A watermark is embedded as a set of Haar spectral coefficient
constraints.  The strength of an ownership claim substantiated by revealing a
subset C is

    strength(C) = -log2( Pr[an unrelated design satisfies C] )
                = N - log2 k(C)   bits.

Revealing a constraint raises the strength but exposes that constraint to
removal, so the figure of merit in B.2 is strength gained per constraint
revealed.  B.3 measures what survives when an adversary removes a fraction.

The quantity of real interest is the CLAIMED strength (computed by the
independence metric of Kahng et al.) minus the TRUE strength (computed
exactly).  That difference is the number of bits of ownership evidence a
claimant would assert but not possess.

Outputs results/b2_results.json, results/b3_results.json.
"""
import json, math, os, random, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "common"))
import haarcount_v1 as hc

SEED = 20260826


def strength_exact(n, C):
    k = hc.k_dp(n, C)
    if k <= 0:
        return None
    return (1 << n) - math.log2(k)


def strength_indep(n, C):
    """N - log2(k_indep) = -sum_i log2 Pr[coef_i = h_i]."""
    tot = 0.0
    for key, h in C.items():
        p = hc.marginal_prob(n, key, h)
        if p <= 0:
            return None
        tot += -math.log2(p)
    return tot


# ------------------------------------------------------- embedding orders

def order_coarse_first(n, keys):
    """H0, then level 1, then level 2 ...  This is ancestor-closed at every
    prefix, and it is what a designer who wants informative constraints would
    naturally choose, since coarse coefficients carry Theta(n) bits each."""
    def rank(k):
        return (0, 0) if k == "H0" else (k[0], k[1])
    return sorted(keys, key=rank)


def build_hierarchical(n, rng, t):
    """Grow an ancestor-closed constraint set from the root.

    This is what a designer who wants the MOST INFORMATIVE constraints would
    embed: coarse coefficients carry Theta(n) bits each (Proposition 4), so
    filling the tree top-down maximizes evidence per constraint.  It is also,
    by Proposition 5, exactly the maximal-failure regime for the independence
    metric.  The alignment is the point.
    """
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


def order_random(n, keys, rng):
    ks = list(keys)
    rng.shuffle(ks)
    return ks


def order_greedy(n, pool, sp, rng, take):
    """Select and order by maximum EXPECTED information gain (Proposition 2).

    Two things matter here and an earlier version of this file got both wrong.

    (1) The rule must score by expected gain, the Shannon entropy of the
        candidate's conditional value distribution, NOT by the realized
        reduction using the target's actual value.  The latter is clairvoyant
        and is a different algorithm.

    (2) It must be allowed to SELECT from the whole pool, not merely permute a
        pre-chosen subset.  k(C) depends only on the set, so a rule handed
        exactly `take` coefficients has no influence at all on the endpoint,
        and comparing its endpoint against schemes that do select is
        meaningless.
    """
    remaining = list(pool)
    chosen, C = [], {}
    while remaining and len(chosen) < take:
        k0 = hc.k_dp(n, C) if C else (1 << (1 << n))
        best, bestH = None, -1.0
        for q in remaining:
            m = hc.block_size(n, q)
            ps = []
            for h in range(-m, m + 1, 2):
                trial = dict(C)
                trial[q] = h
                kk = hc.k_dp(n, trial)
                if kk > 0:
                    ps.append(kk / k0)
            if not ps:
                continue
            H = -sum(p * math.log2(p) for p in ps if p > 0)
            if H > bestH:
                best, bestH = q, H
        if best is None:
            break
        chosen.append(best)
        C[best] = sp[best]
        remaining.remove(best)
    return chosen


# --------------------------------------------------------------- B.2

def run_b2(n=6, n_embed=10, trials=150, seed=SEED):
    rng = random.Random(seed)
    keys_all = hc.all_keys(n)
    out = {"n": n, "n_embed": n_embed, "trials": trials, "curves": {}}

    schemes = ["hierarchical", "coarse-first", "random", "greedy"]
    acc = {s: {} for s in schemes}

    for _ in range(trials):
        f = tuple(rng.getrandbits(1) for _ in range(1 << n))
        sp = hc.spectrum(f, n)
        pool = rng.sample([k for k in keys_all], min(n_embed * 2, len(keys_all)))

        for s in schemes:
            if s == "hierarchical":
                order = build_hierarchical(n, rng, n_embed)
            elif s == "coarse-first":
                order = order_coarse_first(n, pool)[:n_embed]
            elif s == "random":
                order = order_random(n, pool, rng)[:n_embed]
            else:
                order = order_greedy(n, pool, sp, rng, n_embed)
            C = {}
            for i, k in enumerate(order, start=1):
                C[k] = sp[k]
                se = strength_exact(n, C)
                si = strength_indep(n, C)
                if se is None or si is None:
                    continue
                d = acc[s].setdefault(i, {"exact": [], "indep": [], "gap": []})
                d["exact"].append(se)
                d["indep"].append(si)
                d["gap"].append(si - se)

    for s in schemes:
        rows = []
        for i in sorted(acc[s]):
            d = acc[s][i]
            ex = sorted(d["exact"]); ind = sorted(d["indep"]); gp = sorted(d["gap"])
            rows.append({
                "revealed": i,
                "strength_exact_median": ex[len(ex) // 2],
                "strength_indep_median": ind[len(ind) // 2],
                "overstatement_bits_median": gp[len(gp) // 2],
                "overstatement_bits_p90": gp[int(0.9 * len(gp))],
                "trials": len(ex),
            })
        out["curves"][s] = rows
    return out


# --------------------------------------------------------------- B.3

def run_b3(n=6, n_embed=10, trials=400, seed=SEED + 1):
    rng = random.Random(seed)
    keys_all = hc.all_keys(n)
    fracs = [i / 20 for i in range(0, 21)]
    schemes = ["hierarchical", "coarse-first", "random"]
    acc = {s: {f: [] for f in fracs} for s in schemes}

    for _ in range(trials):
        f = tuple(rng.getrandbits(1) for _ in range(1 << n))
        sp = hc.spectrum(f, n)
        pool = rng.sample(keys_all, min(n_embed * 2, len(keys_all)))
        for s in schemes:
            if s == "hierarchical":
                order = build_hierarchical(n, rng, n_embed)
            elif s == "coarse-first":
                order = order_coarse_first(n, pool)[:n_embed]
            else:
                order = order_random(n, pool, rng)[:n_embed]
            full = list(order)
            for fr in fracs:
                nrem = int(round(fr * len(full)))
                surviving = list(full)
                for _r in range(nrem):
                    if surviving:
                        surviving.pop(rng.randrange(len(surviving)))
                C = {k: sp[k] for k in surviving}
                se = strength_exact(n, C) if C else 0.0
                if se is not None:
                    acc[s][fr].append(se)

    out = {"n": n, "n_embed": n_embed, "trials": trials, "curves": {}}
    for s in schemes:
        rows = []
        for fr in fracs:
            v = sorted(acc[s][fr])
            if not v:
                continue
            rows.append({
                "fraction_removed": fr,
                "strength_median": v[len(v) // 2],
                "strength_p10": v[int(0.1 * len(v))],
                "strength_p90": v[int(0.9 * len(v))],
            })
        out["curves"][s] = rows
    return out


if __name__ == "__main__":
    os.makedirs("results", exist_ok=True)

    print("=== B.2: strength per revealed constraint (n=6, 10 embedded) ===")
    b2 = run_b2()
    with open("results/b2_results.json", "w") as fh:
        json.dump(b2, fh, indent=1)
    print("%-14s %9s %12s %12s %14s" %
          ("order", "revealed", "true bits", "claimed bits", "overstated by"))
    for s, rows in b2["curves"].items():
        for r in rows:
            if r["revealed"] in (1, 2, 3, 5, 8, 10):
                print("%-14s %9d %12.2f %12.2f %12.2f b" %
                      (s, r["revealed"], r["strength_exact_median"],
                       r["strength_indep_median"], r["overstatement_bits_median"]))
        print()

    print("=== B.3: surviving strength under adversarial removal ===")
    b3 = run_b3()
    with open("results/b3_results.json", "w") as fh:
        json.dump(b3, fh, indent=1)
    print("%-14s %10s %14s %16s" %
          ("order", "removed", "strength bits", "[p10, p90]"))
    for s, rows in b3["curves"].items():
        for r in rows:
            if abs(r["fraction_removed"] * 20 % 4) < 1e-9:
                print("%-14s %9.0f%% %14.2f   [%.2f, %.2f]" %
                      (s, 100 * r["fraction_removed"], r["strength_median"],
                       r["strength_p10"], r["strength_p90"]))
        print()
    print("wrote results/b2_results.json, results/b3_results.json")
