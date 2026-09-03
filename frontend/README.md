# Frontend

React/Vite single-page app for the shop (product catalog, cart, checkout,
order tracking) and the admin dashboard (order overview, audit timeline,
product/warehouse management). Talks to shop-service over HTTP; the
shop-service loads audit data from audit-service. The frontend is built as a
static bundle and served via nginx in production.

## Prerequisites

- Docker and Docker Compose (recommended - runs the frontend together with
  the backend services it depends on)
- Alternatively, for standalone development: Node.js 22+ and npm, plus a
  reachable shop-service and its dependencies

## Getting started

Via Docker Compose, from the repository root (starts the full stack, builds
the production bundle and serves it via nginx on port `3000`):

```bash
docker compose up --build frontend
```

Standalone, from this directory, with hot reload against already-running
backend services:

```bash
npm install
npm run dev
```

This starts the Vite dev server on `http://localhost:5173` (see `npm run dev`
output for the exact port). Configure the backend URLs via environment
variables before starting (see below), or edit them in a local `.env` file.

Production build (creates the static bundle also used by the Docker image):

```bash
npm run build
```

## Configuration

- `VITE_SHOP_API_URL` - base URL of shop-service (default in Docker Compose:
  `http://localhost:8000`)

## Admin area

Reachable under `/admin`, protected by an HttpOnly session cookie issued by
shop-service (`POST /admin/login`). Default local credentials:
`admin` / `admin123` (see root `README.md`).
