# Design Document: Store Intelligence System

## Overview

The Store Intelligence System converts raw CCTV footage from Apex Retail's physical stores into real-time business analytics. The system is structured as four integrated parts:

- **Part A — Detection Pipeline**: Ingests video frames, detects persons via YOLOv8, tracks identities via ByteTrack, maps positions to store zones, classifies staff vs. visitors, and emits structured JSON events.
- **Part B — Intelligence API**: A FastAPI service that ingests events, computes metrics (conversion rate, dwell, funnel, heatmap, anomalies), and exposes REST endpoints.
- **Part C — Production Readiness**: Docker Compose orchestration, structured logging, health checks, and automated test coverage.
- **Part D — AI Engineering Documentation**: Design rationale and AI-assisted development notes.
- **Part E (optional) — Live Dashboard**: A Streamlit application that auto-refreshes store analytics from the API.

The north star metric is **Conversion Rate**: unique visitors who made a purchase divided by total unique visitors in a given time window.

The system covers 40 stores, each with multiple camera angles (Entry, Main Floor, Billing), and integrates with POS transaction data to compute purchase-correlated metrics.

---

## Architecture

```mermaid
graph TD
    subgraph "Part A: Detection Pipeline"
        V[CCTV Video / Simulate Mode] --> D[detect.py - YOLOv8]
        D --> T[tracker.py - ByteTrack]
        T --> ZM[zone_mapper.py - Shapely]
        T --> SC[staff_classifier.py - HSV + Heuristics]
        ZM --> E[emit.py - Event Emitter]
        SC --> E
        E --> EF[events/*.jsonl]
    end

    subgraph "Part B: Intelligence API"
        EF --> ING[POST /events/ingest]
        ING --> DB[(PostgreSQL / SQLite)]
        DB --> MET[GET /stores/{id}/metrics]
        DB --> FUN[GET /stores/{id}/funnel]
        DB --> HM[GET /stores/{id}/heatmap]
        DB --> ANO[GET /stores/{id}/anomalies]
        DB --> HLT[GET /health]
    end

    subgraph "Part E: Dashboard"
        MET --> DASH[Streamlit Dashboard]
        FUN --> DASH
        HM --> DASH
        ANO --> DASH
        HLT --> DASH
    end
```

### Data Flow Summary

1. The pipeline reads video frames (or replays `sample_events.jsonl` in simulate mode).
2. YOLOv8 detects persons (class 0, confidence ≥ 0.4); ByteTrack assigns stable track IDs.
3. Each track is mapped to a zone via Shapely point-in-polygon; staff are classified via HSV.
4. The emitter writes newline-delimited JSON events to `data/events/`.
5. The API ingests events via `POST /events/ingest` and persists them to PostgreSQL.
6. Analytics endpoints query the event store and compute metrics on demand.
7. The Streamlit dashboard polls the API at a configurable interval (≥ 5 s).

---

## Components and Interfaces

### A.1 — `pipeline/detect.py`

Wraps the YOLOv8 model. Accepts a video frame (numpy array) and returns a list of bounding boxes with confidence scores.

```python
def detect_persons(frame: np.ndarray, conf_threshold: float = 0.4) -> list[Detection]:
    """Returns detections for class 0 (person) above conf_threshold."""
```

`Detection` carries: `bbox: tuple[float,float,float,float]`, `confidence: float`, `class_id: int`.

### A.2 — `pipeline/tracker.py`

Wraps ByteTrack (via ultralytics). Accepts a list of `Detection` objects and returns tracked objects with stable `track_id` values.

```python
def update_tracks(detections: list[Detection]) -> list[TrackedPerson]:
    """Maintains ByteTrack state; returns active tracks with stable IDs."""
```

`TrackedPerson` carries: `track_id: int`, `bbox: tuple`, `is_lost: bool`, `frames_lost: int`.

### A.3 — `pipeline/zone_mapper.py`

