# Author: Mitchell A. Thornton
# Copyright (c) 2026 Mitchell A. Thornton
"""j2_verify_v20.py -- machine verification of the J2 tree-factorization theory.

Setting (Thornton/Drechsler/Guenther, VLSI Design 14(1):53-64, 2002):
uniform prior over all 2^(2^n) Boolean functions; modified-Haar spectral
coefficients; the paper's governing expression P[E | matched set] =
1/k(H_i,...,H_q) reduces everything to the counting function k.

Conventions (pinned against the paper's Table IV, n=2):
  s_hat(x) = +1 if f(x)=0, -1 if f(x)=1     (paper: +1 logic-0, -1 logic-1)
  index x = (x_1 ... x_n), x_1 the MSB
  H0            = sum_x s_hat(x)
  node (j, c)   for level j in 1..n and cube c over (x_1..x_{j-1}):
  H(j,c)        = sum_{block(c), x_j=0} s_hat  -  sum_{block(c), x_j=1} s_hat
The 2^n coefficients {H0} u {H(j,c)} are the modified-Haar spectrum; for
n=2 the paper's (H0,H1,H2,H3) = (H0, H(1,()), H(2,(0,)), H(2,(1,))).

Claims verified here:
  V1  the convention reproduces the paper's Table IV exactly (all 16 rows);
  V2  single-coefficient closed form  k(H_i) = 2^(N-m) * C(m,(m+h)/2),
      m = block size  (Vandermonde collapse; paper's Eq. 24), incl. the
      paper's worked example k(H0=2)=4 at n=2 (P[E|S_0]=1/4);
  V3  the tree DP computes k(C) for ARBITRARY constraint sets C -- checked
      exhaustively against brute-force enumeration for n=2,3 (all
      functions) and n=4 (65536 functions), over many random subsets;
  V4  ancestor-closed sets: closed-form product of binomials, incl.
      k(H0,H1) = C(N/2,(N/2+SL)/2)*C(N/2,(N/2+SR)/2) with
      SL=(H0+H1)/2, SR=(H0-H1)/2  -- the closed form for the pair the
      2002 paper could only reach by enumeration (its Eq. 41);
  V5  the paper's independence example k(H2=0, H3=-2)=2, P[E|.]=1/2
      (its Eq. 42), and disjoint-support factorization generally;
  V6  sequential posterior refinement is monotone and hits 1 when all
      coefficients are conditioned (k -> 1).

PYTHONHASHSEED=0; deterministic seeds only.
"""
import itertools
import random
from functools import lru_cache
from math import comb

# ---------------------------------------------------------------- spectrum

def spectrum(fbits, n):
    """All 2^n modified-Haar coefficients of f (tuple of 2^n bits).

    Returns dict: key 'H0' or (j, cube-tuple) -> integer coefficient."""
    N = 1 << n
    s = [1 - 2 * b for b in fbits]              # +1 logic-0, -1 logic-1
    out = {"H0": sum(s)}
    for j in range(1, n + 1):
        blk = 1 << (n - j + 1)                  # block size at level j
        for ci, c in enumerate(itertools.product((0, 1), repeat=j - 1)):
            base = ci * blk
            half = blk >> 1
            out[(j, c)] = sum(s[base:base + half]) - sum(s[base + half:base + blk])
    return out


# ------------------------------------------------------------- brute force

def k_brute(n, constraints):
    """Count functions whose spectrum matches every constrained value."""
    N = 1 << n
    cnt = 0
    for bits in itertools.product((0, 1), repeat=N):
        sp = spectrum(bits, n)
        if all(sp[key] == v for key, v in constraints.items()):
            cnt += 1
    return cnt


# ----------------------------------------------------------------- tree DP

