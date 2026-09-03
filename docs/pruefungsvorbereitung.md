# Prüfungsvorbereitung: Webshop-VSE

Diese Übersicht ist auf die mündliche Fragerunde und den Schwerpunkt
`shop-service` zugeschnitten. Sie beschreibt den Stand der Implementierung und
nennt bewusst auch Grenzen, die man in einer kritischen Reflexion offen
ansprechen sollte.

## 1. Das Projekt in 60 Sekunden erklären

Der Retro-Parts-Webshop ist eine Microservice-Anwendung mit fünf
FastAPI-Services und einem React-Frontend. Der `shop-service` ist der fachliche
Einstiegspunkt: Er verwaltet Produkt- und Bestelldaten, nimmt Bestellungen mit
`POST /orders` an und koordiniert den verteilten Bestellprozess. Die
bestandsverändernden Schritte laufen asynchron über einen langlebigen
RabbitMQ-Topic-Exchange. Jeder Service besitzt seine Daten selbst; technisch
laufen die getrennten PostgreSQL-Datenbanken in einem gemeinsamen Container.

Die verteilte Transaktion wird als Saga umgesetzt. Es gibt kein globales
ACID-Commit über alle Datenbanken. Stattdessen werden lokale Transaktionen
durch Commands und Events verbunden. Bei einem späteren Fehler werden bereits
ausgeführte Schritte fachlich kompensiert, beispielsweise durch Freigabe einer
Reservierung oder Refund einer Zahlung. Der `audit-service` hört mit dem
Routing-Key `#` alle Nachrichten mit und speichert sie append-only als
Audit-Snapshots. `correlationId` verbindet alle Nachrichten, Logs und Snapshots
einer Bestellung.

Zentrale Qualitätsziele sind:

- Wartbarkeit durch getrennte Verantwortlichkeiten und Schichten
- Erweiterbarkeit durch Payment-Fassade und Adapter-Registry
- Nachvollziehbarkeit durch Audit-Snapshots und strukturierte JSON-Logs
- Resilienz durch Saga-Kompensation, Retries und Circuit Breaker
- Reproduzierbarkeit durch Docker Compose, OpenAPI und automatisierte Tests

## 2. Systemübersicht

| Baustein | Verantwortung | Kommunikation | Eigene Daten |
| --- | --- | --- | --- |
| React-Frontend | Katalog, Warenkorb, Checkout, Tracking, Adminbereich | HTTP zum Shop-Service | keine |
| Shop-Service, Port 8000 | Produktkatalog, Orders, Saga-Koordination, Admin-API, SSE | extern HTTP; intern überwiegend RabbitMQ; HTTP-Lesezugriffe zu Warehouse/Audit | `shop_service` |
| Warehouse-Service, Port 8001 | Bestand, Reservierung, Commit, Cancel | RabbitMQ für Saga; HTTP für Bestand/Admin | `warehouse_service` |
| Billing-Service, Port 8002 | Payment-Fassade, Stripe/PayPal, Webhook, Refund | RabbitMQ; HTTP nur für technische Payment-Endpunkte/Webhook und externe Provider | bewusst zustandslos |
| Invoice-Service, Port 8003 | ein PDF-Erstellungsversuch je Command, Metadaten und Download | RabbitMQ; HTTP für Abruf/Download | `invoice_service` plus PDF-Volume |
| Audit-Service, Port 8004 | generischer, unveränderlicher Ereignisspeicher und Timeline | konsumiert RabbitMQ `#`; HTTP-Lese-API | `audit_service` |
| RabbitMQ | Topic-Exchange `webshop.events`, Routing und Entkopplung | AMQP | langlebige Queues/Nachrichten |
| Loki/Promtail/Grafana | zentrale Sammlung und Auswertung der JSON-Logs | Docker-Logs/HTTP | Loki-Volume |

### Wer darf auf welche Daten zugreifen?

Jeder Service greift nur auf seine eigene Datenbank zu. Der Shop-Service liest
Lagerbestand und Audit-Timeline über die APIs der Eigentümer. Das verhindert
eine gemeinsame Datenbank als versteckte Kopplung. Dass alle vier Datenbanken
im selben PostgreSQL-Container liegen, ist eine lokale Deployment-Optimierung,
keine gemeinsame fachliche Datenhoheit.

### Warum ist Billing zustandslos?

Der Billing-Service hält keinen eigenen Zahlungsstatus vor. Den aktuellen
Status liest die Payment-Fassade bei Bedarf beim Anbieter. Dauerhafter
Bestellstatus und `transactionId` liegen beim Shop-Service. Vorteil sind wenig
duplizierter Zustand und ein einfacher Service; Nachteile sind die Abhängigkeit
vom Provider beim Statusabruf und weniger lokale Historie.

## 3. Shop-Service im Detail

Der Shop-Service ist API-Einstiegspunkt, Aggregate-Eigentümer der Bestellung
und faktischer Saga-Koordinator.

### Schichten und wichtige Dateien

| Datei | Aufgabe |
| --- | --- |
| `src/main.py` | Composition Root: App, Middleware, CORS, Startup/Shutdown, Consumer-Thread |
| `src/routes.py` | REST-Endpunkte, `POST /orders`, Payment-Confirmation und Admin-API |
| `src/schemas.py` | Pydantic-Modelle und Eingabevalidierung |
| `src/service.py` | Router-Hilfen, Serialisierung, Idempotency-Key und Adminprüfung |
| `src/saga.py` | Reaktion auf Saga-Events, Folgeschritte, Kompensation, Invoice-Retry |
| `src/database.py` | eigene PostgreSQL-Tabellen und atomare Statusänderungen |
| `src/messaging.py` | gemeinsames Envelope, RabbitMQ Publish/Consume, ACK/NACK, Reconnect |
| `src/clients.py` | synchrone HTTP-Aufrufe zu Warehouse und Audit |
| `src/resilience/circuit_breaker.py` | Zustände `CLOSED`, `OPEN`, `HALF_OPEN` |
| `src/realtime.py` | In-Process-Pub/Sub für SSE-Updates im Admin-Dashboard |

