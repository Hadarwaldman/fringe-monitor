#!/usr/bin/env bash
# Publish a local scan's output to the live site's data bucket.
#
# This is the manual fallback for when the Lambdas can't reach edfringe
# (Cloudflare blocks datacenter IPs; the residential proxy can run out of
# credit — err 402). Run `python scan_fringe.py` from a residential IP, then
# this script uploads the JSON exactly the way full_scan's put_json_s3 does:
# pre-gzipped with Content-Encoding so CloudFront passes it through (it will
# NOT compress objects >10MB itself) and browsers decode transparently.
#
# Usage: scripts/publish_scan.sh [output-dir]     (default: output)
set -euo pipefail

DIR="${1:-output}"
export AWS_PROFILE="${AWS_PROFILE:-hadar-pc}"
export AWS_REGION="${AWS_REGION:-us-east-1}"

if [[ ! -f "$DIR/latest.json" ]]; then
  echo "error: $DIR/latest.json not found — run: python scan_fringe.py" >&2
  exit 1
fi

BUCKET=$(aws s3api list-buckets --query 'Buckets[].Name' --output text | tr '\t' '\n' | grep '^fringe-monitor-data-' | head -1)
if [[ -z "$BUCKET" ]]; then
  echo "error: could not find the fringe-monitor data bucket" >&2
  exit 1
fi
echo "Publishing $DIR/ → s3://$BUCKET/data/"

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

upload_json_gz() {
  local name="$1"
  [[ -f "$DIR/$name" ]] || { echo "  skip $name (not in $DIR)"; return 0; }
  gzip -9 -c "$DIR/$name" > "$TMP/$name"
  aws s3 cp --only-show-errors "$TMP/$name" "s3://$BUCKET/data/$name" \
    --content-type application/json \
    --content-encoding gzip \
    --cache-control no-cache
  echo "  data/$name ($(du -h "$TMP/$name" | cut -f1) gzipped, was $(du -h "$DIR/$name" | cut -f1))"
}

upload_json_gz latest.json
upload_json_gz details.json
upload_json_gz history.json

if [[ -f "$DIR/fringe_availability.csv" ]]; then
  aws s3 cp --only-show-errors "$DIR/fringe_availability.csv" "s3://$BUCKET/data/fringe_availability.csv" \
    --content-type text/csv --cache-control no-cache
  echo "  data/fringe_availability.csv"
fi

echo "Done. CloudFront /data/* TTL is ≤60s — the site picks this up within a minute."
