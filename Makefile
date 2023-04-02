.PHONY: install
install:
	pip install -r requirements.txt

.PHONY: install-dev
install-dev: install
	pip install -r requirements-dev.txt
	pip install -e .

.PHONY: fmt
fmt:
	isort .
	black .
	ruff --fix azchaosaws/ tests/

.PHONY: lint
lint:
	ruff azchaosaws/ tests/

.PHONY: test
test:
	python setup.py test

.PHONY: build
build: install-dev
	python setup.py bdist_wheel