### Öffentliche und administrative Endpunkte

- `GET /products`: Shop-Katalog aus Shop-DB, angereichert mit Live-Bestand
  aus `GET warehouse-service/stock`; bei Warehouse-Ausfall bleibt der Katalog
  nutzbar und der Bestand wird `UNKNOWN`.
- `POST /orders`: validiert, berechnet Preise serverseitig, erzeugt
  `orderId`/`correlationId`, persistiert `PENDING`, publiziert den Saga-Start
  und antwortet sofort mit `202 Accepted`.
- `GET /orders/{orderId}`: aktueller Bestell- und Saga-Status; das
  Kunden-Frontend pollt diesen Endpunkt.
- `POST /orders/{orderId}/payment-confirmation`: verarbeitet Rückkehr von
  Stripe/PayPal als `approved` oder `cancelled`.
- `/admin/...`: Login/Logout, Orders, Audit-Timeline, SSE, Produkte, Bilder
  und Lagerbestand.

### Warum `202 Accepted` statt `201 Created`?

Die Order-Ressource ist zwar angelegt, der fachliche Bestellprozess aber noch
nicht abgeschlossen. `202` bedeutet: angenommen und zur asynchronen
Verarbeitung eingeplant. Der Endstatus kommt später über Polling oder im
Adminbereich über SSE. Die initiale Antwort zeigt deshalb immer `PENDING`,
selbst wenn die schnelle Saga intern schon weitergelaufen sein sollte.

### Warum wird der Preis serverseitig ermittelt?

Der Client sendet nur `productId` und Menge. Der Shop-Service liest Name,
Einzelpreis und Währung aus seinem Katalog, berechnet mit `Decimal` den Betrag
und speichert die angereicherten Positionen als Bestell-Snapshot. Dadurch kann
ein Client keinen niedrigeren Preis einschleusen und eine spätere Rechnung
bleibt trotz Katalogänderung nachvollziehbar.

### Verbindungen des Shop-Service

| Gegenstelle | Richtung | Technik | Zweck |
| --- | --- | --- | --- |
| Frontend | Frontend → Shop | HTTP/JSON | Produkte, Orders, Admin, SSE |
| Warehouse | Shop ↔ Warehouse | RabbitMQ | Reserve, Commit, Cancel und Ergebnis-Events |
| Warehouse | Shop ↔ Warehouse | HTTP | Katalogbestand sowie Admin-Create/Patch |
| Billing | Shop ↔ Billing | RabbitMQ | Charge, Payment-Confirmation, Refund und Resultate |
| Invoice | Shop ↔ Invoice | RabbitMQ | PDF anfordern, Erfolg/Fehler empfangen |
| Audit | Shop → Audit | HTTP | Timeline für das Admin-Dashboard lesen |
| RabbitMQ | bidirektional | Topic-Exchange | Saga-Commands und -Events |
| PostgreSQL | Shop → eigene DB | SQL | Orders, Produkte, Adminbenutzer und Sessions |

## 4. RabbitMQ und Nachrichtenmodell

Alle Services verwenden den langlebigen Topic-Exchange `webshop.events`. Der
Routing-Key entspricht dem Nachrichtentyp, zum Beispiel
`warehouse.reserve.requested`. Jeder fachliche Service besitzt eine eigene
durable Queue mit expliziten Bindings. Der Audit-Service bindet seine Queue an
`#` und empfängt dadurch jede Nachricht.

Ein Envelope enthält:

```json
{
  "messageId": "UUID der einzelnen Nachricht",
  "correlationId": "UUID des gesamten Vorgangs",
  "type": "billing.payment.succeeded",
  "sourceService": "billing-service",
  "timestamp": "UTC/ISO-8601",
  "payload": {},
  "previousEventId": "ID der auslösenden Nachricht oder null"
}
```

Die Begriffe müssen sitzen:

- Command: Aufforderung, etwas zu tun, meist `*.requested`.
- Event: Feststellung, dass etwas geschehen ist, meist `*.succeeded`,
  `*.failed`, `*.created` oder `*.completed`.
- `messageId`: identifiziert genau diese Nachricht.
- `correlationId`: identifiziert den gesamten Bestellvorgang serviceübergreifend.
- `previousEventId`: bildet die kausale Beziehung zum Auslöser ab.
- Routing-Key: entscheidet, welche Queues eine Nachricht erhalten.

Die Queues und Nachrichten sind durable/persistent. Consumer bestätigen erst
nach erfolgreicher Handler-Ausführung mit ACK. Bei Handlerfehlern wird mit
NACK und `requeue=false` verworfen, um Endlosschleifen durch Poison Messages zu
vermeiden. Ein produktionsreifer Ausbau würde dafür eine Dead-Letter-Queue
verwenden.

## 5. Happy Path auswendig können

```text
Client
  → POST /orders
Shop
  → Order PENDING speichern
  → order.created
  → warehouse.reserve.requested
  → 202 Accepted
Warehouse
  → Bestand atomar prüfen und reservieren
  → warehouse.reservation.succeeded
Shop
  → RESERVED, dann PAYMENT_PENDING
  → billing.payment.requested
Billing
  → PaymentFacade.charge(...)
  → billing.payment.succeeded
Shop
  → PAYMENT_SUCCEEDED + transactionId
  ├→ invoice.create.requested
  └→ warehouse.commit.requested
Invoice
  → PDF + Metadaten speichern
  → invoice.created
Warehouse
  → quantity_on_hand und reserved_quantity reduzieren
  → warehouse.commit.succeeded
Shop
  → nur wenn Payment + Invoice + Commit fertig: COMPLETED
  → order.completed
Audit
  → speichert jede Nachricht der Kette
```

