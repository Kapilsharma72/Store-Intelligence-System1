# Store Intelligence System — Design Notes

## System Overview

The Store Intelligence System converts raw CCTV footage into real-time retail analytics. It is structured as four integrated parts: a detection pipeline (YOLOv8 + ByteTrack), an Intelligence API (FastAPI + PostgreSQL), production infrastructure (Docker Compose + structlog), and this documentation layer.

---

## AI-Assisted Decisions

AI tooling played a central role throughout the development of this system — not as a replacement for engineering judgment, but as a force multiplier that accelerated design iteration, surfaced edge cases early, and reduced boilerplate overhead.

### Prompt Engineering

Effective use of AI assistance required deliberate prompt construction. Rather than asking open-ended questions, prompts were scoped to specific components with explicit constraints. For example, when designing the ingestion endpoint, the prompt specified the idempotency requirement, the batch size limit, and the expected response schema upfront. This produced focused, directly usable output rather than generic patterns that needed heavy adaptation. Prompts for data model design included the full list of query patterns (staff exclusion, time-window filtering, per-store aggregation) so the AI could reason about index placement and column types in context.

### Code Generation

AI tooling was used to generate boilerplate-heavy components: SQLAlchemy model definitions, Pydantic v2 schema classes, Alembic migration stubs, and FastAPI router scaffolding. These are areas where the structure is well-defined but tedious to write correctly from scratch. The generated code was reviewed and adjusted — particularly around nullable fields, server defaults, and the `ON CONFLICT DO NOTHING` insert pattern, which required explicit correction to match PostgreSQL semantics.

The `TraceIDMiddleware` implementation was also AI-assisted. The prompt described the requirement (UUID v4 per request, injected into structlog context, added to response headers) and the generated implementation was validated against the FastAPI middleware lifecycle to confirm correct placement of `await call_next(request)`.

### Test Generation

Hypothesis property-based tests were co-developed with AI assistance. The process involved describing each correctness property in plain language (e.g., "for any set of events with mixed is_staff values, metrics counts must equal counts from non-staff events only") and asking the AI to translate that into a Hypothesis `@given` strategy. The AI was particularly useful for constructing composite strategies — for example, generating correlated event batches where visitor IDs, timestamps, and event types needed to be internally consistent. Manual review focused on ensuring strategies covered boundary conditions (zero visitors, all-staff batches, empty time windows).

### Design Review

AI tooling was used as a sounding board for architecture decisions before implementation. Key decisions reviewed this way included: whether to store staff events separately or filter at query time (query-time filtering won for audit trail preservation), how to handle conversion rate when POS records lack customer identity (temporal correlation was validated as the correct privacy-preserving approach), and whether the health endpoint should use a connection pool probe or a raw socket check (connection pool probe was chosen for consistency with the ORM layer).

---

## Architecture Decisions

### Idempotency via Database Unique Constraint

Idempotency for `POST /events/ingest` is enforced by a `UNIQUE` constraint on `events.event_id` combined with `INSERT ... ON CONFLICT (event_id) DO NOTHING`. This eliminates the check-then-insert race condition that would arise from application-level duplicate detection. Under concurrent ingestion from multiple pipeline workers, the database constraint guarantees exactly-once storage regardless of how many times the same event arrives.

### Staff Exclusion at Query Time

Staff events are stored in the same `events` table with `is_staff = TRUE` and excluded via `WHERE is_staff = FALSE` in every analytics query. The `is_staff` column carries a database index to keep this filter cheap. Storing staff events preserves the complete audit log — useful for debugging pipeline classification errors — while ensuring staff movement never contaminates customer metrics.

### Conversion Rate Without Customer Identity

POS records contain no `customer_id`. Conversion rate is computed as `COUNT(DISTINCT pos_records) / COUNT(DISTINCT visitor_id)` within a shared time window. This is a deliberate privacy-preserving design: the system never attempts to link a specific visitor token to a specific transaction. The temporal correlation approach is an approximation, but it is the correct one given the data available and the privacy constraints.

### Heatmap Normalization

Zone intensity is a weighted combination of `visit_count` and `avg_dwell_seconds`, both normalized to [0, 1] relative to the store maximum, then scaled to [0, 100]. When all zones have zero visits, all intensities are 0 — no division by zero. When any zone has visits, the maximum intensity is exactly 100 by construction. This ensures the heatmap always uses the full dynamic range when data is present.

### Health Endpoint Resilience

The health endpoint uses a short-timeout database probe (`SELECT 1`) wrapped in a broad exception handler. Any connectivity failure returns HTTP 503 with `{"status": "degraded", "db": "unavailable"}` rather than propagating an unhandled exception. This guarantees that monitoring systems always receive parseable JSON, even during database outages or network partitions. The endpoint never raises a 500.
