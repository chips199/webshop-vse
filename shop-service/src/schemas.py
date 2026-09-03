"""API-Datenmodelle des Shop-Service."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Antwort des Health-Endpunkts."""

    status: str = "ok"
    service: str


class OrderItem(BaseModel):
    """Bestellposition ohne clientseitige Preisdaten."""

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
    """Zahlungsauswahl und optionales Testszenario."""

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
    """Neue Bestellung aus dem Checkout."""

    customerId: str | None = None
    customer: Customer
    shippingAddress: Address
    billingAddress: Address | None = None
    items: list[OrderItem] = Field(min_length=1)
    payment: PaymentSelection


class ProductResponse(BaseModel):
    """Produkt mit optionalen Bestandsdaten."""

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
    """Aenderung der Produktstammdaten."""

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
    """Neues Produkt mit Anfangsbestand."""

    quantityOnHand: int = Field(default=0, ge=0)
    location: str | None = "RETRO-A1"


class StockUpdateRequest(BaseModel):
    """Aenderung eines Lagerbestands."""

    quantityOnHand: int = Field(ge=0)
    location: str | None = None


class OrderResponse(BaseModel):
    """Oeffentliche Sicht auf eine Bestellung."""

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
    """Bestaetigung oder Abbruch einer externen Zahlung."""

    outcome: Literal["approved", "cancelled"]


class AdminLoginRequest(BaseModel):
    username: str
    password: str


class AdminSessionResponse(BaseModel):
    authenticated: bool
    username: str | None = None


class AdminOrderResponse(BaseModel):
    """Vollstaendige Bestellsicht des Admin-Dashboards."""

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
    """Audit-Timeline einer Bestellung."""

    orderId: str
    snapshots: list[dict]


class ImageUploadResponse(BaseModel):
    imageUrl: str
    filename: str