Invoice-Erzeugung und Warehouse-Commit laufen nach erfolgreicher Zahlung
logisch parallel. Deshalb darf der Shop-Service nicht beim zuerst eintreffenden
Erfolg voreilig abschließen. `complete_order_if_ready()` führt ein atomares
`UPDATE ... WHERE` aus und verlangt:

- `transaction_id IS NOT NULL`
- `invoice_status = 'CREATED'`
- `warehouse_commit_status = 'SUCCEEDED'`
- aktueller Status ist noch nicht `COMPLETED`

Das macht den Abschluss unabhängig von der Reihenfolge der Events und
verhindert ein zweites `order.completed` bei normaler doppelter Prüfung.

## 6. Fehlerszenarien und Kompensation

### Lager nicht ausreichend

```text
reserve.requested → reservation.failed → Order OUT_OF_STOCK
```

Danach werden weder Billing noch Invoice aufgerufen. Es wurde noch kein
externer Effekt erzeugt, deshalb ist keine weitere Kompensation nötig.

### Zahlung abgelehnt oder Timeout

```text
Reservierung erfolgreich
→ billing.payment.failed
→ Order PAYMENT_FAILED
→ warehouse.cancel.requested
→ reserved_quantity wird freigegeben
→ warehouse.cancel.succeeded
```

`quantity_on_hand` bleibt unverändert, weil die Ware nur reserviert und noch
nicht ausgebucht war.

### Invoice-Erstellung schlägt fehl

```text
invoice.create.requested (1)
→ invoice.failed (1)
→ INVOICE_RETRY_PENDING
→ invoice.retry.scheduled (2)
→ invoice.create.requested (2)
→ invoice.failed (2)
→ invoice.retry.scheduled (3)
→ invoice.create.requested (3)
→ invoice.failed (3)
→ Circuit CLOSED → OPEN
→ Order INVOICE_FAILED
```

Zahlung und Lager werden bewusst nicht zurückgerollt. Die Rechnung ist ein
nachgelagerter, wiederholbarer Vorgang. Der Invoice-Service macht pro Command
genau einen Versuch; der Shop-Service besitzt Retry-Zähler und Circuit-Breaker-
Entscheidung. Das vermeidet zwei konkurrierende Retry-Mechanismen.

### Warehouse-Commit schlägt nach Zahlung fehl

```text
PAYMENT_SUCCEEDED
→ warehouse.commit.failed
→ Order REFUND_PENDING
→ billing.refund.requested
→ PaymentFacade.refund(...)
→ billing.refund.succeeded
→ Order ROLLBACK_COMPLETED
→ order.rollback.completed
```

Schlägt auch der Refund fehl, endet die Order in `REFUND_FAILED` und benötigt
manuelle Bearbeitung. Das ist ehrlicher als fälschlich einen erfolgreichen
Rollback zu behaupten.

### Externe Zahlung per Redirect oder Webhook

- Mit Sandbox-Credentials liefern Stripe und PayPal zunächst `PENDING` plus
  Redirect-URL. Der Shop setzt `PAYMENT_ACTION_REQUIRED`. Nach Rückkehr des
  Browsers publiziert der Shop `billing.payment.confirm.requested`; Billing
  ruft `getStatus()` auf (bei PayPal inklusive Capture).
- Ohne Credentials ist Stripe ein sofort erfolgreicher lokaler Stub.
- Ohne Credentials ist PayPal ein asynchroner Stub: `PENDING` ohne Redirect,
  später ruft ein Timer `POST /webhooks/payment-stub` im Billing-Service auf.
  Erst dieser Webhook erzeugt das endgültige Payment-Event.

## 7. Wichtige Bestellstatus

| Status | Bedeutung |
| --- | --- |
| `PENDING` | angenommen, Reservierung noch offen |
| `RESERVED` | Lagerreservierung erfolgreich |
| `PAYMENT_PENDING` | Zahlungsauftrag wurde gestartet |
| `PAYMENT_ACTION_REQUIRED` | Browser muss externen Checkout durchführen |
| `PAYMENT_CONFIRMATION_PENDING` | Rückkehr wurde atomar beansprucht; Statusprüfung läuft |
| `PAYMENT_SUCCEEDED` | bezahlt; Invoice und Commit laufen noch |
| `INVOICE_RETRY_PENDING` | weitere Rechnungserstellung ist geplant |
| `INVOICE_FAILED` | Rechnung nach allen Versuchen endgültig fehlgeschlagen |
| `REFUND_PENDING` | Lager-Commit fehlgeschlagen; Erstattung läuft |
| `REFUND_FAILED` | Erstattung technisch/fachlich fehlgeschlagen |
| `OUT_OF_STOCK` | keine vollständige Reservierung möglich |
| `PAYMENT_FAILED` | Zahlung abgelehnt/fehlgeschlagen, Reservierung wird gelöst |
| `ROLLBACK_COMPLETED` | Zahlung nach spätem Fehler erfolgreich erstattet |
| `COMPLETED` | Zahlung, Rechnung und Lager-Commit erfolgreich |

## 8. Payment-Fassade und Entwurfsmuster

### Fassade, Adapter und Strategie auseinanderhalten

- Die Fassade bietet Billing eine kleine, einheitliche API:
  `charge`, `refund`, `get_status`. Sie kapselt Logging, Fehlerübersetzung und
  Retry-Regeln.
- Adapter übersetzen die einheitlichen Domänentypen zu den unterschiedlichen
  Stripe-/PayPal-APIs und zurück.
- Die Auswahl des konkreten Adapters per Konfiguration ist Strategy-Verhalten.
- `PaymentResult` und `PaymentStatus` sind anbieterunabhängige Domänentypen;
  Stripe-/PayPal-Payloads verlassen die Adaptergrenze nicht.

