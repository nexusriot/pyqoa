# PyQOA — development, test and packaging tasks.
#
# Quick start:
#   make venv deps deps-dev   # set up a virtualenv with runtime + test deps
#   make check                # lint, test and self-test
#   make deb                  # build a self-contained .deb in dist/

PYTHON      ?= python3
VENV        ?= .venv
PREFIX      ?= /usr/local
DESTDIR     ?=
PYTEST_ARGS ?=
DEB_REVISION ?= 1

# Prefer the project virtualenv when it exists, so `make test` works whether or
# not the caller has activated it.
PY := $(shell test -x $(VENV)/bin/python && echo $(VENV)/bin/python || echo $(PYTHON))

VERSION := $(shell $(PY) -c "import sys; sys.path.insert(0, '.'); from version import __version__; print(__version__)")
ARCH    := $(shell dpkg --print-architecture 2>/dev/null || uname -m)
DEB     := dist/pyqoa_$(VERSION)-$(DEB_REVISION)_$(ARCH).deb
TARBALL := dist/pyqoa-$(VERSION)-linux-$(shell uname -m).tar.gz

# Qt needs no display for the test suite or the self-test.
export QT_QPA_PLATFORM ?= offscreen

.DEFAULT_GOAL := help
.PHONY: help version venv deps deps-core deps-dev deps-build run test \
        test-cov lint \
        selftest check binary onefile verify-binary tarball deb deb-lint \
        sdist install uninstall clean distclean

help: ## Show this help
	@echo "PyQOA $(VERSION) — available targets:"
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[1m%-15s\033[0m %s\n", $$1, $$2}'
	@echo
	@echo "Interpreter: $(PY)"
	@echo "Variables:   PYTHON=$(PYTHON) VENV=$(VENV) PREFIX=$(PREFIX) DESTDIR=$(DESTDIR)"

version: ## Print the version
	@echo $(VERSION)

## --- environment ---

venv: ## Create the project virtualenv
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/pip install --upgrade pip

deps: ## Install runtime dependencies, optional features included
	$(PY) -m pip install -r requirements.txt

deps-core: ## Install only the dependencies PyQOA needs to run
	$(PY) -m pip install -r requirements-core.txt

deps-dev: ## Install test dependencies
	$(PY) -m pip install -r requirements-dev.txt

deps-build: ## Install packaging dependencies (PyInstaller)
	$(PY) -m pip install -r requirements-build.txt

## --- develop ---

run: ## Run the app from source
	env -u QT_QPA_PLATFORM $(PY) main.py

test: ## Run the test suite
	$(PY) -m pytest $(PYTEST_ARGS)

test-cov: ## Run the test suite with a coverage report
	$(PY) -m pytest --cov=. --cov-report=term-missing $(PYTEST_ARGS)

lint: ## Check for unused imports and undefined names
	$(PY) -m pyflakes *.py ui/*.py tests/*.py

selftest: ## Check the feature surface from source
	$(PY) main.py --selftest

check: lint test selftest ## Lint, test and self-test

## --- package ---

binary: ## Build the one-directory bundle in dist/pyqoa/
	PYTHON=$(PY) packaging/build-binary.sh

onefile: ## Build a single-file executable in dist/onefile/pyqoa
	PYTHON=$(PY) packaging/build-binary.sh --onefile

verify-binary: ## Run the built bundle's self-test
	./dist/pyqoa/pyqoa --selftest
	@test ! -x dist/onefile/pyqoa || ./dist/onefile/pyqoa --selftest

tarball: binary ## Build a portable .tar.gz
	PYTHON=$(PY) packaging/build-tarball.sh

deb: binary ## Build a self-contained .deb
	PYTHON=$(PY) packaging/build-deb.sh $(DEB_REVISION)

deb-lint: deb ## Run lintian over the built .deb
	lintian --no-tag-display-limit $(DEB) || true

sdist: ## Build a source tarball from the git tree
	@mkdir -p dist
	git archive --format=tar.gz --prefix=pyqoa-$(VERSION)/ \
		-o dist/pyqoa-$(VERSION)-src.tar.gz HEAD
	@echo "sdist: dist/pyqoa-$(VERSION)-src.tar.gz"

install: binary ## Install the bundle under $(PREFIX) (use sudo for /usr/local)
	PYTHON=$(PY) packaging/install-tree.sh "$(DESTDIR)" "$(PREFIX)"

uninstall: ## Remove an installation made by `make install`
	rm -rf "$(DESTDIR)$(PREFIX)/lib/pyqoa"
	rm -f  "$(DESTDIR)$(PREFIX)/bin/pyqoa"
	rm -f  "$(DESTDIR)$(PREFIX)/share/applications/pyqoa.desktop"
	rm -f  "$(DESTDIR)$(PREFIX)/share/icons/hicolor/scalable/apps/pyqoa.svg"
	rm -rf "$(DESTDIR)$(PREFIX)/share/doc/pyqoa"
	@echo "uninstall: removed from $(DESTDIR)$(PREFIX)"

## --- clean ---

clean: ## Remove build intermediates and caches
	rm -rf build .pytest_cache .coverage htmlcov
	find . -path ./$(VENV) -prune -o -name __pycache__ -type d -print0 \
		| xargs -0 -r rm -rf

distclean: clean ## Also remove built artefacts in dist/
	rm -rf dist