Loads `store_layout.json` and performs Shapely point-in-polygon tests.

```python
def load_layout(path: str) -> StoreLayout:
    """Validates all polygons are closed and non-self-intersecting."""

def map_to_zone(point: tuple[float, float], layout: StoreLayout) -> str | None:
    """Returns zone_id of the highest-priority zone containing point, or None."""
```

Camera priority order is defined per-zone in `store_layout.json`. Overlapping zones resolve to the highest-priority camera's zone.

### A.4 — `pipeline/staff_classifier.py`

Two-stage classifier: HSV color detection on the bounding box region, with rule-based fallback.

```python
def classify(frame: np.ndarray, bbox: tuple, hsv_config: HSVConfig) -> ClassificationResult:
    """Returns is_staff: bool, confidence: float, method: 'hsv' | 'heuristic'."""
```

If `confidence < hsv_config.threshold`, the heuristic classifier (movement pattern analysis) is applied.

### A.5 — `pipeline/emit.py`

Constructs and writes events conforming to the canonical event schema.

```python
def emit_event(event_type: EventType, visitor_id: str, store_id: str,
               camera_id: str, zone_id: str | None, **kwargs) -> Event:
    """Assigns event_id (UUID v4), timestamp (UTC ISO-8601), session_seq."""
```

`session_seq` is a monotonically increasing integer scoped to the current processing session.

Visitor token generation:

```python
def make_visitor_token(store_id: str, track_id: int, session_start: str) -> str:
    raw = f"{store_id}_{track_id}_{session_start}"
    return "VIS_" + hashlib.md5(raw.encode()).hexdigest()[:6]
```

### A.6 — `pipeline/run.sh`

Entry point. Accepts `--simulate` flag to replay `data/sample/sample_events.jsonl` at 10× speed. Without `--simulate`, iterates over `data/clips/` video files.

---

### B.1 — `app/main.py`

FastAPI application factory. Registers routers, middleware (Trace_ID injection, structured logging), and exception handlers.

```python
app = FastAPI(title="Store Intelligence API")
app.add_middleware(TraceIDMiddleware)
app.include_router(ingestion.router)
app.include_router(metrics.router)
app.include_router(funnel.router)
app.include_router(heatmap.router)
app.include_router(anomalies.router)
app.include_router(health.router)
```

### B.2 — `app/ingestion.py`

```
POST /events/ingest
Body: list[EventSchema] (max 500 items)
Response 200: { "ingested": int, "rejected": list[{ "event_id": str, "reason": str }] }
Response 422: { "detail": [{ "loc": [...], "msg": str, "type": str }] }
```

Idempotency is enforced via a unique constraint on `event_id` in the database; duplicate inserts are silently ignored.

### B.3 — `app/metrics.py`

```
GET /stores/{store_id}/metrics?start=<ISO>&end=<ISO>
Response 200: {
  "store_id": str,
  "unique_visitors": int,
  "conversion_rate": float,       # [0.0, 1.0]
  "avg_dwell_seconds": float,
  "queue_depth": int,
  "abandonment_rate": float
}
```

Returns zeros/nulls for stores with no events (never 404).

### B.4 — `app/funnel.py`

```
GET /stores/{store_id}/funnel?start=<ISO>&end=<ISO>
Response 200: {
  "store_id": str,
  "stages": [
    { "stage": "ENTRY",         "count": int, "drop_off_pct": null },
    { "stage": "ZONE_VISIT",    "count": int, "drop_off_pct": float },
    { "stage": "BILLING_QUEUE", "count": int, "drop_off_pct": float },
    { "stage": "PURCHASE",      "count": int, "drop_off_pct": float }
  ]
}
```

Each stage count ≤ previous stage count. Visitor_Tokens are deduplicated per stage per session.

### B.5 — `app/heatmap.py`

