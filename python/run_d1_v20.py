#!/usr/bin/env python3
# Author: Mitchell A. Thornton
# Copyright (c) 2026 Mitchell A. Thornton
"""
D.1 -- separator width of the census block reconstruction fiber.

This is a FEASIBILITY MEASUREMENT, run before any experiment is built on the
census-reconstruction application, to decide whether the application is viable
at all.  It is the step the Track B campaign skipped.

The question.  For a single census block, the published tables impose integer
linear constraints on the block's person-by-attribute table.  The set of
tables consistent with them is what the Census Bureau calls the "solution
universe."  The Bureau enumerates it by SAT on a seven-person toy block and
then abandons enumeration at scale, substituting "solution variability," an
upper bound on pairwise disagreement between two valid reconstructions.  If
the exact count is cheap, it can be computed instead.

Whether it is cheap is decided by ONE number: the size of the DP separator,
that is, how many distinct residual column-margin vectors are reachable when
the table is filled in row by row.  This script measures that number exactly.

Geometry (2010 census block-level tables):
  rows    = sex (2) x age bin (38)          -> 76 row categories, margins P12
  columns = detailed race (63) x hispanic(2)-> 126 column categories, margins P9
  P12A-G give sex x agebin margins for each of 7 collapsed race groups,
  P12H the hispanic margin, P12I the white-alone-non-hispanic cell.  Given
  those, the block table DECOMPOSES into 7 independent sub-transportation
  problems, six of which span a single detailed race code.

What is real here and what is not.  The table GEOMETRY above is the real 2010
structure and the DP is exact.  The block POPULATIONS are simulated, because
no block-level microdata exists to draw from -- that is the entire point of
the reconstruction literature.  Populations are drawn to match two published
aggregates (308,745,538 persons over 6,207,027 occupied blocks, mean 49.7)
and the well-documented racial concentration of individual blocks.  Every
number this script reports is an exact state count for a simulated block, not
a measurement of any real block.  It answers "is this tractable," not "what is
the answer for block X."

Outputs results/d1_results.json.
"""
import json, os, random, sys, time

SEED = 20260827

N_ROWS = 76             # sex x agebin
N_COLS = 126            # race63 x hisp
MEAN_BLOCK_POP = 49.7   # 308745538 / 6207027


# ------------------------------------------------------- the exact DP

def fiber_states(row_margins, col_margins, cap=20_000_000):
    """Exact fiber count and separator profile for a 2-way transportation fiber.

    Fills the table row by row.  The DP state after row i is the vector of
    residual column margins; the separator width at that boundary is the
    number of distinct reachable states.  Returns (count, max_states,
    states_by_row) or (None, None, None) if the cap is exceeded.
    """
    cols = [c for c in col_margins if c > 0]
    rows = [r for r in row_margins if r > 0]
    if sum(rows) != sum(cols):
        raise ValueError("margins do not agree")
    if not rows:
        return 1, 1, [1]

    states = {tuple(cols): 1}
    profile = [1]
    for r in rows:
        nxt = {}
        for resid, ways in states.items():
            # distribute r units among the columns, bounded by residuals
            for alloc in _allocations(resid, r):
                key = tuple(a - b for a, b in zip(resid, alloc))
                nxt[key] = nxt.get(key, 0) + ways * _multiplicity(alloc)
        states = nxt
        profile.append(len(states))
        if len(states) > cap:
            return None, None, None
    total = sum(states.get(tuple([0] * len(cols)), 0) for _ in (0,))
    return total, max(profile), profile


def _multiplicity(alloc):
    """Number of distinct tables giving this row allocation: exactly 1.

    The table entries ARE the allocation, so each allocation is one table
    row.  Kept as a named function so the counting semantics are explicit.
    """
    return 1


def _allocations(resid, r):
    """All ways to write r as an ordered sum bounded componentwise by resid."""
    k = len(resid)
    out = []

    def rec(i, left, cur):
        if i == k - 1:
            if left <= resid[i]:
                out.append(tuple(cur + [left]))
            return
        hi = min(left, resid[i])
        for v in range(hi + 1):
            cur.append(v)
            rec(i + 1, left - v, cur)
            cur.pop()

    rec(0, r, [])
    return out


# --------------------------------------------------- simulated blocks

