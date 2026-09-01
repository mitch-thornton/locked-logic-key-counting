# Obtaining the benchmark data

This repository contains code only. One experiment, campaign E.2, runs on the
**Trust-Hub obfuscation benchmark release**, which is third-party data. It is
**not vendored here** and cannot be redistributed from this repository; you
download it once from Trust-Hub and point the tools at it. Everything else in
the repository — the closed-form verification suite, the counting kernel
self-test, the parity gates, and the generated-netlist experiments (Tracks B,
C, D, and E.1) — needs no external data and runs out of the box.

## What the dataset is

The locked netlists are the hardware-obfuscation benchmark suite of

> S. Amir, B. Shakya, X. Xu, Y. Jin, S. Bhunia, M. Tehranipoor, and D. Forte,
> "Development and Evaluation of Hardware Obfuscation Benchmarks," *Journal of
> Hardware and Systems Security* 2:142–161, 2018.
> DOI [10.1007/s41635-018-0036-3](https://doi.org/10.1007/s41635-018-0036-3)

The suite locks the ten ISCAS-85 combinational circuits with a range of
locking methods at advertised key lengths from 32 to 955 bits, and is
distributed through the **Trust-Hub** portal.

## Where to get it

Download the obfuscation benchmark set from Trust-Hub:

- <https://trust-hub.org/> → **Benchmarks** → **Obfuscation** (also called
  "Logic Locking" / "Obfuscation Benchmarks").

Trust-Hub distributes the suite as **one `.zip` per instance**. A free
account may be required to download; the licensing and terms are Trust-Hub's,
so read them there. Unpack the archives into a single directory — call it
`OBFUSCATION/benchmarks` — with one zip (or one unpacked instance directory)
per benchmark. That directory is what you pass as `--bench-dir`.

## Instance naming, and which file to use

Instance names follow `circuit-METHODkeylen`, e.g. `c880-RN640` is the ISCAS-85
`c880` circuit locked by method `RN` with a 640-gate insertion (advertised key
length is reported in each instance's ReadMe). The method codes in the release,
read from its shipped taxonomy:

| code | obfuscation method | SAT defense |
|---|---|---|
| RN | random key-gate insertion | none |
| SL | secure logic locking | none |
| CS | logic-cone-size based | none |
| CY | cyclic logic locking | none |
| NR | random key-gate insertion | AntiSAT |
| NS | secure logic locking | AntiSAT |
| NC | logic-cone-size based | AntiSAT |
| BE / BR / BS | BDD-based functional obfuscation | none |
| dfx_sfll_k | SFLL | none |

Each archive ships an **unsynthesized `.v`** and a **synthesized `synt_*.v`**.
The tools here use the **unsynthesized** file: it is flat structural Verilog
over primitive gates and is read directly by the built-in reader (`verilog.py`).
The synthesized form is mapped to SAED90nm standard cells (`OA21X1`, `AO22X1`,
`MUX21X1`, …) and would need a cell library first; no reader here accepts it,
and `count.py` deliberately prefers the unsynthesized file inside an archive.

No external front end is needed to read these files. The reader in
`python/lockcount/verilog.py` is self-contained; it was surveyed against all
295 unsynthesized files of the release. (The 43 cyclic-locking `CY` instances
parse but are then rejected on an explicit acyclicity check that names the
cycle — they are out of scope for the method, not for the parser.)

## Confirm you have the same files

`data/benchmark_manifest.json` records the SHA-256, byte count, and instance
name of every archive used for the published c432 and c1355 families. Check
your download against it before comparing any numbers:

```bash
python3 - <<'EOF'
import hashlib, json, os
BENCH = "/path/to/OBFUSCATION/benchmarks"      # <-- edit this
man = json.load(open("data/benchmark_manifest.json"))
bad = 0
for r in man["instances"]:
    p = os.path.join(BENCH, r["file"])
    if not os.path.exists(p):
        print("MISSING       ", r["file"]); bad += 1; continue
    h = hashlib.sha256(open(p, "rb").read()).hexdigest()
    if h != r["sha256"]:
        print("DIGEST DIFFERS", r["file"]); bad += 1
print("checked %d, %d problem(s)" % (len(man["instances"]), bad))
EOF
```

If a digest differs, Trust-Hub has revised that archive since the paper was
run. The experiment will still run; the numbers may differ, and that is worth
knowing before you compare.

## Pointing the tools at it

Everything reads the data through `--bench-dir` (and, for a single instance,
`--bench`). No `--renesis` flag exists any more; the built-in reader handles
Verilog.

```bash
cd python/lockcount

# one instance, every engine, cross-checked
python3 count.py --bench c880-RN640.zip \
    --bench-dir /path/to/OBFUSCATION/benchmarks --engine all -t 5

# reachability screen over a family
python3 screen.py --bench-dir /path/to/OBFUSCATION/benchmarks \
    --out results/screen.json --only c432,c1355

# the entropy campaign
python3 run_e2_v20.py --phase all \
    --bench-dir /path/to/OBFUSCATION/benchmarks \
    --only c880-RN640,c880-RN320 --out results/phase6.json
```

## What the numbers mean

Reported `log2_V` values are **upper bounds** on the surviving secret. Queries
are drawn uniformly at random, so an attacker who chooses them arrives sooner;
the stopping rule is a plateau under random queries, which is not a proof that
no further query separates the survivors; and more queries can only shrink the
version space. "At most this many bits remain" is supported; "exactly this
many" is not, except where `certify.py` returns an affordable tightness
certificate. See the paper for the full statement of scope and limits.
