#!/usr/bin/env bash
# Reproduce the Kulikowski (JCDL '26) TF-IDF baseline on his curated benchmark
# (109 papers, 5,812 cross-field pairs, 210 twins, 92 queries).
#
# Usage, from anywhere:  bash eval/results/kulikowski-baseline/run.sh
# Needs git, curl, unzip, shasum, md5 and Python >= 3.12. Network is used only for the clone,
# the KU Leuven RDR cross-check and the five restricted abstracts. No model, no API key.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
PKG="$ROOT/data/raw/kulikowski-spdf"                 # gitignored
RDR="$ROOT/data/raw/kulikowski-rdr"                  # gitignored
COMMIT="ef1ebe7fe69f2550bc246d19a7728452a04ad302"    # tag v1.0.0
RDR_FILE_ID="404154"                                 # same-problem-different-field-1.0.0.zip
RDR_MD5="0f18239236534db42b10d47776c27667"
RESTRICTED="kalman-epidemic-001 hawkes-crime-001 gp-geo-noname-001 pca-psychometrics-001 smoke-01"
VENV="${VENV:-$HOME/.venvs/rediscovery-layer}"       # outside the exFAT drive
OUT="$HERE/outputs"
UA="rediscovery-layer-research/0.1 (+https://github.com/IskakovDamir)"
mkdir -p "$OUT"

echo "== 1. code and data at the pinned commit"
[ -d "$PKG/.git" ] || git clone -q https://github.com/ErykKul/same-problem-different-field.git "$PKG"
git -C "$PKG" checkout -q "$COMMIT"
git -C "$PKG" checkout -q -- .   # restore the shipped reproduce_out/ before the integrity check

echo "== 2. environment (core requirements only: no torch, so the neural baseline rows are skipped)"
[ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"
PY="$VENV/bin/python"
"$PY" -m pip install -q -r "$HERE/requirements-lock.txt"

echo "== 3. integrity: shipped files, and GitHub v1.0.0 against the RDR deposit (DOI 10.48804/W3B9WC)"
cd "$PKG"
shasum -a 256 -c --quiet SHA256SUMS   # prints only failures; a failure stops the script
echo "SHA256SUMS: all $(wc -l < SHA256SUMS | tr -d ' ') shipped files OK" | tee "$OUT/sha256_check.txt"
mkdir -p "$RDR"
ZIP="$RDR/same-problem-different-field-1.0.0.zip"
[ -f "$ZIP" ] || curl -sS -L -A "$UA" -o "$ZIP" "https://rdr.kuleuven.be/api/access/datafile/$RDR_FILE_ID"
[ "$(md5 -q "$ZIP")" = "$RDR_MD5" ] || { echo "RDR zip MD5 mismatch"; exit 1; }
[ -d "$RDR/unzipped" ] || unzip -q "$ZIP" -d "$RDR/unzipped"
cmp "$RDR/unzipped/same-problem-different-field-1.0.0/SHA256SUMS" SHA256SUMS
echo "RDR zip MD5 matches the deposit listing; RDR SHA256SUMS identical to GitHub v1.0.0" | tee "$OUT/rdr_check.txt"

echo "== 4. rebuild the five non-redistributable abstracts (writes only missing files)"
"$PY" src/fetch_restricted.py 2>&1 | tee "$OUT/fetch_restricted.txt"
for id in $RESTRICTED; do shasum -a 256 "data/md/$id.md"; done > "$OUT/restricted_rebuilt_sha256.txt"

echo "== 5. headline tables (AP, AUROC, recall@K)"
"$PY" reproduce.py 2>&1 | tee "$OUT/reproduce.txt"
cp reproduce_out/curated_table.md reproduce_out/extended_table.md "$OUT/"
git diff --no-color -- reproduce_out > "$OUT/reproduce_out_vs_shipped.diff" || true

echo "== 6. P@1 and MRR, abstract vs mechanism skeleton (paired bootstrap over 92 queries)"
"$PY" src/p1mrr_boot.py 2>&1 | tee "$OUT/p1mrr_boot.txt"

echo "== 7. AP bootstrap CIs, TF-IDF rows only (neural rows removed: they need torch;"
echo "      the resampling is drawn independently of the method list, so TF-IDF CIs are unchanged)"
sed -E '/"st:|"semcse"|"qwen3"/d' src/ap_bootstrap.py > src/_ap_bootstrap_tfidf.py
"$PY" src/_ap_bootstrap_tfidf.py 2>&1 | tee "$OUT/ap_bootstrap_tfidf.txt"
rm -f src/_ap_bootstrap_tfidf.py

echo "== 8. whole-fingerprint P@1 and leave-one-family-out test"
"$PY" src/held_out_family.py 2>&1 | tee "$OUT/held_out_family.txt"

echo "== 9. name-free audit (compare with the shipped expected output)"
"$PY" src/name_free_audit.py > "$OUT/name_free_audit.txt" 2>&1
cmp "$OUT/name_free_audit.txt" reproduce_out/name_free_audit.txt && echo "name-free audit identical to shipped"

echo "== 10. where the abstract-row drift comes from"
"$PY" "$HERE/scripts/abstract_drift_diagnostic.py" 2>&1 | tee "$OUT/abstract_drift_diagnostic.txt"

{ echo "date_utc: $(date -u +%Y-%m-%dT%H:%M:%SZ)"; echo "package_commit: $(git rev-parse HEAD)";
  echo "python: $("$PY" --version 2>&1)"; echo "platform: $(uname -sm)"; "$PY" -m pip freeze; } > "$OUT/environment.txt"
echo "done: outputs in $OUT"
