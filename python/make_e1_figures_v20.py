#!/usr/bin/env python3
# Author: Mitchell A. Thornton
# Copyright (c) 2026 Mitchell A. Thornton
"""
Figures for E.1.

    fig_e1_width.pdf       key-moral width against factor width, by scheme
    fig_e1_trajectory.pdf  |V_t| against query count, by scheme

Inputs are the JSON written by run_e1_v20.py and run_e1b_v20.py. This script
looks first for a freshly generated run under python/results/ (where those two
drivers write when run from the python/ directory), and falls back to the
reference copies shipped in data/e1/ so the figures reproduce out of the box.

Figures are written to figures/ at the repository root. Needs matplotlib
(`pip install -r python/requirements.txt`); it is the only part of this
repository that does.
"""
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

PAL = ["#2f6fd0", "#d97706", "#128a6e", "#a3339b"]
STYLE = [("-", "o"), ("--", "s"), ("-.", "^"), (":", "D")]

plt.rcParams.update({
    "font.family": "serif",
    "font.serif": ["Times New Roman", "DejaVu Serif"],
    "font.size": 8, "axes.labelsize": 8, "axes.titlesize": 8,
    "legend.fontsize": 7, "xtick.labelsize": 7, "ytick.labelsize": 7,
    "axes.linewidth": 0.6, "lines.linewidth": 1.1, "lines.markersize": 3.6,
    "grid.linewidth": 0.4, "grid.alpha": 0.35, "figure.dpi": 200,
    "savefig.bbox": "tight", "savefig.pad_inches": 0.02,
})

W, H = 3.4, 2.5
HERE = os.path.dirname(os.path.abspath(__file__))          # python/
ROOT = os.path.abspath(os.path.join(HERE, ".."))           # repo root
OUT = os.path.join(ROOT, "figures")

# Fresh run first (python/results/), then the shipped reference (data/e1/).
E1_CANDIDATES = (os.path.join(HERE, "results", "e1_results.json"),
                 os.path.join(ROOT, "data", "e1", "e1_results.json"))
E1B_CANDIDATES = (os.path.join(HERE, "results", "e1b_results.json"),
                  os.path.join(ROOT, "data", "e1", "e1b_results.json"))

NAME = {"rll": "random (RLL)", "sll": "interference-max (SLL)",
        "point": "point function"}


def _first(paths):
    for p in paths:
        if os.path.exists(p):
            return p
    return None


def load():
    p = _first(E1_CANDIDATES)
    if not p:
        raise SystemExit("e1_results.json not found; run "
                         "`cd python && python3 run_e1_v20.py` first, or use "
                         "the reference copy in data/e1/.")
    return json.load(open(p))


def load_e1b():
    p = _first(E1B_CANDIDATES)
    return json.load(open(p)) if p else None


def fig_width(e1):
    fig, ax = plt.subplots(figsize=(W, H))
    for i, scheme in enumerate(("rll", "sll", "point")):
        rows = [r for r in e1["widths"] if r["lock"] == scheme]
        rows.sort(key=lambda r: r["K"])
        ax.scatter([r["key_moral_width"] for r in rows],
                   [r["factor_width"] for r in rows],
                   s=16, color=PAL[i], marker=STYLE[i][1],
                   label=NAME[scheme], zorder=3, alpha=0.85,
                   edgecolors="white", linewidths=0.4)
    lo, hi = 0, 34
    ax.plot([lo, hi], [lo, hi], color="0.55", linewidth=0.7,
            linestyle=(0, (3, 3)), zorder=1)
    ax.annotate("equal width", xy=(21, 21), xytext=(15.5, 21.6),
                fontsize=6, color="0.45", rotation=38)
    ax.set_xlim(lo, hi)
    ax.set_ylim(0, 24)
    ax.set_xlabel("width over key variables alone")
    ax.set_ylabel("width of the gate-level factor graph")
    ax.grid(True)
    ax.legend(frameon=False, loc="upper left")
    fig.savefig(os.path.join(OUT, "fig_e1_width.pdf"))
    fig.savefig(os.path.join(OUT, "fig_e1_width.png"))
    plt.close(fig)
    print("  fig_e1_width.pdf")


def fig_traj(e1):
    """|V_t| against queries, out as far as either engine reaches.

    When the deeper E.1b run is present the curves are drawn from it, because
    the interesting part of the point-function trajectory is that it keeps
    going: Engine A stops around the ninth query and Engine B does not.  A
    marker shows where Engine A stopped, so the figure distinguishes what the
    elimination engine established from what the diagram engine established.
    """
    b = load_e1b()
    fig, ax = plt.subplots(figsize=(W, H))
    for i, scheme in enumerate(("rll", "sll", "point")):
        stop = None
        if b:
            inst = [x for x in b["instances"] if x["scheme"] == scheme]
            if not inst:
                continue
            pts = [(r["t"], r["V_t"]) for r in inst[0]["rows"] if r["V_t"]]
            stop = inst[0].get("A_stops_at")
        else:
            recs = [r for r in e1["trajectory"] if r["lock"] == scheme]
            if not recs:
                continue
            pts = [(p["t"], p["V_t"]) for p in recs[0]["points"] if p["V_t"]]
        ls, mk = STYLE[i]
        every = max(1, len(pts) // 10)
        ax.plot([p[0] for p in pts], [p[1] for p in pts], ls,
                marker=mk, markevery=every, color=PAL[i], label=NAME[scheme])
        if stop:
            hit = [p for p in pts if p[0] == stop - 1]
            if hit:
                ax.plot([hit[0][0]], [hit[0][1]], marker="x", color=PAL[i],
                        markersize=5, markeredgewidth=1.1, linestyle="none",
                        zorder=5)
    ax.set_yscale("log")
    if b:
        ax.set_xscale("log")
    ax.set_xlabel("queries applied to the oracle")
    ax.set_ylabel(r"$|V_t|$, keys still consistent")
    ax.grid(True, which="major")
    h, l = ax.get_legend_handles_labels()
    if b:
        h.append(plt.Line2D([], [], marker="x", linestyle="none",
                            color="0.35", markersize=5, markeredgewidth=1.1))
        l.append("last query Engine A reached")
        ax.legend(h, l, frameon=False, loc="upper right",
                  bbox_to_anchor=(1.0, 0.54))
    else:
        ax.legend(h, l, frameon=False, loc="lower left")
    fig.savefig(os.path.join(OUT, "fig_e1_trajectory.pdf"))
    fig.savefig(os.path.join(OUT, "fig_e1_trajectory.png"))
    plt.close(fig)
    print("  fig_e1_trajectory.pdf")


def main():
    e1 = load()
    os.makedirs(OUT, exist_ok=True)
    fig_width(e1)
    fig_traj(e1)
    print("wrote figures to %s" % OUT)


if __name__ == "__main__":
    main()
