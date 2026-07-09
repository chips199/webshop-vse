import React, { useEffect, useMemo, useState } from "react";
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
  PackageCheck,
  Plus,
  ReceiptText,
  ShieldCheck,
  ShoppingCart,
  Terminal,
} from "lucide-react";
import "./styles.css";

const SHOP_API = import.meta.env.VITE_SHOP_API_URL || "http://localhost:8000";

const emptyCustomer = {
  firstName: "",
  lastName: "",
  email: "",
  phone: "",
};

const emptyAddress = {
  street: "",
  houseNumber: "",
  postalCode: "",
  city: "",
  country: "Deutschland",
};

const emptyPaymentDetails = {
  stripe: {
    cardholder: "",
    testPaymentMethod: "pm_card_visa",
  },
  paypal: {
    paypalEmail: "",
  },
};

function formatPrice(value, currency = "EUR") {
  return `${Number(value).toFixed(2)} ${currency}`;
}

function navigate(path) {
  window.history.pushState({}, "", path);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

function App() {
  const [path, setPath] = useState(window.location.pathname);
  const [products, setProducts] = useState([]);
  const [cart, setCart] = useState({});
  const [provider, setProvider] = useState("stripe");
  const [paymentDetails, setPaymentDetails] = useState(emptyPaymentDetails);
  const [customer, setCustomer] = useState(emptyCustomer);
  const [shippingAddress, setShippingAddress] = useState(emptyAddress);
  const [order, setOrder] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

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

  async function submitOrder(event) {
    event.preventDefault();
    if (cartItems.length === 0) return;
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
          customer,
          shippingAddress,
          items: cartItems.map((item) => ({ productId: item.id, quantity: item.quantity })),
          payment: {
            provider,
            currency: "EUR",
            mode: "sandbox",
            ...(provider === "stripe" ? paymentDetails.stripe : paymentDetails.paypal),
          },
        }),
      });
      if (!response.ok) {
        throw new Error(`Bestellung fehlgeschlagen: HTTP ${response.status}`);
      }
      const created = await response.json();
      setOrder(created);
      setCart({});
    } catch (caught) {
      setError(caught.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="terminal-shell">
      <Header path={path} />
      {path.startsWith("/admin") ? (
        <AdminPage />
      ) : path === "/checkout" ? (
        <CheckoutPage
          cartItems={cartItems}
          customer={customer}
          error={error}
          order={order}
          paymentDetails={paymentDetails}
          provider={provider}
          setCustomer={setCustomer}
          setPaymentDetails={setPaymentDetails}
          setProvider={setProvider}
          setShippingAddress={setShippingAddress}
          shippingAddress={shippingAddress}
          submitOrder={submitOrder}
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
        <button className={path === "/checkout" ? "active" : ""} onClick={() => navigate("/checkout")}>
          <ShoppingCart size={16} />
          Checkout
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
            <button className="checkout-button compact" disabled={quantity === 0} onClick={() => navigate("/checkout")}>
              Zur Kasse
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
      <button className="checkout-button" disabled={cartItems.length === 0} onClick={() => navigate("/checkout")}>
        Zur Kasse
      </button>
    </aside>
  );
}

function CheckoutPage({
  busy,
  cartItems,
  customer,
  error,
  order,
  paymentDetails,
  provider,
  setCustomer,
  setPaymentDetails,
  setProvider,
  setShippingAddress,
  shippingAddress,
  submitOrder,
  total,
}) {
  return (
    <section className="checkout-page">
      <button className="link-button" onClick={() => navigate("/")}>
        <ArrowLeft size={16} />
        Weiter einkaufen
      </button>
      <form className="checkout-form" onSubmit={submitOrder}>
        <section className="form-section">
          <div className="panel-title">
            <ReceiptText size={18} />
            <h2>Kontakt</h2>
          </div>
          <div className="form-grid">
            <TextInput label="Vorname" value={customer.firstName} onChange={(value) => setCustomer({ ...customer, firstName: value })} />
            <TextInput label="Nachname" value={customer.lastName} onChange={(value) => setCustomer({ ...customer, lastName: value })} />
            <TextInput label="E-Mail" type="email" value={customer.email} onChange={(value) => setCustomer({ ...customer, email: value })} />
            <TextInput label="Telefon" value={customer.phone} required={false} onChange={(value) => setCustomer({ ...customer, phone: value })} />
          </div>
        </section>

        <section className="form-section">
          <div className="panel-title">
            <PackageCheck size={18} />
            <h2>Lieferadresse</h2>
          </div>
          <div className="form-grid">
            <TextInput label="Strasse" value={shippingAddress.street} onChange={(value) => setShippingAddress({ ...shippingAddress, street: value })} />
            <TextInput label="Hausnummer" value={shippingAddress.houseNumber} onChange={(value) => setShippingAddress({ ...shippingAddress, houseNumber: value })} />
            <TextInput label="PLZ" value={shippingAddress.postalCode} onChange={(value) => setShippingAddress({ ...shippingAddress, postalCode: value })} />
            <TextInput label="Ort" value={shippingAddress.city} onChange={(value) => setShippingAddress({ ...shippingAddress, city: value })} />
            <TextInput label="Land" value={shippingAddress.country} onChange={(value) => setShippingAddress({ ...shippingAddress, country: value })} />
          </div>
        </section>

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
            paymentDetails={paymentDetails}
            provider={provider}
            setPaymentDetails={setPaymentDetails}
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
            {busy ? "Bestellung wird verarbeitet..." : "Kostenpflichtig bestellen"}
          </button>
          {order && (
            <p className="success">
              Bestellung angenommen: {order.orderId}. Den Bearbeitungsstatus sieht das Admin-Team im Monitor.
            </p>
          )}
          {error && <p className="error">{error}</p>}
        </aside>
      </form>
    </section>
  );
}

function PaymentFields({ paymentDetails, provider, setPaymentDetails }) {
  if (provider === "paypal") {
    return (
      <div className="payment-box">
        <div className="panel-title">
          <CreditCard size={16} />
          <strong>PayPal Sandbox</strong>
        </div>
        <TextInput
          label="PayPal Sandbox E-Mail"
          type="email"
          value={paymentDetails.paypal.paypalEmail}
          onChange={(value) =>
            setPaymentDetails({
              ...paymentDetails,
              paypal: { ...paymentDetails.paypal, paypalEmail: value },
            })
          }
        />
        <p className="muted">Die eigentliche Zahlung laeuft ueber den Billing-Service gegen PayPal Sandbox Credentials.</p>
      </div>
    );
  }
  return (
    <div className="payment-box">
      <div className="panel-title">
        <CreditCard size={16} />
        <strong>Stripe Sandbox</strong>
      </div>
      <TextInput
        label="Karteninhaber"
        value={paymentDetails.stripe.cardholder}
        onChange={(value) =>
          setPaymentDetails({
            ...paymentDetails,
            stripe: { ...paymentDetails.stripe, cardholder: value },
          })
        }
      />
      <label className="field">
        <span>Test-Zahlungsmethode</span>
        <select
          value={paymentDetails.stripe.testPaymentMethod}
          onChange={(event) =>
            setPaymentDetails({
              ...paymentDetails,
              stripe: { ...paymentDetails.stripe, testPaymentMethod: event.target.value },
            })
          }
        >
          <option value="pm_card_visa">Visa Erfolg</option>
          <option value="pm_card_chargeDeclined">Karte abgelehnt</option>
          <option value="pm_card_authenticationRequired">3D Secure erforderlich</option>
        </select>
      </label>
      <p className="muted">Es werden keine echten Kartennummern im Shop gespeichert.</p>
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
