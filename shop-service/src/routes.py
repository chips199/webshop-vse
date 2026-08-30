"""HTTP-Router (Router-Schicht) des shop-service.

Enthaelt alle synchronen REST-Endpunkte: oeffentlicher Produktkatalog und
Checkout (/products, /orders/...), sowie das komplette Admin-Dashboard-
Backend (Login, Bestelluebersicht, Audit-Timeline, Echtzeit-Updates per SSE,
Produktverwaltung). Business-Logik ist in service.py (Router-Hilfsfunktionen,
Serialisierung), saga.py (Saga-Schritte, die vom HTTP-Layer ausgeloest
werden) und clients.py (HTTP-Aufrufe an warehouse-/audit-service) ausgelagert.
"""

import asyncio
from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
import queue
import re
import secrets
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from psycopg.errors import UniqueViolation

from . import realtime
from .clients import create_warehouse_stock, fetch_audit_snapshots, fetch_warehouse_stock, update_warehouse_stock
from .config import settings
from .database import (
    calculate_total,
    claim_payment_confirmation,
    create_admin_session,
    create_order as create_order_record,
    create_product as create_product_record,
    delete_admin_session,
    enrich_items_from_products,
    get_order as get_order_record,
    get_order_by_idempotency_key,
    get_products,
    list_admin_orders,
    update_order_status,
    update_product as update_product_record,
    verify_admin_credentials,
)
from .messaging import build_message, publish_message
from .saga import notify_admin_dashboard
from .schemas import (
    AdminAuditResponse,
    AdminLoginRequest,
    AdminOrderResponse,
    AdminSessionResponse,
    CreateOrderRequest,
    HealthResponse,
    ImageUploadResponse,
    OrderResponse,
    PaymentConfirmationRequest,
    ProductCreateRequest,
    ProductResponse,
    ProductUpdateRequest,
    StockUpdateRequest,
)
from .service import (
    ADMIN_SESSION_COOKIE,
    _idempotency_key_from_request,
    _initial_order_response,
    _order_response,
    _request_hash,
    _serialize_order,
    _serialize_product,
    _token_hash,
    require_admin,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(service=settings.service_name)


@router.get("/products", response_model=list[ProductResponse])
async def list_products(request: Request, response: Response) -> list[ProductResponse]:
    """Oeffentlicher Produktkatalog inkl. Live-Lagerbestand (No-Store-Cache,
    damit Verfuegbarkeitsstatus nie veraltet aus dem Browser-Cache kommt)."""
    response.headers["Cache-Control"] = "no-store"
    stock_by_product_id = fetch_warehouse_stock(request.state.correlation_id)
    return [
        ProductResponse(**_serialize_product(product, stock_by_product_id.get(str(product["id"]))))
        for product in get_products()
    ]


@router.post("/orders", response_model=OrderResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_order(request: Request, order: CreateOrderRequest) -> OrderResponse:
    """Startet die Saga: legt die Bestellung an (Status PENDING) und
    publiziert order.created sowie den ersten Saga-Schritt
    (warehouse.reserve.requested). 202 Accepted statt 201/200, da die
    Verarbeitung danach vollstaendig asynchron ueber RabbitMQ weiterlaeuft.

    Ist ein Idempotency-Key-
    Header gesetzt und wurde er schon einmal fuer denselben Request-Body
    verwendet, wird die bereits angelegte Bestellung zurueckgegeben statt
    eine zweite anzulegen; bei gleichem Key aber anderem Body gibt es 409.
    """
    correlation_id = request.state.correlation_id
    idempotency_key = _idempotency_key_from_request(request)
    request_hash = _request_hash(order)
    if idempotency_key:
        existing_order = get_order_by_idempotency_key(idempotency_key)
        if existing_order:
            if existing_order.get("requestHash") != request_hash:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Idempotency-Key was already used for a different request body.",
                )
            return _initial_order_response(existing_order)

    order_id = str(uuid4())
    customer_id = order.customerId or str(uuid4())
    raw_items = [item.model_dump() for item in order.items]
    try:
        enriched_items = enrich_items_from_products(raw_items)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    amount = calculate_total(enriched_items)
    currency = order.payment.currency
    payment = order.payment.model_dump()
    try:
        create_order_record(
            order_id,
            correlation_id,
            customer_id,
            order.customer.model_dump(),
            order.shippingAddress.model_dump(),
            order.billingAddress.model_dump() if order.billingAddress else None,
            enriched_items,
            payment,
            str(amount),
            currency,
            idempotency_key,
            request_hash,
        )
    except UniqueViolation as exc:
        # Race Condition: zwei parallele Requests mit demselben Idempotency-
        # Key haben den Vorab-Check oben beide "nicht gefunden" gesehen und
        # versuchen nun gleichzeitig, die Bestellung anzulegen - der
        # eindeutige Index auf idempotency_key (siehe database.py) laesst
        # nur den ersten INSERT durch. Statt den Fehler durchzureichen, wird
        # hier die vom konkurrierenden Request angelegte Bestellung
        # nachgeladen und wie ein normaler Idempotenz-Treffer behandelt.
        if not idempotency_key:
            raise
        existing_order = get_order_by_idempotency_key(idempotency_key)
        if existing_order and existing_order.get("requestHash") == request_hash:
            return _initial_order_response(existing_order)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Idempotency-Key was already used for a different request body.",
        ) from exc
    order_created = build_message(
        "order.created",
        correlation_id,
        {
            "orderId": order_id,
            "customerId": customer_id,
            "customer": order.customer.model_dump(),
            "shippingAddress": order.shippingAddress.model_dump(),
            "items": enriched_items,
            "payment": payment,
            "amount": str(amount),
            "currency": currency,
            "status": "PENDING",
        },
    )
    publish_message("order.created", order_created)
    notify_admin_dashboard(order_id, correlation_id, "order.created")

    reserve_requested = build_message(
        "warehouse.reserve.requested",
        correlation_id,
        {
            "orderId": order_id,
            "items": enriched_items,
            "amount": str(amount),
            "currency": currency,
            "provider": order.payment.provider,
            "scenario": order.payment.scenario,
            "payment": payment,
        },
        previous_event_id=order_created["messageId"],
    )
    publish_message("warehouse.reserve.requested", reserve_requested)

    logger.info(
        "Order accepted for asynchronous processing",
        extra={
            "correlation_id": correlation_id,
            "context": {"eventType": "order.accepted", "orderId": order_id, "itemCount": len(order.items)},
        },
    )
    return OrderResponse(
        orderId=order_id,
        correlationId=correlation_id,
        status="PENDING",
        amount=str(amount),
        currency=currency,
    )


