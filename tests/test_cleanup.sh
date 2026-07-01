#!/usr/bin/env bash
# test_cleanup.sh — D2: Verify cleanup-logs.sh outputs CLEANUP_OK + row count
set -euo pipefail

echo "=== D2: Cleanup Test ==="

export HERMES_HOME="${HERMES_HOME:-$HOME/workspace/.hermes}"

CLEANUP_SCRIPT="${HERMES_HOME}/agents/_shared/cleanup-logs.sh"

if [ ! -f "$CLEANUP_SCRIPT" ]; then
    echo "FAIL: Cleanup script not found at $CLEANUP_SCRIPT"
    exit 1
fi

echo "Running: bash $CLEANUP_SCRIPT"
OUTPUT=$(bash "$CLEANUP_SCRIPT" 2>&1)
EXIT_CODE=$?

echo "--- Output ---"
echo "$OUTPUT"
echo "--- End ---"
echo "Exit code: $EXIT_CODE"

FAIL=0

# Check CLEANUP_OK
if echo "$OUTPUT" | grep -q "CLEANUP_OK"; then
    echo "PASS: CLEANUP_OK found in output"
else
    echo "FAIL: CLEANUP_OK not found in output"
    FAIL=1
fi

# Check "Cleanup complete: N rows retained"
if echo "$OUTPUT" | grep -qE "Cleanup complete: [0-9]+ rows retained"; then
    ROWS=$(echo "$OUTPUT" | grep -oE "Cleanup complete: ([0-9]+) rows retained" | grep -oE "[0-9]+")
    echo "PASS: Cleanup complete: $ROWS rows retained"
else
    echo "FAIL: 'Cleanup complete: N rows retained' not found in output"
    FAIL=1
fi

# Check exit code
if [ "$EXIT_CODE" -eq 0 ]; then
    echo "PASS: Script exited 0"
else
    echo "FAIL: Script exited with code $EXIT_CODE"
    FAIL=1
fi

if [ "$FAIL" -eq 0 ]; then
    echo "TEST_RESULT: PASS"
    exit 0
else
    echo "TEST_RESULT: FAIL"
    exit 1
fi