### Open/Closed Principle

`PaymentAdapter.__init_subclass__` registriert jede Unterklasse über deren
`provider_name`. Für einen dritten Anbieter braucht man eine neue Adapterklasse,
Konfiguration und Tests. Fassade und Billing-Handler bleiben unverändert. Das
System ist für Erweiterung offen, aber für Änderungen am Kern geschlossen.

### Warum wird `charge()` nicht automatisch wiederholt?

Nach einem Timeout ist unbekannt, ob der Provider bereits belastet hat. Ein
blindes Retry könnte doppelt abbuchen. Deshalb führt die Fassade `charge()` nur
einmal aus. Technische Fehler bei `get_status()` und `refund()` werden dagegen
mit konfigurierbarem linearem Backoff wiederholt. Für sichere Charge-Retries
wären providerseitige Idempotency-Keys nötig.

## 9. Saga: Orchestrierung oder Choreografie?

Die präziseste Antwort lautet: eine eventgetriebene, orchestrierte Saga mit
choreografischen Elementen.

- Choreografisch ist, dass Services über Events entkoppelt reagieren und der
  Audit-Service völlig unabhängig mithört.
- Orchestriert ist, dass der Shop-Service die nächsten Commands auswählt,
  zentralen Orderstatus hält und Kompensationen anstößt.

Im Code und in Teilen der Doku steht „Choreografie“. In einer Prüfung sollte
man nicht auf diesem Wort beharren: Der Shop-Service ist faktisch der
Prozessmanager/Saga-Koordinator. Vorteil ist ein gut auffindbarer Ablauf;
Nachteil ist die höhere fachliche Verantwortung und Kopplung dieses Services.

### Warum keine verteilte ACID-Transaktion oder Two-Phase Commit?

Jeder Service besitzt eine autonome Datenbank und externe Provider nehmen an
einem Datenbank-Commit nicht teil. 2PC koppelt Verfügbarkeit, hält Sperren und
passt schlecht zu asynchronen und externen Systemen. Eine Saga erreicht
eventuelle Konsistenz durch lokale Transaktionen plus fachliche Kompensation.
Kompensation ist kein technisches Zurückspulen: Ein Refund ist ein neuer,
sichtbarer Geschäftsprozess.

## 10. Warehouse-Konsistenz

Der Warehouse-Service trennt:

- `quantity_on_hand`: physischer Gesamtbestand
- `reserved_quantity`: für laufende Orders reservierte Menge
- `available_quantity = quantity_on_hand - reserved_quantity`

Reservieren, Commit und Cancel laufen jeweils in lokalen PostgreSQL-
Transaktionen. Zeilensperren mit `FOR UPDATE` verhindern konkurrierendes
Überreservieren sowie gleichzeitiges Commit und Cancel derselben Reservierung.

- Reserve: nur `reserved_quantity` erhöhen.
- Commit: `quantity_on_hand` und `reserved_quantity` um dieselbe Menge senken.
- Cancel: nur `reserved_quantity` senken.

Die Reservierung besitzt die Zustände `RESERVED`, `COMMITTED` und `CANCELLED`.
Wiederholtes Commit einer bereits committeten bzw. Cancel einer bereits
stornierten Reservierung liefert erfolgreich zurück und ist damit teilweise
idempotent.

## 11. Audit und Event Sourcing Light

Der Audit-Service kennt keine Bestell- oder Payment-Regeln. Er konsumiert jede
Nachricht und bildet den Nachrichtentyp generisch ab, zum Beispiel
`billing.payment.failed` → `BILLING_PAYMENT_FAILED`. Gespeichert werden
`correlationId`, `eventType`, `service`, `timestamp`, `payload`,
`previousEventId`, `actor` und `statusCode`.

Warum „Event Sourcing Light“ und kein vollständiges Event Sourcing?

- Die Nachrichten werden append-only als Historie gespeichert.
- Der operative Orderzustand wird aber nicht ausschließlich durch Replay der
  Events aufgebaut, sondern separat in `shop_orders` aktualisiert.
- Das Audit-Log ist also Nachvollziehbarkeitsquelle, nicht alleinige Source of
  Truth für den aktuellen Zustand.

Die ursprüngliche `messageId` wird zur Snapshot-ID. `ON CONFLICT DO NOTHING`
macht doppelte Zustellung derselben Audit-Nachricht idempotent. Es gibt nur
INSERT und SELECT im Audit-Code. Für eine produktive, stärkere Garantie würde
man UPDATE/DELETE zusätzlich über eingeschränkte DB-Rechte oder Trigger
verbieten.

`previousEventId` bildet in der Implementierung den kausalen Auslöser ab. Weil
Invoice und Warehouse nach Payment parallel laufen, entsteht eher ein
gerichteter Ereignisgraph als eine einzige lineare Kette. Die chronologische
Anzeige sortiert deshalb zusätzlich nach `timestamp` und `created_at`.

## 12. Circuit Breaker und Retry

Der Circuit Breaker schützt Invoice-Aufrufe:

- `CLOSED`: Aufrufe passieren; aufeinanderfolgende Fehler werden gezählt.
- Nach drei Fehlern: `OPEN`; weitere Aufrufe werden sofort abgewiesen.
- Nach 30 Sekunden: Der nächste Aufruf wechselt zu `HALF_OPEN` und dient als
  Testaufruf.
- Erfolg im Half-Open: `CLOSED` und Fehlerzähler zurücksetzen.
- Fehler im Half-Open: sofort wieder `OPEN`.

Zustandswechsel werden als `invoice.circuit.state.changed` publiziert und damit
auditiert. Retry beantwortet „wann erneut versuchen?“, Circuit Breaker
beantwortet „darf der Downstream aktuell überhaupt aufgerufen werden?“.

## 13. Idempotenz und Nebenläufigkeit

`POST /orders` unterstützt `Idempotency-Key`:

