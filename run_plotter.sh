#!/usr/bin/env bash

set -u

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

PYTHON="$SCRIPT_DIR/.venv/bin/python"

if [[ ! -x "$PYTHON" ]]; then
    echo "ERROR: Virtual environment not found."
    echo "Create it with:"
    echo "  python -m venv .venv"
    echo "  .venv/bin/python -m pip install -r REQUIREMENTS"
    exit 1
fi

if [[ -f "$SCRIPT_DIR/Src/plotter_gui.py" ]]; then
    GUI_SCRIPT="$SCRIPT_DIR/Src/plotter_gui.py"
elif [[ -f "$SCRIPT_DIR/plotter_gui.py" ]]; then
    GUI_SCRIPT="$SCRIPT_DIR/plotter_gui.py"
else
    echo "ERROR: Could not find plotter_gui.py."
    exit 1
fi

exec "$PYTHON" "$GUI_SCRIPT"
