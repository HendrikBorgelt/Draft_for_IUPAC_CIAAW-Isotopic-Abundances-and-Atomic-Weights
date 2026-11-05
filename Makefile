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

# SPARQL endpoint: oxigraph-cloudflare-webworker, run from a pinned commit (see README.md).
# SPARQL_ENDPOINT is the public URL. Without it the site leaves out the live query form.
WORKER_REPO ?= https://github.com/JervenBolleman/oxigraph-cloudflare-webworker.git
WORKER_REF  ?= 406eef7a49cfc497787ca03e0772d309d21d14ca
WORKER_DIR  ?= $(BUILD_DIR)/sparql-worker
WORKER_NAME ?= ciaaw-sparql-prototype
SPARQL_DATA ?= https://hendrikborgelt.github.io/Draft_for_IUPAC_CIAAW-Isotopic-Abundances-and-Atomic-Weights/data/ciaaw.ttl
SPARQL_ENDPOINT ?=

# The queries in sparql/queries as sparql-examples Turtle in sparql/examples, made by sparql-examples-utils
# (https://github.com/sib-swiss/sparql-examples-utils). The release jar is downloaded into $(BUILD_DIR).
# The base IRI is where the .rq files are published on the site.
SPARQL_EXAMPLES_UTILS_VERSION ?= 2.0.24
SPARQL_EXAMPLES_JAR  ?= $(BUILD_DIR)/sparql-examples-utils-$(SPARQL_EXAMPLES_UTILS_VERSION)-uber.jar
SPARQL_EXAMPLES_URL  ?= https://github.com/sib-swiss/sparql-examples-utils/releases/download/v$(SPARQL_EXAMPLES_UTILS_VERSION)/sparql-examples-utils-$(SPARQL_EXAMPLES_UTILS_VERSION)-uber.jar
SPARQL_EXAMPLES_BASE ?= https://hendrikborgelt.github.io/Draft_for_IUPAC_CIAAW-Isotopic-Abundances-and-Atomic-Weights/sparql/queries/

.PHONY: help install download ttl example quickstatements check-wikidata wikidata-links wikidata-comparison references test model validate sparql-examples print-sparql-examples-version site serve sparql-dev sparql-deploy clean

help:
	@echo "make install         install the Python dependencies"
	@echo "make download        download the CIAAW pages into $(DATA_DIR)"
	@echo "make ttl             create $(TTL) from the saved CIAAW pages"
	@echo "make example         create the example Turtle in $(BUILD_DIR) from the test fixtures, offline"
	@echo "make quickstatements create $(QS) from the Turtle"
	@echo "make check-wikidata compare the abundances in $(TTL) with Wikidata"
	@echo "make wikidata-links refresh site/wikidata.csv, the Wikidata items linked from the element pages"
	@echo "make wikidata-comparison compare $(SITE_DATA) with Wikidata, for the site (site/wikidata-comparison.json)"
	@echo "make references refresh site/references.json, the formatted sources for the Bibliography"
	@echo "make test            run all tests"
	@echo "make model           regenerate the SHACL shapes and diagrams from the DCTAP"
	@echo "make validate        check the site data against the SHACL shapes"
	@echo "make sparql-examples convert the queries in sparql/queries to sparql-examples Turtle in sparql/examples"
	@echo "make site            build the static site into $(SITE_DIR)"
	@echo "make serve           build the site and serve it on http://localhost:8000"
	@echo "make sparql-dev      run the SPARQL worker locally on http://localhost:8787/sparql"
	@echo "make sparql-deploy   deploy the SPARQL worker to Cloudflare"
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

wikidata-comparison:
	$(PYTHON) check_wikidata.py $(SITE_DATA) --json site/wikidata-comparison.json > /dev/null

wikidata-links:
	$(PYTHON) wikidata_links.py

# override if the system npm does not work, e.g. make references NPM="node ~/opt/npm10/bin/npm-cli.js"
NPM ?= npm

references: node_modules
	node update_references.js $(SITE_DATA) $(SITE_REVISION)

node_modules: package.json
	$(NPM) install
	touch node_modules

test:
	$(PYTHON) -m pytest -q

model:
	$(PYTHON) tap2model.py

validate:
	$(PYTHON) tap2model.py --validate $(SITE_DATA) $(SITE_REVISION)

print-sparql-examples-version:
	@echo $(SPARQL_EXAMPLES_UTILS_VERSION)

# The jar is downloaded once per version. CI caches $(BUILD_DIR) to avoid downloading it again.
$(SPARQL_EXAMPLES_JAR):
	@mkdir -p $(dir $@)
	curl -fsSL -o $@.partial $(SPARQL_EXAMPLES_URL)
	mv $@.partial $@

# Committed, so the site build does not need Java. Run again after changing a query.
sparql-examples: $(SPARQL_EXAMPLES_JAR) $(wildcard sparql/queries/*.rq)
	java -jar $(SPARQL_EXAMPLES_JAR) import-rq -i sparql/queries -b $(SPARQL_EXAMPLES_BASE) -o sparql/examples

site:
	$(PYTHON) build_site.py --data $(SITE_DATA) --revision $(SITE_REVISION) --out $(SITE_DIR) \
		--sparql-endpoint "$(SPARQL_ENDPOINT)"

serve: site
	$(PYTHON) -m http.server 8000 --directory $(SITE_DIR)

$(WORKER_DIR)/.installed-$(WORKER_REF):
	rm -rf $(WORKER_DIR)
	git clone $(WORKER_REPO) $(WORKER_DIR)
	git -C $(WORKER_DIR) checkout --detach $(WORKER_REF)
	cd $(WORKER_DIR) && npm ci
	touch $@

sparql-dev: $(WORKER_DIR)/.installed-$(WORKER_REF)
	cd $(WORKER_DIR) && npx wrangler dev --name $(WORKER_NAME) --var TURTLE_URL:$(SPARQL_DATA)

sparql-deploy: $(WORKER_DIR)/.installed-$(WORKER_REF)
	cd $(WORKER_DIR) && npx wrangler deploy --name $(WORKER_NAME) --var TURTLE_URL:$(SPARQL_DATA)

clean:
	rm -rf $(BUILD_DIR) $(SITE_DIR) .pytest_cache __pycache__
