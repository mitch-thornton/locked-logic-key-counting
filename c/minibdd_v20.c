/*
 * Author: Mitchell A. Thornton
 * Copyright (c) 2026 Mitchell A. Thornton
 */
/* minibdd.c -- a minimal ROBDD package, enough to measure what happens when
 * you try to build a complete BDD for a 16x16 array multiplier.
 *
 * Standard construction: unique table for canonicity (reduced), fixed variable
 * order (ordered), ITE with a memo table, no complement edges, no reordering.
 * A node budget causes construction to abort and report, which is exactly the
 * partial-BDD trigger condition the J2 method cares about.
 *
 * This is deliberately small and dependency-free.  It is not a CUDD
 * replacement; it exists so the blow-up measurement is reproducible from the
 * bundle alone, with no external package to install.
 *
 * Build:  cc -O2 -o minibdd minibdd.c
 * Usage:  ./minibdd <file.bench> [node_budget] [order]
 *         order: "interleave" (default, a0,b0,a1,b1,...) or "sequential"
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

typedef unsigned int  u32;
typedef unsigned long long u64;

#define BDD_FALSE 0u
#define BDD_TRUE  1u

typedef struct { u32 var, lo, hi; } Node;

static Node *nodes = NULL;
static u32 n_nodes = 0, cap_nodes = 0;
static u32 node_budget = 0;
static int budget_hit = 0;

/* ---- unique table ---- */
static u32 *utab = NULL;  static u32 utab_sz = 0, utab_mask = 0;
static u32 *unext = NULL;

/* ---- ITE memo ---- */
typedef struct { u32 f, g, h, r; } Memo;
static Memo *memo = NULL; static u32 memo_sz = 0, memo_mask = 0;

static u64 ite_calls = 0, ite_hits = 0;

static void die(const char *m){ fprintf(stderr,"error: %s\n", m); exit(2); }

static u32 hash3(u32 a,u32 b,u32 c){
    u64 h = 1469598103934665603ULL;
    h = (h ^ a) * 1099511628211ULL;
    h = (h ^ b) * 1099511628211ULL;
    h = (h ^ c) * 1099511628211ULL;
    return (u32)(h ^ (h>>32));
}

static void grow_nodes(void){
    cap_nodes = cap_nodes ? cap_nodes*2 : (1u<<20);
    nodes = realloc(nodes, (size_t)cap_nodes*sizeof(Node));
    unext = realloc(unext, (size_t)cap_nodes*sizeof(u32));
    if(!nodes||!unext) die("out of memory growing node table");
}

static void utab_init(u32 bits){
    utab_sz = 1u<<bits; utab_mask = utab_sz-1;
    utab = malloc((size_t)utab_sz*sizeof(u32));
    if(!utab) die("out of memory for unique table");
    for(u32 i=0;i<utab_sz;i++) utab[i]=0xFFFFFFFFu;
}
static void memo_init(u32 bits){
    memo_sz = 1u<<bits; memo_mask = memo_sz-1;
    memo = calloc(memo_sz,sizeof(Memo));
    if(!memo) die("out of memory for memo table");
    for(u32 i=0;i<memo_sz;i++) memo[i].r = 0xFFFFFFFFu;
}

/* rehash the unique table when the node count outgrows it */
static void utab_rehash(void){
    u32 bits = 1; while((1u<<bits) < n_nodes*2u) bits++;
    if(bits < 20) bits = 20;
    free(utab);
    utab_init(bits);
    for(u32 i=2;i<n_nodes;i++){
        u32 h = hash3(nodes[i].var,nodes[i].lo,nodes[i].hi) & utab_mask;
        unext[i] = utab[h]; utab[h] = i;
    }
}

static u32 mk(u32 var,u32 lo,u32 hi){
    if(lo==hi) return lo;                      /* reduction rule 1 */
    u32 h = hash3(var,lo,hi) & utab_mask;
    for(u32 i=utab[h]; i!=0xFFFFFFFFu; i=unext[i])
        if(nodes[i].var==var && nodes[i].lo==lo && nodes[i].hi==hi)
            return i;                          /* reduction rule 2 */
    if(node_budget && n_nodes >= node_budget){ budget_hit = 1; return BDD_FALSE; }
    if(n_nodes >= cap_nodes) grow_nodes();
    u32 id = n_nodes++;
    nodes[id].var=var; nodes[id].lo=lo; nodes[id].hi=hi;
    if(n_nodes*2u > utab_sz){ utab_rehash(); h = hash3(var,lo,hi)&utab_mask; }
    unext[id]=utab[h]; utab[h]=id;
    return id;
}

