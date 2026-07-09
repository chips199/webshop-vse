#!/usr/bin/env bash
set -euo pipefail

SHOP_API="${SHOP_API:-http://localhost:8000}"
WAREHOUSE_API="${WAREHOUSE_API:-http://localhost:8001}"
BILLING_API="${BILLING_API:-http://localhost:8002}"
INVOICE_API="${INVOICE_API:-http://localhost:8003}"
AUDIT_API="${AUDIT_API:-http://localhost:8004}"
FRONTEND_URL="${FRONTEND_URL:-http://localhost:3000}"

CUSTOMER_ID="11111111-1111-1111-1111-111111111111"
PRODUCT_ID="22222222-2222-2222-2222-222222222222"

json_field() {
  python3 -c 'import json, sys; print(json.load(sys.stdin).get(sys.argv[1], ""))' "$1"
}

snapshot_count() {
  python3 -c 'import json, sys; print(len(json.load(sys.stdin).get("snapshots", [])))'
}

check_url() {
  local label="$1"
  local url="$2"
  curl -fsS "$url" >/dev/null
  echo "ok - $label"
}

create_order() {
  local scenario="$1"
  local correlation_id="$2"
  curl -fsS \
    -X POST "$SHOP_API/orders" \
    -H "Content-Type: application/json" \
    -H "X-Correlation-Id: $correlation_id" \
    -d "{\"customerId\":\"$CUSTOMER_ID\",\"items\":[{\"productId\":\"$PRODUCT_ID\",\"quantity\":1}],\"payment\":{\"provider\":\"stripe\",\"currency\":\"EUR\",\"scenario\":\"$scenario\"}}"
}

wait_for_status() {
  local order_id="$1"
  local expected_status="$2"
  local status=""

  for _ in 1 2 3 4 5 6 7 8 9 10; do
    status="$(curl -fsS "$SHOP_API/orders/$order_id" | json_field status)"
    if [[ "$status" == "$expected_status" ]]; then
      echo "ok - $order_id reached $expected_status"
      return 0
    fi
    sleep 1
  done

  echo "error - $order_id expected $expected_status but got $status" >&2
  return 1
}

check_audit() {
  local correlation_id="$1"
  local count=""
  count="$(curl -fsS "$AUDIT_API/audit/orders/$correlation_id" | snapshot_count)"
  if [[ "$count" -lt 1 ]]; then
    echo "error - no audit snapshots for $correlation_id" >&2
    return 1
  fi
  echo "ok - audit contains $count snapshots for $correlation_id"
}

run_scenario() {
  local scenario="$1"
  local expected_status="$2"
  local correlation_id
  local response
  local order_id

  correlation_id="$(python3 -c 'import uuid; print(uuid.uuid4())')"
  response="$(create_order "$scenario" "$correlation_id")"
  order_id="$(printf '%s' "$response" | json_field orderId)"

  echo "scenario - $scenario -> $order_id"
  wait_for_status "$order_id" "$expected_status"
  check_audit "$correlation_id"
}

echo "Checking service health"
check_url "shop" "$SHOP_API/health"
check_url "warehouse" "$WAREHOUSE_API/health"
check_url "billing" "$BILLING_API/health"
check_url "invoice" "$INVOICE_API/health"
check_url "audit" "$AUDIT_API/health"
check_url "frontend" "$FRONTEND_URL"

echo "Checking saga scenarios"
run_scenario "happy_path" "COMPLETED"
run_scenario "out_of_stock" "OUT_OF_STOCK"
run_scenario "payment_failed" "PAYMENT_FAILED"
run_scenario "invoice_failed" "INVOICE_RETRY_PENDING"
run_scenario "warehouse_commit_failed" "ROLLBACK_COMPLETED"

echo "Smoke test completed successfully"
