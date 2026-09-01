#!/usr/bin/env python3
# Author: Mitchell A. Thornton
# Copyright (c) 2026 Mitchell A. Thornton
"""
C.2 -- greedy information-gain ordering against the exhaustively optimal one.

Because k(C) is exact and cheap, the OPTIMAL adaptive query policy can be
computed by dynamic programming over constraint states for small n, and the
greedy policy of Proposition 2 can be measured against it rather than bounded.
The known bounds for greedy on this problem are worst case: O(log n)
approximate with matching hardness (Chakaravarthy et al.), tightly
Theta(log n / log OPT) under a uniform prior (Li, Liang and Mussmann).  This
experiment asks what the gap actually is on the dyadic block-sum query class.

Objective.  Identify the function exactly, that is drive k(C) to 1.  The cost
of a policy is the expected number of coefficient queries, the expectation
taken over a uniformly drawn target function, which is exactly the
k-weighted average the DP computes.

Policies compared:
  optimal        exhaustive DP over constraint states
  greedy         query the maximum-entropy available coefficient (Prop. 2)
  coarse-first   H0, then level 1, then level 2, ... (a fixed order)
  random         a uniformly random fixed order, averaged over draws
  finest-first   the reverse of coarse-first, included as a control

Outputs results/c2_results.json.
"""
import json, math, os, sys, time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "common"))
import haarcount_v1 as hc

SEED = 20260827


def outcomes(n, C, q, k):
    """(value, count, probability) for querying q in state C with |VS| = k."""
    m = hc.block_size(n, q)
    out = []
    for h in range(-m, m + 1, 2):
        Cq = dict(C)
        Cq[q] = h
        kk = hc.k_dp(n, Cq)
        if kk > 0:
            out.append((h, kk, kk / k))
    return out


def optimal_cost(n, cap=3_000_000):
    """Expected queries under the optimal adaptive policy, by DP."""
    keys = hc.all_keys(n)
    memo = {}

    def rec(C, k):
        if k <= 1:
            return 0.0
        if C in memo:
            return memo[C]
        if len(memo) > cap:
            raise RuntimeError("state cap exceeded")
        used = {a for a, _ in C}
        best = float("inf")
        for q in keys:
            if q in used:
                continue
            tot = 0.0
            any_out = False
            for h, kk, p in outcomes(n, dict(C), q, k):
                any_out = True
                tot += p * rec(C | {(q, h)}, kk)
            if any_out:
                best = min(best, 1.0 + tot)
        memo[C] = best
        return best

    root = frozenset()
    return rec(root, 1 << (1 << n)), len(memo)


def entropy(ps):
    return -sum(p * math.log2(p) for p in ps if p > 0)


def policy_cost(n, choose, cap=3_000_000):
    """Expected queries under an adaptive policy given by `choose(n, C, k)`."""
    memo = {}

    def rec(C, k):
        if k <= 1:
            return 0.0
        if C in memo:
            return memo[C]
        if len(memo) > cap:
            raise RuntimeError("state cap exceeded")
        q = choose(n, dict(C), k)
        if q is None:
            memo[C] = float("inf")
            return memo[C]
        tot = 0.0
        for h, kk, p in outcomes(n, dict(C), q, k):
            tot += p * rec(C | {(q, h)}, kk)
        memo[C] = 1.0 + tot
        return memo[C]

    return rec(frozenset(), 1 << (1 << n)), len(memo)


def make_greedy():
    def choose(n, C, k):
        best, bestH = None, -1.0
        for q in hc.all_keys(n):
            if q in C:
                continue
            ps = [p for _, _, p in outcomes(n, C, q, k)]
            if not ps:
                continue
            H = entropy(ps)
            if H > bestH:
                best, bestH = q, H
        return best
    return choose


def make_fixed(order):
    def choose(n, C, k):
        for q in order:
            if q not in C:
                return q
        return None
    return choose


def coarse_order(n):
    return hc.all_keys(n)


def finest_order(n):
    return list(reversed(hc.all_keys(n)))


def main():
    import random
    rng = random.Random(SEED)
    out = {"seed": SEED, "objective": "expected queries to reach k=1",
           "rows": []}
    for n in (2, 3):
        t0 = time.time()
        opt, nstates = optimal_cost(n)
        row = {"n": n, "optimal": opt, "optimal_states": nstates,
               "n_coefficients": len(hc.all_keys(n))}
        g, _ = policy_cost(n, make_greedy())
        row["greedy"] = g
        cf, _ = policy_cost(n, make_fixed(coarse_order(n)))
        row["coarse_first"] = cf
        ff, _ = policy_cost(n, make_fixed(finest_order(n)))
        row["finest_first"] = ff
        rnds = []
        for _ in range(8):
            o = hc.all_keys(n)[:]
            rng.shuffle(o)
            rc, _ = policy_cost(n, make_fixed(o))
            rnds.append(rc)
        row["random_mean"] = sum(rnds) / len(rnds)
        row["random_min"] = min(rnds)
        row["random_max"] = max(rnds)
        for kk in ("greedy", "coarse_first", "finest_first", "random_mean"):
            row[kk + "_ratio"] = row[kk] / opt
        row["seconds"] = round(time.time() - t0, 1)
        out["rows"].append(row)
        print("n=%d  optimal=%.4f  greedy=%.4f (%.4fx)  coarse=%.4f (%.4fx)  "
              "finest=%.4f (%.4fx)  random=%.4f (%.4fx)  [%d states, %.1fs]"
              % (n, opt, g, row["greedy_ratio"], cf, row["coarse_first_ratio"],
                 ff, row["finest_first_ratio"], row["random_mean"],
                 row["random_mean_ratio"], nstates, row["seconds"]))
        sys.stdout.flush()

    os.makedirs("results", exist_ok=True)
    with open("results/c2_results.json", "w") as fh:
        json.dump(out, fh, indent=1)
    print("\nwrote results/c2_results.json")
    return out


if __name__ == "__main__":
    main()