```
GET /stores/{store_id}/heatmap?start=<ISO>&end=<ISO>
Response 200: {
  "store_id": str,
  "zones": [
    { "zone_id": str, "visit_count": int, "avg_dwell_seconds": float, "intensity": float }
  ]
}
```

`intensity` is in [0, 100]. When any zone has visits, the zone with the highest combined score receives intensity = 100.

### B.6 — `app/anomalies.py`

```
GET /stores/{store_id}/anomalies?start=<ISO>&end=<ISO>
Response 200: {
  "store_id": str,
  "anomalies": [
    { "type": "BILLING_QUEUE_SPIKE|CONVERSION_DROP|DEAD_ZONE",
      "severity": "LOW|MEDIUM|HIGH",
      "timestamp": str,
      "description": str }
  ]
}
```

Always returns a list (never null). Empty list when no anomalies detected.

### B.7 — `app/health.py`

```
GET /health
Response 200: { "status": "ok", "db": "ok", "stores": [ { "store_id": str, "feed_status": "ok|STALE_FEED", "last_event_timestamp": str|null } ] }
Response 503: { "status": "degraded", "db": "unavailable", "stores": [...] }
```

Always returns valid JSON with a `status` field, even when the database is unreachable.

---

## Data Models

### Event Table (`events`)

| Column | Type | Notes |
|---|---|---|
| `event_id` | UUID PK | Unique constraint enforces idempotency |
| `store_id` | VARCHAR(50) | Indexed |
| `camera_id` | VARCHAR(50) | |
| `visitor_id` | VARCHAR(12) | Format: `VIS_[a-f0-9]{6}` |
| `event_type` | VARCHAR(30) | ENTRY, EXIT, ZONE_ENTER, ZONE_EXIT, ZONE_DWELL, BILLING_QUEUE_JOIN, BILLING_QUEUE_ABANDON, REENTRY |
| `timestamp` | TIMESTAMPTZ | Indexed |
| `zone_id` | VARCHAR(50) | Nullable |
| `dwell_ms` | INTEGER | Nullable |
| `is_staff` | BOOLEAN | Default false; indexed for fast exclusion |
| `confidence` | FLOAT | |
| `metadata` | JSONB | queue_depth, sku_zone, session_seq |
| `ingested_at` | TIMESTAMPTZ | Server default NOW() |

### POS Records Table (`pos_records`)

| Column | Type | Notes |
|---|---|---|
| `transaction_id` | UUID PK | |
| `store_id` | VARCHAR(50) | Indexed |
| `timestamp` | TIMESTAMPTZ | Indexed |
| `basket_value_inr` | NUMERIC(12,2) | |

POS records contain no `customer_id`. Conversion is computed by correlating POS transaction counts with unique visitor counts in the same time window.

### SQLAlchemy Models (`app/models.py`)

```python
class Event(Base):
    __tablename__ = "events"
    event_id = Column(UUID, primary_key=True)
    store_id = Column(String(50), index=True, nullable=False)
    camera_id = Column(String(50))
    visitor_id = Column(String(12), nullable=False)
    event_type = Column(String(30), nullable=False)
    timestamp = Column(DateTime(timezone=True), index=True, nullable=False)
    zone_id = Column(String(50))
    dwell_ms = Column(Integer)
    is_staff = Column(Boolean, default=False, index=True)
    confidence = Column(Float)
    metadata_ = Column("metadata", JSONB)
    ingested_at = Column(DateTime(timezone=True), server_default=func.now())

class POSRecord(Base):
    __tablename__ = "pos_records"
    transaction_id = Column(UUID, primary_key=True)
    store_id = Column(String(50), index=True, nullable=False)
    timestamp = Column(DateTime(timezone=True), index=True, nullable=False)
    basket_value_inr = Column(Numeric(12, 2))
```

### Pydantic Event Schema (`app/models.py`)