@router.get("/orders/{orderId}", response_model=OrderResponse)
async def get_order(orderId: str) -> OrderResponse:
    """Liefert den aktuellen Saga-/Bestellstatus (Frontend pollt diesen
    Endpunkt waehrend der asynchronen Verarbeitung)."""
    order = get_order_record(orderId)
    if not order:
        raise HTTPException(status_code=404, detail=f"Order {orderId} not found")
    return _order_response(order)


@router.post("/orders/{orderId}/payment-confirmation", response_model=OrderResponse, status_code=status.HTTP_202_ACCEPTED)
async def confirm_order_payment(orderId: str, confirmation: PaymentConfirmationRequest) -> OrderResponse:
    """Kunde bestaetigt oder storniert eine ausstehende externe Zahlung
    (z.B. nach Rueckkehr von einer PayPal-Sandbox-Seite). Bei "approved"
    wird billing.payment.confirm.requested publiziert (billing-service
    fuehrt dann den eigentlichen Capture durch), bei "cancelled" direkt die
    Warehouse-Reservierung storniert."""
    order = get_order_record(orderId)
    if not order:
        raise HTTPException(status_code=404, detail=f"Order {orderId} not found")
    if order["status"] != "PAYMENT_ACTION_REQUIRED":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Order {orderId} is not awaiting a payment confirmation (status={order['status']}).",
        )
    # Claim ist die eigentliche (atomare) Absicherung gegen doppelte Aufrufe;
    # die Pruefung oben ist nur ein schneller Vorab-Check fuer den 404/409-Fall
    # ohne Beruehrung der Order. Zwischen diesem SELECT und dem claim liegt
    # zwar weiterhin ein Zeitfenster, aber das UPDATE...WHERE status=... in
    # claim_payment_confirmation() laesst bei einem parallelen Zweitaufruf
    # keinen zweiten Treffer zu - siehe Docstring dort.
    if not claim_payment_confirmation(orderId):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Order {orderId} payment confirmation was already processed.",
        )
    correlation_id = str(order["correlationId"])
    notify_admin_dashboard(orderId, correlation_id, "order.payment-confirmation")

    if confirmation.outcome == "cancelled":
        update_order_status(orderId, "PAYMENT_FAILED")
        cancel_requested = build_message(
            "warehouse.cancel.requested",
            correlation_id,
            {
                "orderId": orderId,
                "reasonCode": "PAYMENT_CANCELLED",
                "message": "Kunde hat die Zahlung abgebrochen, Warehouse-Reservierung wird storniert.",
            },
        )
        publish_message("warehouse.cancel.requested", cancel_requested)
    else:
        confirm_requested = build_message(
            "billing.payment.confirm.requested",
            correlation_id,
            {
                "orderId": orderId,
                "transactionId": order.get("transactionId"),
                "provider": (order.get("payment") or {}).get("provider"),
                "amount": str(order["amount"]),
                "currency": order["currency"],
            },
        )
        publish_message("billing.payment.confirm.requested", confirm_requested)

    return _order_response(get_order_record(orderId))


