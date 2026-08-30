import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  Boxes,
  Clock3,
  ClipboardList,
  CreditCard,
  Cpu,
  Eye,
  FileText,
  Lock,
  LogOut,
  Minus,
  Plus,
  ReceiptText,
  RefreshCw,
  Search,
  ShieldCheck,
  ShoppingCart,
  Terminal,
  Trash2,
  Truck,
  User,
  Wifi,
  WifiOff,
} from "lucide-react";
import "./styles.css";

const SHOP_API = import.meta.env.VITE_SHOP_API_URL || "http://localhost:8000";
const CART_STORAGE_KEY = "retro-parts-cart";
const PENDING_CHECKOUT_KEY = "retro-parts-pending-checkout";

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
  },
  paypal: {
    paypalEmail: "buyer@example.test",
  },
};

function formatPrice(value, currency = "EUR") {
  return `${Number(value).toFixed(2)} ${currency}`;
}

function formatDateTime(value) {
  if (!value) return "-";
  return new Intl.DateTimeFormat("de-DE", {
    dateStyle: "short",
    timeStyle: "medium",
  }).format(new Date(value));
}

function statusTone(status = "") {
  if (["COMPLETED"].includes(status)) return "success";
  if (["PAYMENT_FAILED", "OUT_OF_STOCK", "REFUND_FAILED", "ROLLBACK_COMPLETED", "INVOICE_FAILED"].includes(status)) return "danger";
  if (["INVOICE_RETRY_PENDING", "REFUND_PENDING", "PAYMENT_ACTION_REQUIRED", "PAYMENT_CONFIRMATION_PENDING"].includes(status)) return "warning";
  return "pending";
}

function shortId(value = "") {
  return value ? `${value.slice(0, 8)}...${value.slice(-6)}` : "-";
}

function availableQuantity(product = {}) {
  return typeof product.availableQuantity === "number" ? product.availableQuantity : null;
}

function stockLabel(product = {}) {
  const available = availableQuantity(product);
  if (available === null) return "Bestand wird geprueft";
  if (available <= 0) return "Ausverkauft";
  if (available === 1) return "Noch 1 Stueck verfuegbar";
  return `Noch ${available} Stueck verfuegbar`;
}

function isOutOfStock(product = {}) {
  const available = availableQuantity(product);
  return available !== null && available <= 0;
}

