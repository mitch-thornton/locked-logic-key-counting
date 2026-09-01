#!/usr/bin/env python3
# Author: Mitchell A. Thornton
# Copyright (c) 2026 Mitchell A. Thornton
"""
C.1 -- exact version-space cardinality against the estimators that stand in
for it when the count is intractable.

Under a uniform prior over a finite hypothesis class, k(C) is the cardinality
of the version space consistent with the observations in C, and 1/k(C) is the
posterior on any surviving hypothesis.  This quantity is #P-hard for
essentially every hypothesis class of interest, which is why active learning
works with volume estimates, committee disagreement and greedy bounds rather
than with counts.  The dyadic block-sum query class admits an exact count, so
the standard proxies can be measured against ground truth instead of against
each other.

Three estimators are compared.

  rejection    sample M functions uniformly, keep those consistent with C,
               estimate k = 2^N * hits/M.  The textbook version-space volume
               estimator.
  independence treat the constraints as independent (the B.1 estimator),
               included here for a different reason: as a version-space
               proxy rather than as a credibility metric.
  committee    sample a committee from the version space and use vote
               disagreement on candidate queries to RANK them; compared
               against the exact information-gain ranking rather than against
               k itself, since disagreement estimates informativeness, not size.

Outputs results/c1_results.json.
"""
import json, math, os, random, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "common"))
import haarcount_v1 as hc

SEED = 20260827


def sample_consistent(n, C, rng, tries):
    """Rejection-sample functions consistent with C.  Returns (hits, tries)."""
    hits = []
    for _ in range(tries):
        f = tuple(rng.getrandbits(1) for _ in range(1 << n))
        sp = hc.spectrum(f, n)
        if all(sp[k] == v for k, v in C.items()):
            hits.append(f)
    return hits


def entropy(ps):
    return -sum(p * math.log2(p) for p in ps if p > 0)


def exact_infogain(n, C, q):
    k = hc.k_dp(n, C)
    if k <= 0:
        return None
    m = hc.block_size(n, q)
    ps = []
    for h in range(-m, m + 1, 2):
        Cq = dict(C)
        Cq[q] = h
        kk = hc.k_dp(n, Cq)
        if kk > 0:
            ps.append(kk / k)
    return entropy(ps)


def committee_infogain(n, C, q, committee):
    """Vote-disagreement proxy: entropy of the committee's values for q."""
    if not committee:
        return None
    vals = {}
    for f in committee:
        h = hc.spectrum(f, n)[q]
        vals[h] = vals.get(h, 0) + 1
    tot = sum(vals.values())
    return entropy([c / tot for c in vals.values()])


def spearman(a, b):
    """Rank correlation between two equal-length score lists."""
    def ranks(x):
        order = sorted(range(len(x)), key=lambda i: x[i])
        r = [0.0] * len(x)
        for pos, i in enumerate(order):
            r[i] = pos
        return r
    ra, rb = ranks(a), ranks(b)
    nn = len(a)
    if nn < 2:
        return None
    ma = sum(ra) / nn
    mb = sum(rb) / nn
    num = sum((ra[i] - ma) * (rb[i] - mb) for i in range(nn))
    da = math.sqrt(sum((ra[i] - ma) ** 2 for i in range(nn)))
    db = math.sqrt(sum((rb[i] - mb) ** 2 for i in range(nn)))
    return num / (da * db) if da and db else None