@router.post("/admin/login", response_model=AdminSessionResponse)
async def admin_login(credentials: AdminLoginRequest, response: Response) -> AdminSessionResponse:
    """Prueft Admin-Zugangsdaten und setzt bei Erfolg ein httpOnly-
    Session-Cookie (siehe require_admin() fuer die Pruefung auf den
    folgenden Requests)."""
    if not verify_admin_credentials(credentials.username, credentials.password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=settings.admin_session_hours)
    create_admin_session(_token_hash(token), credentials.username, expires_at)
    response.set_cookie(
        ADMIN_SESSION_COOKIE,
        token,
        httponly=True,
        secure=settings.admin_cookie_secure,
        samesite="lax",
        max_age=settings.admin_session_hours * 60 * 60,
        path="/",
    )
    return AdminSessionResponse(authenticated=True, username=credentials.username)


@router.post("/admin/logout", response_model=AdminSessionResponse)
async def admin_logout(request: Request, response: Response) -> AdminSessionResponse:
    """Loescht die Server-seitige Session sowie das Cookie."""
    token = request.cookies.get(ADMIN_SESSION_COOKIE)
    if token:
        delete_admin_session(_token_hash(token))
    response.delete_cookie(ADMIN_SESSION_COOKIE, path="/")
    return AdminSessionResponse(authenticated=False)


@router.get("/admin/session", response_model=AdminSessionResponse)
async def admin_session(username: str = Depends(require_admin)) -> AdminSessionResponse:
    """Erlaubt dem Frontend zu pruefen, ob eine bestehende Session noch
    gueltig ist (z.B. beim Neuladen der Admin-Seite)."""
    return AdminSessionResponse(authenticated=True, username=username)


@router.get("/admin/orders", response_model=list[AdminOrderResponse])
async def admin_orders(_: str = Depends(require_admin)) -> list[AdminOrderResponse]:
    """Bestelluebersicht fuer das Admin-Dashboard (neueste zuerst, siehe
    list_admin_orders())."""
    return [AdminOrderResponse(**_serialize_order(order)) for order in list_admin_orders()]


