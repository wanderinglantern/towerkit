#!/bin/sh
# Installer for corporate machines.
#
#   ./install.sh
#
# Tries PyPI first (standard pip env like PIP_INDEX_URL / HTTPS_PROXY is
# honored, so corporate mirrors work). If that fails, the reason is saved to
# .install-pypi.log and the prebuilt wheelhouse from the GitHub release takes
# over (github.com is the only network needed; macOS Intel/Apple Silicon,
# Python 3.12-3.13). A wheelhouse that cannot satisfy the install is thrown
# away and re-downloaded once — stale caches must never fail the install.
#
# Everything installs into ./.venv so the ./towerctl wrapper always works and
# the system Python is never touched. Re-run after every `git pull`.
#
# Afterwards:  ./towerctl edit

set -eu
cd "$(dirname "$0")"

WHEELHOUSE_URL="https://github.com/wanderinglantern/towerkit/releases/download/v0.1.0/towerkit-wheelhouse-macos.zip"
PY="${PYTHON:-python3}"
WHEELHOUSE_SHA256="6b665030ed828d14c9cfa3bee0cdb3defaf1b423ae36842687f4b5eb4fae6bde"

version=$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')

echo "→ creating .venv with $PY ($version) …"
rm -rf .venv
"$PY" -m venv .venv

fetch_wheelhouse() {
    echo "→ downloading wheelhouse (~160MB) …"
    curl -fSL --progress-bar -o wheelhouse.zip "$WHEELHOUSE_URL"
    # the corporate proxy has been caught altering pip downloads —
    # verify this artifact against the hash pinned in git
    echo "$WHEELHOUSE_SHA256  wheelhouse.zip" | shasum -a 256 -c - || {
        echo "error: wheelhouse.zip hash mismatch — the download was altered in transit." >&2
        echo "Do NOT bypass this. Re-try on a trusted network, or copy the zip manually." >&2
        rm -f wheelhouse.zip
        exit 1
    }
    rm -rf wheelhouse
    mkdir wheelhouse
    unzip -q wheelhouse.zip -d wheelhouse
    rm wheelhouse.zip
}

offline_install() {
    ./.venv/bin/pip install -q --no-index --find-links wheelhouse hatchling editables         && ./.venv/bin/pip install -q --no-index --find-links wheelhouse --no-build-isolation -e .
}

if [ "${OFFLINE:-0}" = "1" ]; then
    echo "→ OFFLINE=1 — skipping PyPI, using the wheelhouse …"
    [ -d wheelhouse ] || fetch_wheelhouse
    if ! offline_install; then
        echo "→ cached wheelhouse could not satisfy the install — refreshing it …"
        fetch_wheelhouse
        offline_install
    fi
    echo "✓ installed from the wheelhouse"
elif ./.venv/bin/pip install -q -e . >.install-pypi.log 2>&1; then
    echo "✓ installed from PyPI"
    rm -f .install-pypi.log
else
    echo "→ PyPI attempt failed (reason saved to .install-pypi.log):"
    tail -3 .install-pypi.log | sed 's/^/    /'
    echo "→ falling back to the local wheelhouse …"
    case "$version" in
        3.12|3.13) ;;
        *) echo "warning: wheelhouse targets Python 3.12/3.13; found $version" \
               "(set PYTHON=/path/to/python3.12 to override)" ;;
    esac
    [ -d wheelhouse ] || fetch_wheelhouse
    if ! offline_install; then
        echo "→ cached wheelhouse could not satisfy the install — refreshing it …"
        fetch_wheelhouse
        offline_install
    fi
    echo "✓ installed from the wheelhouse"
fi

echo
echo "✓ installed. Run:"
echo "    ./towerctl edit"