function isAtStockLimit(product = {}, quantity = 0) {
  const available = availableQuantity(product);
  return available !== null && quantity >= available;
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
  const stopStatuses = new Set([
    "COMPLETED",
    "PAYMENT_FAILED",
    "OUT_OF_STOCK",
    "ROLLBACK_COMPLETED",
    "PAYMENT_ACTION_REQUIRED",
  ]);
  for (let attempt = 0; attempt < 12; attempt += 1) {
    await sleep(500);
    const response = await fetch(`${SHOP_API}/orders/${orderId}`);
    if (!response.ok) continue;
    latest = await response.json();
    if (stopStatuses.has(latest.status)) break;
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

function buildOrderConfirmation(confirmedOrder, { customer, shippingAddress, items, provider, total }) {
  // confirmedOrder.customer/.shippingAddress kommen vom Backend und sind nur
  // gesetzt, wenn der Kaeufer sie auf der echten Stripe-/PayPal-Sandbox-Seite
  // eingegeben hat (siehe billing-service PaymentResult). Erst dann werden
  // sie bevorzugt - sonst bleiben die im eigenen Checkout-Formular erfassten
  // Werte stehen (z.B. im lokalen Stub-Modus ohne Sandbox-Credentials).
  return {
    order: confirmedOrder,
    customer: confirmedOrder.customer || customer,
    shippingAddress: confirmedOrder.shippingAddress || shippingAddress,
    items,
    payment: {
      provider,
      mode: "sandbox",
      status: confirmedOrder.status,
      transactionId: confirmedOrder.transactionId,
    },
    total,
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

  const loadProducts = useCallback(async () => {
    const response = await fetch(`${SHOP_API}/products`, { cache: "no-store" });
    if (!response.ok) throw new Error(`Produktkatalog nicht erreichbar: HTTP ${response.status}`);
    const loadedProducts = await response.json();
    setProducts(loadedProducts);
    return loadedProducts;
  }, []);

  useEffect(() => {
    const onPopState = () => setPath(window.location.pathname);
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  useEffect(() => {
    window.scrollTo({ top: 0, behavior: "smooth" });
  }, [path]);

  useEffect(() => {
    loadProducts().catch((caught) => setError(caught.message));
  }, [loadProducts]);

  useEffect(() => {
    if (path === "/") {
      loadProducts().catch((caught) => setError(caught.message));
    }
  }, [loadProducts, path]);

  useEffect(() => {
    localStorage.setItem(CART_STORAGE_KEY, JSON.stringify(cart));
  }, [cart]);

  useEffect(() => {
    if (products.length === 0) return;
    setCart((current) => {
      let changed = false;
      const next = { ...current };
      products.forEach((product) => {
        const selected = next[product.id];
        const available = availableQuantity(product);
        if (!selected || available === null) return;
        if (available <= 0) {
          delete next[product.id];
          changed = true;
        } else if (selected > available) {
          next[product.id] = available;
          changed = true;
        }
      });
      return changed ? next : current;
    });
  }, [products]);

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
      const product = products.find((entry) => entry.id === productId);
      const maxQuantity = availableQuantity(product) ?? Number.MAX_SAFE_INTEGER;
      const next = Math.max(0, Math.min(maxQuantity, (current[productId] || 0) + delta));
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
    const product = products.find((entry) => entry.id === productId);
    const currentQuantity = cart[productId] || 0;
    if (isAtStockLimit(product, currentQuantity)) {
      setError("Mehr Bestand ist fuer diesen Artikel aktuell nicht verfuegbar.");
      return;
    }
    setError("");
    changeQuantity(productId, 1);
  }

  function removeFromCart(productId) {
    setCart((current) => {
      const copy = { ...current };
      delete copy[productId];
      return copy;
    });
  }

  async function createShopOrder() {
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
          customer,
          shippingAddress,
          items: selectedItems.map((item) => ({ productId: item.id, quantity: item.quantity })),
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
      // Order kommt PENDING zurueck; shop-service prueft erst Lager, bevor es Zahlung beauftragt.
      const confirmedOrder = (await waitForOrderStatus(created.orderId)) || created;

      if (confirmedOrder.status === "PAYMENT_ACTION_REQUIRED" && confirmedOrder.paymentRedirectUrl) {
        sessionStorage.setItem(
          PENDING_CHECKOUT_KEY,
          JSON.stringify({ orderId: confirmedOrder.orderId, customer, shippingAddress, items: selectedItems, provider, total }),
        );
        window.location.assign(confirmedOrder.paymentRedirectUrl);
        return;
      }

      await loadProducts();
      setOrder(confirmedOrder);
      setOrderConfirmation(
        buildOrderConfirmation(confirmedOrder, { customer, shippingAddress, items: selectedItems, provider, total }),
      );
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
    await createShopOrder();
  }

  async function resumePaymentReturn(orderId, approved, pending) {
    setBusy(true);
    setError("");
    try {
      await fetch(`${SHOP_API}/orders/${orderId}/payment-confirmation`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() },
        body: JSON.stringify({ outcome: approved ? "approved" : "cancelled" }),
      });
      if (!approved) {
        setError(`${pending.provider === "paypal" ? "PayPal" : "Stripe"}-Zahlung wurde abgebrochen.`);
        return;
      }
      const confirmedOrder = await waitForOrderStatus(orderId);
      if (!confirmedOrder) {
        throw new Error("Bestellstatus konnte nicht ermittelt werden.");
      }
      await loadProducts();
      setOrder(confirmedOrder);
      setOrderConfirmation(buildOrderConfirmation(confirmedOrder, pending));
      setCart({});
      window.history.replaceState({}, "", "/confirmation");
      setPath("/confirmation");
    } catch (caught) {
      setError(caught.message);
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const returnProvider = params.has("paypal") ? "paypal" : params.has("stripe") ? "stripe" : null;
    const returnState = returnProvider && params.get(returnProvider);
    const orderId = params.get("orderId");
    if (path !== "/checkout" || !returnProvider || !orderId) return;
    const stored = sessionStorage.getItem(PENDING_CHECKOUT_KEY);
    const pending = stored ? JSON.parse(stored) : null;
    if (!pending || pending.orderId !== orderId) return;
    sessionStorage.removeItem(PENDING_CHECKOUT_KEY);
    window.history.replaceState({}, "", "/checkout");
    resumePaymentReturn(orderId, returnState === "approved", pending);
  }, [path]);

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
  const productsPerPage = 8;
  const [page, setPage] = useState(1);
  const [searchTerm, setSearchTerm] = useState("");
  const normalizedSearch = searchTerm.trim().toLowerCase();
  const filteredProducts = useMemo(() => {
    if (!normalizedSearch) return products;
    return products.filter((product) =>
      [product.name, product.year, product.description]
        .filter(Boolean)
        .join(" ")
        .toLowerCase()
        .includes(normalizedSearch),
    );
  }, [normalizedSearch, products]);
  const pageCount = Math.max(1, Math.ceil(filteredProducts.length / productsPerPage));
  const visibleProducts = filteredProducts.slice((page - 1) * productsPerPage, page * productsPerPage);

  function goToPage(nextPage) {
    setPage(Math.min(pageCount, Math.max(1, nextPage)));
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  useEffect(() => {
    setPage(1);
  }, [products.length, normalizedSearch]);

  useEffect(() => {
    if (page > pageCount) {
      setPage(pageCount);
    }
  }, [page, pageCount]);

  return (
    <section className="shop-layout">
      <div className="catalog">
        <div className="catalog-toolbar">
          <label className="catalog-search">
            <Search size={16} />
            <input
              value={searchTerm}
              onChange={(event) => setSearchTerm(event.target.value)}
              placeholder="Artikel suchen"
            />
          </label>
          <span className="catalog-count">
            {filteredProducts.length} von {products.length} Artikeln
          </span>
        </div>
        {visibleProducts.length === 0 && (
          <p className="empty-catalog">Keine passenden historischen Computerteile gefunden.</p>
        )}
        {visibleProducts.map((product) => (
          <article className={isOutOfStock(product) ? "product out-of-stock" : "product"} key={product.id}>
            <button className="image-button" onClick={() => navigate(`/products/${product.id}`)}>
              <img src={product.imageUrl} alt={product.imageAlt} />
            </button>
            <div className="product-body">
              <button className="product-title product-title-button" onClick={() => navigate(`/products/${product.id}`)}>
                <Cpu size={18} />
                <h2>{product.name}</h2>
              </button>
              <p className="year">{product.year}</p>
              <p className={isOutOfStock(product) ? "stock-line danger" : "stock-line"}>
                <Boxes size={15} />
                {stockLabel(product)}
              </p>
              <p>{product.description}</p>
              <div className="product-actions">
                <strong>{formatPrice(product.price, product.currency)}</strong>
                <div className="stepper">
                  <button onClick={() => changeQuantity(product.id, -1)} aria-label="Menge verringern">
                    <Minus size={16} />
                  </button>
                  <span>{cartItems.find((item) => item.id === product.id)?.quantity || 0}</span>
                  <button
                    disabled={isAtStockLimit(product, cartItems.find((item) => item.id === product.id)?.quantity || 0)}
                    onClick={() => changeQuantity(product.id, 1)}
                    aria-label="Menge erhoehen"
                  >
                    <Plus size={16} />
                  </button>
                </div>
              </div>
              <div className="product-buttons">
                <button className="secondary-button" onClick={() => navigate(`/products/${product.id}`)}>
                  <Eye size={16} />
                  Details
                </button>
                <button className="add-button" disabled={isOutOfStock(product)} onClick={() => addToCart(product.id)}>
                  <ShoppingCart size={16} />
                  {isOutOfStock(product) ? "Ausverkauft" : "In den Warenkorb"}
                </button>
              </div>
            </div>
          </article>
        ))}
        {filteredProducts.length > productsPerPage && (
          <div className="pagination">
            <button className="secondary-button" disabled={page === 1} onClick={() => goToPage(page - 1)}>
              <ArrowLeft size={16} />
              Zurueck
            </button>
            <div className="page-jump">
              {Array.from({ length: pageCount }, (_, index) => index + 1).map((pageNumber) => (
                <button
                  className={pageNumber === page ? "page-number active" : "page-number"}
                  key={pageNumber}
                  onClick={() => goToPage(pageNumber)}
                  aria-label={`Seite ${pageNumber} aufrufen`}
                >
                  {pageNumber}
                </button>
              ))}
            </div>
            <span>
              Seite {page} von {pageCount}
            </span>
            <button className="secondary-button" disabled={page === pageCount} onClick={() => goToPage(page + 1)}>
              Weiter
            </button>
          </div>
        )}
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
          <p className={isOutOfStock(product) ? "stock-line danger" : "stock-line"}>
            <Boxes size={16} />
            {stockLabel(product)}
          </p>
          <p>{product.description}</p>
          <dl className="spec-grid">
            <div>
              <dt>Preis</dt>
              <dd>{formatPrice(product.price, product.currency)}</dd>
            </div>
            <div>
              <dt>Bestand</dt>
              <dd>{stockLabel(product)}</dd>
            </div>
          </dl>
          <div className="detail-actions">
            <div className="stepper">
              <button onClick={() => changeQuantity(product.id, -1)} aria-label="Menge verringern">
                <Minus size={16} />
              </button>
              <span>{quantity}</span>
              <button disabled={isAtStockLimit(product, quantity)} onClick={() => changeQuantity(product.id, 1)} aria-label="Menge erhoehen">
                <Plus size={16} />
              </button>
            </div>
            <button className="add-button" disabled={isOutOfStock(product)} onClick={() => addToCart(product.id)}>
              <ShoppingCart size={16} />
              {isOutOfStock(product) ? "Ausverkauft" : "In den Warenkorb"}
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
                <small className={isOutOfStock(item) ? "stock-inline danger" : "stock-inline"}>{stockLabel(item)}</small>
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
                    <small className={isOutOfStock(item) ? "stock-line danger" : "stock-line"}>
                      <Boxes size={14} />
                      {stockLabel(item)}
                    </small>
                    <span>{formatPrice(item.price, item.currency)} pro Stueck</span>
                  </div>
                  <div className="cart-editor-controls">
                    <div className="stepper">
                      <button onClick={() => changeQuantity(item.id, -1)} aria-label="Menge verringern">
                        <Minus size={16} />
                      </button>
                      <span>{item.quantity}</span>
                      <button disabled={isAtStockLimit(item, item.quantity)} onClick={() => changeQuantity(item.id, 1)} aria-label="Menge erhoehen">
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
          <PaymentFields provider={provider} />
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
                    <small className={isOutOfStock(item) ? "stock-inline danger" : "stock-inline"}>{stockLabel(item)}</small>
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
  const succeeded = payment.status === "COMPLETED";

  return (
    <section className="confirmation-page">
      <section className="confirmation-panel">
        <div className="confirmation-hero">
          <div className="panel-title">
            <ReceiptText size={22} />
            <h2>Bestellung bestaetigt</h2>
          </div>
          <p className={succeeded ? "success" : "muted"}>
            {succeeded
              ? "Zahlung erfolgreich. Deine Bestellung wurde angelegt."
              : `Bestellung wird verarbeitet. Aktueller Status: ${payment.status}.`}
          </p>
        </div>

        <div className="confirmation-grid">
          <div className="confirmation-block">
            <span>Bestellnummer</span>
            <strong>{order.orderId}</strong>
          </div>
          <div className="confirmation-block">
            <span>Status</span>
            <strong>{payment.status}</strong>
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

function PaymentFields({ provider }) {
  if (provider === "paypal") {
    return (
      <div className="payment-box">
        <div className="panel-title">
          <CreditCard size={16} />
          <strong>PayPal</strong>
        </div>
        <p className="muted">
          Nach dem Absenden prueft das System zuerst den Lagerbestand und beauftragt danach die Zahlung ueber PayPal.
          Du siehst das Ergebnis auf der Bestellbestaetigung.
        </p>
      </div>
    );
  }
  return (
    <div className="payment-box">
      <div className="panel-title">
        <CreditCard size={16} />
        <strong>Stripe</strong>
      </div>
      <p className="muted">
        Nach dem Absenden prueft das System zuerst den Lagerbestand und beauftragt danach die Zahlung ueber Stripe.
        Du siehst das Ergebnis auf der Bestellbestaetigung.
      </p>
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

function TextArea({ label, onChange, value }) {
  return (
    <label className="field">
      <span>{label}</span>
      <textarea required value={value || ""} onChange={(event) => onChange(event.target.value)} />
    </label>
  );
}

const emptyProductDraft = {
  name: "",
  year: "",
  description: "",
  price: "0.00",
  currency: "EUR",
  imageUrl: "",
  imageAlt: "",
  imageSource: "",
  imageLicense: "",
  imageCredit: "",
  quantityOnHand: 0,
  location: "RETRO-A1",
};

function AdminPage() {
  const [session, setSession] = useState({ authenticated: false });
  const [credentials, setCredentials] = useState({ username: "admin", password: "" });
  const [orders, setOrders] = useState([]);
  const [products, setProducts] = useState([]);
  const [selectedOrder, setSelectedOrder] = useState(null);
  const [timeline, setTimeline] = useState([]);
  const [error, setError] = useState("");
  const [productError, setProductError] = useState("");
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [loading, setLoading] = useState(false);
  const [savingProductId, setSavingProductId] = useState("");
  const [activeAdminView, setActiveAdminView] = useState("orders");
  const [liveStatus, setLiveStatus] = useState("connecting");

  // Ref statt State: der SSE-useEffect unten soll die Verbindung NICHT bei
  // jedem Klick auf eine andere Bestellung neu aufbauen, braucht aber trotzdem
  // Zugriff auf die aktuell ausgewaehlte Bestellung, um bei einem passenden
  // Live-Event gezielt deren Timeline nachzuladen (siehe flushPendingReload
  // im SSE-useEffect weiter unten).
  const selectedOrderIdRef = useRef(null);
  useEffect(() => {
    selectedOrderIdRef.current = selectedOrder?.orderId ?? null;
  }, [selectedOrder]);

  useEffect(() => {
    fetch(`${SHOP_API}/admin/session`, { credentials: "include" })
      .then((response) => (response.ok ? response.json() : { authenticated: false }))
      .then(setSession)
      .catch(() => setSession({ authenticated: false }));
  }, []);

  useEffect(() => {
    if (!session.authenticated) return;
    loadOrders();
    loadAdminProducts();
  }, [session.authenticated]);

  // Server-Sent Events aktualisieren Bestellliste und geoeffnete Details,
  // ohne dass der Browser pollen muss.
  useEffect(() => {
    if (!session.authenticated) return undefined;

    setLiveStatus("connecting");
    const source = new EventSource(`${SHOP_API}/admin/orders/events`, { withCredentials: true });

    // Mehrere Saga-Events fuer dieselbe Bestellung koennen kurz hintereinander
    // eintreffen (z.B. Reservierung, Zahlung, Rechnung, Warehouse-Commit beim
    // Happy Path). Statt bei jedem einzelnen Event sofort neu zu laden, wird
    // kurz gesammelt und dann in einem Rutsch aktualisiert.
    let debounceTimer = null;
    const pendingOrderIds = new Set();

    function flushPendingReload() {
      debounceTimer = null;
      loadOrders();
      if (selectedOrderIdRef.current && pendingOrderIds.has(selectedOrderIdRef.current)) {
        loadTimeline(selectedOrderIdRef.current);
      }
      pendingOrderIds.clear();
    }

    source.onopen = () => setLiveStatus("live");
    source.onerror = () => {
      // EventSource baut die Verbindung nach einem Fehler automatisch
      // wieder auf - hier wird nur die Anzeige aktualisiert, kein manueller
      // Reconnect noetig.
      setLiveStatus("offline");
    };
    source.onmessage = (event) => {
      if (!event.data) return;
      try {
        const payload = JSON.parse(event.data);
        if (payload.orderId) {
          pendingOrderIds.add(payload.orderId);
        }
      } catch {
        return;
      }
      if (debounceTimer) clearTimeout(debounceTimer);
      debounceTimer = setTimeout(flushPendingReload, 400);
    };

    return () => {
      if (debounceTimer) clearTimeout(debounceTimer);
      source.close();
    };
  }, [session.authenticated]);

  const statusOptions = useMemo(() => ["all", ...Array.from(new Set(orders.map((entry) => entry.status))).sort()], [orders]);
  const filteredOrders = useMemo(() => {
    const normalizedSearch = search.trim().toLowerCase();
    // dateFrom/dateTo kommen aus <input type="date"> als "YYYY-MM-DD" (lokale
    // Zeit) - dateFrom wird auf 00:00:00 des Tages verankert, dateTo auf
    // 23:59:59.999, damit der gewaehlte Endtag noch vollstaendig eingeschlossen
    // ist statt bei Mitternacht abgeschnitten zu werden.
    const fromTime = dateFrom ? new Date(`${dateFrom}T00:00:00`).getTime() : null;
    const toTime = dateTo ? new Date(`${dateTo}T23:59:59.999`).getTime() : null;
    return orders.filter((entry) => {
      const matchesStatus = statusFilter === "all" || entry.status === statusFilter;
      const createdAtTime = entry.createdAt ? new Date(entry.createdAt).getTime() : null;
      const matchesFrom = fromTime === null || (createdAtTime !== null && createdAtTime >= fromTime);
      const matchesTo = toTime === null || (createdAtTime !== null && createdAtTime <= toTime);
      const haystack = [
        entry.orderId,
        entry.correlationId,
        entry.status,
        entry.customer?.firstName,
        entry.customer?.lastName,
        entry.customer?.email,
        entry.transactionId,
        entry.invoiceId,
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return matchesStatus && matchesFrom && matchesTo && (!normalizedSearch || haystack.includes(normalizedSearch));
    });
  }, [orders, search, statusFilter, dateFrom, dateTo]);
  const dashboardStats = useMemo(() => {
    const completed = orders.filter((entry) => entry.status === "COMPLETED");
    const open = orders.filter((entry) => !["COMPLETED", "PAYMENT_FAILED", "OUT_OF_STOCK", "ROLLBACK_COMPLETED"].includes(entry.status));
    const failed = orders.filter((entry) => ["PAYMENT_FAILED", "OUT_OF_STOCK", "REFUND_FAILED", "ROLLBACK_COMPLETED"].includes(entry.status));
    const revenue = completed.reduce((sum, entry) => sum + Number(entry.amount || 0), 0);
    return {
      total: orders.length,
      completed: completed.length,
      open: open.length,
      failed: failed.length,
      revenue,
    };
  }, [orders]);

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
    setLoading(true);
    const response = await fetch(`${SHOP_API}/admin/orders`, { credentials: "include" });
    setLoading(false);
    if (!response.ok) {
      setError("Admin-Bestellungen konnten nicht geladen werden.");
      return;
    }
    const loadedOrders = await response.json();
    setOrders(loadedOrders);
    if (!selectedOrder && loadedOrders.length > 0) {
      await selectOrder(loadedOrders[0]);
    }
  }

  async function loadAdminProducts() {
    const response = await fetch(`${SHOP_API}/admin/products`, { credentials: "include", cache: "no-store" });
    if (!response.ok) {
      setProductError("Artikel konnten nicht geladen werden.");
      return;
    }
    setProducts(await response.json());
  }

  async function uploadProductImage(file) {
    setProductError("");
    const body = new FormData();
    body.append("file", file);
    const response = await fetch(`${SHOP_API}/admin/product-images`, {
      method: "POST",
      credentials: "include",
      body,
    });
    if (!response.ok) {
      throw new Error(`Bild konnte nicht hochgeladen werden: HTTP ${response.status}`);
    }
    return response.json();
  }

  async function createProduct(productDraft) {
    setSavingProductId("new");
    setProductError("");
    try {
      const response = await fetch(`${SHOP_API}/admin/products`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(productDraft),
      });
      if (!response.ok) {
        throw new Error(`Artikel konnte nicht angelegt werden: HTTP ${response.status}`);
      }
      await loadAdminProducts();
      setActiveAdminView("products");
      return true;
    } catch (caught) {
      setProductError(caught.message);
      return false;
    } finally {
      setSavingProductId("");
    }
  }

  async function saveProduct(productId, productDraft) {
    setSavingProductId(productId);
    setProductError("");
    try {
      const productResponse = await fetch(`${SHOP_API}/admin/products/${productId}`, {
        method: "PUT",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(productDraft),
      });
      if (!productResponse.ok) {
        throw new Error(`Artikel konnte nicht gespeichert werden: HTTP ${productResponse.status}`);
      }
      await loadAdminProducts();
    } catch (caught) {
      setProductError(caught.message);
    } finally {
      setSavingProductId("");
    }
  }

  async function saveStock(productId, stockDraft) {
    setSavingProductId(productId);
    setProductError("");
    try {
      const stockResponse = await fetch(`${SHOP_API}/admin/products/${productId}/stock`, {
        method: "PATCH",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(stockDraft),
      });
      if (!stockResponse.ok) {
        throw new Error(`Warehouse-Menge konnte nicht gespeichert werden: HTTP ${stockResponse.status}`);
      }
      await loadAdminProducts();
    } catch (caught) {
      setProductError(caught.message);
    } finally {
      setSavingProductId("");
    }
  }

  async function loadTimeline(orderId) {
    // Getrennt von selectOrder(), damit ein Live-Update (siehe SSE-useEffect
    // oben) die Timeline der gerade geoeffneten Bestellung nachladen kann,
    // ohne setSelectedOrder()/setTimeline([]) erneut auszuloesen - das wuerde
    // sonst bei jedem Live-Event kurz einen leeren Timeline-Zustand aufblitzen
    // lassen.
    const response = await fetch(`${SHOP_API}/admin/orders/${orderId}/audit`, { credentials: "include" });
    if (response.ok) {
      const audit = await response.json();
      setTimeline(audit.snapshots || []);
    } else {
      setError("Audit-Timeline konnte nicht geladen werden.");
    }
  }

  async function selectOrder(order) {
    setSelectedOrder(order);
    setTimeline([]);
    await loadTimeline(order.orderId);
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
          <Activity size={18} />
          <h2>Admin Dashboard</h2>
        </div>
        <div className="admin-actions">
          <span className={`live-indicator ${liveStatus}`} title="Echtzeit-Aktualisierung (Server-Sent Events)">
            {liveStatus === "live" ? <Wifi size={16} /> : <WifiOff size={16} />}
            {liveStatus === "live" ? "Live" : liveStatus === "connecting" ? "Verbinde..." : "Offline"}
          </span>
          <button className="link-button" onClick={loadOrders} disabled={loading}>
            <RefreshCw size={16} />
            Aktualisieren
          </button>
          <button className="link-button" onClick={logout}>
            <LogOut size={16} />
            Logout
          </button>
        </div>
      </div>

      {error && <p className="error">{error}</p>}

      <div className="admin-metrics">
        <Metric icon={<ClipboardList size={18} />} label="Bestellungen" value={dashboardStats.total} />
        <Metric icon={<ShieldCheck size={18} />} label="Abgeschlossen" value={dashboardStats.completed} tone="success" />
        <Metric icon={<Clock3 size={18} />} label="In Bearbeitung" value={dashboardStats.open} tone="warning" />
        <Metric icon={<AlertTriangle size={18} />} label="Auffaellig" value={dashboardStats.failed} tone="danger" />
        <Metric icon={<CreditCard size={18} />} label="Umsatz" value={formatPrice(dashboardStats.revenue)} />
      </div>

      <div className="admin-tabs">
        <button className={activeAdminView === "orders" ? "tab-button active" : "tab-button"} onClick={() => setActiveAdminView("orders")}>
          <ClipboardList size={16} />
          Bestellungen
        </button>
        <button className={activeAdminView === "products" ? "tab-button active" : "tab-button"} onClick={() => setActiveAdminView("products")}>
          <Cpu size={16} />
          Artikel
        </button>
        <button className={activeAdminView === "warehouse" ? "tab-button active" : "tab-button"} onClick={() => setActiveAdminView("warehouse")}>
          <Boxes size={16} />
          Warehouse
        </button>
      </div>

      {activeAdminView === "products" && (
        <ProductAdminPanel
          error={productError}
          onCreate={createProduct}
          onImageUpload={uploadProductImage}
          onReload={loadAdminProducts}
          products={products}
          savingProductId={savingProductId}
          onSave={saveProduct}
        />
      )}

      {activeAdminView === "warehouse" && (
        <WarehouseAdminPanel
          error={productError}
          onReload={loadAdminProducts}
          onSave={saveStock}
          products={products}
          savingProductId={savingProductId}
        />
      )}

      {activeAdminView === "orders" && (
        <>
          <div className="admin-toolbar">
            <label className="admin-search">
              <Search size={16} />
              <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Order, Kunde, Transaktion suchen" />
            </label>
            <label className="admin-filter">
              <span>Status</span>
              <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
                {statusOptions.map((option) => (
                  <option key={option} value={option}>
                    {option === "all" ? "Alle" : option}
                  </option>
                ))}
              </select>
            </label>
            <label className="admin-filter">
              <span>Von</span>
              <input type="date" value={dateFrom} onChange={(event) => setDateFrom(event.target.value)} max={dateTo || undefined} />
            </label>
            <label className="admin-filter">
              <span>Bis</span>
              <input type="date" value={dateTo} onChange={(event) => setDateTo(event.target.value)} min={dateFrom || undefined} />
            </label>
            {(dateFrom || dateTo) && (
              <button
                type="button"
                className="link-button"
                onClick={() => {
                  setDateFrom("");
                  setDateTo("");
                }}
              >
                Zeitraum zuruecksetzen
              </button>
            )}
          </div>

          <div className="admin-grid">
            <aside className="order-list">
              {filteredOrders.map((entry) => (
                <button
                  className={selectedOrder?.orderId === entry.orderId ? "order-row active" : "order-row"}
                  key={entry.orderId}
                  onClick={() => selectOrder(entry)}
                >
                  <span className={`status-pill ${statusTone(entry.status)}`}>{entry.status}</span>
                  <strong>{entry.customer?.firstName} {entry.customer?.lastName}</strong>
                  <small>{formatDateTime(entry.createdAt)} // {formatPrice(entry.amount, entry.currency)}</small>
                  <small>{shortId(entry.orderId)}</small>
                </button>
              ))}
              {filteredOrders.length === 0 && <p className="muted">Keine Bestellung passt zum Filter.</p>}
            </aside>

            <OrderAdminDetail order={selectedOrder} timeline={timeline} />
          </div>
        </>
      )}
    </section>
  );
}

function Metric({ icon, label, tone = "", value }) {
  return (
    <div className={`metric ${tone}`}>
      {icon}
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function ProductAdminPanel({ error, onCreate, onImageUpload, onReload, onSave, products, savingProductId }) {
  const adminProductsPerPage = 5;
  const [page, setPage] = useState(1);
  const [searchTerm, setSearchTerm] = useState("");
  const normalizedSearch = searchTerm.trim().toLowerCase();
  const filteredProducts = useMemo(() => {
    if (!normalizedSearch) return products;
    return products.filter((product) =>
      [product.name, product.year, product.description, product.location, product.id]
        .filter(Boolean)
        .join(" ")
        .toLowerCase()
        .includes(normalizedSearch),
    );
  }, [normalizedSearch, products]);
  const pageCount = Math.max(1, Math.ceil(filteredProducts.length / adminProductsPerPage));
  const visibleProducts = filteredProducts.slice((page - 1) * adminProductsPerPage, page * adminProductsPerPage);

  function goToPage(nextPage) {
    setPage(Math.min(pageCount, Math.max(1, nextPage)));
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  useEffect(() => {
    setPage(1);
  }, [products.length, normalizedSearch]);

  useEffect(() => {
    if (page > pageCount) {
      setPage(pageCount);
    }
  }, [page, pageCount]);

  return (
    <section className="product-admin-panel">
      <div className="panel-title">
        <Cpu size={18} />
        <h2>Artikel</h2>
      </div>
      <ProductCreateForm onCreate={onCreate} onImageUpload={onImageUpload} saving={savingProductId === "new"} />
      {error && <p className="error">{error}</p>}
      <div className="product-admin-actions">
        <button className="link-button" type="button" onClick={onReload}>
          <RefreshCw size={16} />
          Artikel neu laden
        </button>
      </div>
      <div className="admin-list-toolbar">
        <label className="admin-search">
          <Search size={16} />
          <input
            value={searchTerm}
            onChange={(event) => setSearchTerm(event.target.value)}
            placeholder="Artikel suchen"
          />
        </label>
        <span className="catalog-count">
          {filteredProducts.length} von {products.length} Artikeln
        </span>
      </div>
      {filteredProducts.length === 0 ? (
        <p className="muted">Keine passenden Artikel gefunden.</p>
      ) : (
        <div className="product-admin-list">
          {visibleProducts.map((product) => (
            <ProductAdminRow
              key={product.id}
              product={product}
              saving={savingProductId === product.id}
              onImageUpload={onImageUpload}
              onSave={onSave}
            />
          ))}
        </div>
      )}
      {filteredProducts.length > adminProductsPerPage && (
        <AdminPagination page={page} pageCount={pageCount} onPageChange={goToPage} />
      )}
    </section>
  );
}

function ProductCreateForm({ onCreate, onImageUpload, saving }) {
  const [draft, setDraft] = useState(emptyProductDraft);
  const [uploading, setUploading] = useState(false);
  async function handleImageUpload(file) {
    setUploading(true);
    try {
      const uploaded = await onImageUpload(file);
      setDraft({
        ...draft,
        imageUrl: uploaded.imageUrl,
        imageAlt: draft.imageAlt || draft.name || "Produktbild",
        imageSource: "Admin upload",
        imageLicense: "Uploaded project asset",
        imageCredit: "Shop admin",
      });
    } finally {
      setUploading(false);
    }
  }
  return (
    <form
      className="product-create-form"
      onSubmit={async (event) => {
        event.preventDefault();
        const created = await onCreate({ ...draft, quantityOnHand: Number(draft.quantityOnHand) });
        if (created) {
          setDraft(emptyProductDraft);
        }
      }}
    >
      <div className="product-admin-heading">
        <strong>Neuen Artikel anlegen</strong>
      </div>
      <div className="product-admin-grid">
        <TextInput label="Name" value={draft.name} onChange={(value) => setDraft({ ...draft, name: value })} />
        <TextInput label="Jahr" value={draft.year} onChange={(value) => setDraft({ ...draft, year: value })} />
        <TextInput label="Preis" type="number" value={draft.price} onChange={(value) => setDraft({ ...draft, price: value })} />
        <TextInput label="Waehrung" value={draft.currency} onChange={(value) => setDraft({ ...draft, currency: value })} />
        <TextInput label="Bild-URL" value={draft.imageUrl} onChange={(value) => setDraft({ ...draft, imageUrl: value })} />
        <ImageUploadField label="Bild hochladen" onUpload={handleImageUpload} uploading={uploading} />
        <TextInput label="Alt-Text" value={draft.imageAlt} onChange={(value) => setDraft({ ...draft, imageAlt: value })} />
        <TextInput
          label="Startbestand"
          type="number"
          value={draft.quantityOnHand}
          onChange={(value) => setDraft({ ...draft, quantityOnHand: Number(value) })}
        />
        <TextInput label="Lagerort" value={draft.location} onChange={(value) => setDraft({ ...draft, location: value })} />
      </div>
      <TextArea label="Beschreibung" value={draft.description} onChange={(value) => setDraft({ ...draft, description: value })} />
      <div className="product-admin-actions">
        <button className="add-button" type="submit" disabled={saving}>
          <Plus size={16} />
          {saving ? "Legt an..." : "Artikel anlegen"}
        </button>
      </div>
    </form>
  );
}

function ProductAdminRow({ onImageUpload, onSave, product, saving }) {
  const [draft, setDraft] = useState(() => productDraftFromProduct(product));
  const [uploading, setUploading] = useState(false);

  useEffect(() => {
    setDraft(productDraftFromProduct(product));
  }, [product]);

  async function handleImageUpload(file) {
    setUploading(true);
    try {
      const uploaded = await onImageUpload(file);
      setDraft({
        ...draft,
        imageUrl: uploaded.imageUrl,
        imageAlt: draft.imageAlt || product.name || "Produktbild",
        imageSource: "Admin upload",
        imageLicense: "Uploaded project asset",
        imageCredit: "Shop admin",
      });
    } finally {
      setUploading(false);
    }
  }

  return (
    <article className="product-admin-row">
      <img src={product.imageUrl} alt={product.imageAlt} />
      <div className="product-admin-form">
        <div className="product-admin-heading">
          <strong>{product.name}</strong>
          <span className={isOutOfStock(product) ? "stock-inline danger" : "stock-inline"}>
            {stockLabel(product)} // reserviert: {product.reservedQuantity ?? "-"}
          </span>
        </div>
        <div className="product-admin-grid">
          <TextInput label="Name" value={draft.name} onChange={(value) => setDraft({ ...draft, name: value })} />
          <TextInput label="Jahr" value={draft.year} onChange={(value) => setDraft({ ...draft, year: value })} />
          <TextInput label="Preis" type="number" value={draft.price} onChange={(value) => setDraft({ ...draft, price: value })} />
          <TextInput label="Waehrung" value={draft.currency} onChange={(value) => setDraft({ ...draft, currency: value })} />
          <TextInput label="Bild-URL" value={draft.imageUrl} onChange={(value) => setDraft({ ...draft, imageUrl: value })} />
          <ImageUploadField label="Bild hochladen" onUpload={handleImageUpload} uploading={uploading} />
          <TextInput label="Alt-Text" value={draft.imageAlt} onChange={(value) => setDraft({ ...draft, imageAlt: value })} />
        </div>
        <TextArea label="Beschreibung" value={draft.description} onChange={(value) => setDraft({ ...draft, description: value })} />
        <div className="product-admin-actions">
          <button
            className="secondary-button"
            type="button"
            onClick={() => {
              setDraft(productDraftFromProduct(product));
            }}
          >
            Zuruecksetzen
          </button>
          <button className="add-button" type="button" disabled={saving} onClick={() => onSave(product.id, draft)}>
            {saving ? "Speichert..." : "Artikel speichern"}
          </button>
        </div>
      </div>
    </article>
  );
}

function ImageUploadField({ label, onUpload, uploading }) {
  const [error, setError] = useState("");
  return (
    <label className="field image-upload-field">
      <span>{label}</span>
      <input
        accept="image/png,image/jpeg,image/webp"
        disabled={uploading}
        type="file"
        onChange={async (event) => {
          const file = event.target.files?.[0];
          if (!file) return;
          setError("");
          try {
            await onUpload(file);
          } catch (caught) {
            setError(caught.message);
          } finally {
            event.target.value = "";
          }
        }}
      />
      {error && <small className="error">{error}</small>}
    </label>
  );
}

function productDraftFromProduct(product) {
  return {
    name: product.name || "",
    year: product.year || "",
    description: product.description || "",
    price: String(product.price || "0.00"),
    currency: product.currency || "EUR",
    imageUrl: product.imageUrl || "",
    imageAlt: product.imageAlt || product.name || "",
    imageSource: product.imageSource || "",
    imageLicense: product.imageLicense || "",
    imageCredit: product.imageCredit || "",
  };
}

function WarehouseAdminPanel({ error, onReload, onSave, products, savingProductId }) {
  const warehouseProductsPerPage = 8;
  const [page, setPage] = useState(1);
  const [searchTerm, setSearchTerm] = useState("");
  const normalizedSearch = searchTerm.trim().toLowerCase();
  const filteredProducts = useMemo(() => {
    if (!normalizedSearch) return products;
    return products.filter((product) =>
      [
        product.name,
        product.year,
        product.location,
        product.stockStatus,
        product.id,
        product.quantityOnHand,
        product.availableQuantity,
        product.reservedQuantity,
      ]
        .filter((value) => value !== null && value !== undefined)
        .join(" ")
        .toLowerCase()
        .includes(normalizedSearch),
    );
  }, [normalizedSearch, products]);
  const pageCount = Math.max(1, Math.ceil(filteredProducts.length / warehouseProductsPerPage));
  const visibleProducts = filteredProducts.slice((page - 1) * warehouseProductsPerPage, page * warehouseProductsPerPage);

  function goToPage(nextPage) {
    setPage(Math.min(pageCount, Math.max(1, nextPage)));
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  useEffect(() => {
    setPage(1);
  }, [products.length, normalizedSearch]);

  useEffect(() => {
    if (page > pageCount) {
      setPage(pageCount);
    }
  }, [page, pageCount]);

  return (
    <section className="product-admin-panel">
      <div className="panel-title">
        <Boxes size={18} />
        <h2>Warehouse</h2>
      </div>
      {error && <p className="error">{error}</p>}
      <div className="product-admin-actions">
        <button className="link-button" type="button" onClick={onReload}>
          <RefreshCw size={16} />
          Bestände neu laden
        </button>
      </div>
      <div className="admin-list-toolbar">
        <label className="admin-search">
          <Search size={16} />
          <input
            value={searchTerm}
            onChange={(event) => setSearchTerm(event.target.value)}
            placeholder="Bestand suchen"
          />
        </label>
        <span className="catalog-count">
          {filteredProducts.length} von {products.length} Artikeln
        </span>
      </div>
      {filteredProducts.length === 0 ? (
        <p className="muted">Keine passenden Bestände gefunden.</p>
      ) : (
        <div className="warehouse-list">
          {visibleProducts.map((product) => (
            <WarehouseAdminRow
              key={product.id}
              onSave={onSave}
              product={product}
              saving={savingProductId === product.id}
            />
          ))}
        </div>
      )}
      {filteredProducts.length > warehouseProductsPerPage && (
        <AdminPagination page={page} pageCount={pageCount} onPageChange={goToPage} />
      )}
    </section>
  );
}

function AdminPagination({ page, pageCount, onPageChange }) {
  return (
    <div className="pagination admin-pagination">
      <button className="secondary-button" disabled={page === 1} onClick={() => onPageChange(page - 1)}>
        <ArrowLeft size={16} />
        Zurueck
      </button>
      <div className="page-jump">
        {Array.from({ length: pageCount }, (_, index) => index + 1).map((pageNumber) => (
          <button
            className={pageNumber === page ? "page-number active" : "page-number"}
            key={pageNumber}
            onClick={() => onPageChange(pageNumber)}
            aria-label={`Seite ${pageNumber} aufrufen`}
          >
            {pageNumber}
          </button>
        ))}
      </div>
      <span>
        Seite {page} von {pageCount}
      </span>
      <button className="secondary-button" disabled={page === pageCount} onClick={() => onPageChange(page + 1)}>
        Weiter
      </button>
    </div>
  );
}

function WarehouseAdminRow({ onSave, product, saving }) {
  const [stockDraft, setStockDraft] = useState(() => stockDraftFromProduct(product));

  useEffect(() => {
    setStockDraft(stockDraftFromProduct(product));
  }, [product]);

  return (
    <article className="warehouse-row">
      <div>
        <strong>{product.name}</strong>
        <small>{product.year} // {shortId(product.id)}</small>
      </div>
      <span className={isOutOfStock(product) ? "stock-inline danger" : "stock-inline"}>
        {stockLabel(product)} // reserviert: {product.reservedQuantity ?? "-"}
      </span>
      <TextInput
        label="Menge"
        type="number"
        value={stockDraft.quantityOnHand}
        onChange={(value) => setStockDraft({ ...stockDraft, quantityOnHand: Number(value) })}
      />
      <TextInput label="Lagerort" value={stockDraft.location} onChange={(value) => setStockDraft({ ...stockDraft, location: value })} />
      <button className="add-button" type="button" disabled={saving} onClick={() => onSave(product.id, stockDraft)}>
        {saving ? "Speichert..." : "Bestand speichern"}
      </button>
    </article>
  );
}

function stockDraftFromProduct(product) {
  return {
    quantityOnHand: product.quantityOnHand ?? 0,
    location: product.location || "",
  };
}

function OrderAdminDetail({ order, timeline }) {
  if (!order) {
    return (
      <div className="timeline-panel admin-empty">
        <ClipboardList size={26} />
        <p className="muted">Bestellung auswaehlen, um Kundendaten, Zahlungsstatus und Audit-Timeline zu sehen.</p>
      </div>
    );
  }
  const items = order.items || [];
  const payment = order.payment || {};
  return (
    <div className="admin-detail">
      <section className="timeline-panel">
        <div className="detail-header">
          <div>
            <span className={`status-pill ${statusTone(order.status)}`}>{order.status}</span>
            <h2>{shortId(order.orderId)}</h2>
          </div>
          <strong>{formatPrice(order.amount, order.currency)}</strong>
        </div>
        <div className="order-grid">
          <span>Order-ID</span><strong>{order.orderId}</strong>
          <span>Correlation-ID</span><strong>{order.correlationId}</strong>
          <span>Erstellt</span><strong>{formatDateTime(order.createdAt)}</strong>
          <span>Aktualisiert</span><strong>{formatDateTime(order.updatedAt)}</strong>
          <span>Payment</span><strong>{payment.provider || "-"} // {payment.mode || "-"}</strong>
          <span>Transaktion</span><strong>{order.transactionId || "-"}</strong>
          <span>Rechnung</span><strong>{order.invoiceId || "-"} {order.invoiceStatus ? `// ${order.invoiceStatus}` : ""}</strong>
          <span>Warehouse</span><strong>{order.warehouseCommitStatus || "-"}</strong>
        </div>
      </section>

      <section className="admin-detail-grid">
        <InfoPanel icon={<User size={17} />} title="Kunde">
          <p>{order.customer?.firstName} {order.customer?.lastName}</p>
          <p>{order.customer?.email || "-"}</p>
          <p>{order.customer?.phone || "-"}</p>
        </InfoPanel>
        <InfoPanel icon={<Truck size={17} />} title="Lieferadresse">
          <p>{formatAddress(order.shippingAddress)}</p>
        </InfoPanel>
        <InfoPanel icon={<Boxes size={17} />} title="Artikel">
          <ul className="admin-items">
            {items.map((item) => (
              <li key={item.productId}>
                <span>{item.quantity}x {item.name}</span>
                <strong>{formatPrice(item.lineTotal || Number(item.unitPrice || 0) * Number(item.quantity || 0), order.currency)}</strong>
              </li>
            ))}
          </ul>
        </InfoPanel>
        <InfoPanel icon={<FileText size={17} />} title="Audit">
          <p>{timeline.length} Snapshots</p>
          <p>Letztes Event: {timeline.at(-1)?.eventType || "-"}</p>
        </InfoPanel>
      </section>

      <section className="timeline-panel">
        <div className="panel-title">
          <ReceiptText size={17} />
          <h2>Audit Timeline</h2>
        </div>
        <div className="timeline">
          {timeline.map((event, index) => (
            <div className={`event ${event.statusCode?.toLowerCase() || ""}`} key={event.id}>
              <div className="event-index">{String(index + 1).padStart(2, "0")}</div>
              <span>{event.eventType}</span>
              <small>{event.service} // {event.statusCode}</small>
              <small>{formatDateTime(event.timestamp)}</small>
              <details>
                <summary>Payload</summary>
                <pre>{JSON.stringify(event.payload || {}, null, 2)}</pre>
              </details>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

function InfoPanel({ children, icon, title }) {
  return (
    <div className="info-panel">
      <div className="panel-title">
        {icon}
        <strong>{title}</strong>
      </div>
      {children}
    </div>
  );
}

function formatAddress(address = {}) {
  return [
    `${address.street || "-"} ${address.houseNumber || ""}`.trim(),
    `${address.postalCode || ""} ${address.city || ""}`.trim(),
    address.country || "",
  ]
    .filter(Boolean)
    .join(", ");
}

createRoot(document.getElementById("root")).render(<App />);