```python
class EventSchema(BaseModel):
    event_id: UUID
    store_id: str
    camera_id: str
    visitor_id: str = Field(pattern=r"^VIS_[a-f0-9]{6}$")
    event_type: EventType
    timestamp: datetime
    zone_id: str | None = None
    dwell_ms: int | None = None
    is_staff: bool = False
    confidence: float = Field(ge=0.0, le=1.0)
    metadata: EventMetadata | None = None
```

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system — essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

Property-based testing is applicable here because the system contains pure functions (token generation, zone mapping, normalization, metric computation) with large input spaces where universal properties must hold. The PBT library used is **Hypothesis** (Python).

### Property 1: Visitor Token Format

*For any* combination of `store_id`, `track_id`, and `session_start`, the generated Visitor_Token must match the regular expression `^VIS_[a-f0-9]{6}$`.

**Validates: Requirements 1.3**

---

### Property 2: Staff Exclusion

*For any* batch of events containing events with `is_staff=True`, none of those events shall appear in the counts returned by the metrics, funnel, or heatmap endpoints.

**Validates: Requirements 3.3, 7.4**

---

### Property 3: Conversion Rate Bounds

*For any* set of ingested events and POS records for a store, the `conversion_rate` field returned by `GET /stores/{store_id}/metrics` shall be a float in the closed interval [0.0, 1.0], shall never be null, and shall never cause the endpoint to return a non-200 response — including when the store has zero visitors.

**Validates: Requirements 7.2, 7.3**

---

### Property 4: Funnel Monotonicity

*For any* set of ingested events for a store, the visitor counts across funnel stages returned by `GET /stores/{store_id}/funnel` shall be monotonically non-increasing: `ENTRY ≥ ZONE_VISIT ≥ BILLING_QUEUE ≥ PURCHASE`.

**Validates: Requirements 8.3**

---

### Property 5: Heatmap Intensity Bounds

*For any* set of zone visit data for a store, all `intensity` scores returned by `GET /stores/{store_id}/heatmap` shall be in the closed interval [0, 100]. When at least one zone has visits, the maximum intensity score across all zones shall equal exactly 100.

**Validates: Requirements 9.2, 9.3**

---

### Property 6: Anomalies Always Returns a List

*For any* store_id and any event set (including empty), `GET /stores/{store_id}/anomalies` shall return a JSON object whose `anomalies` field is a list (never null). When no anomaly conditions are met, the list shall be empty.

**Validates: Requirements 10.5**

---

### Property 7: Health Endpoint Always Returns Valid JSON

*For any* database connectivity state (connected or disconnected), `GET /health` shall return a valid JSON body containing a `status` field. When the database is unavailable, the response shall be HTTP 503 with `status: "degraded"`. The endpoint shall never return an unstructured error or raise an unhandled exception.

**Validates: Requirements 11.1, 11.2**

---

### Property 8: Idempotent Event Ingestion

*For any* valid event payload, calling `POST /events/ingest` with the same `event_id` any number of times shall result in exactly one record in the database. The response shall be a success response on every call.

**Validates: Requirements 6.2**

---

### Property 9: Re-entry Deduplication in Funnel

*For any* visitor who has both ENTRY and REENTRY events in the same store session, that visitor shall be counted exactly once per funnel stage — not once per ENTRY/REENTRY event.

**Validates: Requirements 8.2**

---

### Property 10: Session Sequence Monotonicity

*For any* sequence of events emitted by the pipeline for a single visitor within a single processing session, the `session_seq` values (stored in `metadata.session_seq`) shall be strictly increasing.

**Validates: Requirements 4.10**

---

### Property 11: Zone Mapping Correctness

*For any* point and store layout configuration, `map_to_zone` shall return the `zone_id` of the highest-priority zone whose polygon contains the point, or `None` if no zone contains the point. A point that falls inside multiple overlapping zones shall always resolve to the zone with the highest camera priority.

**Validates: Requirements 2.1, 2.3**

---

### Property 12: Validation Error Response Format

