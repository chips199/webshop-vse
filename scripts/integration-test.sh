#!/usr/bin/env bash
# Integrationstests gegen einen laufenden Docker-Compose-Stack:
#
#   1. Happy Path            -> Order=COMPLETED, vollstaendige Event-Kette
#   2. Zahlung abgelehnt     -> Order=PAYMENT_FAILED, Warehouse-Reservierung
#                                storniert (Saga-Kompensation)
#   3. Lager nicht ausreichend -> Order=OUT_OF_STOCK, KEIN weiterer Aufruf
#                                (keine Zahlung, keine Rechnung)
#
# Jedes Szenario prueft Endstatus und Audit-Ereignisse.
set -euo pipefail

SHOP_API="${SHOP_API:-http://localhost:8000}"
ADMIN_USERNAME="${ADMIN_USERNAME:-admin}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-admin123}"

PRODUCT_ID="22222222-2222-2222-2222-222222222222"

json_field() {
  python3 -c 'import json, sys; print(json.load(sys.stdin).get(sys.argv[1], ""))' "$1"
}

event_types() {
  # Ein eventType pro Zeile.
  python3 -c 'import json, sys; [print(s["eventType"]) for s in json.load(sys.stdin).get("snapshots", [])]'
}

admin_login() {
  local cookie_file="$1"
  curl -fsS \
    -c "$cookie_file" \
    -X POST "$SHOP_API/admin/login" \
    -H "Content-Type: application/json" \
    -d "{\"username\":\"$ADMIN_USERNAME\",\"password\":\"$ADMIN_PASSWORD\"}" >/dev/null
}

create_order() {
  # $1: correlationId, $2: Zahlungsszenario
  local correlation_id="$1"
  local scenario="$2"
  curl -fsS \
    -X POST "$SHOP_API/orders" \
    -H "Content-Type: application/json" \
    -H "X-Correlation-Id: $correlation_id" \
    -d "{\"customer\":{\"firstName\":\"Ada\",\"lastName\":\"Lovelace\",\"email\":\"ada@example.test\",\"phone\":\"+49 30 123456\"},\"shippingAddress\":{\"street\":\"Retroallee\",\"houseNumber\":\"8\",\"postalCode\":\"10115\",\"city\":\"Berlin\",\"country\":\"Deutschland\"},\"items\":[{\"productId\":\"$PRODUCT_ID\",\"quantity\":1}],\"payment\":{\"provider\":\"stripe\",\"currency\":\"EUR\",\"scenario\":\"$scenario\"}}"
}

wait_for_status() {
  local order_id="$1"
  local expected_status="$2"
  local status=""

  for _ in 1 2 3 4 5 6 7 8 9 10 11 12; do
    status="$(curl -fsS "$SHOP_API/orders/$order_id" | json_field status)"
    if [[ "$status" == "$expected_status" ]]; then
      echo "ok - $order_id reached $expected_status"
      return 0
    fi
    sleep 1
  done

  echo "error - $order_id expected $expected_status but got '$status'" >&2
  return 1
}

assert_events_present() {
  # Alle angegebenen eventTypes muessen vorkommen.
  local order_id="$1"
  local cookie_file="$2"
  shift 2
  local expected="$*"
  local actual
  actual="$(curl -fsS -b "$cookie_file" "$SHOP_API/admin/orders/$order_id/audit" | event_types)"

  for event_type in $expected; do
    if ! grep -q "^${event_type}$" <<<"$actual"; then
      echo "error - expected audit event '$event_type' missing for $order_id. Got:" >&2
      echo "$actual" >&2
      return 1
    fi
    echo "ok - audit contains $event_type"
  done
}

assert_events_absent() {
  # Keiner der angegebenen eventTypes darf vorkommen.
  local order_id="$1"
  local cookie_file="$2"
  shift 2
  local unexpected="$*"
  local actual
  actual="$(curl -fsS -b "$cookie_file" "$SHOP_API/admin/orders/$order_id/audit" | event_types)"

  for event_type in $unexpected; do
    if grep -q "^${event_type}$" <<<"$actual"; then
      echo "error - unexpected audit event '$event_type' present for $order_id (should not have been reached)" >&2
      echo "$actual" >&2
      return 1
    fi
    echo "ok - audit does not contain $event_type"
  done
}

cookie_file="$(mktemp)"
trap 'rm -f "$cookie_file"' EXIT
admin_login "$cookie_file"

echo "=== Szenario 1: Happy Path ==="
correlation_id="$(python3 -c 'import uuid; print(uuid.uuid4())')"
order_id="$(create_order "$correlation_id" "happy_path" | json_field orderId)"
echo "order - $order_id"
wait_for_status "$order_id" "COMPLETED"
assert_events_present "$order_id" "$cookie_file" \
  ORDER_CREATED WAREHOUSE_RESERVATION_SUCCEEDED BILLING_PAYMENT_SUCCEEDED \
  INVOICE_CREATED WAREHOUSE_COMMIT_SUCCEEDED ORDER_COMPLETED

echo
echo "=== Szenario 2: Zahlung abgelehnt (Saga-Kompensation) ==="
correlation_id="$(python3 -c 'import uuid; print(uuid.uuid4())')"
order_id="$(create_order "$correlation_id" "payment_failed" | json_field orderId)"
echo "order - $order_id"
wait_for_status "$order_id" "PAYMENT_FAILED"
assert_events_present "$order_id" "$cookie_file" \
  WAREHOUSE_RESERVATION_SUCCEEDED BILLING_PAYMENT_FAILED \
  WAREHOUSE_CANCEL_REQUESTED WAREHOUSE_CANCEL_SUCCEEDED
assert_events_absent "$order_id" "$cookie_file" INVOICE_CREATE_REQUESTED ORDER_COMPLETED

echo
echo "=== Szenario 3: Lager nicht ausreichend (kein weiterer Aufruf) ==="
correlation_id="$(python3 -c 'import uuid; print(uuid.uuid4())')"
order_id="$(create_order "$correlation_id" "out_of_stock" | json_field orderId)"
echo "order - $order_id"
wait_for_status "$order_id" "OUT_OF_STOCK"
assert_events_present "$order_id" "$cookie_file" WAREHOUSE_RESERVATION_FAILED
assert_events_absent "$order_id" "$cookie_file" \
  BILLING_PAYMENT_REQUESTED BILLING_PAYMENT_SUCCEEDED BILLING_PAYMENT_FAILED \
  INVOICE_CREATE_REQUESTED INVOICE_CREATED

echo
echo "Integrationstests fuer den Bestellprozess erfolgreich"
