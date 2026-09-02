# Locked-logic key counting

Companion code for the version-space key-counting results in:

> M. A. Thornton, "A Version Space Approach for Digital Circuit Analysis,"
> arXiv:2609.00609 [cs.CR], 2026. <https://arxiv.org/abs/2609.00609>

Darwin Deason Institute for Cyber Security and Department of Electrical and
Computer Engineering, Southern Methodist University.

Repository: <https://github.com/mitch-thornton/locked-logic-key-counting>

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.22218068.svg)](https://doi.org/10.5281/zenodo.22218068)

Archived releases: DOI [10.5281/zenodo.22218068](https://doi.org/10.5281/zenodo.22218068),
which always resolves to the latest archived version.

## This repository contains

- `python/run_e1b_v20.py`, the two-engine depth comparison on generated
  netlists.
- `python/lockcount/`, the version-space counting package used for the
  published-benchmark campaign: netlist IR and readers (including a
  self-contained structural-Verilog reader), two independent counting engines,
  the locking-scheme generators, the width screen, the parity harness, and the
  campaign driver.
- `c/engineB_cudd.c`, the same counting engine in C on CUDD, with a
  `Makefile`. Optional; the Python engines produce identical counts.
- `python/haarcount_v1.py`, the counting kernel: the block-sum tree dynamic
  program, the closed forms, coefficient marginals, the independence estimate,
  and the lattice index of Proposition 1. Standard library only.
- `python/j2_verify_v20.py`, the machine-verification suite for every closed
  form in the paper.
- `python/run_b1_v20.py`, `run_b23_v20.py`, `run_c1_v20.py`, `run_c2_v20.py`, the
  experiment drivers for Tracks B and C, each seeded and self-contained.
- `python/run_d1_v20.py`, the census block separator-width feasibility
  measurement. Self-validates against brute force and a multinomial closed
  form before measuring.
- `python/mk_mult_bench_v20.py`, generates array-multiplier netlists and
  self-verifies them against integer multiplication before writing.
- `c/minibdd_v20.c`, a small dependency-free ROBDD package with a node budget
  that aborts and reports, which is the partial-BDD trigger condition.
- `validate_repo.py`, the one-command self-check for a fresh clone.
- `DATA.md`, where to obtain the third-party Trust-Hub benchmark data (not
  redistributed here), with a manifest to verify your download.

The paper PDF is not included in this repository; cite the paper by the entry
below.

## Validating a fresh clone

Right after cloning, run the self-check. It needs nothing external — no CUDD,
no benchmark download, standard library only:

```bash
python3 validate_repo.py
```

It confirms every source file is present, the package imports, every closed
form in the paper matches brute force, the counting kernel self-tests pass, the
four-way parity gates pass, and the built-in Verilog reader reads and counts a
netlist with no external front end. It exits non-zero if anything fails.

## Quick start

```bash
# 1. verify every closed form in the paper against brute force
python3 python/j2_verify_v20.py

# 2. self-test the counting kernel (DP vs brute force, closed forms,
#    marginals, Corollary 2, the Parseval identity, the lattice index)
python3 python/haarcount_v1.py

# 3. reproduce the Track C result: greedy vs the optimal query policy
cd python && python3 run_c2_v20.py

# 4. build and run the BDD tool
cc -O2 -o minibdd c/minibdd_v20.c
python3 python/mk_mult_bench_v20.py 8 mult_8x8.bench
./minibdd mult_8x8.bench                # completes
./minibdd mult_8x8.bench 100000         # aborts on the node budget

# 5. run the version-space counting gates.  These must pass before anything
#    else in python/lockcount/ is worth believing.
cd python/lockcount && python3 validate.py

# 6. optional: build the C counting engine and re-run the gates, which then
#    check it against both Python engines and against brute force
cd c && make CUDD=/path/to/cudd-3.0.0
cd ../python/lockcount && python3 validate.py

# 7. reproduce the published-benchmark campaign.  Needs the Trust-Hub
#    obfuscation archives; see DATA.md for how to obtain and verify them.
python3 run_e2_v20.py --phase all \
    --bench-dir /path/to/OBFUSCATION/benchmarks \
    --only c880-RN640,c880-RN320 --out results/phase6.json
```

The counting gates run without CUDD and without any external netlist front
end. Verilog is read by the self-contained reader in
`python/lockcount/verilog.py`, and the ISCAS `.isc` and `.bench` readers are
self-contained as well, so nothing outside this repository is needed to read
the benchmark release. The C engine is optional and the counts are identical
without it.

Requires Python 3.8+ and a C compiler. Everything in the Quick start uses the
standard library only. The one exception is the E.1 figure generator
(`python/make_e1_figures_v20.py`), which needs matplotlib
(`pip install -r python/requirements.txt`); nothing else in the repository
imports it.

## Building the CUDD engine (optional)

The C engine `c/engineB_cudd.c` is a genuine port of the second counting engine
and gives a third independent, arbitrary-precision count. It is optional: every
number in the paper is reachable from the Python engines alone. CUDD is **not
vendored**; obtain and build it yourself, then point the `Makefile` at it.

1. **Get CUDD 3.0.0.** The maintained mirror is
   <https://github.com/ivmai/cudd>. Clone or download and unpack it.

2. **Build CUDD.** In the unpacked tree:

   ```bash
   cd cudd-3.0.0
   ./configure
   make
   ```

   This leaves `libcudd.a` under `cudd/.libs` and the headers spread across
   `cudd/`, `util/`, `st/`, `epd/`, and `mtr/`. (An install prefix from
   `make install`, with headers under `include/` and the library under `lib/`,
   also works.)

3. **Build the engine here**, pointing `CUDD` at either the source tree or an
   install prefix:

   ```bash
   cd c
   make CUDD=/path/to/cudd-3.0.0 probe   # reports what it finds, no build
   make CUDD=/path/to/cudd-3.0.0         # builds ./engineB_cudd
   ```

   Both tree layouts are probed automatically, so the usual case needs no
   editing. If your layout is neither, set the include and library flags
   directly: `make INC="-I/somewhere/include" LIB="-L/somewhere/lib -lcudd -lm"`.
   Run `probe` first when paths are in doubt; the compiler error for a wrong
   CUDD path is not helpful.

4. **Re-run the gates.** `cd python/lockcount && python3 validate.py` now runs
   six checks instead of five, the extra one comparing the CUDD engine against
   both Python engines and brute force.

The engine uses `Cudd_ApaCountMinterm`, the arbitrary-precision count, because
the double-returning variant loses precision above 2^53 and a 64-bit key space
is 2^64. On macOS, if you build CUDD as a shared library rather than static,
watch for the `dyld` / System Integrity Protection trap; a static `libcudd.a`
avoids it.

## The benchmark data

Campaign E.2 runs on the Trust-Hub obfuscation benchmark release, which is
third-party and is **not redistributed here**. `DATA.md` explains where to
download it, how to confirm you have the same archives (via
`data/benchmark_manifest.json`), the instance naming convention, and which file
inside each archive the tools use. Everything else in this repository runs with
no external data.

## Reproducing the E.1 figures

E.1 measures the counting width and the version-space trajectory on generated
netlists (ripple-carry adders, array multipliers of the c6288 family, and
equality-comparator trees). The netlists are built deterministically in memory
by `lockkit_v20.py`, so no external data is needed; the customized
array-multiplier netlists used for the OBDD-growth calibration are also shipped,
in `benchmarks/e1/`, together with the measured growth table
(`benchmarks/e1/bdd_growth_results.md`).

```bash
cd python
python3 run_e1_v20.py     # width sweep + trajectories -> results/e1_results.json (~30 s)
python3 run_e1b_v20.py    # how far each engine gets    -> results/e1b_results.json (~4 min)
python3 make_e1_figures_v20.py   # writes ../figures/fig_e1_width.* and fig_e1_trajectory.*
```

`run_e1_v20.py` first checks the counter against exhaustive enumeration over all
2^K keys (81 cases) and stops if any mismatch. The figure generator uses a fresh
run under `python/results/` when present and otherwise falls back to the
reference results shipped in `data/e1/`, so `make_e1_figures_v20.py` produces the
paper's two E.1 figures even before you re-run the drivers. The customized
multiplier netlists reproduce byte-for-byte from
`python3 mk_mult_bench_v20.py <W>`; they are included so the calibration and the
`minibdd` demonstration run against fixed inputs.

## Repository layout

```
.
├── README.md
├── LICENSE                     MIT
├── PATENTS.md                  patent notice (MIT does not grant patent rights)
├── CITATION.cff                how to cite the software and the paper
├── DATA.md                     obtaining the third-party benchmark data
├── CODEBASE.md                 how this image maps to the reproduction bundle
├── validate_repo.py            one-command self-check for a fresh clone
├── validate.sh                 thin wrapper over validate_repo.py
├── data/
│   ├── benchmark_manifest.json SHA-256s to verify a Trust-Hub download
│   └── e1/                     reference E.1 results, for figure reproduction
├── benchmarks/
│   └── e1/                     customized multiplier netlists + growth table
├── figures/                    E.1 figures land here (created on demand)
├── python/
│   ├── haarcount_v1.py         counting kernel
│   ├── j2_verify_v20.py        verification suite
│   ├── run_b1_v20.py           B.1 independence gap
│   ├── run_b23_v20.py          B.2 revelation order, B.3 robustness
│   ├── run_c1_v20.py           C.1 version-space estimators
│   ├── run_c2_v20.py           C.2 greedy vs optimal
│   ├── run_d1_v20.py           D.1 block separator width
│   ├── run_e1_v20.py           E.1 locking width sweep and trajectories
│   ├── run_e1b_v20.py          E.1b how far each engine gets
│   ├── lockkit_v20.py          E.1 netlists, locking schemes, counter
│   ├── mk_mult_bench_v20.py    netlist generator, self-verifying
│   ├── make_e1_figures_v20.py  E.1 figures (needs matplotlib)
│   ├── requirements.txt
│   ├── e3/                     random-vs-chosen query experiment (needs a SAT solver)
│   └── lockcount/              E.2 package, published benchmarks
│       ├── netlist.py          IR, BENCH reader/writer, simulation
│       ├── isc.py              ISCAS-85 .isc distribution dialect
│       ├── verilog.py          self-contained structural Verilog reader
│       ├── engineA.py          variable elimination, pays induced width
│       ├── engineB.py          decision diagram over the key bits
│       ├── cudd_bridge.py      runs the C engine, falls back to engineB
│       ├── lockschemes.py      generated netlists and locks, for parity
│       ├── screen.py           residual-width reachability screen
│       ├── smoke.py            small end-to-end checks
│       ├── count.py            run one engine on one instance
│       ├── certify.py          plateau tightness certificate
│       ├── validate.py         the parity gates
│       ├── run_e2_v20.py       the E.2 campaign driver
│       └── runpar_v20.py       parallel campaign runner
└── c/
    ├── minibdd_v20.c           minimal ROBDD with a node budget
    ├── engineB_cudd.c          the counting engine on CUDD
    └── Makefile                make CUDD=/path/to/cudd-3.0.0
```

## What's new in v20.8

- **arXiv identifier recorded.** The companion paper is published as
  arXiv:2609.00609 [cs.CR]; `README.md` and `CITATION.cff` now cite it by
  identifier instead of carrying a placeholder.

## What's new in v20.7

- **Zenodo DOI recorded.** The archived-release DOI 10.5281/zenodo.22218068 is
  now in `CITATION.cff` and badged above; it resolves to the latest archived
  version, and each release carries its own version DOI on Zenodo.

## What's new in v20.6

- **Patent application number recorded.** `PATENTS.md` now cites U.S.
  Provisional Patent Application No. 64/145,457, assigned to Clearpoint
  Research, LLC, in place of the general patents-pending statement.

## What's new in v20.5

- **Patent status.** `PATENTS.md` now states that methods in this repository are
  covered by one or more U.S. patents pending, assigned to Clearpoint Research,
  LLC.

## What's new in v20.4

- **Patent notice rewritten.** `PATENTS.md` now covers patent rights in the
  methods for Clearpoint Research, LLC, drops the SMU mention, and states plainly
  that MIT grants copyright permissions only, not patent rights.
- **Author/copyright headers.** Every source and script file now carries an
  `Author: Mitchell A. Thornton` / `Copyright (c) 2026 Mitchell A. Thornton`
  header.

## What's new in v20.3

- **Title corrected to the singular:** the cited arXiv paper is now
  "A Version Space Approach for Digital Circuit *Analysis*" throughout
  (`README.md` and `CITATION.cff`).

## What's new in v20.2

- **Citation points to the arXiv paper.** The companion paper is now cited as
  the arXiv preprint "A Version Space Approach for Digital Circuit Analysis"
  (identifier to be assigned); `README.md` and `CITATION.cff` were updated
  accordingly. A journal citation will be added if and when that version is
  accepted.
- **E.1 is reproducible from the repository.** The customized array-multiplier
  netlists and the OBDD-growth table are shipped in `benchmarks/e1/`, the E.1
  figure generator `python/make_e1_figures_v20.py` is included, and reference
  E.1 results are in `data/e1/`, so the two E.1 figures reproduce out of the
  box. See "Reproducing the E.1 figures" above.

## What's new in v20.1

This is a standalone code release, prepared for public distribution.

- **No dependence on any external netlist front end.** Earlier cuts could
  route Verilog through an external "Renesis" adapter; that adapter and its
  `--renesis` options have been removed. Structural Verilog is read by the
  self-contained `python/lockcount/verilog.py`, which was surveyed against all
  295 unsynthesized files of the benchmark release. Nothing outside this
  repository is needed to read the data.
- **`validate_repo.py`**, a one-command self-check for a fresh clone.
- **`CITATION.cff`** and **`DATA.md`** added; `data/benchmark_manifest.json`
  lets you verify a Trust-Hub download. CUDD obtain-and-build instructions are
  now spelled out above.

## What's new in v17 versus v16

The paper was rewritten in short declarative sentences and given an abstract
that summarizes it rather than introducing it.  No result changed.

One number did change, because a defect was fixed.  The consolidation script
inferred whether a run had plateaued from the shape of its trajectory, and the
trajectory stores log2 of the count rounded to three decimals.  On one
instance a still-falling count sat at 49.812 for twenty consecutive queries
and was misread as a plateau.  The consolidator now believes the driver, which
compares exact counts.  The campaign classification is 57 plateaued, 4
budget-exhausted and 9 on which an engine gave up, where v16 reported 58, 3
and 9.  No count, entropy or bits-lost figure moved.

## What's new in v16 versus v13

- **The campaign covers the complete published release**: 70 instances, all ten
  ISCAS-85 circuits, key lengths 32 to 256 bits. Losses run from 4.65 to
  152.09 bits, and two instances are reduced to a single surviving key.
- **`python/lockcount/count.py`**: run one engine on one instance.
  `--engine all` runs Engine A, both Engine B implementations and brute force
  on the same queries and fails loudly if they disagree.
- **The Makefile probes for CUDD** rather than assuming one layout;
  `make CUDD=... probe` reports what it finds.

## What v13 added over v12

- **E.1b added** (`python/run_e1b_v20.py`): both engines on the same instances
  and the same queries, 300 queries deep, with agreement enforced at every
  query where both answer. The one-key-per-query rate of a point-function lock
  is measured over three hundred queries rather than eight.
- **The reachability screen answers for both engines.** It reports Engine A's
  residual width against a threshold and Engine B's diagram against a node
  budget. Over 54 published archives the two disagree in both directions: six
  instances only Engine A reaches, four only Engine B.
- **Three corrections** where v12 reported an Engine A limitation as a
  limitation of the method. See the bundle `HISTORY.md`.

## What v12 added over v11

- **Published benchmarks are now run.** v11 measured only netlists we locked
  ourselves and said so. v16 adds campaign E.2 on the Trust-Hub obfuscation
  release, read directly from the shipped Verilog.
- **A second counting engine.** `python/lockcount/engineB.py` represents the
  version space as a decision diagram over the key bits instead of
  eliminating variables over a factor graph. Its cost falls as queries
  accumulate while the first engine's rises. `c/engineB_cudd.c` is the same
  engine on CUDD, and it counts with `Cudd_ApaCountMinterm`, the
  arbitrary-precision count, because the double-returning variant loses
  precision above 2^53 and a 64-bit key space is 2^64.
- **A four-way parity gate.** `python/lockcount/validate.py` checks engine A
  against the Python diagram engine against the CUDD engine against exhaustive
  enumeration, and it runs on every invocation rather than on request.
- **Readers for the ISCAS `.isc` dialect and for Trust-Hub Verilog**, both
  self-contained. The `.isc` and Verilog readers are cross-checked
  functionally against each other on c432, c880 and c6288.
- **A width screen** that decides reachability without computing the count,
  so a campaign does not spend its budget discovering that an instance is out
  of range.

## What it shows

The set of keys an attacker cannot yet rule out is computable exactly. It
looks like projected model counting, which is doubly exponential in width,
but a combinational netlist determines its own internal signals, so the count
is a plain sum-product and singly exponential.

The width that governs the cost is not the width over key variables. At 32
key bits, a point-function lock has width 31 over key variables alone and
width 9 to 10 over the gate-level factor graph. The factor width does not
grow with key length; it grows with the number of oracle queries, because
each query adds a copy of the residual circuit.

Three schemes then behave in three ways, measured to three hundred queries. A
point-function lock satisfies |V_t| = 2^24 - t exactly throughout, one key per
query. An interference-maximizing lock falls to a single key. A random lock
stalls at a plateau of functionally equivalent keys that no further query can
separate.

Representing the version space directly, as a decision diagram over the key
bits, inverts the width story. Each query further constrains the diagram, so
its cost falls where the elimination engine's rises. It is the engine that gets
through on four of the five published benchmarks compared head to head, and on
the fifth the order reverses. Neither engine is the method;
the pair is. A screen over 54 published archives finds six instances only the
elimination engine reaches and four only the diagram engine reaches.

On the Trust-Hub obfuscation release the advertised key length is not what
survives. See the paper for the table; the short version is that every
instance measured loses most of its key material to a modest number of
uniformly random queries, and on one instance the random queries are usually
worth nothing at all, which iteration counting cannot see.

Honest limits. The reported entropies are upper bounds: queries are drawn
uniformly at random, an attacker choosing them arrives sooner, and a plateau
under random queries is not a proof that no further query separates the
survivors. Cyclic locking is out of scope, the BDD-based instances expose no
key ports under any naming convention in the release, and the synthesized
variants would need a standard cell library. Routing-network locks are
predicted to defeat the method and are untested. Sequential locking is out of
scope. This is measurement, not attack: it says how much of the key is still
hidden, not what the key is.

## Methods compared

The independence estimate is the Haar translation of the binomial-tail
credibility metric of Kahng et al. (IEEE TCAD 20(10):1236-1252, 2001). The
version-space estimators in C.1 are rejection sampling and query-by-committee
disagreement, the standard substitutes when an exact count is unavailable. The
query policies in C.2 are greedy expected information gain, coarse-first,
finest-first and random fixed orders, all against the exhaustively optimal
policy computed by dynamic programming.

## How the implementations relate

Python is canonical. `c/engineB_cudd.c` is a genuine port of the second
counting engine and is checked against its Python twin, not merely against
invariants: `python/lockcount/validate.py` runs engine A against the Python
diagram engine against the CUDD engine against exhaustive enumeration, and the
comparison is on exact integers rather than to a floating-point tolerance,
because these are counts. The C engine is optional. Every number in the paper
is reachable from Python alone; the C engine buys speed and a third
independent count.

The Haar counting recursion in `haarcount_v1.py` is pure integer arithmetic
and ports to C without floating point; that port is still not present, so
there is no cross-language test for it. `minibdd_v20.c` is C because it is
decision-diagram machinery rather than a port of the kernel, and it is
validated by construction invariants.

## License and patents

MIT, see `LICENSE`. The MIT license grants copyright permissions and does
**not** grant patent rights; see `PATENTS.md`.

## Citation

A `CITATION.cff` file is included, so GitHub's "Cite this repository" button
and Zenodo will pick up the right metadata automatically. For BibTeX:

```bibtex
@misc{thornton2026versionspace,
  author        = {Thornton, Mitchell A.},
  title         = {A Version Space Approach for Digital Circuit Analysis},
  year          = {2026},
  eprint        = {2609.00609},
  archivePrefix = {arXiv},
  primaryClass  = {cs.CR}
}
```

The antecedent this work continues:

```bibtex
@article{thornton2002vlsi,
  author  = {Thornton, Mitchell A. and Drechsler, Rolf and G{\"u}nther, Wolfgang},
  title   = {Logic Circuit Equivalence Checking Using {H}aar Spectral
             Coefficients and Partial {BDD}s},
  journal = {VLSI Design},
  volume  = {14}, number = {1}, pages = {53--64}, year = {2002},
  doi     = {10.1080/10655140290009800}
}
```

## Contact

Mitchell A. Thornton, mitch@smu.edu, ORCID 0000-0003-3559-9511