# Approximate 2010 national race-group shares, used as the base measure for
# the per-block Dirichlet.  The six single-race groups carry ~97% of the
# population and "two or more races" ~3%.  This matters more than it looks:
# "two or more races" is the ONLY group spanning many detailed codes (57 of
# the 63), so a simulation that oversamples it manufactures a wide separator
# that does not exist in real data.  An earlier version of this script drew
# the 63 detailed codes uniformly, putting ~90% of simulated people in that
# group, and reported a blow-up that was an artifact of the draw.
RACE_GROUP_SHARE = [0.72, 0.13, 0.009, 0.048, 0.002, 0.062, 0.029]
HISPANIC_SHARE = 0.163


def _race_codes_in_group(g):
    """Detailed race codes belonging to collapsed group g."""
    if g < 6:
        return [g]
    return list(range(6, 63))


def draw_block(rng, pop=None, concentration=0.35):
    """Simulate one block's person list as (row, col) pairs.

    Sex and age spread broadly over the 76 row categories.  Race group is
    drawn from a Dirichlet whose base measure is the national share above and
    whose concentration is low, because real blocks are far more racially
    concentrated than the nation.  Hispanic origin is drawn per person.

    Concentration REDUCES the number of occupied columns and therefore the
    width, so it is the parameter this measurement is most sensitive to; a
    sweep over it is run below so the choice is not load-bearing.
    """
    if pop is None:
        pop = max(1, int(rng.lognormvariate(3.2, 0.9)))
    w = [rng.gammavariate(concentration * 7 * s, 1.0) + 1e-12
         for s in RACE_GROUP_SHARE]
    tot = sum(w)
    w = [x / tot for x in w]
    people = []
    for _ in range(pop):
        rrow = rng.randrange(N_ROWS)
        u = rng.random()
        acc = 0.0
        g = len(w) - 1
        for j, wj in enumerate(w):
            acc += wj
            if u <= acc:
                g = j
                break
        codes = _race_codes_in_group(g)
        race = codes[0] if len(codes) == 1 else rng.choice(codes)
        hisp = 1 if rng.random() < HISPANIC_SHARE else 0
        people.append((rrow, race * 2 + hisp))
    return pop, people


def margins(people):
    rm = [0] * N_ROWS
    cm = [0] * N_COLS
    for r, c in people:
        rm[r] += 1
        cm[c] += 1
    return rm, cm


def decompose_by_race_group(people):
    """Apply the P12A-I decomposition.

    Columns are (race63, hisp) flattened as race*2 + hisp.  The 7 collapsed
    groups are the 6 single-race codes plus 'two or more races' (the
    remaining 57 detailed codes).  P12A-G fix each group's sex x agebin
    margin, so conditional on them the fiber factors over groups.
    """
    groups = {}
    for r, c in people:
        race, hisp = divmod(c, 2)
        g = race if race < 6 else 6
        groups.setdefault(g, []).append((r, race * 2 + hisp))
    return groups


# ------------------------------------------------------------- driver

def measure(people, label, cap=2_000_000):
    rm, cm = margins(people)
    occ_cols = sum(1 for x in cm if x > 0)
    occ_rows = sum(1 for x in rm if x > 0)
    t0 = time.time()
    try:
        cnt, mx, prof = fiber_states(rm, cm, cap=cap)
    except RecursionError:
        cnt, mx, prof = None, None, None
    return {"label": label, "pop": len(people), "occupied_rows": occ_rows,
            "occupied_cols": occ_cols,
            "fiber_count": None if cnt is None else int(cnt),
            "max_states": mx, "seconds": round(time.time() - t0, 3),
            "capped": cnt is None}


