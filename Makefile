.PHONY: test test-all test-mcp lint build publish clean

test:
	python -m pytest tests/ -v

test-all:
	python -m pytest tests/ -v -m ""

test-mcp:
	python -m pytest tests/ -v -m "mcp"

lint:
	python -m ruff check src/ tests/
	python -m ruff format --check src/ tests/

format:
	python -m ruff format src/ tests/

build: clean
	python -m build

publish: build
	python -m twine upload dist/*

clean:
	rm -rf dist/ build/ *.egg-info src/*.egg-info
