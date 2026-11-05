# Build the CIAAW Turtle (and the optional QuickStatements export) and run the tests.
# Run `make help` for an overview.

PYTHON      ?= python3
DATA_DIR    ?= data
BUILD_DIR   ?= build

# Pages saved from https://ciaaw.org (see README.md, step 1)
WEIGHTS     ?= $(DATA_DIR)/atomic-weights.htm
ABUNDANCES  ?= $(DATA_DIR)/isotopic-abundances.htm
SUPPLEMENT  ?= supplement.csv

# Optional: Turtle of an earlier release, enables the deprecation chain
# e.g. make ttl PREVIOUS=example-output.ttl
PREVIOUS    ?=

TTL         ?= $(BUILD_DIR)/ciaaw.ttl
QS          ?= $(BUILD_DIR)/quickstatements.csv

.PHONY: help install ttl quickstatements test clean

help:
	@echo "make install         install the Python dependencies"
	@echo "make ttl             create $(TTL) from the saved CIAAW pages"
	@echo "make quickstatements create $(QS) from the Turtle"
	@echo "make test            run the unit tests"
	@echo "make clean           remove $(BUILD_DIR)"

install:
	$(PYTHON) -m pip install -r requirements.txt

ttl: $(TTL)

$(TTL): ciaaw2ttl.py $(WEIGHTS) $(ABUNDANCES) $(SUPPLEMENT) $(PREVIOUS)
	@mkdir -p $(BUILD_DIR)
	$(PYTHON) ciaaw2ttl.py --weights $(WEIGHTS) --abundances $(ABUNDANCES) \
		--supplement $(SUPPLEMENT) $(if $(PREVIOUS),--previous $(PREVIOUS)) -o $@

quickstatements: $(QS)

$(QS): ttl2quickstatements.py $(TTL)
	$(PYTHON) ttl2quickstatements.py $(TTL) -o $@

test:
	$(PYTHON) -m pytest test_ciaaw2ttl.py

clean:
	rm -rf $(BUILD_DIR)