def selftest():
    """Validate the fiber DP against brute force and against a closed form."""
    import itertools
    from math import factorial

    def brute(rm, cm):
        rm = [r for r in rm if r > 0]
        cm = [c for c in cm if c > 0]
        R, Ct = len(rm), len(cm)
        if sum(rm) != sum(cm):
            return 0

        def rows(r, k):
            if k == 1:
                yield (r,)
                return
            for v in range(r + 1):
                for rest in rows(r - v, k - 1):
                    yield (v,) + rest

        cnt = 0
        for combo in itertools.product(*[list(rows(r, Ct)) for r in rm]):
            if all(sum(combo[i][j] for i in range(R)) == cm[j]
                   for j in range(Ct)):
                cnt += 1
        return cnt

    rng = random.Random(11)
    bad = 0
    for _ in range(300):
        R, Ct, N = rng.randint(1, 4), rng.randint(1, 4), rng.randint(1, 7)
        rm, cm = [0] * R, [0] * Ct
        for _ in range(N):
            rm[rng.randrange(R)] += 1
            cm[rng.randrange(Ct)] += 1
        if fiber_states(rm, cm)[0] != brute(rm, cm):
            bad += 1
    print("  fiber DP == brute force (300 random margin pairs): %s"
          % ("ok" if bad == 0 else "FAIL (%d)" % bad))
    bad2 = 0
    for _ in range(200):
        Ct, R = rng.randint(1, 5), rng.randint(1, 7)
        cm = [0] * Ct
        for _ in range(R):
            cm[rng.randrange(Ct)] += 1
        closed = factorial(R)
        for c in cm:
            closed //= factorial(c)
        if fiber_states([1] * R, cm)[0] != closed:
            bad2 += 1
    print("  all-ones row margins == multinomial closed form (200): %s"
          % ("ok" if bad2 == 0 else "FAIL (%d)" % bad2))
    return bad == 0 and bad2 == 0


def main():
    rng = random.Random(SEED)
    out = {"seed": SEED, "geometry": {"rows": N_ROWS, "cols": N_COLS,
                                      "mean_block_pop": MEAN_BLOCK_POP},
           "caveat": "simulated block populations, real 2010 table geometry",
           "undecomposed": [], "decomposed": [], "sensitivity": []}

    print("Validation")
    out["selftest_passed"] = selftest()

    # --- A. undecomposed block table, whole-table margins only
    print("\nA. undecomposed (P12 rows x P9 columns only)")
    for pop in (5, 10, 15, 20, 30):
        _, people = draw_block(rng, pop=pop)
        row = measure(people, "undecomposed pop=%d" % pop, cap=150_000)
        out["undecomposed"].append(row)
        print("   pop=%3d  occ_cols=%2d  max_states=%s  count=%s  %.2fs"
              % (row["pop"], row["occupied_cols"],
                 row["max_states"], row["fiber_count"], row["seconds"]))
        sys.stdout.flush()

    # --- B. with the P12A-I decomposition applied
    print("\nB. decomposed by race group (P12A-I applied)")
    for pop in (10, 25, 50, 100, 200, 400, 800):
        _, people = draw_block(rng, pop=pop)
        groups = decompose_by_race_group(people)
        widths, counts, secs = [], [], 0.0
        ok = True
        for g, gp in groups.items():
            r = measure(gp, "g%d" % g, cap=400_000)
            secs += r["seconds"]
            if r["capped"]:
                ok = False
                break
            widths.append(r["max_states"])
            counts.append(r["fiber_count"])
        rec = {"pop": pop, "n_groups": len(groups),
               "max_group_width": max(widths) if ok and widths else None,
               "total_fiber": None, "seconds": round(secs, 3), "capped": not ok}
        if ok and counts:
            tot = 1
            for c in counts:
                tot *= c
            rec["total_fiber"] = int(tot)
        out["decomposed"].append(rec)
        print("   pop=%3d  groups=%d  max_group_width=%s  fiber=%s  %.2fs"
              % (pop, rec["n_groups"], rec["max_group_width"],
                 rec["total_fiber"], rec["seconds"]))
        sys.stdout.flush()

    # --- C. sensitivity to the racial-concentration assumption
    print("\nC. sensitivity to block racial concentration (pop=50, decomposed)")
    for conc in (0.15, 0.35, 1.0, 3.0, 10.0):
        _, people = draw_block(rng, pop=50, concentration=conc)
        groups = decompose_by_race_group(people)
        widths = []
        ok = True
        for g, gp in groups.items():
            r = measure(gp, "g%d" % g, cap=400_000)
            if r["capped"]:
                ok = False
                break
            widths.append(r["max_states"])
        rec = {"concentration": conc, "n_groups": len(groups),
               "occupied_cols": len({c for _, c in people}),
               "max_group_width": max(widths) if ok and widths else None,
               "capped": not ok}
        out["sensitivity"].append(rec)
        print("   conc=%.2f  occ_cols=%2d  groups=%d  max_group_width=%s"
              % (conc, rec["occupied_cols"], rec["n_groups"],
                 rec["max_group_width"]))
        sys.stdout.flush()

    os.makedirs("results", exist_ok=True)
    with open("results/d1_results.json", "w") as fh:
        json.dump(out, fh, indent=1)
    print("\nwrote results/d1_results.json")
    return out


if __name__ == "__main__":
    main()
