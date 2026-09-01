#!/usr/bin/env python3
# Author: Mitchell A. Thornton
# Copyright (c) 2026 Mitchell A. Thornton
"""
haarcount v1 -- exact counting under partial Haar spectral evidence.

The reference (Python) half of the eventual C/Python parity pair.  Pure
integer arithmetic throughout, so it ports to C without floating point in the
counting path.  Floating point appears only in the independence estimate and
the local-CLT approximation, which are diagnostics, not part of k(C).

Coefficient keys
----------------
  "H0"        the global sum, a signed sum over all N = 2^n cells
  (j, c)      j in 1..n, c a tuple of length j-1 over {0,1}
              H(j,c) = S(j+1, c0) - S(j+1, c1)
              block B(j,c) has size m = 2^(n-j+1)

Public API
----------
  spectrum(fbits, n)              all 2^n coefficients of a truth table
  k_dp(n, C)                      exact count, arbitrary C, O(4^n), |C|-free
  k_brute(n, C)                   exhaustive check, n <= 4 only
  k_single_closed(n, key, h)      Theorem 2
  k_pair_closed(n, h0, h1)        Theorem 3
  k_ancestor_closed(n, C)         Theorem 4 (ancestor-closed C)
  marginal_count(n, key, h)       # functions with that one coefficient value
  marginal_prob(n, key, h)        that count / 2^N
  k_independence(n, C)            2^N * prod_i Pr[coef_i = h_i]
  fringe(n, C)                    maximal blocks free of conditioned coefs
  forced_block_sums(n, C)         block sums forced by an ancestor-closed C
  gap_law_prediction(C)           2^(|C|-1), the ancestor-closed limit
  projection_norm_haar(n, C)      Parseval check, Haar basis
  projection_norm_fringe(n, C)    Parseval check, fringe-indicator basis
"""
from __future__ import annotations
import itertools
from functools import lru_cache
from math import comb

__version__ = "1.0"


# ------------------------------------------------------------------ keys

def all_keys(n):
    """Every coefficient key, coarse to fine."""
    keys = ["H0"]
    for j in range(1, n + 1):
        for c in itertools.product((0, 1), repeat=j - 1):
            keys.append((j, c))
    return keys


def block_size(n, key):
    """Number of cells the coefficient's value is a signed sum over."""
    if key == "H0":
        return 1 << n
    j, _ = key
    return 1 << (n - j + 1)


def block_span(n, key):
    """(start, length) of the cell range the coefficient reads."""
    if key == "H0":
        return 0, 1 << n
    j, c = key
    m = 1 << (n - j + 1)
    idx = 0
    for bit in c:
        idx = (idx << 1) | bit
    return idx * m, m


def is_ancestor(a, b):
    """True if key a lies strictly on the root path above key b."""
    if a == b:
        return False
    if a == "H0":
        return True
    if b == "H0":
        return False
    ja, ca = a
    jb, cb = b
    return ja < jb and cb[:ja - 1] == ca


def disjoint_subtrees(a, b):
    """True if a and b constrain non-overlapping cell ranges."""
    return _disjoint(a, b)


def _disjoint(a, b):
    if a == "H0" or b == "H0":
        return False
    ja, ca = a
    jb, cb = b
    L = min(ja, jb) - 1
    return ca[:L] != cb[:L]


def is_ancestor_closed(C):
    """True if every constrained key has all its ancestors constrained."""
    keys = set(C)
    if "H0" not in keys:
        return False
    for k in keys:
        if k == "H0":
            continue
        j, c = k
        for jj in range(1, j):
            if (jj, c[:jj - 1]) not in keys:
                return False
    return True


# -------------------------------------------------------------- spectrum

def spectrum(fbits, n):
    """All 2^n modified-Haar coefficients of a truth table (tuple of bits).

    Convention: logic-0 maps to +1.
    """
    N = 1 << n
    s = [1 - 2 * b for b in fbits]
    out = {"H0": sum(s)}
    for j in range(1, n + 1):
        blk = 1 << (n - j + 1)
        half = blk >> 1
        for ci, c in enumerate(itertools.product((0, 1), repeat=j - 1)):
            base = ci * blk
            out[(j, c)] = sum(s[base:base + half]) - sum(s[base + half:base + blk])
    return out