*For any* event payload that fails schema validation, `POST /events/ingest` shall return HTTP 422 with a structured JSON body containing at least one entry with a `loc` (field path) and `msg` (violation description). The response body shall never contain a raw Python stack trace.

**Validates: Requirements 6.4, 12.2**

---

### Property 13: Trace ID Uniqueness

*For any* N concurrent or sequential requests to any API endpoint, all `X-Trace-ID` response header values shall be distinct UUID v4 strings.

**Validates: Requirements 12.3**

---

## Error Handling

### Pipeline Error Handling

| Scenario | Behavior |
|---|---|
| `store_layout.json` has invalid polygon | Raise `ConfigurationError` at startup; pipeline does not start |
| Video file not found and `--simulate` not set | Log warning, exit with code 1 |
| YOLOv8 model file missing | Raise `ModelLoadError` at startup |
| Frame decode failure | Log warning, skip frame, continue |
| Occlusion > 60 s | Emit EXIT event, retire track |
| Empty store > 5 min | Record empty period in event log; no spurious events |

### API Error Handling

| Scenario | HTTP Status | Response Body |
|---|---|---|
| Schema validation failure | 422 | `{ "detail": [{ "loc": [...], "msg": str }] }` |
| Unknown store_id (no events) | 200 | Zero-value metrics/funnel/heatmap |
| Database unavailable | 503 | `{ "status": "degraded", "db": "unavailable", "trace_id": str }` |
| Unhandled exception | 500 | `{ "trace_id": str, "message": "Internal server error" }` |
| Batch > 500 events | 422 | Structured validation error |

All 500 responses include the `trace_id` and a human-readable message. Raw stack traces are never included in response bodies. Stack traces are written only to structured logs.

### Middleware

`TraceIDMiddleware` runs on every request:
1. Generates a UUID v4 `trace_id`.
2. Injects it into the request state.
3. Adds `X-Trace-ID` to the response headers.
4. Passes `trace_id` to structlog's context for the duration of the request.

---

## Testing Strategy

### Dual Testing Approach

The system uses both **unit/example-based tests** and **property-based tests** (Hypothesis). They are complementary:

- Unit tests verify specific examples, edge cases, and integration points.
- Property tests verify universal invariants across large randomized input spaces.

### Property-Based Tests (Hypothesis, min 100 iterations each)

Each property test is tagged with a comment referencing the design property:

```
# Feature: store-intelligence-system, Property N: <property_text>
```

| Test File | Property | What is Generated |
|---|---|---|
| `tests/test_ingestion.py` | P8: Idempotent ingestion | Random valid EventSchema payloads |
| `tests/test_ingestion.py` | P12: Validation error format | Random invalid payloads (missing fields, wrong types) |
| `tests/test_ingestion.py` | P13: Trace ID uniqueness | N random requests |
| `tests/test_metrics.py` | P2: Staff exclusion | Random event batches with mixed is_staff values |
| `tests/test_metrics.py` | P3: Conversion rate bounds | Random event + POS record sets |
| `tests/test_funnel.py` | P4: Funnel monotonicity | Random event sets per store |
| `tests/test_funnel.py` | P9: Re-entry deduplication | Random visitor sequences with REENTRY events |
| `tests/test_anomalies.py` | P6: Anomalies always a list | Random store_ids and event sets |
| `tests/test_pipeline.py` | P1: Visitor token format | Random (store_id, track_id, session_start) tuples |
| `tests/test_pipeline.py` | P10: Session seq monotonicity | Random visitor event sequences |
| `tests/test_pipeline.py` | P11: Zone mapping correctness | Random points and polygon layouts |
| `tests/test_pipeline.py` | P5: Heatmap intensity bounds | Random zone visit count distributions |

### Unit / Example-Based Tests