@router.get("/admin/orders/{orderId}/audit", response_model=AdminAuditResponse)
async def admin_order_audit(orderId: str, _: str = Depends(require_admin)) -> AdminAuditResponse:
    """Liefert die vollstaendige Audit-Snapshot-Timeline einer Bestellung
    ueber alle Services hinweg (per HTTP-Aufruf an audit-service, siehe
    fetch_audit_snapshots())."""
    order = get_order_record(orderId)
    if not order:
        raise HTTPException(status_code=404, detail=f"Order {orderId} not found")
    return AdminAuditResponse(
        orderId=orderId,
        snapshots=fetch_audit_snapshots(str(order["correlationId"])),
    )


@router.get("/admin/orders/events")
async def admin_orders_events(request: Request, _: str = Depends(require_admin)) -> StreamingResponse:
    """Server-Sent-Events-Stream fuer Echtzeit-Updates im Admin-Dashboard.

    Statt bei jeder Aenderung den vollen Bestellzustand zu pushen (was hier
    doppelte Business-Logik ggue. den bestehenden REST-Endpunkten bedeuten
    wuerde), sendet dieser Stream nur ein minimales "orderId X hat sich
    veraendert"-Signal (siehe realtime.py/notify_admin_dashboard in saga.py).
    Das Frontend reagiert darauf, indem es die Bestellliste bzw. die gerade
    geoeffnete Detailansicht per bestehendem REST-Aufruf neu laedt - einzige
    Quelle der Wahrheit bleibt die Datenbank, nicht der Event-Stream selbst.
    """

    async def event_stream():
        subscriber_queue = realtime.subscribe()
        try:
            # Direkt nach Verbindungsaufbau ein Kommentar-Event senden, damit
            # der Browser die SSE-Verbindung sofort als erfolgreich geoeffnet
            # erkennt (EventSource wertet erst eine "data:"-Zeile als Event,
            # ein Kommentar reicht aber schon, um die Verbindung "warm" zu
            # halten und Proxies/Load-Balancer nicht vorzeitig timeouten zu
            # lassen).
            yield ": connected\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    # queue.Queue.get() ist blockierend (Thread-API) - daher
                    # im Executor ausfuehren, damit der asyncio-Event-Loop
                    # waehrenddessen andere Requests/Verbindungen bedienen
                    # kann. Der Timeout sorgt dafuer, dass request.is_
                    # disconnected() regelmaessig neu geprueft wird, auch
                    # wenn gerade keine Events ankommen.
                    event = await asyncio.get_running_loop().run_in_executor(
                        None, _get_with_timeout, subscriber_queue, 15.0
                    )
                except queue.Empty:
                    yield ": keep-alive\n\n"
                    continue
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            realtime.unsubscribe(subscriber_queue)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Defensiv: viele Reverse-Proxies (z.B. Nginx) puffern
            # Streaming-Responses standardmaessig, was SSE-Events nur
            # verzoegert oder gar nicht beim Browser ankommen liesse. Im
            # lokalen Docker-Compose-Aufbau ruft das Frontend shop-service
            # zwar direkt auf (kein Nginx davor, siehe frontend/nginx.conf),
            # der Header schadet aber nicht und macht den Endpunkt auch
            # hinter einem echten Reverse-Proxy sofort funktionsfaehig.
            "X-Accel-Buffering": "no",
        },
    )


def _get_with_timeout(q: "queue.Queue", timeout: float):
    """Kleiner Wrapper um Queue.get(), damit run_in_executor() sauber mit
    dem `timeout`-Keyword-Argument statt einer positional-only-Signatur
    umgehen kann."""
    return q.get(timeout=timeout)


@router.get("/admin/products", response_model=list[ProductResponse])
async def admin_products(request: Request, _: str = Depends(require_admin)) -> list[ProductResponse]:
    """Wie /products, aber hinter Admin-Login (Basis fuer die
    Produktverwaltung im Dashboard)."""
    stock_by_product_id = fetch_warehouse_stock(request.state.correlation_id)
    return [
        ProductResponse(**_serialize_product(product, stock_by_product_id.get(str(product["id"]))))
        for product in get_products()
    ]


