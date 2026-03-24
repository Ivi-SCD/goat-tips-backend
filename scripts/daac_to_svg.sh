#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DIAGRAMS_DIR="$ROOT_DIR/docs/diagrams"
ASSETS_DIR="$DIAGRAMS_DIR/assets"

mkdir -p "$ASSETS_DIR"

if ! command -v d2 &>/dev/null; then
  echo "❌  d2 não encontrado. Instale em: https://d2lang.com/tour/install"
  exit 1
fi

shopt -s nullglob
files=("$DIAGRAMS_DIR"/*.d2)

if [[ ${#files[@]} -eq 0 ]]; then
  echo "⚠️   Nenhum arquivo .d2 encontrado em $DIAGRAMS_DIR"
  exit 0
fi

ok=0
fail=0

for d2_file in "${files[@]}"; do
  name="$(basename "$d2_file" .d2)"
  svg_file="$ASSETS_DIR/$name.svg"

  printf "  %-45s" "$name.d2"

  if d2 --layout elk "$d2_file" "$svg_file" 2>/tmp/d2_err; then
    echo "✅  $name.svg"
    (( ++ok ))
  else
    echo "❌  falhou"
    sed 's/^/       /' /tmp/d2_err
    (( ++fail )) || true
  fi
done

echo ""
echo "────────────────────────────────────────"
echo "  Renderizados : $ok"
[[ $fail -gt 0 ]] && echo "  Com erro      : $fail"
echo "  Destino       : $ASSETS_DIR"