1. Der normalisierte Request-Body wird stabil gehasht.
2. Key und Hash werden mit der Order gespeichert.
3. Gleicher Key + gleicher Body liefert dieselbe Order und publiziert nichts
   erneut.
4. Gleicher Key + anderer Body liefert `409 Conflict`.
5. Ein partieller Unique Index schützt auch gegen parallele Erstrequests.
6. Eine `UniqueViolation` wird abgefangen und die Gewinner-Order nachgeladen.

Auch die externe Payment-Confirmation wird atomar mit
`UPDATE ... WHERE status='PAYMENT_ACTION_REQUIRED' RETURNING` beansprucht. So
kann nur ein paralleler Request das Confirm-/Capture-Command publizieren.

Wichtig: Request-Idempotenz bedeutet noch nicht, dass alle Message-Consumer
vollständig „exactly once“ arbeiten. RabbitMQ liefert praktisch mindestens
einmal; für robuste Produktionsverarbeitung bräuchte jeder Consumer eine
Inbox/Deduplizierung oder idempotente fachliche Operationen.

## 14. API, Fehler, Logging und Admin

### API und Fehler

- Ressourcenorientierte URLs statt `/createOrder`.
- Pydantic validiert Bodies; fachliche Fehler nutzen passende Statuscodes.
- Fehler sind RFC-7807-artige `application/problem+json`-Antworten mit
  `type`, `title`, `status`, `detail`, `instance`, `correlationId`.
- Jeder Service besitzt eine statische OpenAPI-3.0-Datei und FastAPI stellt
  zur Laufzeit `/docs` und `/openapi.json` bereit.

### Correlation ID

- HTTP: Middleware liest `X-Correlation-Id` oder erzeugt eine UUID, legt sie
  in `request.state` und setzt sie auch in die Response.
- Interne HTTP-Clients geben den Header weiter.
- RabbitMQ: `correlationId` liegt im standardisierten Envelope.
- Logging/Audit: dasselbe Feld erlaubt serviceübergreifendes Suchen.

Eine Correlation ID ist keine Security-ID und kein Ersatz für eine eindeutige
`messageId`. Sie darf bei allen Nachrichten eines Vorgangs gleich sein.

### Logging und zentrale Beobachtbarkeit

Alle Backend-Services schreiben einzeiliges JSON auf stdout und in täglich
rotierende Dateien mit 14 Backups. Felder sind mindestens Service, Level,
UTC-Timestamp, Message, Correlation ID und Context. Promtail liest die
Container-Logs, extrahiert Labels und sendet sie an Loki; Grafana zeigt
Bestellanzahl, Fehlerrate und Zahlungsresultate.

Audit und Logging sind nicht dasselbe:

- Log: technische Diagnose, darf rotiert und gelöscht werden.
- Audit-Snapshot: dauerhafte fachliche Nachvollziehbarkeit, append-only.

### Admin-Sicherheit

- Passwort als PBKDF2-HMAC-SHA256 mit individuellem Salt.
- Vergleich mit `hmac.compare_digest`.
- zufälliges Sessiontoken; in der DB liegt nur dessen SHA-256-Hash.
- Cookie ist `HttpOnly`, `SameSite=Lax`, lokal wegen HTTP noch nicht `Secure`.
- SSE-Endpunkt und Admin-REST-Endpunkte verlangen eine gültige Session.

## 15. Tests und Qualitätsnachweise

Im Repository liegen derzeit 150 automatisch benannte Unit-Testfälle:

| Service | Testfälle |
| --- | ---: |
| Shop | 72 |
| Billing | 38 |
| Warehouse | 16 |
| Invoice | 10 |
| Audit | 14 |

Getestet werden unter anderem Fassade/Providerwechsel, Payment-Fehler,
Webhook, Saga-Zweige, Circuit-Zustände, Idempotenz, Warehouse-Operationen,
PDF-Inhalte, Audit-Mapping und REST-Helfer. Der Smoke-Test prüft Erreichbarkeit
und einen Happy Path. `scripts/integration-test.sh` prüft end-to-end Happy Path,
Payment-Fehler und Out-of-Stock samt vorhandenen bzw. fehlenden Audit-Events.

Für die Prüfung wichtig: `docs/quality-assurance.md` listet zusätzlich
`invoice_failed` und `warehouse_commit_failed`, das aktuelle Integrationsskript
automatisiert diese beiden Szenarien aber noch nicht. Sie sind durch Unit-Tests
der beteiligten Handler abgedeckt, nicht durch dieses End-to-End-Skript. Nicht
behaupten, alle fünf Szenarien würden dort bereits E2E laufen.

## 16. Kritische Reflexion: echte Grenzen der Lösung

Diese Punkte eignen sich als gute, ehrliche Antwort auf „Was würden Sie als
Nächstes verbessern?“:

1. **Transactional Outbox:** Datenbankänderung und RabbitMQ-Publish sind keine
   gemeinsame Transaktion. Ein Crash dazwischen kann eine gespeicherte Order
   ohne Startnachricht oder einen Status ohne Folge-Command hinterlassen.
2. **Inbox/Deduplizierung:** Audit dedupliziert per `messageId` und einige
   Warehouse-Schritte sind idempotent; für Billing und alle Shop-Events fehlt
   eine durchgängige Consumer-Inbox. Doppelte Zustellung kann Nebenwirkungen
   wiederholen.
3. **Dead-Letter-Queue:** Poison Messages werden aktuell mit `requeue=false`
   verworfen. Eine DLQ plus Alarmierung wäre nachvollziehbarer.
4. **Persistente Scheduler:** Invoice-Retry und PayPal-Stub nutzen
   `threading.Timer`. Ein Prozessneustart verliert geplante Timer. Besser wären
   verzögerte Queues, ein Scheduler oder persistierte Jobs.
