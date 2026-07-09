#!/usr/bin/env bash
set -euo pipefail

SHOP_API="${SHOP_API:-http://localhost:8000}"
WAREHOUSE_API="${WAREHOUSE_API:-http://localhost:8001}"
BILLING_API="${BILLING_API:-http://localhost:8002}"
INVOICE_API="${INVOICE_API:-http://localhost:8003}"
AUDIT_API="${AUDIT_API:-http://localhost:8004}"
FRONTEND_URL="${FRONTEND_URL:-http://localhost:3000}"
ADMIN_USERNAME="${ADMIN_USERNAME:-admin}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-admin123}"

PRODUCT_ID="22222222-2222-2222-2222-222222222222"

json_field() {
  python3 -c 'import json, sys; print(json.load(sys.stdin).get(sys.argv[1], ""))' "$1"
}

json_len() {
  python3 -c 'import json, sys; print(len(json.load(sys.stdin)))'
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
  local correlation_id="$1"
  curl -fsS \
    -X POST "$SHOP_API/orders" \
    -H "Content-Type: application/json" \
    -H "X-Correlation-Id: $correlation_id" \
    -d "{\"customer\":{\"firstName\":\"Ada\",\"lastName\":\"Lovelace\",\"email\":\"ada@example.test\",\"phone\":\"+49 30 123456\"},\"shippingAddress\":{\"street\":\"Retroallee\",\"houseNumber\":\"8\",\"postalCode\":\"10115\",\"city\":\"Berlin\",\"country\":\"Deutschland\"},\"items\":[{\"productId\":\"$PRODUCT_ID\",\"quantity\":1}],\"payment\":{\"provider\":\"stripe\",\"currency\":\"EUR\"}}"
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

check_admin() {
  local order_id="$1"
  local cookie_file
  local status_code
  local count
  local audit_count

  cookie_file="$(mktemp)"

  status_code="$(curl -s -o /dev/null -w "%{http_code}" "$SHOP_API/admin/orders")"
  if [[ "$status_code" != "401" ]]; then
    echo "error - admin orders should require login, got HTTP $status_code" >&2
    return 1
  fi
  echo "ok - admin orders require login"

  curl -fsS \
    -c "$cookie_file" \
    -X POST "$SHOP_API/admin/login" \
    -H "Content-Type: application/json" \
    -d "{\"username\":\"$ADMIN_USERNAME\",\"password\":\"$ADMIN_PASSWORD\"}" >/dev/null
  echo "ok - admin login"

  count="$(curl -fsS -b "$cookie_file" "$SHOP_API/admin/orders" | json_len)"
  if [[ "$count" -lt 1 ]]; then
    echo "error - admin orders returned no orders" >&2
    return 1
  fi
  echo "ok - admin orders contains $count orders"

  audit_count="$(curl -fsS -b "$cookie_file" "$SHOP_API/admin/orders/$order_id/audit" | snapshot_count)"
  if [[ "$audit_count" -lt 1 ]]; then
    echo "error - protected admin audit returned no snapshots" >&2
    return 1
  fi
  echo "ok - protected admin audit contains $audit_count snapshots"
  rm -f "$cookie_file"
}

echo "Checking service health"
check_url "shop" "$SHOP_API/health"
check_url "warehouse" "$WAREHOUSE_API/health"
check_url "billing" "$BILLING_API/health"
check_url "invoice" "$INVOICE_API/health"
check_url "audit" "$AUDIT_API/health"
check_url "frontend" "$FRONTEND_URL"

echo "Checking product catalog"
product_count="$(curl -fsS "$SHOP_API/products" | json_len)"
if [[ "$product_count" -lt 4 ]]; then
  echo "error - expected at least 4 products, got $product_count" >&2
  exit 1
fi
echo "ok - product catalog contains $product_count products"

echo "Checking customer checkout happy path"
correlation_id="$(python3 -c 'import uuid; print(uuid.uuid4())')"
response="$(create_order "$correlation_id")"
order_id="$(printf '%s' "$response" | json_field orderId)"
echo "order - $order_id"
wait_for_status "$order_id" "COMPLETED"
check_admin "$order_id"

echo "Smoke test completed successfully"
