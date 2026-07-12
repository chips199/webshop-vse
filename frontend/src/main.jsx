import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  ArrowLeft,
  ClipboardList,
  CreditCard,
  Cpu,
  Eye,
  Lock,
  LogOut,
  Minus,
  Plus,
  ReceiptText,
  ShieldCheck,
  ShoppingCart,
  Terminal,
  Trash2,
} from "lucide-react";
import "./styles.css";

const SHOP_API = import.meta.env.VITE_SHOP_API_URL || "http://localhost:8000";
const BILLING_API = import.meta.env.VITE_BILLING_API_URL || "http://localhost:8002";
const CART_STORAGE_KEY = "retro-parts-cart";

const emptyCustomer = {
  firstName: "Ada",
  lastName: "Lovelace",
  email: "ada.lovelace@example.test",
  phone: "+49 30 12345678",
};

const emptyAddress = {
  street: "Retroallee",
  houseNumber: "8",
  postalCode: "10115",
  city: "Berlin",
  country: "Deutschland",
};

const emptyPaymentDetails = {
  stripe: {
    cardholder: "Ada Lovelace",
    testPaymentMethod: "pm_card_visa",
    stripeSessionId: "",
    stripeSessionStatus: "",
    stripePaymentStatus: "",
  },
  paypal: {
    paypalEmail: "buyer@example.test",
    paypalOrderId: "",
    paypalCaptureId: "",
    approveUrl: "",
    status: "",
  },
};

function formatPrice(value, currency = "EUR") {
  return `${Number(value).toFixed(2)} ${currency}`;
}

