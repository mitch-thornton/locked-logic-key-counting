/*
 * Author: Mitchell A. Thornton
 * Copyright (c) 2026 Mitchell A. Thornton
 */
/* ------------------------------------------------------------------------
 * engineB_cudd.c -- exact key version-space counting with CUDD.
 *
 * Reads a problem file emitted by the Python front end: a locked netlist
 * already reduced to primitive gates, plus a list of oracle queries.  Fixes
 * the primary inputs of each query, symbolically simulates so every net
 * becomes a BDD over the key variables, conjoins `net XNOR observed` over the
 * outputs, accumulates across queries, and counts the satisfying assignments.
 *
 * Why the front end stays in Python.  The Verilog, BLIF and ISCAS readers are
 * validated there against round-trip and key-recovery gates, and against a
 * second independent parser.  Re-implementing them in C would add a class of
 * bug the project has no way to detect.  The problem file is a flat,
 * unambiguous format, so the C side has nothing to guess.
 *
 * EXACTNESS.  Cudd_CountMinterm returns a double and silently loses precision
 * above 2^53, which is inside the range of interest: a 64-bit key space is
 * 2^64.  This uses Cudd_ApaCountMinterm, the arbitrary-precision form, and
 * prints the decimal digits.  A count that has been through a double is not
 * an exact count.
 *
 * Build:
 *   cc -O2 -o engineB_cudd engineB_cudd.c -I$CUDD/cudd -I$CUDD/util \
 *      -I$CUDD -L$CUDD/cudd/.libs -lcudd -lm
 *
 * Usage:
 *   engineB_cudd problem.txt [--reorder sift] [--node-limit N] [--json]
 * ---------------------------------------------------------------------- */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "cudd.h"

#define LINE   (1 << 22)

typedef struct {
    char *out;
    char *op;
    char **ins;
    int   nins;
} Gate;

typedef struct {
    char **inputs;  int ninputs;
    char **keys;    int nkeys;
    char **outputs; int noutputs;
    Gate  *gates;   int ngates;
    /* queries: nq rows, each ninputs input bits and noutputs output bits */
    char **qx;      char **qy;   int nq;
} Problem;

/* ------------------------------------------------------------ utilities */

static void *xmalloc(size_t n)
{
    void *p = malloc(n);
    if (!p) { fprintf(stderr, "out of memory\n"); exit(2); }
    return p;
}

static char *xstrdup(const char *s)
{
    char *p = xmalloc(strlen(s) + 1);
    strcpy(p, s);
    return p;
}

/* A tiny open-addressing map from net name to index. */
typedef struct { char **key; int *val; int cap, n; } Map;

static unsigned long hashs(const char *s)
{
    unsigned long h = 1469598103934665603UL;
    while (*s) { h ^= (unsigned char)*s++; h *= 1099511628211UL; }
    return h;
}

static void map_init(Map *m, int cap)
{
    m->cap = cap; m->n = 0;
    m->key = xmalloc(sizeof(char *) * cap);
    m->val = xmalloc(sizeof(int) * cap);
    for (int i = 0; i < cap; i++) m->key[i] = NULL;
}

static void map_put(Map *m, const char *k, int v)
{
    unsigned long i = hashs(k) % (unsigned long)m->cap;
    while (m->key[i]) {
        if (!strcmp(m->key[i], k)) { m->val[i] = v; return; }
        i = (i + 1) % (unsigned long)m->cap;
    }
    m->key[i] = xstrdup(k);
    m->val[i] = v;
    m->n++;
}

static int map_get(Map *m, const char *k)
{
    unsigned long i = hashs(k) % (unsigned long)m->cap;
    while (m->key[i]) {
        if (!strcmp(m->key[i], k)) return m->val[i];
        i = (i + 1) % (unsigned long)m->cap;
    }
    return -1;
}

/* --------------------------------------------------------------- parsing */

/* Tokenize into a growable array.  A fixed cap is a real hazard here: a
 * KEYS line for a 256-bit key carries 257 tokens, and silently dropping the
 * tail leaves a gate referring to a net that was never defined.  That failed
 * as a segmentation fault rather than an error, which is why the array grows
 * and why lookups below are checked. */
