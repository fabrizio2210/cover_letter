#!/usr/bin/env bash
# eval-scorer.sh — manual eval runner for AI scorer prompt/model quality.
#
# Runs candidate model vs baseline on canonical golden fixtures and writes
# artifacts to eval-results/.
#
# Usage:
#   bash scripts/eval-scorer.sh                          # self-compare baseline
#   bash scripts/eval-scorer.sh smollm2:1.7b             # compare new model vs baseline
#   bash scripts/eval-scorer.sh smollm2:1.7b qwen2.5:1.5b /path/to/fixtures.json
#
# Environment variables:
#   OLLAMA_HOST            Ollama base URL (default: http://localhost:11434)
#   EVAL_BASELINE_MODEL    Baseline model  (default: qwen2.5:1.5b)
#   EVAL_CANDIDATE_MODEL   Candidate model (default: same as baseline)
#   EVAL_FIXTURES          Path to canonical fixture file
#   EVAL_OUTPUT_DIR        Output directory for artifacts (default: eval-results)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

OLLAMA_HOST="${OLLAMA_HOST:-http://localhost:11434}"
EVAL_BASELINE_MODEL="${EVAL_BASELINE_MODEL:-qwen2.5:1.5b}"
EVAL_CANDIDATE_MODEL="${1:-${EVAL_CANDIDATE_MODEL:-$EVAL_BASELINE_MODEL}}"
EVAL_FIXTURES="${3:-${EVAL_FIXTURES:-src/python/ai_scorer/evals/data/canonical/v1.json}}"
EVAL_OUTPUT_DIR="${EVAL_OUTPUT_DIR:-eval-results}"

if [[ $# -ge 2 ]]; then
    EVAL_BASELINE_MODEL="$2"
fi

echo "[eval-scorer] Baseline model : $EVAL_BASELINE_MODEL"
echo "[eval-scorer] Candidate model: $EVAL_CANDIDATE_MODEL"
echo "[eval-scorer] Fixtures       : $EVAL_FIXTURES"
echo "[eval-scorer] Ollama host    : $OLLAMA_HOST"
echo "[eval-scorer] Output dir     : $EVAL_OUTPUT_DIR"

if [[ ! -f "$EVAL_FIXTURES" ]]; then
    echo ""
    echo "[eval-scorer] ERROR: canonical fixture file not found: $EVAL_FIXTURES"
    echo ""
    echo "To bootstrap golden data from your local Mongo:"
    echo "  1. Extract candidate stubs:"
    echo "       python -m src.python.ai_scorer.evals.cli extract \\"
    echo "           --mongo-uri 'mongodb://root:develop@localhost:27017/' \\"
    echo "           --global-db cover_letter_global \\"
    echo "           --output src/python/ai_scorer/evals/data/proposed/candidates.json"
    echo ""
    echo "  2. Propose labels using the baseline model:"
    echo "       python -m src.python.ai_scorer.evals.cli label \\"
    echo "           --ollama-host $OLLAMA_HOST \\"
    echo "           --model $EVAL_BASELINE_MODEL \\"
    echo "           --input  src/python/ai_scorer/evals/data/proposed/candidates.json \\"
    echo "           --output src/python/ai_scorer/evals/data/proposed/labeled.json"
    echo ""
    echo "  3. Review and correct labels, then copy to canonical:"
    echo "       cp src/python/ai_scorer/evals/data/proposed/labeled.json \\"
    echo "          src/python/ai_scorer/evals/data/canonical/v1.json"
    echo ""
    exit 1
fi

PYTHONPATH="$REPO_ROOT" python3 -m src.python.ai_scorer.evals.cli eval \
    --ollama-host "$OLLAMA_HOST" \
    --baseline    "$EVAL_BASELINE_MODEL" \
    --candidate   "$EVAL_CANDIDATE_MODEL" \
    --fixtures    "$EVAL_FIXTURES" \
    --output-dir  "$EVAL_OUTPUT_DIR" \
    --verbose

echo ""
echo "[eval-scorer] Artifacts in $EVAL_OUTPUT_DIR/"
echo "  $(ls "$EVAL_OUTPUT_DIR"/ 2>/dev/null | tr '\n' ' ')"
