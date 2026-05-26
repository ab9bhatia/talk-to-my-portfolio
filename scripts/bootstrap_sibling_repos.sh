#!/usr/bin/env bash
# One-time: create standalone expenses + learnings repos next to portfolio/.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PA_DIR="$(cd "$ROOT/.." && pwd)"
EXPENSES_DIR="$PA_DIR/expenses"
LEARNINGS_DIR="$PA_DIR/learnings"

echo "Portfolio repo: $ROOT"
echo "Creating: $EXPENSES_DIR"
echo "Creating: $LEARNINGS_DIR"

mkdir -p "$EXPENSES_DIR" "$LEARNINGS_DIR"

# --- Expenses ---
rsync -a --delete \
  "$ROOT/modules/expenses/" "$EXPENSES_DIR/modules/expenses/"
mkdir -p "$EXPENSES_DIR/modules"
cp "$ROOT/modules/__init__.py" "$EXPENSES_DIR/modules/"

mkdir -p "$EXPENSES_DIR/shared/web"
cp "$ROOT/shared/__init__.py" "$EXPENSES_DIR/shared/" 2>/dev/null || touch "$EXPENSES_DIR/shared/__init__.py"
cp "$ROOT/shared/web/__init__.py" "$EXPENSES_DIR/shared/web/" 2>/dev/null || true

rsync -a "$ROOT/shared/web/templates/expenses/" "$EXPENSES_DIR/shared/web/templates/expenses/"
rsync -a "$ROOT/shared/web/templates/shared/" "$EXPENSES_DIR/shared/web/templates/shared/"
cp "$ROOT/shared/web/templates/base.html" "$EXPENSES_DIR/shared/web/templates/base.html.expenses-stub" 2>/dev/null || true

rsync -a "$ROOT/shared/web/static/css/" "$EXPENSES_DIR/shared/web/static/css/"
for f in expenses.js expenses-month.js expenses-month-close.js expenses-tag.js nav-loader.js; do
  cp "$ROOT/shared/web/static/js/$f" "$EXPENSES_DIR/shared/web/static/js/" 2>/dev/null || true
done
mkdir -p "$EXPENSES_DIR/shared/web/static/js"

# --- Learnings ---
rsync -a --delete \
  "$ROOT/modules/learnings/" "$LEARNINGS_DIR/modules/learnings/"
cp "$ROOT/modules/__init__.py" "$LEARNINGS_DIR/modules/"

mkdir -p "$LEARNINGS_DIR/shared/web"
cp "$ROOT/shared/__init__.py" "$LEARNINGS_DIR/shared/" 2>/dev/null || touch "$LEARNINGS_DIR/shared/__init__.py"

rsync -a "$ROOT/shared/web/templates/learnings/" "$LEARNINGS_DIR/shared/web/templates/learnings/"
rsync -a "$ROOT/shared/web/templates/shared/" "$LEARNINGS_DIR/shared/web/templates/shared/"
rsync -a "$ROOT/shared/web/static/css/" "$LEARNINGS_DIR/shared/web/static/css/"
cp "$ROOT/shared/web/static/js/learnings.js" "$LEARNINGS_DIR/shared/web/static/js/" 2>/dev/null || true
cp "$ROOT/shared/web/static/js/nav-loader.js" "$LEARNINGS_DIR/shared/web/static/js/" 2>/dev/null || true
mkdir -p "$LEARNINGS_DIR/shared/web/static/js"

echo "Done copying module trees. Apply main.py / config from docs/multi-repo.md if not already present."
