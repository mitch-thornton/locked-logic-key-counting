# Measured OBDD growth for array multipliers (calibration datum)

Built with `minibdd` (this directory), interleaved variable order
a0,b0,a1,b1,.... Peak output BDD = the largest single-output BDD across all 2W
outputs (always a middle output bit). Netlists generated and verified against
integer multiplication by `mk_mult_bench_v20.py` (306 cases, 0 mismatches at every
width).

| W (WxW mult) | peak output BDD nodes | total nodes allocated |
|---|---|---|
| 4  | 57     | 637       |
| 6  | 498    | 9,432     |
| 8  | 4,723  | 109,537   |
| 10 | 43,634 | 1,165,426 |

Growth ratio of the peak output BDD: 8.74x from W=4 to 6, 9.48x from 6 to 8,
9.24x from 8 to 10. That is a factor of roughly **3.0 per bit of operand
width**, consistent across the range.

Extrapolated to W=16 (the c6288 function): peak output BDD on the order of
3e7 nodes, with total allocation orders of magnitude beyond that. **No attempt
was made to complete W=16.** The exponential lower bound is a theorem (Bryant,
IEEE Trans. Computers 40(2):205-213, 1991) and does not need re-demonstration.
W=12 was started under a 60M-node budget and did not complete within the time
limit, which is the expected behavior.

## Why this table is worth keeping

It is not evidence that multipliers are hard to *verify* (they are not; see
Kaufmann and Biere, STTT 25(2):133-144, 2023, which verifies multipliers to
2048-bit). It is a **calibration curve for the partial-BDD experiments**: it
says what node budget corresponds to what fraction of a multiplier's BDD
actually gets built, which is exactly the independent variable in the
availability sweeps. The budget at which construction aborts determines which
Haar coefficients are exactly determined, and that set is the input to the
posterior.

For the c6288-class experiment the relevant fact is the one this table makes
quantitative: at any feasible node budget, only a small and structurally
non-ancestor-closed subset of the coefficient tree is resolved. That is
precisely the regime the general recursion was built for, and the regime the
2002 method could not evaluate.

## Reproducing

```
python3 mk_mult_bench_v20.py 8 mult_8x8.bench     # generates and self-verifies
cc -O2 -o minibdd minibdd_v20.c
./minibdd mult_8x8.bench                       # unlimited
./minibdd c6288_like_16x16.bench 10000000      # aborts on budget, as expected
```