5. **Circuit-Breaker-Zustand:** Er liegt nur im Speicher eines Shop-Prozesses,
   ist nicht thread-sicher und würde bei mehreren Instanzen auseinanderlaufen.
   Mögliche Lösung: Redis/geteilter Zustand oder Breaker pro Instanz mit klarer
   Skalierungsstrategie und Locking.
6. **Netzwerkgrenzen:** Fachlich soll der Shop der externe Einstiegspunkt sein,
   Docker Compose veröffentlicht lokal aber alle Service-Ports. In Produktion
   wären nur Frontend/Gateway öffentlich, interne Services in einem privaten
   Netz und mit Authentisierung/mTLS.
7. **Audit-Unveränderlichkeit:** Der Anwendungscode bietet kein Update/Delete;
   ein mächtiger DB-Benutzer könnte es trotzdem. Separate DB-Rollen oder
   Trigger würden die Garantie härten.
8. **Admin-Produktanlage:** Produktdaten im Shop und Bestand im Warehouse
   werden über zwei getrennte HTTP-Schreibvorgänge angelegt. Fällt der zweite
   aus, bleibt ein partieller Zustand. Dafür wäre ebenfalls eine kleine Saga
   oder ein Outbox-basierter Workflow sinnvoll.
9. **Vertragsdokumentation:** Die Flows beschreiben
   `billing.payment.pending` und `billing.payment.confirm.requested`, in der
   oberen Routing-Key-Tabelle von `docs/event-contracts.md` fehlen sie jedoch.
10. **Begriffsschärfe:** Die Dokumentation nennt den Ablauf teils
    „Choreografie“, obwohl der Shop faktisch orchestriert. Das sollte
    vereinheitlicht werden.
11. **Kausalkette:** `previousEventId` zeigt auf den Auslöser, nicht garantiert
    auf den unmittelbar zuvor chronologisch gespeicherten Snapshot. Bei
    parallelen Zweigen ist ein Graph fachlich sinnvoller als eine Liste; das
    sollte so dokumentiert werden.
12. **E2E-Abdeckung:** Invoice-Ausfall und fehlgeschlagener Warehouse-Commit
    sollten noch in `integration-test.sh` aufgenommen werden.

### Abweichung vom Aufgabenblatt erklären

Das Aufgabenblatt nennt Shop→Warehouse und Shop→Billing als synchrone
REST-Kommunikation. Die Implementierung nutzt für alle bestandsverändernden
Saga-Schritte RabbitMQ und antwortet sofort mit `202`. Das ist eine bewusste
Architekturentscheidung zugunsten von Entkopplung, Resilienz und einer
einheitlich auditierbaren Message-Kette. Der Preis sind eventuelle Konsistenz,
Polling/SSE und mehr Komplexität. Synchrone REST-Zugriffe bleiben für
Leseabfragen und Adminverwaltung erhalten. In der Prüfung sollte diese
Abweichung als begründeter Trade-off, nicht als versehentliches Übersehen,
erklärt werden.

Das Aufgabenblatt verlangt nur Stubs/Mocks und keine echten Keys. Der normale
lokale Betrieb erfüllt das: Ohne Credentials werden Stubs verwendet und
Secrets werden nicht committet. Die optionale Sandbox-Unterstützung ist eine
Erweiterung, darf für die Abgabe aber nicht von echten Zugangsdaten abhängen.

## 17. Wahrscheinliche Dozentenfragen mit Musterantworten

### Architektur und Shop-Service

**1. Warum ist der Shop-Service zentral, ist das nicht ein Monolith?**  
Er bündelt ein fachlich zusammengehöriges Order-Aggregat und die externe API,
nicht alle Funktionen. Bestand, Zahlung, Rechnung und Audit besitzen getrennte
Services, Daten und Deployments. Kritisch ist trotzdem, dass der Shop als
Prozessmanager mehr Verantwortung trägt; bei weiterem Wachstum könnte man den
Saga-Koordinator als eigenes Modul oder Service auslagern.

**2. Ist Ihre Saga orchestriert oder choreografiert?**  
Faktisch überwiegend orchestriert: Der Shop empfängt Ergebnis-Events und sendet
die nächsten Commands bzw. Kompensationen. Eventverteilung und Audit sind
choreografisch. Daher ist „eventgetriebene orchestrierte Saga“ die präziseste
Bezeichnung.

**3. Warum RabbitMQ statt Kafka?**  
Wir brauchen zielgerichtete Commands, Topic-Routing, ACKs und langlebige Queues
für einen überschaubaren Workflow. RabbitMQ ist dafür einfacher lokal zu
betreiben. Kafka wäre stärker für sehr hohen Durchsatz, langfristige
Event-Replays und viele unabhängige Stream-Consumer, bringt hier aber mehr
Betriebsaufwand.

**4. Warum nicht alles synchron per REST?**  
Eine lange synchrone Kette koppelt Latenz und Verfügbarkeit aller Services und
kann beim Client-Timeout trotzdem weiterlaufende Seiteneffekte erzeugen.
RabbitMQ entkoppelt Verarbeitung und macht Commands/Events auditierbar. Dafür
muss der Client mit `202`, Polling oder SSE und eventueller Konsistenz umgehen.

**5. Welche synchronen Verbindungen hat der Shop trotzdem?**  
Warehouse-Bestand für den Katalog, Warehouse-Adminänderungen und die
Audit-Timeline. Diese Lese-/Adminpfade sind nicht Teil der kritischen
Bestell-Saga.

**6. Warum hat jeder Service eine eigene Datenbank?**  
Damit Datenhoheit und Servicegrenzen erhalten bleiben. Direkte Tabellenzugriffe
würden Schema und Releases koppeln. Gemeinsamer PostgreSQL-Container bedeutet
nur gemeinsame Infrastruktur, nicht gemeinsame Datenverantwortung.

