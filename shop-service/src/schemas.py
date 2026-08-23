"""Pydantic-Schemas (Request-/Response-Modelle) des shop-service."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Antwort des /health-Endpunkts (fuer Docker-Healthchecks/Monitoring)."""

    status: str = "ok"
    service: str


class OrderItem(BaseModel):
    """Eine rohe Bestellposition aus dem Checkout-Request (nur productId +
    Menge - Preise werden serverseitig nachgeschlagen, siehe
    enrich_items_from_products())."""

    productId: str
    quantity: int = Field(ge=1)


class Customer(BaseModel):
    firstName: str = Field(min_length=1)
    lastName: str = Field(min_length=1)
    email: str = Field(min_length=3)
    phone: str | None = None


class Address(BaseModel):
    street: str = Field(min_length=1)
    houseNumber: str = Field(min_length=1)
    postalCode: str = Field(min_length=1)
    city: str = Field(min_length=1)
    country: str = Field(min_length=1)


class PaymentSelection(BaseModel):
    """Zahlungsauswahl aus dem Checkout - `scenario` steuert das im
    Aufgabenblatt geforderte gezielte Durchspielen von Testszenarien
    (happy_path, warehouse_commit_failed, out_of_stock, ...) end-to-end
    durch die ganze Saga."""

    provider: str
    currency: str = "EUR"
    scenario: str = "happy_path"
    mode: str = "sandbox"
    cardholder: str | None = None
    testPaymentMethod: str | None = None
    paypalEmail: str | None = None
    webhookStatus: str | None = None
    webhookReasonCode: str | None = None
    webhookMessage: str | None = None


class CreateOrderRequest(BaseModel):
    """Body fuer POST /orders (Checkout-Formular)."""

    customerId: str | None = None
    customer: Customer
    shippingAddress: Address
    billingAddress: Address | None = None
    items: list[OrderItem] = Field(min_length=1)
    payment: PaymentSelection


class ProductResponse(BaseModel):
    """Produkt inkl. optionaler Lagerbestand-Felder (None/UNKNOWN, falls
    warehouse-service beim Aufruf nicht erreichbar war, siehe
    fetch_warehouse_stock())."""

    id: str
    name: str
    year: str
    description: str
    price: str
    currency: str
    imageUrl: str
    imageAlt: str
    imageSource: str
    imageLicense: str
    imageCredit: str
    quantityOnHand: int | None = None
    reservedQuantity: int | None = None
    availableQuantity: int | None = None
    location: str | None = None
    stockStatus: str = "UNKNOWN"


class ProductUpdateRequest(BaseModel):
    """Body fuer PUT /admin/products/{productId}."""

    name: str = Field(min_length=1)
    year: str = Field(min_length=1)
    description: str = Field(min_length=1)
    price: str = Field(min_length=1)
    currency: str = "EUR"
    imageUrl: str = Field(min_length=1)
    imageAlt: str = Field(min_length=1)
    imageSource: str | None = ""
    imageLicense: str | None = ""
    imageCredit: str | None = ""


class ProductCreateRequest(ProductUpdateRequest):
    """Body fuer POST /admin/products - erweitert ProductUpdateRequest um die
    initialen Lagerbestand-Felder, die beim Anlegen zusaetzlich an
    warehouse-service durchgereicht werden (siehe admin_create_product())."""

    quantityOnHand: int = Field(default=0, ge=0)
    location: str | None = "RETRO-A1"


class StockUpdateRequest(BaseModel):
    """Body fuer PATCH /admin/products/{productId}/stock."""

    quantityOnHand: int = Field(ge=0)
    location: str | None = None


class OrderResponse(BaseModel):
    """Oeffentliche Sicht auf eine Bestellung (GET/POST /orders/...)."""

    orderId: str
    correlationId: str
    status: str
    amount: str | None = None
    currency: str | None = None
    transactionId: str | None = None
    paymentRedirectUrl: str | None = None
    customer: dict | None = None
    shippingAddress: dict | None = None


class PaymentConfirmationRequest(BaseModel):
    """Body fuer POST /orders/{orderId}/payment-confirmation (Kunde bestaetigt
    oder storniert eine externe Zahlung, z.B. nach PayPal-Redirect)."""

    outcome: Literal["approved", "cancelled"]


class AdminLoginRequest(BaseModel):
    username: str
    password: str


class AdminSessionResponse(BaseModel):
    authenticated: bool
    username: str | None = None


class AdminOrderResponse(BaseModel):
    """Vollstaendige Bestellsicht fuer das Admin-Dashboard (mehr Felder als
    OrderResponse, u.a. items/payment/Zeitstempel fuer die Uebersichtstabelle)."""

    orderId: str
    correlationId: str
    status: str
    amount: str
    currency: str
    customer: dict | None = None
    shippingAddress: dict | None = None
    billingAddress: dict | None = None
    items: list[dict] = []
    payment: dict | None = None
    transactionId: str | None = None
    invoiceId: str | None = None
    invoiceStatus: str | None = None
    warehouseCommitStatus: str | None = None
    createdAt: datetime
    updatedAt: datetime


class AdminAuditResponse(BaseModel):
    """Antwort von GET /admin/orders/{orderId}/audit - die vollstaendige
    Audit-Snapshot-Timeline einer Bestellung ueber alle Services hinweg."""

    orderId: str
    snapshots: list[dict]


class ImageUploadResponse(BaseModel):
    imageUrl: str
    filename: str
