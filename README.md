# TMF921 Intent Management API v5.0.0

A production-ready implementation of the TM Forum TMF921 Intent Management API built with **Python 3.12 + FastAPI** and **Apache Jena Fuseki** as the authoritative RDF graph store.

---

## Prerequisites

- Docker + Docker Compose **or** Python 3.12 + a running Fuseki instance
- `uv` (optional but recommended for local development)

---

## Quick Start — Docker Compose

```bash
# Start Fuseki and the API
docker compose up --build

# In a second terminal, seed sample data
python seed_data/seed_intents.py

# Verify
curl http://localhost:8000/health
curl http://localhost:8000/tmf-api/intentManagement/v5/intent
```

The Fuseki admin console is available at http://localhost:3030 (user: `admin`, password: `admin`).

---

## Local Development

```bash
# Install dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt

# Start Fuseki (Docker only for graph store)
docker compose up fuseki -d

# Run the API
uvicorn src.main:app --reload

# Seed data
python seed_data/seed_intents.py
```

---

## Running Tests

```bash
# All tests with coverage
pytest tests/ -v --cov=src --cov-fail-under=80

# Unit tests only
pytest tests/unit/ -v

# Integration tests
pytest tests/integration/ -v

# Contract tests (Schemathesis)
pytest tests/contract/ -v
```

The test suite uses `respx` to mock Fuseki — no running Fuseki required for tests.

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `FUSEKI_BASE_URL` | `http://localhost:3030` | Fuseki HTTP base URL |
| `FUSEKI_DATASET` | `tmf921` | Fuseki dataset name |
| `LOG_LEVEL` | `info` | uvicorn log level |

---

## API Endpoints

Base path: `/tmf-api/intentManagement/v5`

### Intent

| Method | Path | Description |
|---|---|---|
| `GET` | `/intent` | List intents (pagination: `limit`, `offset`) |
| `POST` | `/intent` | Create intent or ProbeIntent |
| `GET` | `/intent/{id}` | Get intent by ID |
| `PATCH` | `/intent/{id}` | Partial update (RFC 7386 merge patch) |
| `DELETE` | `/intent/{id}` | Delete intent |
| `GET` | `/intent/{id}/intentReport` | List intent reports |
| `GET` | `/intent/{id}/intentReport/{reportId}` | Get intent report by ID |

### IntentSpecification

| Method | Path | Description |
|---|---|---|
| `GET` | `/intentSpecification` | List specifications |
| `POST` | `/intentSpecification` | Create specification |
| `GET` | `/intentSpecification/{id}` | Get specification |
| `PATCH` | `/intentSpecification/{id}` | Update specification |
| `DELETE` | `/intentSpecification/{id}` | Delete specification |

### Event Hub

| Method | Path | Description |
|---|---|---|
| `POST` | `/hub` | Subscribe to events |
| `DELETE` | `/hub/{id}` | Unsubscribe |

### Health

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Service + Fuseki health check |

---

## Lifecycle States

```
Acknowledged → Active → Terminated
             ↘ Rejected
```

Valid PATCH transitions are enforced server-side. See `docs/04-state-machine.md` for the full FSM.

---

## Event Types

The API publishes 10 event types to registered hub callbacks:

- `intentCreateEvent`, `intentDeleteEvent`, `intentAttributeValueChangeEvent`, `intentStatusChangeEvent`
- `intentReportCreateEvent`, `intentReportDeleteEvent`, `intentReportAttributeValueChangeEvent`
- `intentSpecificationCreateEvent`, `intentSpecificationDeleteEvent`, `intentSpecificationAttributeValueChangeEvent`, `intentSpecificationStatusChangeEvent`

---

## Architecture

```
FastAPI app
  └─ Routers (intent, intentReport, intentSpec, hub)
       └─ Services (IntentService, NotificationService)
            └─ Repositories (IntentRepository, IntentReportRepository, …)
                 └─ FusekiClient (SPARQL 1.1 + Graph Store Protocol)
                      └─ Apache Jena Fuseki TDB2
```

---

## Postman Collection

Import `postman/TMF921_collection.json` into Postman. Set the `base_url` collection variable to `http://localhost:8000` (default).

---

## Linting

```bash
ruff check src/
```