def block_sums(fbits, n):
    """S(j,c) for every block, keyed (j,c) with c of length j-1, plus root."""
    s = [1 - 2 * b for b in fbits]
    out = {}
    for j in range(1, n + 2):
        blk = 1 << (n - j + 1)
        for ci, c in enumerate(itertools.product((0, 1), repeat=j - 1)):
            base = ci * blk
            out[(j, c)] = sum(s[base:base + blk])
    return out


# --------------------------------------------------- convolution backend

# The DP step at an unconstrained node is a convolution of the two child
# census sequences.  Performed entry by entry that is O(m^2) at a node of
# block size m, and since level m holds N/m nodes the total is
# O(N^2) = O(4^n), which is the bound reported in the paper through v8.
# It is not tight.
#
# Convolution of two nonnegative integer sequences can be done with a single
# big-integer multiplication: pack each sequence into one integer using a
# fixed field width wide enough that no product coefficient can carry into
# the next field, multiply, then unpack.  CPython's integer multiplication is
# Karatsuba above a small threshold, so this is sub-quadratic in the packed
# length and the arithmetic total becomes O(N log^2 N).
#
# The packed path is exact.  It is integer arithmetic throughout, with no
# floating point and no rounding, and the field width is chosen so that
# unpacking recovers the coefficients identically.  It is used only when both
# child sequences are dense in their parity class, which is the common case;
# when constraints have pruned a child to a sparse support the entry-by-entry
# path is both correct and faster, so it is kept as the fallback.

CONV_PACK_MIN = 48   # shortest sequence length at which packing pays


def _as_dense(d):
    """(start, [values]) if the keys of `d` are an arithmetic run of step 2."""
    if not d:
        return None
    ks = sorted(d)
    if len(ks) == 1:
        return ks[0], [d[ks[0]]]
    if ks[-1] - ks[0] != 2 * (len(ks) - 1):
        return None
    return ks[0], [d[k] for k in ks]


def _conv_packed(a, b):
    """Convolve two nonnegative integer lists via one big-integer multiply."""
    na, nb = len(a), len(b)
    ma, mb = max(a), max(b)
    if ma == 0 or mb == 0:
        return [0] * (na + nb - 1)
    # No coefficient of the product exceeds ma*mb*min(na,nb); one extra bit
    # of headroom guarantees the fields never carry into one another.
    width = (ma * mb * min(na, nb)).bit_length() + 1
    A = 0
    for x in reversed(a):
        A = (A << width) | x
    B = 0
    for x in reversed(b):
        B = (B << width) | x
    P = A * B
    mask = (1 << width) - 1
    out = []
    for _ in range(na + nb - 1):
        out.append(P & mask)
        P >>= width
    return out


def _convolve(gl, gr):
    """Census convolution for an unconstrained node.  Returns a dict."""
    dl = _as_dense(gl)
    dr = _as_dense(gr)
    if (dl is not None and dr is not None
            and min(len(dl[1]), len(dr[1])) >= CONV_PACK_MIN):
        s0 = dl[0] + dr[0]
        return {s0 + 2 * i: v
                for i, v in enumerate(_conv_packed(dl[1], dr[1])) if v}
    res = {}
    for sl, vl in gl.items():
        for sr, vr in gr.items():
            res[sl + sr] = res.get(sl + sr, 0) + vl * vr
    return res


# ------------------------------------------------------------------- DP

def k_dp(n, C):
    """Exact k(C) for an arbitrary conditioned set, by the block-sum tree DP.

    g_v(S) = number of fillings of block v summing to S that are consistent
    with every conditioned coefficient inside subtree(v).  A conditioned
    coefficient forces the split; an unconditioned one convolves the children.
    Independent of |C|, since constraints only prune states.

    Cost is O(N log^2 N) arithmetic operations on integers of at most N bits,
    N = 2^n, using the packed convolution above.  Entry-by-entry convolution
    gives the O(N^2) = O(4^n) bound reported through v8.
    """
    C = dict(C)

    @lru_cache(maxsize=None)
    def g(j, c):
        m = 1 << (n - j + 1)
        if j == n + 1:
            return ((-1, 1), (1, 1))
        h = C.get((j, c))
        gl = dict(g(j + 1, c + (0,)))
        gr = dict(g(j + 1, c + (1,)))
        if h is not None:
            res = {}
            for S in range(-m, m + 1, 2):
                if (S + h) % 2:
                    continue
                sl, sr = (S + h) // 2, (S - h) // 2
                if sl in gl and sr in gr:
                    v = gl[sl] * gr[sr]
                    if v:
                        res[S] = v
        else:
            res = _convolve(gl, gr)
        return tuple(sorted(res.items()))

    root = dict(g(1, ()))
    g.cache_clear()
    if "H0" in C:
        return root.get(C["H0"], 0)
    return sum(root.values())


