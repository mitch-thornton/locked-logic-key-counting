# The codebase, standalone

This is the code image: every engine, every driver, and the CLI. It is the
same code that ships inside the paper bundle `j2_paper_bundle_v20.zip`, laid
out as a public repository rather than as a reproduction bundle. The paper PDF
and the generated results, figures, and LaTeX sources live in the bundle, not
here.

**If you want to reproduce the paper, use the bundle, not this.** The bundle
carries the results JSON, the figures, the tables, the LaTeX sources and
`build.sh`, and `INSTALL-AND-RUN.md` is written against its layout. This image
carries the code only.

## Layout, and how it maps to the bundle

| here | in the bundle |
|---|---|
| `python/lockcount/` | `experiments/E2_published_suite/` |
| `python/run_e1_v20.py`, `run_e1b_v20.py`, `lockkit_v20.py`, `make_e1_figures_v20.py` | `experiments/E1_locking_width/`, `experiments/common/` |
| `python/run_b1_v20.py` … `run_d1_v20.py` | `experiments/B*/`, `C*/`, `D1_*/` |
| `python/haarcount_v1.py`, `j2_verify_v20.py` | `scripts/`, bundle root |
| `benchmarks/e1/`, `data/e1/` | `experiments/common/*.bench`, `experiments/E1_locking_width/results/` |
| `c/` | `c/`, `experiments/common/minibdd_v20.c` |

The drivers probe for both layouts, so a script that needs a sibling package
finds it either way.

## The four engines

| name | file | what it does |
|---|---|---|
| Engine A | `python/lockcount/engineA.py` | variable elimination over the residual factor graph; pays the induced width |
| Engine B, Python | `python/lockcount/engineB.py` | decision diagram over the key bits; pays the diagram size, which is the version space itself |
| Engine B, C | `c/engineB_cudd.c` | the same engine on CUDD, counting with `Cudd_ApaCountMinterm` so the count stays exact past 2^53 |
| brute force | `python/lockcount/count.py` | exhaustive over all 2^k keys; ground truth, refuses above 24 key bits |

## Five minutes to a working install

```bash
# 1. the gates.  These must pass before anything else is worth running.
cd python/lockcount && python3 validate.py

# 2. the C engine, optional.  Point CUDD at your tree; `probe` first if unsure.
cd ../../c && make CUDD=/Users/mitch/src/cudd probe
                make CUDD=/Users/mitch/src/cudd

# 3. the gates again.  Six now instead of five, the extra one covering CUDD.
cd ../python/lockcount && python3 validate.py

# 4. one instance, every engine, cross-checked
python3 count.py --bench /path/to/c880-RN320.zip --engine all -t 3
```

Expected from step 4:

```
c880-RN320.v: 435 gates, 60 inputs, 32 keys, 26 outputs
3 random queries, seed 20260827

  A       27648      0.12s  width 7
  B-py    27648      0.03s  394 nodes
  B-cudd  27648      0.03s  79 nodes
  brute   -          0.00s         refusing to enumerate 32 keys

all engines that completed agree
```

The two Engine B implementations report different node counts because their
variable orders differ. The counts are what must match, and `--engine all`
fails loudly when they do not.

## Dependencies

Python 3.8 or later, standard library only. `matplotlib` appears in
`python/requirements.txt` and is needed only by the figure generators in the
full bundle; nothing here imports it.

CUDD is optional and is not vendored; see the "Building the CUDD engine"
section of `README.md`. Every reader is self-contained: structural Verilog is
read by `python/lockcount/verilog.py` and the ISCAS `.isc` and `.bench` readers
are built in, so no external netlist front end is needed and every validation
gate runs without one.

See `INSTALL-AND-RUN.md` in the bundle for the macOS specifics, including the
`dyld` and System Integrity Protection trap if you build CUDD shared rather
than static.

## v20 additions

`python/lockcount/verilog.py` is the self-contained structural Verilog reader.
It was added to the bundle at v19 but was not copied into this image until
v20, so a clone of the v19 image could not read the benchmark release at all.
`python/lockcount/runpar_v20.py` was missing for the same reason.

`python/e3/` holds the random-against-chosen experiment and its CNF
construction.  It needs a SAT solver: `pip install python-sat`, or any of
cadical, kissat, cryptominisat5, minisat or glucose on PATH.

`python/check_reader_vs_iverilog_v20.py` cross-checks the reader against
Icarus Verilog on the benchmark release, and
`python/check_key_patterns_v20.py` checks the key-port counts against the
key sizes the benchmark documentation declares.  Both need the release, which
is third party and is not vendored, and both skip with an explanatory line
when it is absent.

A sync check belongs in any future cut: every file in
`python/lockcount/` must be byte-identical to its counterpart in
`experiments/E2_published_suite/`.  Three had drifted by v20.