| Test File | Coverage |
|---|---|
| `tests/test_ingestion.py` | Partial success batch (valid + invalid mix), 500-event batch limit, duplicate event_id |
| `tests/test_metrics.py` | Zero-visitor store returns 200 with zeros, queue_depth from join/abandon sequences |
| `tests/test_funnel.py` | Zero ENTRY events returns 200 with zeros, drop-off percentage calculation |
| `tests/test_anomalies.py` | BILLING_QUEUE_SPIKE threshold, CONVERSION_DROP threshold, DEAD_ZONE detection |
| `tests/test_health.py` | DB up → 200, DB down → 503, STALE_FEED detection |

### Coverage Target

Minimum **70% line coverage** across `app/` measured by `pytest-cov`. Run with:

```bash
pytest --cov=app --cov-report=term-missing tests/
```

### Assertions Script

`assertions.py` runs 10 behavioral assertions against a live API instance, covering all major endpoints. It is designed to be run after `docker compose up` as a smoke test.

---

## Containerization Design

### `docker-compose.yml` Services

```yaml
services:
  db:
    image: postgres:15-alpine
    environment:
      POSTGRES_DB: store_intelligence
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER}"]
      interval: 5s
      timeout: 5s
      retries: 10

  api:
    build:
      context: .
      dockerfile: Dockerfile.api
    depends_on:
      db:
        condition: service_healthy
    environment:
      DATABASE_URL: postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@db:5432/store_intelligence
    ports:
      - "8000:8000"

  dashboard:
    build:
      context: .
      dockerfile: Dockerfile.dashboard
    depends_on:
      - api
    environment:
      API_BASE_URL: http://api:8000
    ports:
      - "8501:8501"

volumes:
  pgdata:
```

### `Dockerfile.api`

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
```

Database migrations run automatically via `alembic upgrade head` before the API starts. The system is fully operational within 120 seconds of `docker compose up` in a clean environment.

---

## Observability

### Structured Logging (structlog)

Every request log entry includes:

```json
{
  "timestamp": "2026-03-03T14:22:10.123Z",
  "level": "info",
  "trace_id": "550e8400-e29b-41d4-a716-446655440000",
  "store_id": "STORE_BLR_002",
  "endpoint": "/stores/STORE_BLR_002/metrics",
  "method": "GET",
  "status_code": 200,
  "latency_ms": 12.4
}
```

### Pipeline Logging

The pipeline emits structured JSON logs for:
- Frame processing rate (frames/sec)
- Detection counts per frame
- Zone assignment decisions
- Staff classification results
- Event emission confirmations
- Error conditions (occlusion timeouts, empty store periods)

---

## Key Design Decisions

### 1. Idempotency via Database Unique Constraint

Rather than checking for duplicates in application code (which introduces race conditions), idempotency for `POST /events/ingest` is enforced by a `UNIQUE` constraint on `events.event_id`. Duplicate inserts use `INSERT ... ON CONFLICT DO NOTHING`, making the operation safe under concurrent ingestion.

### 2. Staff Exclusion at Query Time

Staff events are stored in the same `events` table with `is_staff=TRUE`. All analytics queries include a `WHERE is_staff = FALSE` clause. This approach preserves the full event log for audit purposes while ensuring staff never appear in customer metrics. The `is_staff` column is indexed for query performance.

### 3. Conversion Rate Without Customer Identity

POS records contain no `customer_id`. Conversion rate is computed as `COUNT(DISTINCT pos_records) / COUNT(DISTINCT visitor_id)` within a time window, using temporal correlation rather than identity matching. This is a deliberate privacy-preserving design.

### 4. Heatmap Normalization

Intensity is computed as a weighted combination of `visit_count` and `avg_dwell_seconds`, both normalized to [0, 1] relative to the store maximum, then scaled to [0, 100]. When all zones have zero visits, all intensities are 0 (no division by zero).

### 5. Health Endpoint Resilience

The health endpoint uses a separate, short-timeout database probe that catches connection errors and returns a structured 503 response. It never propagates unhandled exceptions, ensuring monitoring systems always receive a parseable response.
