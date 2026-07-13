# Zentrales Log-Management

## Stack

Der zentrale Log-Stack besteht aus:

- Loki als Log-Datenbank
- Promtail als Docker-Log-Collector
- Grafana als UI und Dashboard

Start:

```bash
docker compose up --build
```

Zugriff:

- Grafana: `http://localhost:3001`
- Login: `admin` / `admin`
- Dashboard: `Webshop / Webshop Zentrales Log-Management`
- Loki: `http://localhost:3100`

## Log-Fluss

Alle Backend-Services schreiben strukturierte JSON-Logs nach stdout. Promtail
liest die Docker-Logs ueber den Docker-Socket, extrahiert zentrale Felder und
sendet sie an Loki.

Wichtige Felder:

- `service`
- `level`
- `timestamp`
- `message`
- `correlationId`
- `eventType`
- `paymentResult`
- `provider`
- `reasonCode`

## Dashboard-Panels

### Anzahl der Bestellungen pro Zeitintervall

```logql
sum(count_over_time({service="shop-service", eventType="order.accepted"}[$__interval]))
```

### Fehlerrate nach Service

```logql
sum by (service) (rate({level="ERROR"}[$__rate_interval]))
```

### Zahlungsversuche nach Ergebnis

```logql
sum by (paymentResult) (count_over_time({service="billing-service", eventType=~"billing.payment.succeeded|billing.payment.failed"}[$__interval]))
```

Die Zahlungsreihe unterscheidet:

- `SUCCEEDED`
- `DECLINED`
- `TIMEOUT`