def main():
    rng = random.Random(SEED)
    n = 5
    N = 1 << n
    keys = hc.all_keys(n)
    out = {"seed": SEED, "n": n, "rejection": [], "committee": []}

    # ---- rejection estimator against exact k, as the version space shrinks
    print("=== rejection-sampling estimator vs exact k(C), n=%d ===" % n)
    print("%-5s %14s %10s %12s %12s" %
          ("|C|", "exact k", "log2 k", "M=10^4 est", "rel err"))
    for t in (1, 2, 3, 4, 5, 6, 8):
        rows = []
        for _ in range(12):
            sel = rng.sample(keys, t)
            f = tuple(rng.getrandbits(1) for _ in range(N))
            sp = hc.spectrum(f, n)
            C = {k: sp[k] for k in sel}
            kx = hc.k_dp(n, C)
            if kx <= 0:
                continue
            M = 10000
            hits = sample_consistent(n, C, rng, M)
            est = (2 ** N) * len(hits) / M
            rows.append({"t": t, "exact": kx, "log2_exact": math.log2(kx),
                         "hits": len(hits), "M": M, "estimate": est,
                         "rel_err": (est / kx - 1.0) if kx else None,
                         "zero_hits": len(hits) == 0})
        if not rows:
            continue
        zero = sum(1 for r in rows if r["zero_hits"])
        med_k = sorted(r["exact"] for r in rows)[len(rows) // 2]
        med_lg = math.log2(med_k)
        nz = [r for r in rows if not r["zero_hits"]]
        med_err = (sorted(abs(r["rel_err"]) for r in nz)[len(nz) // 2]
                   if nz else None)
        print("%-5d %14.4g %10.1f %12s %12s   (%d/%d samples got zero hits)" %
              (t, med_k, med_lg,
               "%.3g" % (sorted(r["estimate"] for r in rows)[len(rows) // 2]),
               ("%.1f%%" % (100 * med_err)) if med_err is not None else "n/a",
               zero, len(rows)))
        out["rejection"].append({
            "t": t, "median_exact": med_k, "median_log2_exact": med_lg,
            "zero_hit_fraction": zero / len(rows),
            "median_abs_rel_err": med_err, "trials": len(rows), "M": 10000})

    # ---- committee disagreement as a ranking proxy for information gain
    print("\n=== committee disagreement vs exact information gain (ranking) ===")
    print("%-5s %12s %14s %16s" %
          ("|C|", "committee", "Spearman rho", "top-1 agreement"))
    for t in (1, 2, 3, 4):
        for csize in (8, 32):
            rhos, top1, usable = [], 0, 0
            for _ in range(25):
                sel = rng.sample(keys, t)
                f = tuple(rng.getrandbits(1) for _ in range(N))
                sp = hc.spectrum(f, n)
                C = {k: sp[k] for k in sel}
                if hc.k_dp(n, C) <= 1:
                    continue
                comm = []
                tries = 0
                while len(comm) < csize and tries < 20000:
                    g = tuple(rng.getrandbits(1) for _ in range(N))
                    tries += 1
                    gs = hc.spectrum(g, n)
                    if all(gs[k] == v for k, v in C.items()):
                        comm.append(g)
                if len(comm) < 4:
                    continue
                cand = [q for q in keys if q not in C][:12]
                ex = [exact_infogain(n, C, q) for q in cand]
                cm = [committee_infogain(n, C, q, comm) for q in cand]
                pair = [(a, b) for a, b in zip(ex, cm)
                        if a is not None and b is not None]
                if len(pair) < 3:
                    continue
                usable += 1
                r = spearman([p[0] for p in pair], [p[1] for p in pair])
                if r is not None:
                    rhos.append(r)
                bi_e = max(range(len(pair)), key=lambda i: pair[i][0])
                bi_c = max(range(len(pair)), key=lambda i: pair[i][1])
                if bi_e == bi_c:
                    top1 += 1
            if not rhos:
                continue
            rhos.sort()
            print("%-5d %12d %14.3f %15.0f%%" %
                  (t, csize, rhos[len(rhos) // 2], 100 * top1 / usable))
            out["committee"].append({
                "t": t, "committee_size": csize,
                "median_spearman": rhos[len(rhos) // 2],
                "top1_agreement": top1 / usable, "trials": usable})

    os.makedirs("results", exist_ok=True)
    with open("results/c1_results.json", "w") as fh:
        json.dump(out, fh, indent=1)
    print("\nwrote results/c1_results.json")
    return out


if __name__ == "__main__":
    main()
