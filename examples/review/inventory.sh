#!/usr/bin/env bash
# Run from a checkout with jt (or uv run bash) and jq available. No API key needed.
set -eu
review_dir=${1:-.jevotron/review-demo}
mkdir -p "$review_dir"
review_dir=$(cd "$review_dir" && pwd)
cd "$(dirname "$0")/../.."

jt review import docs/assets/results/inventory.jsonl \
  --source examples/inventory/items.yaml --config examples/inventory/jev_config.py \
  --store "$review_dir/reviews.sqlite3"

# Deliberate example decisions on the two known errors in the bundled inventory.
quantity_decision=$(jt review decide cable /quantity confirmed-error \
  --note 'The synthetic inventory quantity is invalid.' \
  --store "$review_dir/reviews.sqlite3" | jq -r .decision_id)
category_decision=$(jt review decide mug /category confirmed-error \
  --note 'A ceramic mug belongs to kitchenware.' \
  --store "$review_dir/reviews.sqlite3" | jq -r .decision_id)

jt review export --store "$review_dir/reviews.sqlite3" > "$review_dir/decisions.jsonl"
jt review exemplars --decision "$quantity_decision" --decision "$category_decision" \
  --store "$review_dir/reviews.sqlite3" > "$review_dir/exemplars.json"
jt preview examples/inventory/items.yaml --config examples/inventory/jev_config.py \
  --exemplars "$review_dir/exemplars.json" --limit 1 > "$review_dir/preview.jsonl"
printf 'Review store, decisions, exemplars, and preview saved in %s\n' "$review_dir"
