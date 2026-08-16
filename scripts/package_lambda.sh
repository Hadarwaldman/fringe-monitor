#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="$ROOT/build/lambda"
OUT="$ROOT/build/lambda.zip"

rm -rf "$BUILD" "$OUT"
mkdir -p "$BUILD"

python3 -m pip install \
  -r "$ROOT/backend/requirements.txt" \
  -t "$BUILD" \
  --quiet \
  --disable-pip-version-check

cp -R "$ROOT/backend/fringe_lib" "$BUILD/fringe_lib"
cp "$ROOT/backend/lambdas/full_scan/handler.py" "$BUILD/full_scan.py"
cp "$ROOT/backend/lambdas/watchlist/handler.py" "$BUILD/watchlist.py"
cp "$ROOT/backend/lambdas/monitor_check/handler.py" "$BUILD/monitor_check.py"
cp "$ROOT/backend/lambdas/wishlist_refresh/handler.py" "$BUILD/wishlist_refresh.py"
cp "$ROOT/backend/lambdas/api/handler.py" "$BUILD/api.py"

# Drop caches / tests to keep the zip smaller.
find "$BUILD" -type d -name '__pycache__' -prune -exec rm -rf {} +
find "$BUILD" -type d -name 'tests' -prune -exec rm -rf {} +
find "$BUILD" -type d -name '*.dist-info' -prune -exec rm -rf {} + 2>/dev/null || true

cd "$BUILD"
zip -qr "$OUT" .
echo "Wrote $OUT ($(du -h "$OUT" | awk '{print $1}'))"