**7. Was passiert, wenn RabbitMQ beim `POST /orders` nach dem DB-Insert ausfällt?**  
Das ist aktuell ein Dual-Write-Risiko: Die Order kann `PENDING` bleiben, ohne
dass die Saga startet. Produktionslösung wäre eine Transactional Outbox in
derselben Shop-DB und ein separater Publisher.

### Saga und Konsistenz

**8. Was ist der Unterschied zwischen Rollback und Kompensation?**  
Ein DB-Rollback macht eine noch nicht committete lokale Transaktion unsichtbar.
Eine Saga-Kompensation ist eine neue fachliche Aktion nach bereits erfolgtem
Commit, etwa Reservierung lösen oder Geld erstatten; sie kann selbst scheitern
und wird auditiert.

**9. Warum wird bei Invoice-Fehlern nicht refunded?**  
Zahlung und Warenbewegung sind fachlich gültig, nur das wiederholbare Dokument
fehlt. Das Aufgabenblatt fordert Retry statt Rückzahlung. Nach drei Versuchen
bleibt der sichtbare Status `INVOICE_FAILED` für manuelle Nachbearbeitung.

**10. Was passiert, wenn Warehouse-Commit und Invoice in anderer Reihenfolge
ankommen?**  
Beide aktualisieren getrennte Felder. Nach jedem Erfolg prüft ein atomares
`UPDATE ... WHERE`, ob Payment, Invoice und Commit vollständig sind. Erst dann
wird einmalig `COMPLETED` gesetzt und `order.completed` publiziert.

**11. Was ist Eventual Consistency?**  
Unmittelbar nach `202` können Order, Warehouse, Billing und Audit
vorübergehend unterschiedliche Zwischenstände zeigen. Nach Verarbeitung aller
Nachrichten konvergieren sie zu einem konsistenten Endzustand.

**12. Was passiert, wenn der Refund scheitert?**  
Billing publiziert `billing.refund.failed`; der Shop setzt `REFUND_FAILED`.
Die Order wird nicht fälschlich als kompensiert markiert und muss manuell
geklärt werden.

**13. Garantiert RabbitMQ Exactly Once?**  
Nein. Durable Queue, persistente Nachrichten und manuelle ACKs reduzieren
Verlust, führen aber grundsätzlich zu At-Least-Once-Semantik. Exactly-once-
ähnliches Verhalten entsteht erst durch idempotente Handler und Inbox-
Deduplizierung.

**14. Warum `requeue=false` bei Handlerfehlern?**  
Damit eine dauerhaft fehlerhafte Poison Message nicht endlos den Consumer
blockiert. Der Nachteil ist möglicher Nachrichtenverlust; eine DLQ wäre die
professionelle Ergänzung.

### Payment-Fassade

**15. Was bringt die Fassade gegenüber direktem Adapterzugriff?**  
Billing sieht nur einheitliche Operationen, Status und Fehler. Die Fassade
kapselt Querschnittslogik wie Logging, Fehlerübersetzung und erlaubte Retries;
anbieterabhängige Details bleiben in den Adaptern.

**16. Wie fügen Sie einen dritten Payment-Anbieter hinzu?**  
Neue `PaymentAdapter`-Unterklasse mit `provider_name` und den drei Methoden
implementieren, Konfiguration ergänzen und Vertragstests hinzufügen. Die
Registry meldet die Klasse automatisch an; Fassade, Saga und Billing-Handler
bleiben unverändert.

**17. Wie wird der aktive Anbieter gewählt?**  
Standardmäßig über `PAYMENT_PROVIDER`; eine Order kann den Provider im
Payment-Payload mitgeben. `get_payment_facade()` löst den Namen über die
Adapter-Registry auf, nicht über eine hartcodierte if/elif-Kette.

**18. Warum eigener `PaymentResult` statt Stripe-/PayPal-Typen?**  
Damit die Anbietergrenze nicht in die Kernlogik leakt. Der Kern kennt nur
`transaction_id`, `provider`, einheitlichen Status und optionale normalisierte
Kunden-/Adressdaten.

**19. Warum Retry für Status/Refund, aber nicht für Charge?**  
Status ist ein Lesezugriff und Refund kann providerseitig idempotent gestaltet
werden. Bei Charge kann ein Timeout nach bereits erfolgter Belastung auftreten;
blindes Wiederholen riskiert Doppelzahlung.

**20. Warum ist PayPal ohne Credentials trotzdem asynchron?**  
Der Stub simuliert mit Timer und Selbst-Webhook realistisch, dass Annahme einer
Zahlung und endgültige Bestätigung getrennte Zeitpunkte sind. Die Saga wartet
in `PAYMENT_PENDING` auf das spätere Event.

### Daten, Audit und Logging

**21. Was unterscheidet `messageId` und `correlationId`?**  
Jede Nachricht hat eine neue `messageId`; alle Nachrichten derselben Bestellung
teilen eine `correlationId`. Erstere dient Identität/Deduplizierung, letztere
Tracing und Gruppierung.

**22. Wofür ist `previousEventId` da?**  
Es zeigt, welche Nachricht die aktuelle verursacht hat. Dadurch kann man die
Kausalität rekonstruieren. Bei parallelen Zweigen ist dies ein Graph, nicht
zwingend eine lineare Liste.

**23. Warum ist Audit nicht vollständiges Event Sourcing?**  
Der aktuelle Orderzustand wird in `shop_orders` gespeichert und nicht nur aus
Events rekonstruiert. Audit ist ein append-only Verlauf zusätzlich zum
operativen Modell—daher Event Sourcing Light.

**24. Wie verhindern Sie doppelte Audit-Snapshots?**  
Die RabbitMQ-`messageId` wird Primärschlüssel des Snapshots;
`ON CONFLICT DO NOTHING` macht denselben Insert wiederholbar.