def k_dp(n, constraints):
    """k(C) by the block-sum tree DP.

    State: g_v(S) = #ways to fill block v with +-1 entries summing to S,
    consistent with all constrained coefficients inside subtree(v).
    A node at level j (its coefficient H(j,c)) splits its block into the
    two level-(j+1) half-blocks; H = S_left - S_right, S = S_left + S_right.
    """
    @lru_cache(maxsize=None)
    def g(j, c):
        # block of the first j-1 variables fixed to cube c; size 2^(n-j+1)
        m = 1 << (n - j + 1)
        if j == n + 1:                          # single cell: S in {+1,-1}
            return ((1, 1), (-1, 1))
        key = (j, c)
        h = constraints.get(key)
        gl = dict(g(j + 1, c + (0,)))
        gr = dict(g(j + 1, c + (1,)))
        res = {}
        if h is not None:                       # forced split
            for S in range(-m, m + 1, 2):
                sl, sr = (S + h) // 2, (S - h) // 2
                if (S + h) % 2 == 0 and sl in gl and sr in gr:
                    res[S] = gl[sl] * gr[sr]
        else:                                   # convolution
            for sl, vl in gl.items():
                for sr, vr in gr.items():
                    res[sl + sr] = res.get(sl + sr, 0) + vl * vr
        return tuple(sorted(res.items()))
    root = dict(g(1, ()))
    g.cache_clear()
    if "H0" in constraints:
        return root.get(constraints["H0"], 0)
    return sum(root.values())


# ------------------------------------------------ closed forms (theorems)