@router.post("/admin/product-images", response_model=ImageUploadResponse)
async def admin_upload_product_image(
    file: UploadFile = File(...),
    _: str = Depends(require_admin),
) -> ImageUploadResponse:
    """Nimmt ein Produktbild entgegen, validiert Typ/Groesse und speichert es
    unter einem sicheren, kollisionsfreien Dateinamen im Upload-Verzeichnis.

    Der Dateiname wird NIE 1:1 vom Original-Upload uebernommen: der Stem wird
    auf a-z0-9/Bindestrich normalisiert (verhindert Path-Traversal/
    Sonderzeichen-Probleme) und um ein zufaelliges Suffix ergaenzt
    (verhindert, dass zwei Uploads mit gleichem Namen sich gegenseitig
    ueberschreiben).
    """
    content_types = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
    }
    if file.content_type not in content_types:
        raise HTTPException(status_code=415, detail="Only PNG, JPEG and WebP images are supported")
    content = await file.read()
    if len(content) > 6 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Image must not be larger than 6 MB")
    original_stem = Path(file.filename or "product-image").stem.lower()
    safe_stem = re.sub(r"[^a-z0-9]+", "-", original_stem).strip("-") or "product-image"
    filename = f"{safe_stem}-{uuid4().hex[:10]}{content_types[file.content_type]}"
    output_path = Path(settings.product_image_upload_dir) / filename
    output_path.write_bytes(content)
    return ImageUploadResponse(
        imageUrl=f"{settings.shop_public_base_url.rstrip('/')}/product-images/{filename}",
        filename=filename,
    )


@router.post("/admin/products", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
async def admin_create_product(
    request: Request,
    product: ProductCreateRequest,
    _: str = Depends(require_admin),
) -> ProductResponse:
    """Legt ein neues Produkt UND (in einem zweiten Schritt) den zugehoerigen
    initialen Lagerbestand in warehouse-service an - zwei separate
    Datenbanken (product_id existiert erst nach dem ersten Aufruf), daher
    kein gemeinsamer Transaktionsrahmen."""
    product_id = str(uuid4())
    product_payload = product.model_dump(exclude={"quantityOnHand", "location"})
    created = create_product_record(product_id, product_payload)
    created_stock = create_warehouse_stock(
        {
            "productId": product_id,
            "quantityOnHand": product.quantityOnHand,
            "location": product.location or "RETRO-A1",
        },
        request.state.correlation_id,
    )
    return ProductResponse(**_serialize_product(created, created_stock))


@router.put("/admin/products/{productId}", response_model=ProductResponse)
async def admin_update_product(
    request: Request,
    productId: str,
    product: ProductUpdateRequest,
    _: str = Depends(require_admin),
) -> ProductResponse:
    """Aktualisiert die Stammdaten (Name/Preis/Bild/...) eines Produkts.
    Beruehrt NICHT den Lagerbestand - dafuer siehe admin_update_product_stock()."""
    updated = update_product_record(productId, product.model_dump())
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Product {productId} not found")
    stock_by_product_id = fetch_warehouse_stock(request.state.correlation_id)
    return ProductResponse(**_serialize_product(updated, stock_by_product_id.get(str(updated["id"]))))


@router.patch("/admin/products/{productId}/stock", response_model=ProductResponse)
async def admin_update_product_stock(
    request: Request,
    productId: str,
    stock: StockUpdateRequest,
    _: str = Depends(require_admin),
) -> ProductResponse:
    """Aktualisiert nur den Lagerbestand (Menge/Lagerort) eines Produkts -
    delegiert an warehouse-service, das die eigentliche Bestandstabelle
    haelt (shop-service besitzt keine eigenen Bestandsdaten)."""
    update_warehouse_stock(productId, stock.model_dump(), request.state.correlation_id)
    product = next((entry for entry in get_products() if str(entry["id"]) == productId), None)
    if product is None:
        raise HTTPException(status_code=404, detail=f"Product {productId} not found")
    stock_by_product_id = fetch_warehouse_stock(request.state.correlation_id)
    return ProductResponse(**_serialize_product(product, stock_by_product_id.get(productId)))
