#!/usr/bin/env bash
# Drive a running API over real HTTP, the way a browser would. Usage:
#   scripts/http_walkthrough.sh [http://127.0.0.1:8765]
# Needs only curl and python3. Uses the labeled mock mode (no key, no network).
set -u
BASE="${1:-http://127.0.0.1:8765}"
JAR="$(mktemp)"
trap 'rm -f "$JAR"' EXIT
jget() { python3 -c "import sys,json; d=json.load(sys.stdin); print(d$1)"; }

echo "1. health:           $(curl -s "$BASE/api/v1/healthz")"
SESSION=$(curl -s -c "$JAR" -X POST "$BASE/api/v1/session" -H "Origin: $BASE")
CSRF=$(echo "$SESSION" | jget "['csrf_token']")
echo "2. session:          workspace $(echo "$SESSION" | jget "['workspace_id']")  modes $(echo "$SESSION" | jget "['modes_enabled']")"

post() { curl -s -b "$JAR" -X POST "$BASE/api/v1$1" -H "Origin: $BASE" -H "X-CSRF-Token: $CSRF" -H "Content-Type: application/json" -d "$2"; }

echo "3. no CSRF token:    $(curl -s -o /dev/null -w '%{http_code}' -b "$JAR" -X POST "$BASE/api/v1/runs" -H "Origin: $BASE" -H 'Content-Type: application/json' -d '{}') (expect 403)"
echo "4. wrong origin:     $(curl -s -o /dev/null -w '%{http_code}' -b "$JAR" -X POST "$BASE/api/v1/runs" -H 'Origin: https://evil.example' -H "X-CSRF-Token: $CSRF" -H 'Content-Type: application/json' -d '{}') (expect 403)"

wait_run() {
  for _ in $(seq 1 100); do
    R=$(curl -s -b "$JAR" "$BASE/api/v1/runs/$1")
    L=$(echo "$R" | jget "['lifecycle']")
    case "$L" in completed|failed|interrupted) echo "$R"; return;; esac
    sleep 0.1
  done
  echo "$R"
}

RUN=$(post /runs '{"sample_id":"invoice_1012.txt","mode":"mock","request_token":"walkthrough-run-1"}' | jget "['run_id']")
RESULT=$(wait_run "$RUN")
echo "5. messy invoice:    $(echo "$RESULT" | jget "['outcome']")  - $(echo "$RESULT" | jget "['terminal_reason']")"
VERSION=$(echo "$RESULT" | jget "['version_id']")
HASH=$(echo "$RESULT" | jget "['content_hash']")
[ -n "$VERSION" ] && [ "$VERSION" != "None" ] || { echo "no version id in the result: $RESULT"; exit 1; }

echo "6. events:           $(curl -s -b "$JAR" "$BASE/api/v1/runs/$RUN/events" | python3 -c "import sys,json; print(len(json.load(sys.stdin)['events']), 'events')")"
NOACK=$(post "/runs/$RUN/review" "{\"version_id\":\"$VERSION\",\"content_hash\":\"$HASH\",\"decision\":\"approve\",\"comment\":\"Looks fine to me.\",\"resolved_codes\":[],\"idempotency_token\":\"walkthrough-rev-0000\"}")
echo "7. review, nothing named as checked: $(echo "$NOACK" | jget "['error']['code']") (expect review_incomplete)"
REV=$(post "/runs/$RUN/review" "{\"version_id\":\"$VERSION\",\"content_hash\":\"$HASH\",\"decision\":\"approve\",\"comment\":\"Confirmed the rename with the vendor by phone.\",\"resolved_codes\":[\"VENDOR_IDENTITY_CHANGE\"],\"idempotency_token\":\"walkthrough-rev-0001\"}")
echo "8. review accepted:  $(echo "$REV" | jget "['run']['outcome']")  receipt $(echo "$REV" | jget "['run']['receipt']['amount']") $(echo "$REV" | jget "['run']['receipt']['currency']") (simulated)"

DUP=$(post /runs '{"sample_id":"invoice_1012.pdf","mode":"mock","request_token":"walkthrough-run-2"}' | jget "['run_id']")
echo "9. same invoice PDF: $(wait_run "$DUP" | jget "['outcome']") (expect duplicate: no second payment)"
echo "10. unknown route:   $(curl -s -w ' [%{http_code}]' "$BASE/api/v1/nope")"
echo "11. other browser:   $(curl -s -o /dev/null -w '%{http_code}' "$BASE/api/v1/runs/$RUN") (no cookie, expect 401)"
