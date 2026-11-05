# Build the CIAAW Turtle (and the optional QuickStatements export), the model and the site,
# and run the tests. Run `make help` for an overview.

PYTHON      ?= python3
DATA_DIR    ?= data
BUILD_DIR   ?= build
SITE_DIR    ?= _site

# Pages saved from https://ciaaw.org (see README.md, step 2)
WEIGHTS     ?= $(DATA_DIR)/atomic-weights.htm
ABUNDANCES  ?= $(DATA_DIR)/isotopic-abundances.htm
SUPPLEMENT  ?= supplement.csv

# Optional: Turtle of an earlier release, enables the deprecation chain
# e.g. make ttl PREVIOUS=example-output.ttl
PREVIOUS    ?=

TTL         ?= $(BUILD_DIR)/ciaaw.ttl
QS          ?= $(BUILD_DIR)/quickstatements.csv

# Data shown on the site. Use SITE_DATA=build/ciaaw.ttl after `make ttl`.
SITE_DATA     ?= example-output.ttl
SITE_REVISION ?= example-output-next.ttl

WEIGHTS_URL    ?= https://ciaaw.org/atomic-weights.htm
ABUNDANCES_URL ?= https://ciaaw.org/isotopic-abundances.htm

.PHONY: help install download ttl example quickstatements check-wikidata test model validate site serve clean

help:
	@echo "make install         install the Python dependencies"
	@echo "make download        download the CIAAW pages into $(DATA_DIR)"
	@echo "make ttl             create $(TTL) from the saved CIAAW pages"
	@echo "make example         create the example Turtle in $(BUILD_DIR) from the test fixtures, offline"
	@echo "make quickstatements create $(QS) from the Turtle"
	@echo "make check-wikidata compare the abundances in $(TTL) with Wikidata"
	@echo "make test            run all tests"
	@echo "make model           regenerate the SHACL shapes and diagrams from the DCTAP"
	@echo "make validate        check the site data against the SHACL shapes"
	@echo "make site            build the static site into $(SITE_DIR)"
	@echo "make serve           build the site and serve it on http://localhost:8000"
	@echo "make clean           remove $(BUILD_DIR) and $(SITE_DIR)"

install:
	$(PYTHON) -m pip install -r requirements.txt

download:
	@mkdir -p $(DATA_DIR)
	curl -fsSL -o $(WEIGHTS) $(WEIGHTS_URL)
	curl -fsSL -o $(ABUNDANCES) $(ABUNDANCES_URL)

ttl: $(TTL)

$(TTL): ciaaw2ttl.py $(WEIGHTS) $(ABUNDANCES) $(SUPPLEMENT) $(PREVIOUS)
	@mkdir -p $(dir $@)
	$(PYTHON) ciaaw2ttl.py --weights $(WEIGHTS) --abundances $(ABUNDANCES) \
		--supplement $(SUPPLEMENT) $(if $(PREVIOUS),--previous $(PREVIOUS)) -o $@

example:
	@mkdir -p $(BUILD_DIR)
	$(PYTHON) ciaaw2ttl.py --weights fixtures/atomic-weights.htm --abundances fixtures/isotopic-abundances.htm \
		--supplement fixtures/supplement.csv -o $(BUILD_DIR)/example-output.ttl
	$(PYTHON) ciaaw2ttl.py --weights fixtures/atomic-weights-next.htm --abundances fixtures/isotopic-abundances.htm \
		--supplement fixtures/supplement-next.csv --previous $(BUILD_DIR)/example-output.ttl \
		-o $(BUILD_DIR)/example-output-next.ttl

quickstatements: $(QS)

$(QS): ttl2quickstatements.py $(TTL)
	$(PYTHON) ttl2quickstatements.py $(TTL) -o $@

check-wikidata: $(TTL)
	$(PYTHON) check_wikidata.py $(TTL)

test:
	$(PYTHON) -m pytest -q

model:
	$(PYTHON) tap2model.py

validate:
	$(PYTHON) tap2model.py --validate $(SITE_DATA) $(SITE_REVISION)

site:
	$(PYTHON) build_site.py --data $(SITE_DATA) --revision $(SITE_REVISION) --out $(SITE_DIR)

serve: site
	$(PYTHON) -m http.server 8000 --directory $(SITE_DIR)

clean:
	rm -rf $(BUILD_DIR) $(SITE_DIR) .pytest_cache __pycache__
