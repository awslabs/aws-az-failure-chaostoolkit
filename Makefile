.PHONY: install
install:
	pip install -r requirements.txt

.PHONY: install-dev
install-dev: install
	pip install -r requirements-dev.txt
	pip install -e .

.PHONY: fmt
fmt:
	black azchaosaws/ tests/

.PHONY: lint
lint: fmt
	flake8 azchaosaws/ tests/

.PHONY: tests
tests: fmt
	python setup.py test