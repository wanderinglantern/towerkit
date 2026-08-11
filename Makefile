.PHONY: render validate test lint typecheck check clean wheelhouse

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

# Offline wheelhouse for corporate machines: towerkit + all runtime deps,
# macOS x86_64 + arm64, CPython 3.12/3.13. Attach the zip to a GitHub release.
wheelhouse:
	rm -rf wheelhouse towerkit-wheelhouse-macos.zip
	uv build --wheel -o wheelhouse
	uv export --frozen --no-dev --no-emit-project --no-hashes -o wheelhouse/requirements.txt
	for pyver in 312 313; do \
	  python3 -m pip download -r wheelhouse/requirements.txt -d wheelhouse --only-binary=:all: \
	    --python-version $$pyver --implementation cp \
	    --platform macosx_11_0_arm64 --platform macosx_12_0_arm64 \
	    --platform macosx_10_9_universal2 --platform macosx_11_0_universal2 -q; \
	  python3 -m pip download -r wheelhouse/requirements.txt -d wheelhouse --only-binary=:all: \
	    --python-version $$pyver --implementation cp \
	    --platform macosx_10_9_x86_64 --platform macosx_10_12_x86_64 --platform macosx_10_13_x86_64 \
	    --platform macosx_11_0_x86_64 --platform macosx_12_0_x86_64 \
	    --platform macosx_10_9_universal2 --platform macosx_11_0_universal2 -q; \
	done
	cd wheelhouse && zip -q -r ../towerkit-wheelhouse-macos.zip *.whl
