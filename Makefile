.PHONY: install
install:
	pip install -r requirements.txt

.PHONY: install-dev
install-dev: install
	pip install -r requirements-dev.txt
	pip install -e .

.PHONY: fmt
fmt:
	black .

.PHONY: lint
lint: fmt
	flake8 azchaosaws/ tests/

.PHONY: test
test: fmt
	python setup.py test

.PHONY: build
build: install-dev
	python setup.py bdist_wheel