function navigate(path) {
  window.history.pushState({}, "", path);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

function sleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function waitForOrderStatus(orderId) {
  let latest = null;
  const finalStatuses = new Set(["COMPLETED", "PAYMENT_FAILED", "OUT_OF_STOCK", "ROLLBACK_COMPLETED"]);
  for (let attempt = 0; attempt < 12; attempt += 1) {
    await sleep(500);
    const response = await fetch(`${SHOP_API}/orders/${orderId}`);
    if (!response.ok) continue;
    latest = await response.json();
    if (finalStatuses.has(latest.status)) break;
  }
  return latest;
}

function loadStoredCart() {
  try {
    return JSON.parse(localStorage.getItem(CART_STORAGE_KEY) || "{}");
  } catch {
    return {};
  }
}

function mapPayPalCustomer(payer = {}) {
  return {
    firstName: payer.firstName || "PayPal",
    lastName: payer.lastName || "Kunde",
    email: payer.email || "paypal-buyer@example.test",
    phone: "",
  };
}

function mapPayPalShippingAddress(address = {}) {
  return {
    street: address.street || "PayPal-Adresse",
    houseNumber: address.houseNumber || "-",
    postalCode: address.postalCode || "-",
    city: address.city || "-",
    country: address.country || "-",
  };
}

function mapStripeCustomer(customer = {}) {
  return {
    firstName: customer.firstName || "Stripe",
    lastName: customer.lastName || "Kunde",
    email: customer.email || "stripe-buyer@example.test",
    phone: customer.phone || "",
  };
}

function mapStripeShippingAddress(address = {}) {
  return {
    street: address.street || "Stripe-Adresse",
    houseNumber: address.houseNumber || "-",
    postalCode: address.postalCode || "-",
    city: address.city || "-",
    country: address.country || "-",
  };
}

function App() {
  const [path, setPath] = useState(window.location.pathname);
  const [products, setProducts] = useState([]);
  const [cart, setCart] = useState(loadStoredCart);
  const [provider, setProvider] = useState("stripe");
  const [paymentDetails, setPaymentDetails] = useState(emptyPaymentDetails);
  const [customer, setCustomer] = useState(emptyCustomer);
  const [shippingAddress, setShippingAddress] = useState(emptyAddress);
  const [order, setOrder] = useState(null);
  const [orderConfirmation, setOrderConfirmation] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const paypalReturnHandled = useRef("");
  const stripeReturnHandled = useRef("");

  useEffect(() => {
    const onPopState = () => setPath(window.location.pathname);
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  useEffect(() => {
    fetch(`${SHOP_API}/products`)
      .then((response) => {
        if (!response.ok) throw new Error(`Produktkatalog nicht erreichbar: HTTP ${response.status}`);
        return response.json();
      })
      .then(setProducts)
      .catch((caught) => setError(caught.message));
  }, []);

  useEffect(() => {
    localStorage.setItem(CART_STORAGE_KEY, JSON.stringify(cart));
  }, [cart]);

  const cartItems = useMemo(
    () =>
      products
        .filter((product) => cart[product.id])
        .map((product) => ({ ...product, quantity: cart[product.id] })),
    [cart, products],
  );
  const total = cartItems.reduce((sum, item) => sum + Number(item.price) * item.quantity, 0);

  function changeQuantity(productId, delta) {
    setCart((current) => {
      const next = Math.max(0, (current[productId] || 0) + delta);
      const copy = { ...current };
      if (next === 0) {
        delete copy[productId];
      } else {
        copy[productId] = next;
      }
      return copy;
    });
  }

  function addToCart(productId) {
    changeQuantity(productId, 1);
  }

  function removeFromCart(productId) {
    setCart((current) => {
      const copy = { ...current };
      delete copy[productId];
      return copy;
    });
  }

  async function createShopOrder({ customerOverride, shippingAddressOverride, paymentOverride, providerOverride } = {}) {
    const selectedProvider = providerOverride || provider;
    const selectedPayment = paymentOverride || paymentDetails;
    const selectedCustomer = customerOverride || customer;
    const selectedShippingAddress = shippingAddressOverride || shippingAddress;
    const selectedItems = cartItems.map((item) => ({ ...item }));
    setBusy(true);
    setError("");
    setOrder(null);
    try {
      const response = await fetch(`${SHOP_API}/orders`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Correlation-Id": crypto.randomUUID(),
        },
        body: JSON.stringify({
          customer: selectedCustomer,
          shippingAddress: selectedShippingAddress,
          items: selectedItems.map((item) => ({ productId: item.id, quantity: item.quantity })),
          payment: {
            provider: selectedProvider,
            currency: "EUR",
            mode: "sandbox",
            ...(selectedProvider === "stripe" ? selectedPayment.stripe : selectedPayment.paypal),
          },
        }),
      });
      if (!response.ok) {
        throw new Error(`Bestellung fehlgeschlagen: HTTP ${response.status}`);
      }
      const created = await response.json();
      const confirmedOrder = (await waitForOrderStatus(created.orderId)) || created;
      setOrder(confirmedOrder);
      setOrderConfirmation({
        order: confirmedOrder,
        customer: selectedCustomer,
        shippingAddress: selectedShippingAddress,
        items: selectedItems,
        payment: {
          provider: selectedProvider,
          mode: "sandbox",
          status: selectedProvider === "paypal" ? selectedPayment.paypal.status || "COMPLETED" : selectedPayment.stripe.stripePaymentStatus || "paid",
          transactionId: selectedProvider === "paypal" ? selectedPayment.paypal.paypalCaptureId : selectedPayment.stripe.stripeSessionId,
        },
        total,
      });
      setCart({});
      window.history.replaceState({}, "", "/confirmation");
      setPath("/confirmation");
    } catch (caught) {
      setError(caught.message);
    } finally {
      setBusy(false);
    }
  }

  async function submitOrder(event) {
    event.preventDefault();
    if (cartItems.length === 0) return;
    if (provider === "paypal" && !paymentDetails.paypal.paypalCaptureId) {
      await startPayPalCheckout();
      return;
    }
    if (provider === "stripe" && !paymentDetails.stripe.stripeSessionId) {
      await startStripeCheckout();
      return;
    }
    await createShopOrder();
  }

  async function startStripeCheckout() {
    if (cartItems.length === 0 || total <= 0) return;
    setBusy(true);
    setError("");
    try {
      const response = await fetch(`${BILLING_API}/stripe/sessions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          referenceId: crypto.randomUUID(),
          amount: total.toFixed(2),
          currency: "EUR",
          successUrl: `${window.location.origin}/checkout?stripe=approved&session_id={CHECKOUT_SESSION_ID}`,
          cancelUrl: `${window.location.origin}/checkout?stripe=cancelled`,
          customerEmail: customer.email,
          items: cartItems.map((item) => ({
            name: item.name,
            amount: item.price,
            quantity: item.quantity,
          })),
        }),
      });
      if (!response.ok) {
        throw new Error(`Stripe Checkout Session konnte nicht erstellt werden: HTTP ${response.status}`);
      }
      const created = await response.json();
      setPaymentDetails({
        ...paymentDetails,
        stripe: {
          ...paymentDetails.stripe,
          stripeSessionId: created.sessionId,
          stripeSessionStatus: created.status,
          stripePaymentStatus: created.paymentStatus,
        },
      });
      if (created.checkoutUrl) {
        window.location.assign(created.checkoutUrl);
        return;
      }
      await verifyStripeAndCreateOrder(created.sessionId);
    } catch (caught) {
      setError(caught.message);
      setBusy(false);
    }
  }

  async function verifyStripeAndCreateOrder(sessionId) {
    setProvider("stripe");
    setBusy(true);
    setError("");
    const response = await fetch(`${BILLING_API}/stripe/sessions/${sessionId}`);
    if (!response.ok) {
      throw new Error(`Stripe Checkout Session konnte nicht geprueft werden: HTTP ${response.status}`);
    }
    const session = await response.json();
    if (session.paymentStatus !== "paid") {
      throw new Error(`Stripe-Zahlung ist nicht bezahlt: ${session.paymentStatus || "unknown"}`);
    }
    const stripeCustomer = mapStripeCustomer(session.customer);
    const stripeShippingAddress = mapStripeShippingAddress(session.shippingAddress);
    const nextPaymentDetails = {
      ...paymentDetails,
      stripe: {
        ...paymentDetails.stripe,
        cardholder: `${stripeCustomer.firstName} ${stripeCustomer.lastName}`.trim(),
        stripeSessionId: session.sessionId,
        stripeSessionStatus: session.status,
        stripePaymentStatus: session.paymentStatus,
      },
    };
    setCustomer(stripeCustomer);
    setShippingAddress(stripeShippingAddress);
    setPaymentDetails(nextPaymentDetails);
    await createShopOrder({
      customerOverride: stripeCustomer,
      shippingAddressOverride: stripeShippingAddress,
      paymentOverride: nextPaymentDetails,
      providerOverride: "stripe",
    });
  }

  async function startPayPalCheckout() {
    if (cartItems.length === 0 || total <= 0) return;
    setBusy(true);
    setError("");
    try {
      const response = await fetch(`${BILLING_API}/paypal/orders`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          referenceId: crypto.randomUUID(),
          amount: total.toFixed(2),
          currency: "EUR",
          returnUrl: `${window.location.origin}/checkout?paypal=approved`,
          cancelUrl: `${window.location.origin}/checkout?paypal=cancelled`,
        }),
      });
      if (!response.ok) {
        throw new Error(`PayPal-Zahlung konnte nicht gestartet werden: HTTP ${response.status}`);
      }
      const created = await response.json();
      setPaymentDetails({
        ...paymentDetails,
        paypal: {
          ...paymentDetails.paypal,
          paypalOrderId: created.orderId,
          approveUrl: created.approveUrl || "",
          status: created.status,
          paypalCaptureId: "",
        },
      });
      if (created.approveUrl) {
        window.location.assign(created.approveUrl);
        return;
      }
      await capturePayPalAndCreateOrder(created.orderId);
    } catch (caught) {
      setError(caught.message);
      setBusy(false);
    }
  }

  async function capturePayPalAndCreateOrder(paypalOrderId) {
    setProvider("paypal");
    setBusy(true);
    setError("");
    const response = await fetch(`${BILLING_API}/paypal/orders/${paypalOrderId}/capture`, { method: "POST" });
    if (!response.ok) {
      throw new Error(`PayPal-Zahlung konnte nicht bestaetigt werden: HTTP ${response.status}`);
    }
    const captured = await response.json();
    const paypalCustomer = mapPayPalCustomer(captured.payer);
    const paypalShippingAddress = mapPayPalShippingAddress(captured.shippingAddress);
    const nextPaymentDetails = {
      ...paymentDetails,
      paypal: {
        ...paymentDetails.paypal,
        paypalEmail: paypalCustomer.email,
        paypalOrderId: captured.orderId,
        paypalCaptureId: captured.captureId,
        status: captured.status,
      },
    };
    setCustomer(paypalCustomer);
    setShippingAddress(paypalShippingAddress);
    setPaymentDetails(nextPaymentDetails);
    await createShopOrder({
      customerOverride: paypalCustomer,
      shippingAddressOverride: paypalShippingAddress,
      paymentOverride: nextPaymentDetails,
      providerOverride: "paypal",
    });
  }

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const paypalState = params.get("paypal");
    const paypalOrderId = params.get("token");
    if (path === "/checkout" && paypalState === "cancelled") {
      setError("PayPal-Zahlung wurde abgebrochen.");
      window.history.replaceState({}, "", "/checkout");
      return;
    }
    if (path !== "/checkout" || paypalState !== "approved" || !paypalOrderId || cartItems.length === 0) return;
    if (paypalReturnHandled.current === paypalOrderId) return;
    paypalReturnHandled.current = paypalOrderId;

    capturePayPalAndCreateOrder(paypalOrderId).catch((caught) => {
      setError(caught.message);
      setBusy(false);
    });
  }, [path, cartItems.length]);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const stripeState = params.get("stripe");
    const sessionId = params.get("session_id");
    if (path === "/checkout" && stripeState === "cancelled") {
      setError("Stripe-Zahlung wurde abgebrochen.");
      window.history.replaceState({}, "", "/checkout");
      return;
    }
    if (path !== "/checkout" || stripeState !== "approved" || !sessionId || cartItems.length === 0) return;
    if (stripeReturnHandled.current === sessionId) return;
    stripeReturnHandled.current = sessionId;

    verifyStripeAndCreateOrder(sessionId).catch((caught) => {
      setError(caught.message);
      setBusy(false);
    });
  }, [path, cartItems.length]);

  return (
    <main className="terminal-shell">
      <Header path={path} />
      {path.startsWith("/admin") ? (
        <AdminPage />
      ) : path === "/confirmation" ? (
        <OrderConfirmationPage confirmation={orderConfirmation} />
      ) : path === "/cart" ? (
        <CartPage
          cartItems={cartItems}
          changeQuantity={changeQuantity}
          removeFromCart={removeFromCart}
          total={total}
        />
      ) : path === "/checkout" ? (
        <CheckoutPage
          cartItems={cartItems}
          error={error}
          order={order}
          provider={provider}
          setProvider={setProvider}
          submitOrder={submitOrder}
          startPayPalCheckout={startPayPalCheckout}
          startStripeCheckout={startStripeCheckout}
          total={total}
          busy={busy}
        />
      ) : path.startsWith("/products/") ? (
        <ProductDetailPage
          cartItems={cartItems}
          product={products.find((entry) => entry.id === path.split("/").pop())}
          addToCart={addToCart}
          changeQuantity={changeQuantity}
        />
      ) : (
        <ShopPage
          addToCart={addToCart}
          cartItems={cartItems}
          changeQuantity={changeQuantity}
          error={error}
          products={products}
          total={total}
        />
      )}
    </main>
  );
}

function Header({ path }) {
  return (
    <section className="topbar">
      <button className="brand" onClick={() => navigate("/")}>
        <Terminal size={22} />
        <span>
          <small>RETRO PARTS TERMINAL</small>
          Historische Computerteile
        </span>
      </button>
      <nav className="nav">
        <button className={path === "/" ? "active" : ""} onClick={() => navigate("/")}>
          <Cpu size={16} />
          Shop
        </button>
        <button className={path === "/cart" ? "active" : ""} onClick={() => navigate("/cart")}>
          <ShoppingCart size={16} />
          Warenkorb
        </button>
        <button className={path.startsWith("/admin") ? "active" : ""} onClick={() => navigate("/admin")}>
          <ShieldCheck size={16} />
          Admin
        </button>
      </nav>
    </section>
  );
}

function ShopPage({ addToCart, cartItems, changeQuantity, error, products, total }) {
  return (
    <section className="shop-layout">
      <div className="catalog">
        {products.map((product) => (
          <article className="product" key={product.id}>
            <button className="image-button" onClick={() => navigate(`/products/${product.id}`)}>
              <img src={product.imageUrl} alt={product.imageAlt} />
            </button>
            <div className="product-body">
              <button className="product-title product-title-button" onClick={() => navigate(`/products/${product.id}`)}>
                <Cpu size={18} />
                <h2>{product.name}</h2>
              </button>
              <p className="year">{product.year}</p>
              <p>{product.description}</p>
              <small className="credit">
                Bild: {product.imageCredit}, {product.imageLicense}
              </small>
              <div className="product-actions">
                <strong>{formatPrice(product.price, product.currency)}</strong>
                <div className="stepper">
                  <button onClick={() => changeQuantity(product.id, -1)} aria-label="Menge verringern">
                    <Minus size={16} />
                  </button>
                  <span>{cartItems.find((item) => item.id === product.id)?.quantity || 0}</span>
                  <button onClick={() => changeQuantity(product.id, 1)} aria-label="Menge erhoehen">
                    <Plus size={16} />
                  </button>
                </div>
              </div>
              <div className="product-buttons">
                <button className="secondary-button" onClick={() => navigate(`/products/${product.id}`)}>
                  <Eye size={16} />
                  Details
                </button>
                <button className="add-button" onClick={() => addToCart(product.id)}>
                  <ShoppingCart size={16} />
                  In den Warenkorb
                </button>
              </div>
            </div>
          </article>
        ))}
      </div>
      <CartPanel cartItems={cartItems} total={total} />
      {error && <p className="error">{error}</p>}
    </section>
  );
}

function ProductDetailPage({ addToCart, cartItems, changeQuantity, product }) {
  if (!product) {
    return (
      <section className="detail-page">
        <button className="link-button" onClick={() => navigate("/")}>
          <ArrowLeft size={16} />
          Zurueck zum Shop
        </button>
        <p className="muted">Artikel wurde nicht gefunden.</p>
      </section>
    );
  }
  const quantity = cartItems.find((item) => item.id === product.id)?.quantity || 0;
  return (
    <section className="detail-page">
      <button className="link-button" onClick={() => navigate("/")}>
        <ArrowLeft size={16} />
        Zurueck zum Shop
      </button>
      <article className="detail-panel">
        <img src={product.imageUrl} alt={product.imageAlt} />
        <div className="detail-copy">
          <p className="year">{product.year}</p>
          <h1>{product.name}</h1>
          <p>{product.description}</p>
          <dl className="spec-grid">
            <div>
              <dt>Preis</dt>
              <dd>{formatPrice(product.price, product.currency)}</dd>
            </div>
            <div>
              <dt>Bildquelle</dt>
              <dd>{product.imageCredit}</dd>
            </div>
            <div>
              <dt>Lizenzhinweis</dt>
              <dd>{product.imageLicense}</dd>
            </div>
          </dl>
          <div className="detail-actions">
            <div className="stepper">
              <button onClick={() => changeQuantity(product.id, -1)} aria-label="Menge verringern">
                <Minus size={16} />
              </button>
              <span>{quantity}</span>
              <button onClick={() => changeQuantity(product.id, 1)} aria-label="Menge erhoehen">
                <Plus size={16} />
              </button>
            </div>
            <button className="add-button" onClick={() => addToCart(product.id)}>
              <ShoppingCart size={16} />
              In den Warenkorb
            </button>
            <button className="checkout-button compact" disabled={quantity === 0} onClick={() => navigate("/cart")}>
              Warenkorb pruefen
            </button>
          </div>
        </div>
      </article>
    </section>
  );
}

function CartPanel({ cartItems, total }) {
  return (
    <aside className="checkout">
      <div className="panel-title">
        <ShoppingCart size={18} />
        <h2>Warenkorb</h2>
      </div>
      {cartItems.length === 0 ? (
        <p className="muted">Keine Bauteile ausgewaehlt.</p>
      ) : (
        <ul className="cart-list">
          {cartItems.map((item) => (
            <li key={item.id}>
              <span>
                {item.quantity}x {item.name}
              </span>
              <strong>{formatPrice(Number(item.price) * item.quantity, item.currency)}</strong>
            </li>
          ))}
        </ul>
      )}
      <div className="total">
        <span>Total</span>
        <strong>{formatPrice(total)}</strong>
      </div>
      <button className="checkout-button" disabled={cartItems.length === 0} onClick={() => navigate("/cart")}>
        Warenkorb bearbeiten
      </button>
    </aside>
  );
}

function CartPage({ cartItems, changeQuantity, removeFromCart, total }) {
  return (
    <section className="cart-page">
      <button className="link-button" onClick={() => navigate("/")}>
        <ArrowLeft size={16} />
        Weiter einkaufen
      </button>
      <section className="cart-editor">
        <div className="panel-title">
          <ShoppingCart size={20} />
          <h2>Warenkorb</h2>
        </div>

        {cartItems.length === 0 ? (
          <div className="empty-cart">
            <p className="muted">Der Warenkorb ist leer.</p>
            <button className="add-button" onClick={() => navigate("/")}>
              Zum Shop
            </button>
          </div>
        ) : (
          <>
            <ul className="cart-editor-list">
              {cartItems.map((item) => (
                <li className="cart-editor-row" key={item.id}>
                  <img src={item.imageUrl} alt={item.imageAlt} />
                  <div className="cart-editor-copy">
                    <button className="product-title-button" onClick={() => navigate(`/products/${item.id}`)}>
                      <h3>{item.name}</h3>
                    </button>
                    <small className="year">{item.year}</small>
                    <span>{formatPrice(item.price, item.currency)} pro Stueck</span>
                  </div>
                  <div className="cart-editor-controls">
                    <div className="stepper">
                      <button onClick={() => changeQuantity(item.id, -1)} aria-label="Menge verringern">
                        <Minus size={16} />
                      </button>
                      <span>{item.quantity}</span>
                      <button onClick={() => changeQuantity(item.id, 1)} aria-label="Menge erhoehen">
                        <Plus size={16} />
                      </button>
                    </div>
                    <button className="remove-button" onClick={() => removeFromCart(item.id)} aria-label={`${item.name} entfernen`}>
                      <Trash2 size={16} />
                      Entfernen
                    </button>
                  </div>
                  <strong>{formatPrice(Number(item.price) * item.quantity, item.currency)}</strong>
                </li>
              ))}
            </ul>
            <div className="cart-editor-footer">
              <div className="total">
                <span>Total</span>
                <strong>{formatPrice(total)}</strong>
              </div>
              <div className="cart-editor-actions">
                <button className="secondary-button" onClick={() => navigate("/")}>
                  Weiter einkaufen
                </button>
                <button className="checkout-button compact" onClick={() => navigate("/checkout")}>
                  Weiter zur Zahlung
                </button>
              </div>
            </div>
          </>
        )}
      </section>
    </section>
  );
}

function CheckoutPage({
  busy,
  cartItems,
  error,
  order,
  provider,
  setProvider,
  startPayPalCheckout,
  startStripeCheckout,
  submitOrder,
  total,
}) {
  return (
    <section className="checkout-page">
      <button className="link-button" onClick={() => navigate("/cart")}>
        <ArrowLeft size={16} />
        Zurueck zum Warenkorb
      </button>
      <form className="checkout-form" onSubmit={submitOrder}>
        <section className="form-section">
          <div className="panel-title">
            <ShoppingCart size={18} />
            <h2>Zahlung</h2>
          </div>
          <div className="segments">
            <button type="button" className={provider === "stripe" ? "active" : ""} onClick={() => setProvider("stripe")}>
              Stripe
            </button>
            <button type="button" className={provider === "paypal" ? "active" : ""} onClick={() => setProvider("paypal")}>
              PayPal
            </button>
          </div>
          <PaymentFields
            provider={provider}
            startPayPalCheckout={startPayPalCheckout}
            startStripeCheckout={startStripeCheckout}
            total={total}
          />
        </section>

        <aside className="checkout-summary">
          <h2>Bestelluebersicht</h2>
          {cartItems.length === 0 ? (
            <p className="muted">Der Warenkorb ist leer.</p>
          ) : (
            <ul className="cart-list">
              {cartItems.map((item) => (
                <li key={item.id}>
                  <span>
                    {item.quantity}x {item.name}
                  </span>
                  <strong>{formatPrice(Number(item.price) * item.quantity, item.currency)}</strong>
                </li>
              ))}
            </ul>
          )}
          <div className="total">
            <span>Total</span>
            <strong>{formatPrice(total)}</strong>
          </div>
          <button className="checkout-button" disabled={busy || cartItems.length === 0}>
            {busy ? "Bestellung wird verarbeitet..." : provider === "paypal" ? "Mit PayPal bezahlen" : "Mit Stripe bezahlen"}
          </button>
          {order && (
            <p className="success">
              Bestellung angenommen: {order.orderId}.
            </p>
          )}
          {error && <p className="error">{error}</p>}
        </aside>
      </form>
    </section>
  );
}

function OrderConfirmationPage({ confirmation }) {
  if (!confirmation) {
    return (
      <section className="confirmation-page">
        <section className="confirmation-panel">
          <div className="panel-title">
            <ReceiptText size={20} />
            <h2>Bestellbestaetigung</h2>
          </div>
          <p className="muted">Es liegt noch keine abgeschlossene Bestellung vor.</p>
          <button className="add-button" onClick={() => navigate("/")}>
            Zurueck zum Shop
          </button>
        </section>
      </section>
    );
  }

  const { customer, items, order, payment, shippingAddress, total } = confirmation;

  return (
    <section className="confirmation-page">
      <section className="confirmation-panel">
        <div className="confirmation-hero">
          <div className="panel-title">
            <ReceiptText size={22} />
            <h2>Bestellung bestaetigt</h2>
          </div>
          <p className="success">Zahlung erfolgreich. Deine Bestellung wurde angelegt.</p>
        </div>

        <div className="confirmation-grid">
          <div className="confirmation-block">
            <span>Bestellnummer</span>
            <strong>{order.orderId}</strong>
          </div>
          <div className="confirmation-block">
            <span>Status</span>
            <strong>Bezahlt</strong>
          </div>
          <div className="confirmation-block">
            <span>Zahlung</span>
            <strong>{payment.provider === "paypal" ? "PayPal" : "Stripe"}</strong>
          </div>
          <div className="confirmation-block">
            <span>Betrag</span>
            <strong>{formatPrice(total)}</strong>
          </div>
        </div>

        <section className="confirmation-section">
          <h3>Artikel</h3>
          <ul className="confirmation-items">
            {items.map((item) => (
              <li key={item.id}>
                <span>{item.quantity}x {item.name}</span>
                <strong>{formatPrice(Number(item.price) * item.quantity, item.currency)}</strong>
              </li>
            ))}
          </ul>
          <div className="total">
            <span>Gesamt</span>
            <strong>{formatPrice(total)}</strong>
          </div>
        </section>

        <div className="confirmation-columns">
          <section className="confirmation-section">
            <h3>Kontakt</h3>
            <p>{customer.firstName} {customer.lastName}</p>
            <p>{customer.email}</p>
            {customer.phone && <p>{customer.phone}</p>}
          </section>

          <section className="confirmation-section">
            <h3>Lieferadresse</h3>
            <p>{shippingAddress.street} {shippingAddress.houseNumber}</p>
            <p>{shippingAddress.postalCode} {shippingAddress.city}</p>
            <p>{shippingAddress.country}</p>
          </section>
        </div>

        <div className="confirmation-actions">
          <button className="secondary-button" onClick={() => navigate("/")}>
            Weiter einkaufen
          </button>
        </div>
      </section>
    </section>
  );
}

function PaymentFields({ provider, startPayPalCheckout, startStripeCheckout, total }) {
  if (provider === "paypal") {
    return (
      <div className="payment-box">
        <div className="panel-title">
          <CreditCard size={16} />
          <strong>PayPal</strong>
        </div>
        <div className="paypal-actions">
          <button className="add-button" type="button" disabled={total <= 0} onClick={startPayPalCheckout}>
            Zu PayPal wechseln
          </button>
        </div>
        <p className="muted">Du wirst zur sicheren Zahlung weitergeleitet und kommst danach automatisch zur Bestellbestaetigung zurueck.</p>
      </div>
    );
  }
  return (
    <div className="payment-box">
      <div className="panel-title">
        <CreditCard size={16} />
        <strong>Stripe</strong>
      </div>
      <div className="paypal-actions">
        <button className="add-button" type="button" disabled={total <= 0} onClick={startStripeCheckout}>
          Zu Stripe wechseln
        </button>
      </div>
      <p className="muted">Du wirst zur sicheren Zahlung weitergeleitet und kommst danach automatisch zur Bestellbestaetigung zurueck.</p>
    </div>
  );
}

function TextInput({ label, onChange, required = true, type = "text", value }) {
  return (
    <label className="field">
      <span>{label}</span>
      <input required={required} type={type} value={value || ""} onChange={(event) => onChange(event.target.value)} />
    </label>
  );
}

function AdminPage() {
  const [session, setSession] = useState({ authenticated: false });
  const [credentials, setCredentials] = useState({ username: "admin", password: "" });
  const [orders, setOrders] = useState([]);
  const [selectedOrder, setSelectedOrder] = useState(null);
  const [timeline, setTimeline] = useState([]);
  const [error, setError] = useState("");

  useEffect(() => {
    fetch(`${SHOP_API}/admin/session`, { credentials: "include" })
      .then((response) => (response.ok ? response.json() : { authenticated: false }))
      .then(setSession)
      .catch(() => setSession({ authenticated: false }));
  }, []);

  useEffect(() => {
    if (!session.authenticated) return;
    loadOrders();
  }, [session.authenticated]);

  async function login(event) {
    event.preventDefault();
    setError("");
    const response = await fetch(`${SHOP_API}/admin/login`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(credentials),
    });
    if (!response.ok) {
      setError("Login fehlgeschlagen.");
      return;
    }
    setSession(await response.json());
  }

  async function logout() {
    await fetch(`${SHOP_API}/admin/logout`, { method: "POST", credentials: "include" });
    setSession({ authenticated: false });
    setOrders([]);
    setSelectedOrder(null);
    setTimeline([]);
  }

  async function loadOrders() {
    const response = await fetch(`${SHOP_API}/admin/orders`, { credentials: "include" });
    if (!response.ok) {
      setError("Admin-Bestellungen konnten nicht geladen werden.");
      return;
    }
    setOrders(await response.json());
  }

  async function selectOrder(order) {
    setSelectedOrder(order);
    const response = await fetch(`${SHOP_API}/admin/orders/${order.orderId}/audit`, { credentials: "include" });
    if (response.ok) {
      const audit = await response.json();
      setTimeline(audit.snapshots || []);
    }
  }

  if (!session.authenticated) {
    return (
      <section className="admin-login">
        <form className="login-box" onSubmit={login}>
          <div className="panel-title">
            <Lock size={18} />
            <h2>Admin Login</h2>
          </div>
          <TextInput label="Benutzername" value={credentials.username} onChange={(value) => setCredentials({ ...credentials, username: value })} />
          <TextInput label="Passwort" type="password" value={credentials.password} onChange={(value) => setCredentials({ ...credentials, password: value })} />
          <button className="checkout-button">Einloggen</button>
          {error && <p className="error">{error}</p>}
        </form>
      </section>
    );
  }

  return (
    <section className="admin-page">
      <div className="admin-header">
        <div className="panel-title">
          <ClipboardList size={18} />
          <h2>Bestellmonitor</h2>
        </div>
        <button className="link-button" onClick={logout}>
          <LogOut size={16} />
          Logout
        </button>
      </div>

      <div className="admin-grid">
        <div className="order-list">
          {orders.map((entry) => (
            <button
              className={selectedOrder?.orderId === entry.orderId ? "order-row active" : "order-row"}
              key={entry.orderId}
              onClick={() => selectOrder(entry)}
            >
              <strong>{entry.status}</strong>
              <span>{entry.customer?.firstName} {entry.customer?.lastName}</span>
              <small>{entry.orderId}</small>
            </button>
          ))}
        </div>

        <div className="timeline-panel">
          {selectedOrder ? (
            <>
              <div className="order-grid">
                <span>Order</span><strong>{selectedOrder.orderId}</strong>
                <span>Status</span><strong>{selectedOrder.status}</strong>
                <span>Betrag</span><strong>{selectedOrder.amount} {selectedOrder.currency}</strong>
                <span>Correlation</span><strong>{selectedOrder.correlationId}</strong>
              </div>
              <div className="timeline">
                {timeline.map((event) => (
                  <div className="event" key={event.id}>
                    <span>{event.eventType}</span>
                    <small>{event.service} // {event.statusCode}</small>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <p className="muted">Bestellung auswaehlen, um die Audit-Timeline zu sehen.</p>
          )}
        </div>
      </div>
    </section>
  );
}

createRoot(document.getElementById("root")).render(<App />);
