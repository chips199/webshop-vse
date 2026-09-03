"""Command-Verarbeitung des Warehouse-Service."""

from .database import cancel_reservation, commit_reservation, reserve_stock
from .messaging import build_message, publish_message


def handle_warehouse_message(message: dict) -> None:
    """Verarbeitet Reservierungs-, Commit- und Storno-Commands."""
    if message["type"] == "warehouse.cancel.requested":
        payload = message.get("payload", {})
        cancelled = cancel_reservation(payload["orderId"])
        event = build_message(
            "warehouse.cancel.succeeded",
            message["correlationId"],
            {
                "orderId": payload["orderId"],
                "cancelStatus": "SUCCEEDED" if cancelled else "SKIPPED",
                "reasonCode": payload.get("reasonCode", "CANCEL_REQUESTED"),
            },
            previous_event_id=message["messageId"],
        )
        publish_message("warehouse.cancel.succeeded", event)
        return

    if message["type"] == "warehouse.commit.requested":
        payload = message.get("payload", {})
        scenario = payload.get("scenario", "happy_path")
        # Fehlerszenario fuer die Refund-Kompensation.
        if scenario == "warehouse_commit_failed":
            event = build_message(
                "warehouse.commit.failed",
                message["correlationId"],
                {
                    "orderId": payload["orderId"],
                    "transactionId": payload["transactionId"],
                    "provider": payload["provider"],
                    "amount": payload["amount"],
                    "currency": payload["currency"],
                    "reasonCode": "WAREHOUSE_COMMIT_FAILED",
                    "message": "Reservierung konnte nach erfolgreicher Zahlung nicht final verbucht werden.",
                },
                previous_event_id=message["messageId"],
            )
            publish_message("warehouse.commit.failed", event)
            return

        committed = commit_reservation(payload["orderId"])
        if not committed:
            event = build_message(
                "warehouse.commit.failed",
                message["correlationId"],
                {
                    "orderId": payload["orderId"],
                    "transactionId": payload["transactionId"],
                    "provider": payload["provider"],
                    "amount": payload["amount"],
                    "currency": payload["currency"],
                    "reasonCode": "WAREHOUSE_COMMIT_FAILED",
                    "message": "Reservierung wurde nicht gefunden oder ist nicht mehr commitbar.",
                },
                previous_event_id=message["messageId"],
            )
            publish_message("warehouse.commit.failed", event)
            return

        event = build_message(
            "warehouse.commit.succeeded",
            message["correlationId"],
            {
                "orderId": payload["orderId"],
                "transactionId": payload["transactionId"],
                "provider": payload["provider"],
                "amount": payload["amount"],
                "currency": payload["currency"],
                "commitStatus": "SUCCEEDED",
            },
            previous_event_id=message["messageId"],
        )
        publish_message("warehouse.commit.succeeded", event)
        return

    if message["type"] != "warehouse.reserve.requested":
        return
    payload = message.get("payload", {})
    order_id = payload["orderId"]
    items = payload.get("items", [])
    scenario = payload.get("scenario", "happy_path")
    has_stock, reason_code = reserve_stock(order_id, items)
    if scenario == "out_of_stock":
        # Fehlerszenario ohne Aenderung des dauerhaften Lagerbestands.
        cancel_reservation(order_id)
        has_stock = False
        reason_code = "OUT_OF_STOCK"

    if has_stock:
        event_type = "warehouse.reservation.succeeded"
        event_payload = {
            "orderId": order_id,
            "reservationId": f"reservation-{order_id}",
            "items": items,
            "amount": payload["amount"],
            "currency": payload["currency"],
            "provider": payload["provider"],
            "scenario": scenario,
            "payment": payload.get("payment", {}),
        }
    else:
        event_type = "warehouse.reservation.failed"
        event_payload = {
            "orderId": order_id,
            "reasonCode": reason_code or "OUT_OF_STOCK",
            "message": "Mindestens ein historisches Computerteil ist nicht verfuegbar.",
            "items": items,
        }

    event = build_message(
        event_type,
        message["correlationId"],
        event_payload,
        previous_event_id=message["messageId"],
    )
    publish_message(event_type, event)
