#!/bin/sh
# Installer for corporate machines.
#
#   ./install.sh
#
# Tries PyPI first — on machines where pip has network access (directly or
# through the corporate proxy) every dependency, including newly added ones,
# resolves in seconds after a `git pull`. Machines with no PyPI access fall
# back to the prebuilt wheelhouse from the GitHub release (the only network
# needed there is github.com; macOS Intel/Apple Silicon, Python 3.12-3.13).
#
# Everything installs into ./.venv so the ./towerctl wrapper always works and
# the system Python is never touched. Re-run after every `git pull`.
#
# Afterwards:  ./towerctl edit

set -eu
cd "$(dirname "$0")"

WHEELHOUSE_URL="https://github.com/wanderinglantern/towerkit/releases/download/v0.1.0/towerkit-wheelhouse-macos.zip"
PY="${PYTHON:-python3}"

version=$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')

echo "→ creating .venv with $PY ($version) …"
rm -rf .venv
"$PY" -m venv .venv

echo "→ trying PyPI …"
if ./.venv/bin/pip install -q -e . 2>/dev/null; then
    echo "✓ installed from PyPI"
else
    echo "→ no PyPI access — installing from the local wheelhouse …"
    case "$version" in
        3.12|3.13) ;;
        *) echo "warning: wheelhouse targets Python 3.12/3.13; found $version" \
               "(set PYTHON=/path/to/python3.12 to override)" ;;
    esac
    # refresh when a required wheel is missing — new deps land in the wheelhouse
    if [ -d wheelhouse ] && ! ls wheelhouse/textual_autocomplete-*.whl >/dev/null 2>&1; then
        echo "→ wheelhouse is stale (missing textual-autocomplete) — refreshing …"
        rm -rf wheelhouse
    fi
    if [ ! -d wheelhouse ]; then
        echo "→ downloading wheelhouse (~160MB, one time) …"
        curl -fSL --progress-bar -o wheelhouse.zip "$WHEELHOUSE_URL"
        mkdir wheelhouse
        unzip -q wheelhouse.zip -d wheelhouse
        rm wheelhouse.zip
    fi
    ./.venv/bin/pip install -q --no-index --find-links wheelhouse hatchling editables
    ./.venv/bin/pip install -q --no-index --find-links wheelhouse --no-build-isolation -e .
fi

echo
echo "✓ installed. Run:"
echo "    ./towerctl edit"
