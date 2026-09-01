# Author: Mitchell A. Thornton
# Copyright (c) 2026 Mitchell A. Thornton
# Build the CUDD version-space counting engine.
#
#   make CUDD=/path/to/cudd
#
# CUDD is not vendored.  Point CUDD at either
#
#   (a) an unpacked CUDD 3.0.0 source tree (https://github.com/ivmai/cudd)
#       that has been through ./configure && make, which leaves libcudd.a
#       under cudd/.libs and the headers spread across cudd/, util/, st/,
#       epd/ and mtr/; or
#   (b) an install prefix, with headers under include/ and the library under
#       lib/.
#
# Both layouts are probed, so the usual case needs no editing.  If your tree
# is neither, set INC and LIB directly:
#
#   make INC="-I/somewhere/include" LIB="-L/somewhere/lib -lcudd -lm"
#
# `make probe` reports what would be used and what it can actually find,
# without building.  Run that first when the paths are in doubt; the compiler
# error for a wrong CUDD path is not helpful.
#
# The engine is optional.  Everything in the paper is reproducible from the
# Python engines alone; the C engine exists for speed and for a third
# independent count.

CUDD ?= $(HOME)/cudd-3.0.0
CC   ?= cc
CFLAGS ?= -O2 -Wall

# Source-tree layout first, install-prefix layout second.  Naming a directory
# that does not exist is harmless.
INC ?= -I$(CUDD)/cudd -I$(CUDD)/util -I$(CUDD)/st -I$(CUDD)/epd \
       -I$(CUDD)/mtr -I$(CUDD) -I$(CUDD)/include

# .libs is where libtool leaves the archive in a configured source tree, on
# Linux and macOS alike.  lib/ is the install-prefix case.
LIB ?= -L$(CUDD)/cudd/.libs -L$(CUDD)/lib -L$(CUDD) -lcudd -lm

all: engineB_cudd

engineB_cudd: engineB_cudd.c
	@test -f "$(CUDD)/cudd/cudd.h" -o -f "$(CUDD)/include/cudd.h" || \
	  (echo ""; \
	   echo "cudd.h not found under CUDD=$(CUDD)"; \
	   echo "  expected $(CUDD)/cudd/cudd.h      (source tree), or"; \
	   echo "           $(CUDD)/include/cudd.h   (install prefix)"; \
	   echo "Run 'make CUDD=$(CUDD) probe' to see what is there."; \
	   echo ""; exit 1)
	$(CC) $(CFLAGS) -o $@ $< $(INC) $(LIB)
	@echo "built ./engineB_cudd"

probe:
	@echo "CUDD = $(CUDD)"
	@echo "INC  = $(INC)"
	@echo "LIB  = $(LIB)"
	@echo "--- found ---"
	@for f in "$(CUDD)/cudd/cudd.h" "$(CUDD)/include/cudd.h"; do \
	   test -f "$$f" && echo "header  $$f"; done; true
	@for f in "$(CUDD)/cudd/.libs/libcudd.a" "$(CUDD)/lib/libcudd.a" \
	          "$(CUDD)/cudd/.libs/libcudd.dylib" "$(CUDD)/lib/libcudd.dylib" \
	          "$(CUDD)/cudd/.libs/libcudd.so" "$(CUDD)/lib/libcudd.so"; do \
	   test -f "$$f" && echo "library $$f"; done; true
	@echo "(nothing listed under 'found' means CUDD points somewhere else)"

clean:
	rm -f engineB_cudd

.PHONY: all clean probe