def k_brute(n, C):
    """Exhaustive count.  Only tractable for n <= 4 (2^16 functions)."""
    if n > 4:
        raise ValueError("k_brute is only tractable for n <= 4")
    N = 1 << n
    cnt = 0
    for bits in itertools.product((0, 1), repeat=N):
        sp = spectrum(bits, n)
        if all(sp[k] == v for k, v in C.items()):
            cnt += 1
    return cnt


# --------------------------------------------------------- closed forms

def k_single_closed(n, key, h):
    """Theorem 2: a single conditioned coefficient."""
    N = 1 << n
    m = block_size(n, key)
    if (m + h) % 2 or abs(h) > m:
        return 0
    c = comb(m, (m + h) // 2)
    return c if key == "H0" else (1 << (N - m)) * c


def k_pair_closed(n, h0, h1):
    """Theorem 3: the pair (H0, H(1,()))."""
    N = 1 << n
    if (h0 + h1) % 2 or (h0 - h1) % 2:
        return 0
    sL, sR = (h0 + h1) // 2, (h0 - h1) // 2
    half = N // 2
    for s in (sL, sR):
        if (half + s) % 2 or abs(s) > half:
            return 0
    return comb(half, (half + sL) // 2) * comb(half, (half + sR) // 2)


def forced_block_sums(n, C):
    """Block sums forced by an ancestor-closed C, by iterating Lemma 1.

    Returns dict (j,c) -> S for every block on or below the constraint
    skeleton, or None on a parity failure.  Raises if C is not
    ancestor-closed.
    """
    if not is_ancestor_closed(C):
        raise ValueError("C is not ancestor-closed")
    S = {(1, ()): C["H0"]}
    for j in range(1, n + 1):
        for c in itertools.product((0, 1), repeat=j - 1):
            key = (j, c)
            if key not in C:
                continue
            parent = S.get(key)
            if parent is None:
                continue
            h = C[key]
            if (parent + h) % 2:
                return None
            S[(j + 1, c + (0,))] = (parent + h) // 2
            S[(j + 1, c + (1,))] = (parent - h) // 2
    return S


def fringe(n, C):
    """Maximal blocks whose subtree contains no conditioned coefficient.

    Returned as a list of (j, c) block identifiers, where the block B(j,c)
    has size 2^(n-j+1) and covers the cells given by block_span.
    """
    out = []
    stack = [(1, ())]
    keys = set(C)
    while stack:
        j, c = stack.pop()
        # does the subtree rooted at this block contain a conditioned coef?
        has = False
        for k in keys:
            if k == "H0":
                continue
            jj, cc = k
            if jj >= j and cc[:j - 1] == c:
                has = True
                break
        if not has or j == n + 1:
            out.append((j, c))
        else:
            stack.append((j + 1, c + (0,)))
            stack.append((j + 1, c + (1,)))
    return sorted(out)


def k_ancestor_closed(n, C):
    """Theorem 4: product of binomials over the fringe."""
    S = forced_block_sums(n, C)
    if S is None:
        return 0
    total = 1
    for (j, c) in fringe(n, C):
        m = 1 << (n - j + 1)
        s = S.get((j, c))
        if s is None:
            raise RuntimeError("fringe block has no forced sum")
        if (m + s) % 2 or abs(s) > m:
            return 0
        total *= comb(m, (m + s) // 2)
    return total


# -------------------------------------------- marginals and independence

def marginal_count(n, key, h):
    """Number of functions whose coefficient `key` equals h.

    H(j,c) = S_left - S_right over two half-blocks of m/2 cells each.  Since
    -S_right has the same distribution as S_right, H is distributed as a
    signed sum over m cells.  Cells outside the block are free.
    """
    N = 1 << n
    m = block_size(n, key)
    if (m + h) % 2 or abs(h) > m:
        return 0
    return comb(m, (m + h) // 2) * (1 << (N - m))


def marginal_prob(n, key, h):
    N = 1 << n
    return marginal_count(n, key, h) / (1 << N)


def log2_k_independence(n, C):
    """log2 of the independence estimate.  Exact in the exponent, so it does
    not overflow; k_independence() below is the float form and is only valid
    while 2^N fits a float (n <= 9)."""
    from math import log2
    N = 1 << n
    tot = float(N)
    for key, h in C.items():
        c = marginal_count(n, key, h)
        if c <= 0:
            return None
        m = block_size(n, key)
        # Pr = comb(m,(m+h)/2) / 2^m, computed in the exponent
        tot += log2(comb(m, (m + h) // 2)) - m
    return tot


def k_independence(n, C):
    """The independence estimate: 2^N * prod_i Pr[coefficient_i = h_i].

    This is the Haar translation of the binomial-tail credibility metric of
    Kahng et al., evaluated at zero unsatisfied constraints.

    Returned as a float.  For n >= 10 the value 2^N exceeds the float range;
    use log2_k_independence() there, which stays exact in the exponent.
    """
    lg = log2_k_independence(n, C)
    if lg is None:
        return 0.0
    if lg > 1000:
        raise OverflowError(
            "k_independence overflows a float at n=%d; use "
            "log2_k_independence()" % n)
    return 2.0 ** lg


def credibility_exact(n, C):
    """Pr[an unrelated design satisfies every constraint] = k(C)/2^N."""
    return k_dp(n, C) / (1 << (1 << n))


def credibility_independence(n, C):
    N = 1 << n
    p = 1.0
    for key, h in C.items():
        p *= marginal_prob(n, key, h)
    return p


# ------------------------------------------------------- the gap law

def sign_vector(n, key):
    """The +-1/0 vector eps_i in {0,1,-1}^N that the coefficient reads.

    H0 reads all cells with weight +1; H(j,c) reads its block with +1 on the
    left half and -1 on the right half, and 0 elsewhere.
    """
    N = 1 << n
    if key == "H0":
        return [1] * N
    v = [0] * N
    j, c = key
    m = 1 << (n - j + 1)
    half = m >> 1
    idx = 0
    for b in c:
        idx = (idx << 1) | b
    base = idx * m
    for x in range(base, base + half):
        v[x] = 1
    for x in range(base + half, base + m):
        v[x] = -1
    return v


def lattice_index(rows):
    """[Z^t : M(Z^N)] for the integer matrix M with the given rows.

    Computed as the product of the elementary divisors of the Smith normal
    form.  Returns None if the rank is deficient, which cannot happen for a
    set of distinct Haar coefficients since they are linearly independent.
    """
    M = [list(r) for r in rows]
    t = len(M)
    N = len(M[0]) if t else 0
    div = 1
    r = cc = 0
    while r < t and cc < N:
        piv = None
        for i in range(r, t):
            for j2 in range(cc, N):
                if M[i][j2] != 0:
                    if piv is None or abs(M[i][j2]) < abs(M[piv[0]][piv[1]]):
                        piv = (i, j2)
        if piv is None:
            break
        pi, pj = piv
        M[r], M[pi] = M[pi], M[r]
        for row in M:
            row[cc], row[pj] = row[pj], row[cc]
        again = True
        while again:
            again = False
            for i in range(r + 1, t):
                if M[i][cc]:
                    q = M[i][cc] // M[r][cc]
                    for j2 in range(cc, N):
                        M[i][j2] -= q * M[r][j2]
                    if M[i][cc]:
                        M[r], M[i] = M[i], M[r]
                        again = True
            for j2 in range(cc + 1, N):
                if M[r][j2]:
                    q = M[r][j2] // M[r][cc]
                    for i in range(r, t):
                        M[i][j2] -= q * M[i][cc]
                    if M[r][j2]:
                        for i in range(r, t):
                            M[i][cc], M[i][j2] = M[i][j2], M[i][cc]
                        again = True
        div *= abs(M[r][cc])
        r += 1
        cc += 1
    return None if r < t else div


def gap_index(n, C):
    """The predicted k(C)/k_indep(C): the lattice index of the constraint map.

    This is exact in the local-CLT limit, is always a power of two, and does
    NOT depend on n.  For ancestor-closed C it equals 2^(|C|-1); for
    coefficients on pairwise disjoint supports it equals 1.
    """
    keys = list(C)
    return lattice_index([sign_vector(n, k) for k in keys])


def rooted_count(C):
    """Coefficients whose entire root path is also constrained.

    Kept because ancestor-closed sets are the important special case, where
    gap_index equals 2^rooted_count.  It is NOT a valid predictor in general:
    a coefficient together with both of its children has rooted_count 0 but
    lattice index 2.  Use gap_index.
    """
    keys = set(C)
    if "H0" not in keys:
        return 0
    r = 0
    for k in keys:
        if k == "H0":
            continue
        j, c = k
        if all((jj, c[:jj - 1]) in keys for jj in range(1, j)):
            r += 1
    return r


def projection_norm_haar(n, C):
    """sum over C of h^2 / m, the projection norm in the Haar basis."""
    tot = 0.0
    for key, h in C.items():
        tot += (h * h) / block_size(n, key)
    return tot


def projection_norm_fringe(n, C, S=None):
    """sum over fringe blocks of S_B^2 / m_B, the same norm in the other basis."""
    if S is None:
        S = forced_block_sums(n, C)
    tot = 0.0
    for (j, c) in fringe(n, C):
        m = 1 << (n - j + 1)
        tot += (S[(j, c)] ** 2) / m
    return tot


# ------------------------------------------------------------ self-test

def _selftest(verbose=True):
    import random
    ok = True

    def chk(cond, msg):
        nonlocal ok
        if not cond:
            ok = False
            print("  FAIL:", msg)
        return cond

    if verbose:
        print("haarcount v%s self-test" % __version__)

    # 1. DP vs brute force, exhaustive over small constraint sets
    rng = random.Random(11)
    for n in (2, 3):
        keys = all_keys(n)
        for _ in range(60):
            t = rng.randint(1, min(4, len(keys)))
            sel = rng.sample(keys, t)
            f = tuple(rng.getrandbits(1) for _ in range(1 << n))
            C = {k: spectrum(f, n)[k] for k in sel}
            chk(k_dp(n, C) == k_brute(n, C), "DP vs brute n=%d C=%s" % (n, C))
    if verbose:
        print("  DP == brute force (n=2,3, 120 sets): ok")

    # 2. closed forms
    for n in (2, 3, 4):
        for key in all_keys(n):
            m = block_size(n, key)
            for h in range(-m, m + 1, 2):
                chk(k_single_closed(n, key, h) == k_dp(n, {key: h}),
                    "single closed n=%d %s h=%d" % (n, key, h))
    if verbose:
        print("  Theorem 2 == DP (n=2,3,4, all keys and values): ok")

    for n in (2, 3, 4):
        N = 1 << n
        for h0 in range(-N, N + 1, 2):
            for h1 in range(-N, N + 1, 2):
                C = {"H0": h0, (1, ()): h1}
                chk(k_pair_closed(n, h0, h1) == k_dp(n, C),
                    "pair closed n=%d h0=%d h1=%d" % (n, h0, h1))
    if verbose:
        print("  Theorem 3 == DP (n=2,3,4, all pairs): ok")

    # 3. ancestor-closed product form
    rng = random.Random(23)
    cnt = 0
    for n in (3, 4, 5):
        keys = all_keys(n)
        for _ in range(40):
            sel = ["H0"]
            frontier = [(1, ())]
            steps = rng.randint(0, min(5, len(keys) - 1))
            for _s in range(steps):
                if not frontier:
                    break
                k = rng.choice(frontier)
                frontier.remove(k)
                sel.append(k)
                j, c = k
                if j < n:
                    frontier += [(j + 1, c + (0,)), (j + 1, c + (1,))]
            f = tuple(rng.getrandbits(1) for _ in range(1 << n))
            sp = spectrum(f, n)
            C = {k: sp[k] for k in sel}
            chk(is_ancestor_closed(C), "generated set is ancestor-closed")
            chk(k_ancestor_closed(n, C) == k_dp(n, C),
                "Theorem 4 vs DP n=%d C=%s" % (n, sorted(map(str, C))))
            cnt += 1
    if verbose:
        print("  Theorem 4 == DP (%d ancestor-closed sets): ok" % cnt)

    # 4. marginals against brute force
    for n in (2, 3):
        N = 1 << n
        counts = {}
        for bits in itertools.product((0, 1), repeat=N):
            sp = spectrum(bits, n)
            for k, v in sp.items():
                counts.setdefault(k, {}).setdefault(v, 0)
                counts[k][v] += 1
        for k, dist in counts.items():
            for v, c in dist.items():
                chk(marginal_count(n, k, v) == c,
                    "marginal n=%d %s=%d: %d vs %d"
                    % (n, k, v, marginal_count(n, k, v), c))
    if verbose:
        print("  marginal_count == brute force (n=2,3): ok")

    # 5. disjoint supports are exactly independent (Corollary 1)
    rng = random.Random(31)
    for n in (4, 5):
        keys = [k for k in all_keys(n) if k != "H0"]
        for _ in range(40):
            pool = keys[:]
            rng.shuffle(pool)
            sel = []
            for k in pool:
                if all(_disjoint(k, o) for o in sel):
                    sel.append(k)
                if len(sel) == 4:
                    break
            if len(sel) < 2:
                continue
            f = tuple(rng.getrandbits(1) for _ in range(1 << n))
            sp = spectrum(f, n)
            C = {k: sp[k] for k in sel}
            ke, ki = k_dp(n, C), k_independence(n, C)
            chk(abs(ke / ki - 1.0) < 1e-9,
                "disjoint independence n=%d ratio=%.12f" % (n, ke / ki))
    if verbose:
        print("  disjoint supports exactly independent (Cor. 1): ok")

    # 6. Parseval: the two bases give the same projection norm
    rng = random.Random(41)
    worst = 0.0
    for n in (3, 4, 5, 6):
        for _ in range(60):
            sel = ["H0"]
            frontier = [(1, ())]
            for _s in range(rng.randint(0, 6)):
                if not frontier:
                    break
                k = rng.choice(frontier)
                frontier.remove(k)
                sel.append(k)
                j, c = k
                if j < n:
                    frontier += [(j + 1, c + (0,)), (j + 1, c + (1,))]
            f = tuple(rng.getrandbits(1) for _ in range(1 << n))
            sp = spectrum(f, n)
            C = {k: sp[k] for k in sel}
            a = projection_norm_haar(n, C)
            b = projection_norm_fringe(n, C)
            worst = max(worst, abs(a - b))
            chk(abs(a - b) < 1e-9, "Parseval n=%d: %.9f vs %.9f" % (n, a, b))
    if verbose:
        print("  Parseval identity across the two bases (max dev %.2e): ok" % worst)

    # 7. lattice index predicts the independence gap, including the cases
    #    where the rooted-count heuristic fails
    for n in (5, 6):
        for sel in ([(2, (0,)), (3, (0, 0)), (3, (0, 1))],
                    ["H0", (2, (0,)), (2, (1,))],
                    ["H0", (1, ())],
                    [(3, (0, 0)), (3, (1, 1))]):
            if max((k[0] if k != "H0" else 0) for k in sel) > n:
                continue
            idx = gap_index(n, {k: 0 for k in sel})
            chk(idx is not None and idx >= 1, "lattice index defined")
    chk(gap_index(6, {k: 0 for k in [(2, (0,)), (3, (0, 0)), (3, (0, 1))]}) == 2,
        "parent+both children has index 2 (rooted_count says 0)")
    chk(rooted_count({k: 0 for k in [(2, (0,)), (3, (0, 0)), (3, (0, 1))]}) == 0,
        "rooted_count is 0 there, which is why it is not the predictor")
    chk(gap_index(6, {k: 0 for k in ["H0", (1, ()), (2, (0,))]}) == 4,
        "ancestor-closed |C|=3 has index 4")
    chk(gap_index(6, {k: 0 for k in [(3, (0, 0)), (3, (1, 1))]}) == 1,
        "disjoint pair has index 1")
    if verbose:
        print("  lattice index vs rooted-count counterexamples: ok")

    print("RESULT:", "ALL SELF-TESTS PASS" if ok else "FAILURES ABOVE")
    return ok


if __name__ == "__main__":
    import sys
    sys.exit(0 if _selftest() else 1)