static char **TOK = NULL;
static int TOKCAP = 0;

static int split(char *line, int *count)
{
    int n = 0;
    char *p = strtok(line, " \t\r\n");
    while (p) {
        if (n == TOKCAP) {
            TOKCAP = TOKCAP ? TOKCAP * 2 : 256;
            TOK = realloc(TOK, sizeof(char *) * TOKCAP);
            if (!TOK) { fprintf(stderr, "out of memory\n"); exit(2); }
        }
        TOK[n++] = p;
        p = strtok(NULL, " \t\r\n");
    }
    *count = n;
    return n;
}

/* Net lookup that refuses rather than returning an index of -1. */
static int need(Map *m, const char *name)
{
    int s = map_get(m, name);
    if (s < 0) {
        fprintf(stderr, "undefined net %s\n", name);
        exit(2);
    }
    return s;
}

static char **read_list(char **tok, int n, int from, int *count)
{
    *count = n - from;
    char **out = xmalloc(sizeof(char *) * (*count > 0 ? *count : 1));
    for (int i = from; i < n; i++) out[i - from] = xstrdup(tok[i]);
    return out;
}

static Problem *read_problem(const char *path)
{
    FILE *f = fopen(path, "r");
    if (!f) { fprintf(stderr, "cannot open %s\n", path); exit(2); }
    Problem *P = xmalloc(sizeof(Problem));
    memset(P, 0, sizeof(Problem));
    int gcap = 1024, qcap = 64;
    P->gates = xmalloc(sizeof(Gate) * gcap);
    P->qx = xmalloc(sizeof(char *) * qcap);
    P->qy = xmalloc(sizeof(char *) * qcap);

    char *line = xmalloc(LINE);
    char *copy = xmalloc(LINE);
    while (fgets(line, LINE, f)) {
        if (line[0] == '#' || line[0] == '\n') continue;
        strcpy(copy, line);
        int n;
        split(copy, &n);
        char **tok = TOK;
        if (n == 0) continue;
        if (!strcmp(tok[0], "INPUTS"))
            P->inputs = read_list(tok, n, 1, &P->ninputs);
        else if (!strcmp(tok[0], "KEYS"))
            P->keys = read_list(tok, n, 1, &P->nkeys);
        else if (!strcmp(tok[0], "OUTPUTS"))
            P->outputs = read_list(tok, n, 1, &P->noutputs);
        else if (!strcmp(tok[0], "GATE")) {
            if (P->ngates == gcap) {
                gcap *= 2;
                P->gates = realloc(P->gates, sizeof(Gate) * gcap);
            }
            Gate *g = &P->gates[P->ngates++];
            g->out = xstrdup(tok[1]);
            g->op  = xstrdup(tok[2]);
            g->ins = read_list(tok, n, 3, &g->nins);
        } else if (!strcmp(tok[0], "QUERY")) {
            if (P->nq == qcap) {
                qcap *= 2;
                P->qx = realloc(P->qx, sizeof(char *) * qcap);
                P->qy = realloc(P->qy, sizeof(char *) * qcap);
            }
            P->qx[P->nq] = xstrdup(tok[1]);
            P->qy[P->nq] = xstrdup(tok[2]);
            P->nq++;
        }
    }
    fclose(f);
    free(line); free(copy);
    return P;
}

/* ------------------------------------------------------------- the engine */

