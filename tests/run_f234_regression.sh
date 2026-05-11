#!/bin/bash
V="hledac.universal.tools.f234_validate_nonfeed_live_report"
P=0; F=0
check() {
  local file=$1 expected_min=$2 label=$3
  cd /Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal
  out=$(uv run python tools/f234_validate_nonfeed_live_report.py "$file" 2>&1)
  pass_count=$(echo "$out" | grep -c '\[PASS\]' || echo "0")
  if [ "${pass_count:-0}" -ge "$expected_min" ]; then
    echo "✅ $label (score=${pass_count}/19)"
    P=$((P+1))
  else
    echo "❌ $label: score=${pass_count}/19, expected>=$expected_min"
    echo "$out" | tail -5
    F=$((F+1))
  fi
}
check tests/fixtures/f234_valid_nonfeed.json      18 "valid nonfeed"
check tests/fixtures/f234_live_kpi_present.json   19 "live with kpi"
check tests/fixtures/f234_terminality_fail.json    0 "terminality fail"
echo "---"
echo "$P passed, $F failed"
[ $F -eq 0 ] || exit 1