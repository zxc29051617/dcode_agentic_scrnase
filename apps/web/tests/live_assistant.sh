#!/usr/bin/env bash
# Live checks against a running web server, for the things a unit test cannot
# see: what the assistant page actually renders in each configuration state,
# and what the runtime endpoint returns when the model endpoint is wrong.
#
#   WEB=http://127.0.0.1:3000 bash tests/live_assistant.sh configured
#   WEB=http://127.0.0.1:3000 bash tests/live_assistant.sh unconfigured
#   WEB=http://127.0.0.1:3000 SENTINEL=sk-... bash tests/live_assistant.sh invalid-endpoint
#
# Each mode expects the server to already be running in that state; the mode
# name says which. Exits non-zero on the first failed expectation.
set -uo pipefail

WEB="${WEB:-http://127.0.0.1:3000}"
RUN="${RUN:-demo-2026-0001}"
MODE="${1:-configured}"
fails=0

check() {
  local label="$1" condition="$2"
  if [ "$condition" = "1" ]; then
    echo "  PASS  $label"
  else
    echo "  FAIL  $label"
    fails=$((fails + 1))
  fi
}

page="$(curl -s "$WEB/runs/$RUN/assistant")"

case "$MODE" in
  configured)
    echo "== assistant page with a model configured =="
    check "names the model" "$(grep -qE '<code>[^<]+</code>' <<<"$page" && echo 1 || echo 0)"
    check "does not show the unconfigured notice" \
      "$(grep -q 'Assistant model is not configured' <<<"$page" && echo 0 || echo 1)"
    check "lists the five read-only actions" \
      "$(grep -q 'get_provenance' <<<"$page" && echo 1 || echo 0)"
    # The key must not reach the HTML either, not just the JS bundle.
    if [ -n "${SENTINEL:-}" ]; then
      check "no API key in the served HTML" \
        "$(grep -q "$SENTINEL" <<<"$page" && echo 0 || echo 1)"
    fi
    echo "== runtime endpoint =="
    info="$(curl -s -X POST "$WEB/api/copilotkit" -H 'Content-Type: application/json' -d '{"method":"info"}')"
    check "runtime answers info" "$(grep -q '"version"' <<<"$info" && echo 1 || echo 0)"
    check "telemetry is disabled" "$(grep -q '"telemetryDisabled":true' <<<"$info" && echo 1 || echo 0)"
    ;;

  unconfigured)
    echo "== assistant page with no model configured =="
    check "says the model is not configured" \
      "$(grep -q 'Assistant model is not configured' <<<"$page" && echo 1 || echo 0)"
    check "says chat is unavailable" \
      "$(grep -q 'Chat is unavailable' <<<"$page" && echo 1 || echo 0)"
    check "names which variable is missing" \
      "$(grep -q 'ASSISTANT_MODEL_BASE_URL' <<<"$page" && echo 1 || echo 0)"
    # The point of the whole branch: an empty adapter must not be dressed up
    # as a working chat.
    check "renders no chat widget" \
      "$(grep -qE 'copilotKitInput|copilotKitMessages' <<<"$page" && echo 0 || echo 1)"
    echo "== the model-free pages still work =="
    for path in "/runs" "/runs/$RUN" "/runs/$RUN/report"; do
      code="$(curl -s -o /dev/null -w '%{http_code}' "$WEB$path")"
      check "$path returns 200" "$([ "$code" = "200" ] && echo 1 || echo 0)"
    done
    ;;

  invalid-endpoint)
    echo "== runtime endpoint pointed at an unreachable model =="
    body='{"method":"agent/run","params":{"agentId":"default"},"body":{"threadId":"t","runId":"r","messages":[{"id":"m1","role":"user","content":"hello"}],"tools":[],"context":[],"forwardedProps":{}}}'
    out="$(curl -s -N -m 90 -X POST "$WEB/api/copilotkit" -H 'Content-Type: application/json' -d "$body")"
    check "reports an error rather than hanging or succeeding" \
      "$(grep -qE 'RUN_ERROR|assistant_unavailable' <<<"$out" && echo 1 || echo 0)"
    check "the error says something specific" \
      "$(grep -qE 'Cannot connect|ECONNREFUSED|bad port|assistant_unavailable' <<<"$out" && echo 1 || echo 0)"
    if [ -n "${SENTINEL:-}" ]; then
      check "the error carries no API key" \
        "$(grep -q "$SENTINEL" <<<"$out" && echo 0 || echo 1)"
    else
      echo "  SKIP  API-key-in-error check (set SENTINEL to enable)"
    fi
    ;;

  *)
    echo "unknown mode: $MODE (expected configured | unconfigured | invalid-endpoint)"
    exit 2
    ;;
esac

echo
if [ "$fails" -gt 0 ]; then
  echo "$fails check(s) failed"
  exit 1
fi
echo "all checks passed"