int main(int argc, char **argv)
{
    const char *path = NULL;
    const char *reorder = "none";
    long node_limit = 0;
    int json = 0;
    int traj = 0;

    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--reorder") && i + 1 < argc) reorder = argv[++i];
        else if (!strcmp(argv[i], "--node-limit") && i + 1 < argc)
            node_limit = atol(argv[++i]);
        else if (!strcmp(argv[i], "--json")) json = 1;
        else if (!strcmp(argv[i], "--trajectory")) traj = 1;
        else path = argv[i];
    }
    if (!path) {
        fprintf(stderr,
                "usage: engineB_cudd problem.txt [--reorder sift|none] "
                "[--node-limit N] [--json] [--trajectory]\n");
        return 2;
    }

    Problem *P = read_problem(path);

    DdManager *dd = Cudd_Init((unsigned)P->nkeys, 0, CUDD_UNIQUE_SLOTS,
                              CUDD_CACHE_SLOTS, 0);
    if (!dd) { fprintf(stderr, "Cudd_Init failed\n"); return 2; }
    if (!strcmp(reorder, "sift"))
        Cudd_AutodynEnable(dd, CUDD_REORDER_SIFT);
    if (node_limit > 0) Cudd_SetMaxLive(dd, (unsigned int)node_limit);

    /* net name -> slot */
    int nslots = P->ninputs + P->nkeys + P->ngates + 8;
    Map m; map_init(&m, nslots * 4);
    DdNode **val = xmalloc(sizeof(DdNode *) * nslots);
    for (int i = 0; i < nslots; i++) val[i] = NULL;
    int next = 0;

    for (int i = 0; i < P->ninputs; i++) map_put(&m, P->inputs[i], next++);
    int key0 = next;
    for (int i = 0; i < P->nkeys; i++)   map_put(&m, P->keys[i], next++);
    for (int i = 0; i < P->ngates; i++)  map_put(&m, P->gates[i].out, next++);

    DdNode *acc = Cudd_ReadOne(dd);
    Cudd_Ref(acc);

    clock_t t0 = clock();
    int aborted = 0;
    long peak = 0;

    for (int q = 0; q < P->nq && !aborted; q++) {
        /* primary inputs are constants for this query */
        for (int i = 0; i < P->ninputs; i++) {
            DdNode *c = (P->qx[q][i] == '1') ? Cudd_ReadOne(dd)
                                             : Cudd_ReadLogicZero(dd);
            Cudd_Ref(c);
            val[need(&m, P->inputs[i])] = c;
        }
        /* key inputs are the BDD variables */
        for (int i = 0; i < P->nkeys; i++) {
            DdNode *v = Cudd_bddIthVar(dd, i);
            Cudd_Ref(v);
            val[key0 + i] = v;
        }
        for (int g = 0; g < P->ngates && !aborted; g++) {
            Gate *G = &P->gates[g];
            DdNode *r = NULL, *tmp;
            int s0 = need(&m, G->ins[0]);
            DdNode *a = val[s0];
            if (!strcmp(G->op, "NOT")) {
                r = Cudd_Not(a); Cudd_Ref(r);
            } else if (!strcmp(G->op, "BUF") || !strcmp(G->op, "BUFF")) {
                r = a; Cudd_Ref(r);
            } else {
                int isand = !strcmp(G->op, "AND") || !strcmp(G->op, "NAND");
                int isor  = !strcmp(G->op, "OR")  || !strcmp(G->op, "NOR");
                int isxor = !strcmp(G->op, "XOR") || !strcmp(G->op, "XNOR");
                if (!isand && !isor && !isxor) {
                    fprintf(stderr, "unsupported gate %s\n", G->op);
                    return 2;
                }
                r = a; Cudd_Ref(r);
                for (int k = 1; k < G->nins; k++) {
                    DdNode *b = val[need(&m, G->ins[k])];
                    if (isand)      tmp = Cudd_bddAnd(dd, r, b);
                    else if (isor)  tmp = Cudd_bddOr(dd, r, b);
                    else            tmp = Cudd_bddXor(dd, r, b);
                    if (!tmp) { aborted = 1; break; }
                    Cudd_Ref(tmp);
                    Cudd_RecursiveDeref(dd, r);
                    r = tmp;
                }
                if (!aborted && (!strcmp(G->op, "NAND") ||
                                 !strcmp(G->op, "NOR") ||
                                 !strcmp(G->op, "XNOR"))) {
                    tmp = Cudd_Not(r); Cudd_Ref(tmp);
                    Cudd_RecursiveDeref(dd, r);
                    r = tmp;
                }
            }
            if (aborted) break;
            val[need(&m, G->out)] = r;
            long live = (long)Cudd_ReadNodeCount(dd);
            if (live > peak) peak = live;
        }
        if (aborted) break;

        for (int o = 0; o < P->noutputs; o++) {
            DdNode *got = val[need(&m, P->outputs[o])];
            DdNode *want = (P->qy[q][o] == '1') ? got : Cudd_Not(got);
            DdNode *tmp = Cudd_bddAnd(dd, acc, want);
            if (!tmp) { aborted = 1; break; }
            Cudd_Ref(tmp);
            Cudd_RecursiveDeref(dd, acc);
            acc = tmp;
        }
        /* With --trajectory the count is reported after every query, in
         * one invocation.  Rebuilding the whole query set once per query is
         * quadratic in the query count and that is what the campaign spends
         * its time on; the diagram is already correct after each conjunction,
         * so the count is simply read off here. */
        if (traj && !aborted) {
            int tdig;
            DdApaNumber tapa = Cudd_ApaCountMinterm(dd, acc, P->nkeys, &tdig);
            if (json) {
                printf(q == 0 ? "{\"traj\": [" : ", ");
                printf("{\"t\": %d, \"count\": \"", q + 1);
                Cudd_ApaPrintDecimal(stdout, tdig, tapa);
                printf("\", \"acc_nodes\": %ld}", (long)Cudd_DagSize(acc));
            } else {
                printf("t=%d count=", q + 1);
                Cudd_ApaPrintDecimal(stdout, tdig, tapa);
                printf(" acc_nodes=%ld\n", (long)Cudd_DagSize(acc));
            }
            fflush(stdout);
            Cudd_FreeApaNumber(tapa);
        }

        /* release this query's intermediate nets */
        for (int i = 0; i < P->ninputs; i++) {
            int s = need(&m, P->inputs[i]);
            if (val[s]) { Cudd_RecursiveDeref(dd, val[s]); val[s] = NULL; }
        }
        for (int i = 0; i < P->nkeys; i++) {
            if (val[key0 + i]) {
                Cudd_RecursiveDeref(dd, val[key0 + i]);
                val[key0 + i] = NULL;
            }
        }
        for (int g = 0; g < P->ngates; g++) {
            int s = need(&m, P->gates[g].out);
            if (val[s]) { Cudd_RecursiveDeref(dd, val[s]); val[s] = NULL; }
        }
    }

    double secs = (double)(clock() - t0) / CLOCKS_PER_SEC;

    if (aborted) {
        if (json && traj) printf("], \"count\": null, \"note\": \"node limit "
                                 "or CUDD failure\", \"peak_nodes\": %ld, "
                                 "\"seconds\": %.3f}\n", peak, secs);
        else if (json) printf("{\"count\": null, \"note\": \"node limit or CUDD "
                         "failure\", \"peak_nodes\": %ld, \"seconds\": %.3f}\n",
                         peak, secs);
        else printf("ABORTED peak=%ld seconds=%.3f\n", peak, secs);
        return 3;
    }

    /* Exact count.  Cudd_CountMinterm returns a double and loses precision
     * above 2^53; the arbitrary-precision form is the only correct choice
     * once the key space passes that. */
    int digits;
    DdApaNumber apa = Cudd_ApaCountMinterm(dd, acc, P->nkeys, &digits);
    long accnodes = (long)Cudd_DagSize(acc);
    long total = (long)Cudd_ReadNodeCount(dd);

    if (json) {
        printf(traj ? "], \"count\": \"" : "{\"count\": \"");
        Cudd_ApaPrintDecimal(stdout, digits, apa);
        printf("\", \"acc_nodes\": %ld, \"total_nodes\": %ld, "
               "\"peak_nodes\": %ld, \"seconds\": %.3f, \"keys\": %d, "
               "\"queries\": %d}\n",
               accnodes, total, peak, secs, P->nkeys, P->nq);
    } else {
        printf("count=");
        Cudd_ApaPrintDecimal(stdout, digits, apa);
        printf(" acc_nodes=%ld total_nodes=%ld peak=%ld seconds=%.3f\n",
               accnodes, total, peak, secs);
    }
    Cudd_RecursiveDeref(dd, acc);
    Cudd_Quit(dd);
    return 0;
}
