.PHONY: render validate test lint typecheck check clean

THEME ?= themes/marsh.json
OUT   ?= dist

render: validate
	uv run towerctl render programs/atomic-2026.json --theme $(THEME) --out $(OUT) --format svg,pdf,png
	uv run towerctl render programs/atomic-2027.json --theme $(THEME) --out $(OUT) --format svg,pdf,png
	uv run towerctl compare programs/atomic-2026.json programs/atomic-2027.json --theme $(THEME) --out $(OUT) --format svg,pdf,png

validate:
	uv run towerctl validate programs/*.json

test:
	uv run --group dev pytest -q

lint:
	uv run --group dev ruff check src tests

typecheck:
	uv run --group dev mypy src/towerkit

check: lint test validate

clean:
	rm -rf dist/*.svg dist/*.pdf dist/*.png