def k_single_closed(n, j, h):
    """Vandermonde collapse: one coefficient at level j (block size m).
    k = 2^(N-m) * C(m, (m+h)/2).  For 'H0', m = N and no 2^(N-m) factor
    difference (2^0)."""
    N = 1 << n
    m = N if j == "H0" else 1 << (n - j + 1)
    if (m + h) % 2 or abs(h) > m:
        return 0
    return (1 << (N - m)) * comb(m, (m + h) // 2)


def k_pair_H0_H1_closed(n, h0, h1):
    """Closed form for the pair the 2002 paper reached only by enumeration:
    S_left=(h0+h1)/2, S_right=(h0-h1)/2 forced; product of two binomials."""
    N = 1 << n
    m = N >> 1
    if (h0 + h1) % 2:
        return 0
    sl, sr = (h0 + h1) // 2, (h0 - h1) // 2
    if abs(sl) > m or abs(sr) > m or (m + sl) % 2 or (m + sr) % 2:
        return 0
    return comb(m, (m + sl) // 2) * comb(m, (m + sr) // 2)


def k_ancestor_closed(n, constraints):
    """Closed-form product for ancestor-closed constraint sets containing
    H0: every constrained node's parent chain is constrained too, so all
    skeleton block sums are forced; k = product of C(m_B,(m_B+S_B)/2)
    over the maximal unconstrained fringe blocks."""
    if "H0" not in constraints:
        raise ValueError("needs H0")
    total = 1

    def descend(j, c, S):
        nonlocal total
        m = 1 << (n - j + 1)
        if abs(S) > m or (m + S) % 2:
            total = 0
            return
        key = (j, c)
        if j == n + 1 or key not in constraints:
            total *= comb(m, (m + S) // 2)      # free block
            return
        h = constraints[key]
        if (S + h) % 2:
            total = 0
            return
        descend(j + 1, c + (0,), (S + h) // 2)
        if total:
            descend(j + 1, c + (1,), (S - h) // 2)

    descend(1, (), constraints["H0"])
    return total


# ---------------------------------------------------------------- checks

def main(quick=False):
    rng = random.Random(0)
    fails = 0

    # V1: Table IV of the 2002 paper, n=2 (H0,H1,H2,H3), all 16 functions.
    table_iv = {
        (0, 0, 0, 0): (4, 0, 0, 0),   (0, 0, 0, 1): (2, 2, 0, 2),
        (0, 0, 1, 0): (2, 2, 0, -2),  (0, 0, 1, 1): (0, 4, 0, 0),
        (0, 1, 0, 0): (2, -2, 2, 0),  (0, 1, 0, 1): (0, 0, 2, 2),
        (0, 1, 1, 0): (0, 0, 2, -2),  (0, 1, 1, 1): (-2, 2, 2, 0),
        (1, 0, 0, 0): (2, -2, -2, 0), (1, 0, 0, 1): (0, 0, -2, 2),
        (1, 0, 1, 0): (0, 0, -2, -2), (1, 0, 1, 1): (-2, 2, -2, 0),
        (1, 1, 0, 0): (0, -4, 0, 0),  (1, 1, 0, 1): (-2, -2, 0, 2),
        (1, 1, 1, 0): (-2, -2, 0, -2),(1, 1, 1, 1): (-4, 0, 0, 0),
    }
    ok = True
    for bits, expect in table_iv.items():
        sp = spectrum(bits, 2)
        got = (sp["H0"], sp[(1, ())], sp[(2, (0,))], sp[(2, (1,))])
        if got != expect:
            ok = False
            print("V1 MISMATCH f=%s got=%s expect=%s" % (bits, got, expect))
    print("V1 Table IV (16 rows): %s" % ("PASS" if ok else "FAIL"))
    fails += 0 if ok else 1

    # V2: single-coefficient closed form vs brute, all levels/values, n=2,3;
    #     includes the paper's k(H0=2)=4 at n=2.
    ok = True
    for n in (2, 3):
        keys = ["H0"] + [(j, c) for j in range(1, n + 1)
                         for c in itertools.product((0, 1), repeat=j - 1)]
        for key in keys:
            m = (1 << n) if key == "H0" else 1 << (n - key[0] + 1)
            for h in range(-m, m + 1):
                kb = k_brute(n, {key: h})
                j = "H0" if key == "H0" else key[0]
                kc = k_single_closed(n, j, h)
                kd = k_dp(n, {key: h})
                if not (kb == kc == kd):
                    ok = False
                    print("V2 MISMATCH n=%d %s h=%d brute=%d closed=%d dp=%d"
                          % (n, key, h, kb, kc, kd))
    assert k_single_closed(2, "H0", 2) == 4     # paper: P[E|S_0] = 1/4
    print("V2 single-coefficient closed form (Eq. 24) + k(H0=2)=4: %s"
          % ("PASS" if ok else "FAIL"))
    fails += 0 if ok else 1

    # V3: tree DP vs brute on random constraint subsets, n=2,3,4.
    ok = True
    trials = ({2: 40, 3: 40, 4: 6} if quick else {2: 200, 3: 200, 4: 60})
    for n in (2, 3, 4):
        N = 1 << n
        keys = ["H0"] + [(j, c) for j in range(1, n + 1)
                         for c in itertools.product((0, 1), repeat=j - 1)]
        for t in range(trials[n]):
            q = rng.randint(1, len(keys))
            sub = rng.sample(keys, q)
            if t % 2 == 0:  # satisfiable: values from a real function
                f = tuple(rng.randint(0, 1) for _ in range(N))
                sp = spectrum(f, n)
                cons = {k: sp[k] for k in sub}
            else:           # arbitrary values (often k=0)
                cons = {}
                for k in sub:
                    m = N if k == "H0" else 1 << (n - k[0] + 1)
                    cons[k] = rng.randint(-m // 2, m // 2) * 2 - (m % 2)
            kb, kd = k_brute(n, cons), k_dp(n, cons)
            if kb != kd:
                ok = False
                print("V3 MISMATCH n=%d cons=%s brute=%d dp=%d"
                      % (n, cons, kb, kd))
    print("V3 tree DP == brute force (n=2,3,4; %d random subsets): %s"
          % (sum(trials.values()), "PASS" if ok else "FAIL"))
    fails += 0 if ok else 1

    # V4: ancestor-closed product form, incl. closed-form k(H0,H1).
    ok = True
    for n in (2, 3, 4):
        N = 1 << n
        for _ in range(120):
            f = tuple(rng.randint(0, 1) for _ in range(N))
            sp = spectrum(f, n)
            cons = {"H0": sp["H0"]}
            frontier = [(1, ())]
            while frontier:                     # random downward closure
                j, c = frontier.pop()
                if j > n or rng.random() < 0.4:
                    continue
                cons[(j, c)] = sp[(j, c)]
                frontier.append((j + 1, c + (0,)))
                frontier.append((j + 1, c + (1,)))
            ka = k_ancestor_closed(n, cons)
            kd = k_dp(n, cons)
            if ka != kd or (n < 4 and ka != k_brute(n, cons)):
                ok = False
                print("V4 MISMATCH n=%d cons=%s closed=%d dp=%d"
                      % (n, cons, ka, kd))
        for _ in range(100):                    # the (H0,H1) pair, all n
            f = tuple(rng.randint(0, 1) for _ in range(N))
            sp = spectrum(f, n)
            h0, h1 = sp["H0"], sp[(1, ())]
            kc = k_pair_H0_H1_closed(n, h0, h1)
            kd = k_dp(n, {"H0": h0, (1, ()): h1})
            if kc != kd:
                ok = False
                print("V4 MISMATCH pair n=%d (h0,h1)=(%d,%d) closed=%d dp=%d"
                      % (n, h0, h1, kc, kd))
    print("V4 ancestor-closed product form + closed-form k(H0,H1): %s"
          % ("PASS" if ok else "FAIL"))
    fails += 0 if ok else 1

    # V5: the paper's Eq. 42 example -- k(H2=0, H3=-2) = 2, P = 1/2 -- and
    #     disjoint-support factorization  k(A u B)*2^N = k(A)*k(B) when the
    #     supports are disjoint and neither touches H0.
    k23 = k_dp(2, {(2, (0,)): 0, (2, (1,)): -2})
    ok = (k23 == 2)
    for _ in range(100):
        n = 3
        N = 1 << n
        f = tuple(rng.randint(0, 1) for _ in range(N))
        sp = spectrum(f, n)
        A = {(2, (0,)): sp[(2, (0,))], (3, (0, 0)): sp[(3, (0, 0))]}
        B = {(2, (1,)): sp[(2, (1,))], (3, (1, 1)): sp[(3, (1, 1))]}
        lhs = k_dp(n, {**A, **B}) * (1 << N)
        rhs = k_dp(n, A) * k_dp(n, B)
        if lhs != rhs:
            ok = False
            print("V5 factorization MISMATCH")
    print("V5 paper's k(H2=0,H3=-2)=2 (got %d) + disjoint factorization: %s"
          % (k23, "PASS" if ok else "FAIL"))
    fails += 0 if ok else 1

    # V6: sequential refinement -- P[E|first q coefficients] is monotone
    #     nondecreasing in q and reaches 1 at q = 2^n.
    ok = True
    for _ in range(30):
        n = 4
        N = 1 << n
        f = tuple(rng.randint(0, 1) for _ in range(N))
        sp = spectrum(f, n)
        keys = ["H0"] + [(j, c) for j in range(1, n + 1)
                         for c in itertools.product((0, 1), repeat=j - 1)]
        rng.shuffle(keys)
        prev = 0.0
        cons = {}
        for q, k in enumerate(keys, 1):
            cons[k] = sp[k]
            p = 1.0 / k_dp(n, cons)
            if p + 1e-12 < prev:
                ok = False
                print("V6 non-monotone at q=%d" % q)
            prev = p
        if k_dp(n, cons) != 1:
            ok = False
            print("V6 full conditioning k != 1")
    print("V6 sequential posterior monotone, k(all)=1: %s"
          % ("PASS" if ok else "FAIL"))
    fails += 0 if ok else 1

    print()
    print("RESULT: %s" % ("ALL CHECKS PASS" if fails == 0
                          else "%d CHECK GROUP(S) FAILED" % fails))


if __name__ == "__main__":
    import sys as _s
    # --quick shrinks only the V3 brute-force sweep, which dominates runtime.
    # Every check group still runs; the full sweep is what build.sh runs.
    main(quick="--quick" in _s.argv)