static u32 var_of(u32 f){ return (f<2) ? 0xFFFFFFFFu : nodes[f].var; }

static u32 ite(u32 f,u32 g,u32 h){
    if(budget_hit) return BDD_FALSE;
    if(f==BDD_TRUE)  return g;
    if(f==BDD_FALSE) return h;
    if(g==h) return g;
    if(g==BDD_TRUE && h==BDD_FALSE) return f;
    ite_calls++;
    u32 key = hash3(f,g,h) & memo_mask;
    if(memo[key].r!=0xFFFFFFFFu && memo[key].f==f && memo[key].g==g && memo[key].h==h){
        ite_hits++; return memo[key].r;
    }
    u32 vf=var_of(f), vg=var_of(g), vh=var_of(h), v=vf;
    if(vg<v) v=vg; if(vh<v) v=vh;
    u32 fl = (var_of(f)==v)? nodes[f].lo : f, fh = (var_of(f)==v)? nodes[f].hi : f;
    u32 gl = (var_of(g)==v)? nodes[g].lo : g, gh = (var_of(g)==v)? nodes[g].hi : g;
    u32 hl = (var_of(h)==v)? nodes[h].lo : h, hh = (var_of(h)==v)? nodes[h].hi : h;
    u32 lo = ite(fl,gl,hl), hi = ite(fh,gh,hh);
    if(budget_hit) return BDD_FALSE;
    u32 r = mk(v,lo,hi);
    memo[key].f=f; memo[key].g=g; memo[key].h=h; memo[key].r=r;
    return r;
}

static u32 bdd_and(u32 a,u32 b){ return ite(a,b,BDD_FALSE); }
static u32 bdd_or (u32 a,u32 b){ return ite(a,BDD_TRUE,b); }
static u32 bdd_xor(u32 a,u32 b){ return ite(a, ite(b,BDD_FALSE,BDD_TRUE), b); }
static u32 bdd_not(u32 a){ return ite(a,BDD_FALSE,BDD_TRUE); }

/* ---- count live nodes reachable from a root ---- */
static u32 *mark=NULL; static u32 markgen=0; static u32 *markbuf=NULL;
static u32 count_rec(u32 f){
    if(f<2) return 0;
    if(markbuf[f]==markgen) return 0;
    markbuf[f]=markgen;
    return 1 + count_rec(nodes[f].lo) + count_rec(nodes[f].hi);
}
static u32 bdd_size(u32 f){
    markbuf = realloc(markbuf,(size_t)cap_nodes*sizeof(u32));
    static u32 lastcap=0;
    if(lastcap<cap_nodes){ for(u32 i=lastcap;i<cap_nodes;i++) markbuf[i]=0; lastcap=cap_nodes; }
    markgen++;
    return count_rec(f);
}

/* ---- .bench reader ---- */
#define MAXN 200000
typedef struct { char name[64]; u32 bdd; int done; } Net;
static Net *nets=NULL; static int n_nets=0;
static char (*gop)[8]; static int (*gfan)[2]; static int *gout; static int n_gates=0;
static char (*outname)[64]; static int n_outs=0;
static char (*inname)[64]; static int n_ins=0;

static int find_net(const char*s){
    for(int i=0;i<n_nets;i++) if(!strcmp(nets[i].name,s)) return i;
    if(n_nets>=MAXN) die("too many nets");
    strncpy(nets[n_nets].name,s,63); nets[n_nets].name[63]=0;
    nets[n_nets].bdd=0; nets[n_nets].done=0;
    return n_nets++;
}
static void trim(char*s){
    char*p=s; while(*p==' '||*p=='\t') p++;
    if(p!=s) memmove(s,p,strlen(p)+1);
    int L=strlen(s); while(L>0 && (s[L-1]=='\n'||s[L-1]=='\r'||s[L-1]==' '||s[L-1]=='\t')) s[--L]=0;
}

