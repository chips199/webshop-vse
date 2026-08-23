"""Service-Schicht des warehouse-service: RabbitMQ-Command-Handling (Saga).

Enthaelt die Business-Logik fuer die drei Bestand-Commands, die
warehouse-service ueber RabbitMQ konsumiert (siehe lifespan() in main.py).
Die synchronen REST-Endpunkte fuer Lagerbestand (routes.py) sind dagegen so
einfach (direkte CRUD-Weiterleitung an die Repository-Schicht database.py),
dass sie keine eigene Service-Funktion brauchen.
"""

from .database import cancel_reservation, commit_reservation, reserve_stock
from .messaging import build_message, publish_message


def handle_warehouse_message(message: dict) -> None:
    """Verteilt eingehende Bestand-Commands auf die passende Verarbeitung.

    Drei Nachrichtentypen (siehe Aufgabenblatt 2.2/2.3):
      - warehouse.cancel.requested: Reservierung stornieren (Kompensation
        bei abgelehnter Zahlung).
      - warehouse.commit.requested: Reservierung final ausbuchen (nach
        erfolgreicher Zahlung) - inkl. gezieltem Testszenario
        "warehouse_commit_failed" fuer die Refund-Kompensation.
      - warehouse.reserve.requested: Bestand pruefen und reservieren -
        inkl. gezieltem Testszenario "out_of_stock".
    Jeder Zweig publiziert am Ende genau EIN Ergebnis-Event zurueck an die
    Shop-Saga.
    """
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
        # Gezieltes Testszenario: tut so, als wuerde das finale Verbuchen der
        # Reservierung fehlschlagen, OHNE commit_reservation() ueberhaupt
        # aufzurufen - damit laesst sich die Refund-Kompensation der Saga
        # (Zahlung wurde bereits bestaetigt) reproduzierbar durchspielen.
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
        # Gezieltes Testszenario: reserve_stock() lief regulaer (und hat
        # ggf. bereits reserviert), wird hier aber erzwungen wieder
        # storniert und als fehlgeschlagen gemeldet - reproduzierbarer
        # Weg, den "Artikel nicht verfuegbar"-Pfad der Saga zu testen, ohne
        # den tatsaechlichen Lagerbestand vorher leerraeumen zu muessen.
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
