#!/bin/bash
# Render every wiki diagram (*.mmd) to a PNG in wiki-content/images/.
# Needs mmdc (npm i -g @mermaid-js/mermaid-cli). Run from anywhere.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="$DIR/../../wiki-content/images"
mkdir -p "$OUT"
fail=0
for f in "$DIR"/*.mmd; do
  name="$(basename "$f" .mmd)"
  if mmdc -q -i "$f" -o "$OUT/$name.png" -c "$DIR/mermaid.config.json" -b white -w 1500 -s 2; then
    echo "ok   $name"
  else
    echo "FAIL $name"; fail=1
  fi
done
exit $fail
