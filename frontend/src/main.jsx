import React, { useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { Cpu, Minus, Plus, ReceiptText, ShoppingCart, Terminal, Zap } from "lucide-react";
import "./styles.css";

const SHOP_API = import.meta.env.VITE_SHOP_API_URL || "http://localhost:8000";
const AUDIT_API = import.meta.env.VITE_AUDIT_API_URL || "http://localhost:8004";

const products = [
  {
    id: "22222222-2222-2222-2222-222222222222",
    name: "Intel 8086 CPU",
    year: "1978",
    price: 149.9,
    description: "16-Bit-Prozessor, Gold-Ceramic-Look, Herzstueck frueher PC-Geschichte.",
    art: ["00111100", "01111110", "11011011", "11111111", "00100100"],
  },
  {
    id: "33333333-3333-3333-3333-333333333333",
    name: "Commodore 64 SID 6581",
    year: "1982",
    price: 89.9,
    description: "Legendärer Soundchip fuer knisternde Chiptunes und warme Filter.",
    art: ["11100111", "10011001", "11111111", "01011010", "10100101"],
  },
  {
    id: "44444444-4444-4444-4444-444444444444",
    name: "IBM Model M Keyboard",
    year: "1985",
    price: 129,
    description: "Clicky Buckling-Spring-Tastatur, schwer, laut und erfreulich unzerstoerbar.",
    art: ["11111111", "10101010", "11111111", "10101010", "11111111"],
  },
];

const scenarios = [
  { id: "happy_path", label: "Happy Path" },
  { id: "out_of_stock", label: "Out of Stock" },
  { id: "payment_failed", label: "Payment Fail" },
  { id: "invoice_failed", label: "Invoice Fail" },
  { id: "warehouse_commit_failed", label: "Refund Run" },
];

function formatPrice(value) {
  return `${value.toFixed(2)} EUR`;
}

function PixelArt({ rows }) {
  return (
    <div className="pixel-art" aria-hidden="true">
      {rows.flatMap((row, y) =>
        row.split("").map((bit, x) => (
          <span key={`${y}-${x}`} className={bit === "1" ? "on" : ""} />
        )),
      )}
    </div>
  );
}

function App() {
  const [cart, setCart] = useState({});
  const [provider, setProvider] = useState("stripe");
  const [scenario, setScenario] = useState("happy_path");
  const [order, setOrder] = useState(null);
  const [timeline, setTimeline] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const cartItems = useMemo(
    () =>
      products
        .filter((product) => cart[product.id])
        .map((product) => ({ ...product, quantity: cart[product.id] })),
    [cart],
  );
  const total = cartItems.reduce((sum, item) => sum + item.price * item.quantity, 0);

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

  async function refreshOrder(orderId, correlationId) {
    const [orderResponse, auditResponse] = await Promise.all([
      fetch(`${SHOP_API}/orders/${orderId}`),
      fetch(`${AUDIT_API}/audit/orders/${correlationId}`),
    ]);
    if (orderResponse.ok) {
      setOrder(await orderResponse.json());
    }
    if (auditResponse.ok) {
      const audit = await auditResponse.json();
      setTimeline(audit.snapshots || []);
    }
  }

  async function checkout() {
    if (cartItems.length === 0) return;
    setBusy(true);
    setError("");
    setTimeline([]);
    const correlationId = crypto.randomUUID();
    try {
      const response = await fetch(`${SHOP_API}/orders`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Correlation-Id": correlationId,
        },
        body: JSON.stringify({
          customerId: "11111111-1111-1111-1111-111111111111",
          items: cartItems.map((item) => ({ productId: item.id, quantity: item.quantity })),
          payment: { provider, currency: "EUR", scenario },
        }),
      });
      if (!response.ok) {
        throw new Error(`Bestellung fehlgeschlagen: HTTP ${response.status}`);
      }
      const created = await response.json();
      setOrder(created);
      for (let attempt = 0; attempt < 8; attempt += 1) {
        await new Promise((resolve) => setTimeout(resolve, 650));
        await refreshOrder(created.orderId, created.correlationId);
      }
    } catch (caught) {
      setError(caught.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="terminal-shell">
      <section className="topbar">
        <div>
          <p className="kicker">RETRO PARTS TERMINAL // ONLINE</p>
          <h1>Historische Computerteile</h1>
        </div>
        <div className="status-lamp">
          <Terminal size={18} />
          <span>API LINK ACTIVE</span>
        </div>
      </section>

      <section className="layout">
        <div className="products">
          {products.map((product) => (
            <article className="product" key={product.id}>
              <PixelArt rows={product.art} />
              <div className="product-body">
                <div className="product-title">
                  <Cpu size={18} />
                  <h2>{product.name}</h2>
                </div>
                <p className="year">{product.year}</p>
                <p>{product.description}</p>
                <div className="product-actions">
                  <strong>{formatPrice(product.price)}</strong>
                  <div className="stepper">
                    <button onClick={() => changeQuantity(product.id, -1)} aria-label="Menge verringern">
                      <Minus size={16} />
                    </button>
                    <span>{cart[product.id] || 0}</span>
                    <button onClick={() => changeQuantity(product.id, 1)} aria-label="Menge erhoehen">
                      <Plus size={16} />
                    </button>
                  </div>
                </div>
              </div>
            </article>
          ))}
        </div>

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
                  <span>{item.quantity}x {item.name}</span>
                  <strong>{formatPrice(item.quantity * item.price)}</strong>
                </li>
              ))}
            </ul>
          )}

          <div className="provider">
            <span>Payment</span>
            <div className="segments">
              <button className={provider === "stripe" ? "active" : ""} onClick={() => setProvider("stripe")}>
                Stripe
              </button>
              <button className={provider === "paypal" ? "active" : ""} onClick={() => setProvider("paypal")}>
                PayPal
              </button>
            </div>
          </div>

          <div className="provider">
            <span>Szenario</span>
            <div className="scenario-grid">
              {scenarios.map((entry) => (
                <button
                  key={entry.id}
                  className={scenario === entry.id ? "active" : ""}
                  onClick={() => setScenario(entry.id)}
                >
                  <Zap size={14} />
                  {entry.label}
                </button>
              ))}
            </div>
          </div>

          <div className="total">
            <span>Total</span>
            <strong>{formatPrice(total)}</strong>
          </div>
          <button className="checkout-button" disabled={busy || cartItems.length === 0} onClick={checkout}>
            {busy ? "PROCESSING..." : "ORDER EXEC"}
          </button>
          {error && <p className="error">{error}</p>}
        </aside>
      </section>

      <section className="monitor">
        <div className="panel-title">
          <ReceiptText size={18} />
          <h2>Bestellmonitor</h2>
        </div>
        {order ? (
          <div className="order-grid">
            <span>Order</span><strong>{order.orderId}</strong>
            <span>Status</span><strong>{order.status}</strong>
            <span>Betrag</span><strong>{order.amount} {order.currency}</strong>
            <span>Correlation</span><strong>{order.correlationId}</strong>
          </div>
        ) : (
          <p className="muted">Noch keine Bestellung gestartet.</p>
        )}
        <div className="timeline">
          {timeline.map((event) => (
            <div className="event" key={event.id}>
              <span>{event.eventType}</span>
              <small>{event.service}</small>
            </div>
          ))}
        </div>
      </section>
    </main>
  );
}

createRoot(document.getElementById("root")).render(<App />);