**25. Warum Audit-Service ohne Business-Wissen?**  
Er kann dadurch neue Eventtypen aufnehmen, ohne bei jeder fachlichen Änderung
angepasst zu werden. Typ und Status werden generisch aus dem Envelope abgeleitet.

**26. Unterschied Audit und Logging?**  
Audit belegt fachliche Zustandsübergänge dauerhaft und unveränderlich. Logs
dienen technischer Diagnose, enthalten auch Debug-/Fehlerdetails und werden
rotiert bzw. in Loki zeitlich begrenzt gespeichert.

### Resilienz, Parallelität und API

**27. Was macht ein Circuit Breaker?**  
Er verhindert Aufrufe an einen wahrscheinlich defekten Downstream. Nach drei
Fehlern öffnet er, nach 30 Sekunden lässt der nächste Aufruf genau einen
Half-Open-Test zu und schließt bei Erfolg bzw. öffnet bei Fehler erneut.

**28. Unterschied Circuit Breaker und Retry?**  
Retry wiederholt einen einzelnen fehlgeschlagenen Vorgang. Der Circuit Breaker
beobachtet mehrere Fehler und blockiert neue Vorgänge zeitweise, um Last und
kaskadierende Ausfälle zu vermeiden.

**29. Wo liegt der Circuit-Breaker-Zustand und was ist daran problematisch?**  
Als Singleton im Speicher des Shop-Prozesses. Neustart setzt ihn zurück,
mehrere Instanzen hätten unterschiedliche Zustände und die Klasse ist nicht
thread-sicher. Das ist für die lokale Ein-Instanz-Demo ausreichend, aber keine
vollständige Clusterlösung.

**30. Wie verhindert Warehouse Überreservierung?**  
Prüfung und Erhöhung von `reserved_quantity` passieren in einer DB-Transaktion
unter Zeilensperren. Parallel laufende Reservierungen sehen dadurch
serialisierte Bestandsänderungen.

**31. Wie funktioniert die Idempotenz von `POST /orders`?**  
Key plus Hash des kanonischen Bodys werden gespeichert. Gleiches Paar liefert
dieselbe Order; gleicher Key mit anderem Hash ergibt 409. Ein Unique Index und
das Abfangen der Unique-Verletzung schließen das Parallelitätsfenster.

**32. Warum RFC 7807?**  
Alle Services liefern eine vorhersehbare, maschinenlesbare Fehlerstruktur statt
unterschiedlicher Frameworkantworten. Status, Detail, Instanz und
Correlation-ID erleichtern Clientbehandlung und Diagnose.

**33. SSE oder WebSocket—warum SSE?**  
Das Dashboard braucht nur Server→Browser-Benachrichtigungen. SSE arbeitet über
normales HTTP, ist einfacher und unterstützt Reconnect. Bidirektionale
Echtzeitkommunikation wäre ein Grund für WebSocket.

**34. Was geschieht, wenn Warehouse beim Katalog-Lesezugriff ausfällt?**  
Der Shop liefert seine Produkte weiter, markiert Bestand aber als `UNKNOWN`.
Das ist graceful degradation; Checkout-Reservierung prüft später weiterhin
verbindlich im Warehouse.

**35. Welche Verbesserung hätte höchste Priorität?**  
Transactional Outbox plus Inbox/Deduplizierung, weil sie die größte Lücke
zwischen lokaler DB-Transaktion und zuverlässigem Messaging schließt. Danach
DLQ/Monitoring und persistente Retry-Scheduler.

## 18. Fünf-Minuten-Demo und letzte Lernkontrolle

Empfohlene Demo-Reihenfolge:

1. `docker compose up --build`, Health und Shop kurz zeigen.
2. Happy-Path-Order starten; `202/PENDING` betonen.
3. Im Adminmonitor Statuswechsel und am Ende `COMPLETED` zeigen.
4. Audit-Timeline öffnen und eine vollständige Kette anhand der
   `correlationId` erklären.
5. Ein Fehlerszenario zeigen, am besten `payment_failed`, und Cancel als
   Kompensation hervorheben.
6. Im Billing-Code Fassade, Adapter-Registry und Provider-Konfiguration zeigen.
7. Mit einem ehrlichen Verbesserungsziel enden: Outbox/Inbox oder persistente
   Scheduler.

Vor der Prüfung sollte jede Person ohne Unterlagen beantworten können:

- Welche Daten besitzt jeder Service?
- Welche Verbindungen des Shop-Service sind HTTP und welche RabbitMQ?
- Warum antwortet `POST /orders` mit 202?
- Welche drei Bedingungen machen eine Order `COMPLETED`?
- Welcher Fehler löst Cancel, welcher Refund und welcher nur Retry aus?
- Wie unterscheiden sich Command, Event, `messageId` und `correlationId`?
- Warum ist die Saga eher orchestriert als rein choreografiert?
- Wie fügt man einen Payment-Anbieter hinzu?
- Warum wird `charge()` nicht blind wiederholt?
- Warum ist das Audit nur Event Sourcing Light?
- Welche Garantien liefert RabbitMQ nicht?
- Wie würden Outbox, Inbox und DLQ die Lösung verbessern?

## 19. Quellen im Repository

- Aufgabenstellung: `SoSe2026_Aufgabenblatt_VSE_Online-Shop.pdf`
- Gesamtarchitektur: `docs/architecture.md`
- Message-Verträge: `docs/event-contracts.md`
- Architekturentscheidungen: `docs/decisions/`
- Qualitätssicherung: `docs/quality-assurance.md`
- Logging: `docs/log-management.md`
- Shop-Saga: `shop-service/src/saga.py`
- Shop-REST-API: `shop-service/src/routes.py`
- Payment-Fassade: `billing-service/src/payment/facade.py`
- Payment-Adapter: `billing-service/src/payment/adapters.py`
- Warehouse-Transaktionen: `warehouse-service/src/database.py`
- Append-only Audit: `audit-service/src/database.py`

