#!/usr/bin/env bash
# Coggly setup. Installs nothing beyond the optional console script, checks the
# interpreter, and EXITS - the tool itself is started per command, not by this
# script. Safe to re-run.
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"

if ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "error: $PYTHON not found - Coggly needs Python 3.9 or newer on PATH" >&2
    exit 1
fi

if ! "$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'; then
    echo "error: Coggly needs Python 3.9 or newer; $PYTHON is $("$PYTHON" -V 2>&1)" >&2
    exit 1
fi

# Coggly has no third-party dependencies. The editable install only puts the
# `coggly` console script on PATH; when pip or the package index is unavailable
# the tool still runs straight from the file, so that step is best-effort.
if "$PYTHON" -m pip --version >/dev/null 2>&1; then
    if "$PYTHON" -m pip install --upgrade --quiet --disable-pip-version-check pip setuptools wheel &&
       "$PYTHON" -m pip install --quiet --disable-pip-version-check -e .; then
        echo "installed the optional 'coggly' console script"
    else
        echo "note: could not install the optional 'coggly' console script (offline?);" >&2
        echo "      'python3 coggly.py' needs no install at all" >&2
    fi
else
    echo "note: pip is not available; 'python3 coggly.py' needs no install at all" >&2
fi

# Cheap self-check: an import or entry-point error must fail here, not at first use.
"$PYTHON" -c "import coggly"
"$PYTHON" coggly.py --version

echo "Setup complete."
echo "Next: python3 coggly.py --help    (walkthrough: bash demo.sh, tests: python3 -m unittest -v)"
