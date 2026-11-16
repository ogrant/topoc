NAME="$1"

mkdir -p "./configs/$NAME"

uv run topoc generate \
  --topology "./testdata/$NAME.topo" \
  --context "./testdata/$NAME.context.json" \
  --catalog "./testdata/$NAME.catalog.json" \
  --out "./configs/$NAME"

uv run topoc view \
  --cluster "$NAME" \
  --catalog "./testdata/$NAME.catalog.json" \
  --output-dir "./configs/$NAME" ./configs/$NAME/*.xml
