#!/bin/sh
# Offline installer for corporate machines with no PyPI access.
#
#   ./install.sh
#
# Downloads the wheelhouse (towerkit + every dependency, prebuilt for macOS
# Intel/Apple Silicon, Python 3.12-3.13) from the GitHub release — the only
# network access needed is github.com — then installs the CURRENT checkout
# into ./.venv, entirely from local wheels. Re-run after `git pull`; the
# wheelhouse is cached and only re-downloaded if deleted.
#
# Afterwards:  ./towerctl edit

set -eu
cd "$(dirname "$0")"

WHEELHOUSE_URL="https://github.com/wanderinglantern/towerkit/releases/download/v0.1.0/towerkit-wheelhouse-macos.zip"
PY="${PYTHON:-python3}"

version=$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
case "$version" in
    3.12|3.13) ;;
    *) echo "warning: wheelhouse targets Python 3.12/3.13; found $version" \
           "(set PYTHON=/path/to/python3.12 to override)" ;;
esac

if [ ! -d wheelhouse ]; then
    echo "→ downloading wheelhouse (~160MB, one time) …"
    curl -fSL --progress-bar -o wheelhouse.zip "$WHEELHOUSE_URL"
    mkdir wheelhouse
    unzip -q wheelhouse.zip -d wheelhouse
    rm wheelhouse.zip
fi

echo "→ creating .venv with $PY ($version) …"
rm -rf .venv
"$PY" -m venv .venv

echo "→ installing from local wheels (no PyPI) …"
./.venv/bin/pip install -q --no-index --find-links wheelhouse hatchling editables
./.venv/bin/pip install -q --no-index --find-links wheelhouse --no-build-isolation -e .

echo
echo "✓ installed. Run:"
echo "    ./towerctl edit"
