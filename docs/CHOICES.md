# Store Intelligence System — Key Technical Choices

This document records the significant architectural and technical decisions made during development, along with the reasoning behind each choice.

---

## 1. PostgreSQL over SQLite for Production

SQLite was retained as the local development and test database (via the `sqlite:///:memory:` fallback in `app/database.py`) because it requires no external process and makes the test suite self-contained. However, PostgreSQL is the production target for several concrete reasons.

The `ON CONFLICT (event_id) DO NOTHING` idempotency pattern relies on a `UNIQUE` constraint enforced at the database level. SQLite supports this syntax, but its locking model (database-level write lock) makes it unsuitable for concurrent ingestion from multiple pipeline workers. PostgreSQL's row-level locking and MVCC allow multiple writers to insert events simultaneously without serializing on a global lock.

The `metadata` column uses `JSONB` in PostgreSQL, which stores JSON in a binary format that supports indexing and efficient key-based queries. SQLite stores JSON as plain text. For future queries like "find all events where metadata.queue_depth > 5", JSONB provides a significant performance advantage.

PostgreSQL also provides `TIMESTAMPTZ` (timestamp with time zone), which stores UTC offsets correctly and handles daylight saving transitions. SQLite stores timestamps as text or integers, requiring application-level timezone handling. Given that the system ingests events from stores across multiple time zones, correct timezone semantics at the database layer reduces the risk of subtle aggregation bugs.

---

## 2. ByteTrack over DeepSORT for Person Tracking

The tracker selection came down to two candidates: ByteTrack (used via the ultralytics integration) and DeepSORT.

DeepSORT uses a deep appearance model (a re-identification network) to associate detections across frames. This produces high-quality track continuity in crowded scenes but requires a separate model inference pass per frame, adding latency and GPU memory overhead. In a retail environment with 5 cameras per store and 40 stores, the operational cost of running a re-ID model at inference time is significant.

ByteTrack takes a different approach: it uses motion-only association (Kalman filter + IoU matching) for high-confidence detections, and a second association pass for low-confidence detections that DeepSORT would discard. This "byte" strategy recovers occluded tracks without requiring appearance features. In practice, ByteTrack achieves comparable tracking accuracy to DeepSORT on pedestrian benchmarks while running faster and with lower memory usage.

For this system's use case — tracking individuals through a store over minutes, not seconds — ByteTrack's motion-based continuity is sufficient. The 60-second occlusion tolerance (≈ 900 frames at 15 fps) handles the common case of a customer briefly obscured by a shelf or another person.

---

## 3. Hypothesis for Property-Based Testing

The test strategy uses Hypothesis (Python's property-based testing library) alongside conventional example-based pytest tests. The two approaches are complementary rather than substitutes.

Example-based tests verify specific known cases: a batch of exactly 501 events returns 422, a store with zero visitors returns conversion_rate of 0.0, a health check against a down database returns 503. These are precise and fast.

Hypothesis tests verify universal invariants across large randomized input spaces. The system has several properties that must hold for *all* valid inputs, not just the examples a developer thinks to write: funnel stage counts must be monotonically non-increasing for any event set, heatmap intensity must be in [0, 100] for any zone distribution, visitor tokens must match the regex pattern for any (store_id, track_id, session_start) combination. Writing these as Hypothesis properties means the library generates hundreds of inputs automatically, including edge cases (empty strings, very large integers, Unicode store IDs) that manual test authoring would miss.

The `@settings(max_examples=100)` default was retained for CI speed. The `suppress_health_check` setting was used selectively where database fixture setup time caused Hypothesis's health check to fire incorrectly.

---

## 4. structlog over Standard Library logging

Python's standard `logging` module produces unstructured text output by default. Parsing log lines with regex to extract fields like `trace_id`, `store_id`, or `latency_ms` is fragile and breaks when message formats change.

structlog produces JSON-formatted log entries where every field is a named key-value pair. This makes logs directly queryable in log aggregation systems (Datadog, CloudWatch Logs Insights, Loki) without a parsing step. The `trace_id` field, injected by `TraceIDMiddleware` into structlog's thread-local context, appears automatically in every log line emitted during a request — including lines from deep within the call stack — without passing the trace ID explicitly through every function call.

structlog also supports processor pipelines, which allowed adding consistent fields (timestamp in ISO-8601, log level as a string, service name) to every entry without modifying individual log call sites.

---

## 5. Pydantic v2 Validation Approach

The system uses Pydantic v2 for all request and response schemas. The key design choices within Pydantic were:

`visitor_id` uses `Field(pattern=r"^VIS_[a-f0-9]{6}$")` to enforce the token format at the schema boundary. Invalid tokens are rejected with a structured 422 response before reaching any database code. This means the `events` table never contains malformed visitor IDs, which simplifies downstream analytics queries.

`confidence` uses `Field(ge=0.0, le=1.0)` to enforce the valid range. Out-of-range confidence values from a misconfigured pipeline are caught at ingestion time rather than silently corrupting metric calculations.

`EventMetadata` is a nested model rather than a raw `dict`. This provides type safety for the `queue_depth`, `sku_zone`, and `session_seq` fields stored in the JSONB column, and ensures that metadata serialization is consistent across all event types.

Response models (`MetricsResponse`, `FunnelResponse`, etc.) use explicit field defaults (0, 0.0, empty list) rather than `Optional` fields. This guarantees that API consumers always receive a complete, predictable response shape — no null-checking required for fields that represent counts or rates.