int main(int argc,char**argv){
    if(argc<2){ fprintf(stderr,"usage: %s <file.bench> [node_budget] [interleave|sequential]\n",argv[0]); return 1; }
    node_budget = (argc>2)? (u32)strtoul(argv[2],NULL,10) : 0;
    const char *order = (argc>3)? argv[3] : "interleave";

    nets = calloc(MAXN,sizeof(Net));
    gop  = calloc(MAXN,8); gfan = calloc(MAXN,2*sizeof(int)); gout = calloc(MAXN,sizeof(int));
    outname = calloc(4096,64); inname = calloc(4096,64);

    FILE*f=fopen(argv[1],"r"); if(!f) die("cannot open bench file");
    char line[1024];
    while(fgets(line,sizeof line,f)){
        trim(line);
        if(!line[0]||line[0]=='#') continue;
        if(!strncmp(line,"INPUT(",6)){
            char*e=strchr(line,')'); *e=0;
            strncpy(inname[n_ins],line+6,63); n_ins++;
        } else if(!strncmp(line,"OUTPUT(",7)){
            char*e=strchr(line,')'); *e=0;
            strncpy(outname[n_outs],line+7,63); n_outs++;
        } else {
            char*eq=strchr(line,'='); if(!eq) continue;
            *eq=0; char lhs[64]; strncpy(lhs,line,63); lhs[63]=0; trim(lhs);
            char*rhs=eq+1; trim(rhs);
            char*lp=strchr(rhs,'('); char*rp=strrchr(rhs,')');
            if(!lp||!rp) continue;
            *lp=0; *rp=0; char opn[16]; strncpy(opn,rhs,15); opn[15]=0; trim(opn);
            char args[512]; strncpy(args,lp+1,511); args[511]=0;
            char*c1=strchr(args,','); char a1[64],a2[64]; a2[0]=0;
            if(c1){ *c1=0; strncpy(a1,args,63); strncpy(a2,c1+1,63); }
            else strncpy(a1,args,63);
            trim(a1); if(a2[0]) trim(a2);
            strncpy(gop[n_gates],opn,7);
            gfan[n_gates][0]=find_net(a1);
            gfan[n_gates][1]=a2[0]? find_net(a2) : -1;
            gout[n_gates]=find_net(lhs);
            n_gates++;
        }
    }
    fclose(f);

    utab_init(22); memo_init(22); grow_nodes();
    nodes[0].var=0xFFFFFFFFu; nodes[1].var=0xFFFFFFFFu; n_nodes=2;

    /* variable order */
    int W = n_ins/2;
    for(int k=0;k<n_ins;k++){
        char nm[64]; int lvl;
        if(!strcmp(order,"sequential")){
            strncpy(nm,inname[k],63); lvl=k;
        } else {
            int p=k/2, which=k%2;
            snprintf(nm,64,"%c%d", which? 'b':'a', p);
            lvl=k;
        }
        int id=find_net(nm);
        nets[id].bdd = mk((u32)lvl, BDD_FALSE, BDD_TRUE);
        nets[id].done=1;
    }
    (void)W;

    printf("circuit: %s\n", argv[1]);
    printf("inputs=%d outputs=%d gates=%d order=%s budget=%s\n",
           n_ins,n_outs,n_gates,order, node_budget? argv[2]:"unlimited");
    printf("%-6s %-8s %14s %16s %10s\n","out","status","bdd_nodes","total_alloc","secs");
    fflush(stdout);

    clock_t t0=clock();
    /* evaluate gates in file order (the netlist is topologically sorted) */
    int gi=0;
    for(int oi=0; oi<n_outs; oi++){
        int target = find_net(outname[oi]);
        while(gi<n_gates && !nets[target].done){
            int o=gout[gi]; u32 a=nets[gfan[gi][0]].bdd;
            u32 b = (gfan[gi][1]>=0)? nets[gfan[gi][1]].bdd : 0;
            const char*op=gop[gi];
            u32 r;
            if(!strcmp(op,"AND")) r=bdd_and(a,b);
            else if(!strcmp(op,"OR")) r=bdd_or(a,b);
            else if(!strcmp(op,"XOR")) r=bdd_xor(a,b);
            else if(!strcmp(op,"NOT")) r=bdd_not(a);
            else if(!strcmp(op,"BUF")) r=a;
            else die("unknown gate op");
            nets[o].bdd=r; nets[o].done=1;
            gi++;
            if(budget_hit) break;
        }
        double secs=(double)(clock()-t0)/CLOCKS_PER_SEC;
        if(budget_hit){
            printf("%-6d %-8s %14s %16u %10.2f\n", oi, "BUDGET", "--", n_nodes, secs);
            printf("\nNODE BUDGET %u EXHAUSTED at output %d.\n", node_budget, oi);
            printf("This is the partial-BDD trigger condition.\n");
            return 3;
        }
        u32 sz = bdd_size(nets[target].bdd);
        printf("%-6d %-8s %14u %16u %10.2f\n", oi, "ok", sz, n_nodes, secs);
        fflush(stdout);
    }
    printf("\ncompleted: %u total nodes allocated, %llu ite calls (%llu memo hits)\n",
           n_nodes,(unsigned long long)ite_calls,(unsigned long long)ite_hits);
    return 0;
}
