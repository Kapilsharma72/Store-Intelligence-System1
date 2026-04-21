# Implementation Plan: Store Intelligence System

## Overview

Incremental build of the Store Intelligence System following the prescribed build order: project scaffold → Docker → data models → FastAPI skeleton → each endpoint → tests → detection pipeline → dashboard → documentation → README → final integration check.

Each task builds on the previous. All code is Python 3.11 with FastAPI, SQLAlchemy, Pydantic v2, Hypothesis PBT, and Docker Compose.

---

## Tasks

- [x] 1. Scaffold project structure and initialize repository
  - Create the full folder tree: `app/`, `pipeline/`, `dashboard/`, `tests/`, `docs/`, `data/clips/`, `data/events/`, `data/sample/`
  - Create empty `__init__.py` files in `app/`, `pipeline/`, `tests/`
  - Run `git init` and create `.gitignore` (Python, venv, `*.pyc`, `data/clips/`, `.env`)
  - Create `.env.example` with `POSTGRES_USER`, `POSTGRES_PASSWORD`, `DATABASE_URL`, `API_BASE_URL`, `YOLO_MODEL_PATH`, `DWELL_THRESHOLD_SECONDS`, `QUEUE_ABANDON_TIMEOUT_SECONDS`
  - Create `requirements.txt` with pinned versions: `fastapi`, `uvicorn[standard]`, `sqlalchemy`, `alembic`, `psycopg2-binary`, `pydantic[email]>=2`, `structlog`, `pytest`, `pytest-cov`, `hypothesis`, `ultralytics`, `shapely`, `opencv-python-headless`, `streamlit`, `httpx`
  - _Requirements: 13.1, 13.4_

- [x] 2. Create Docker Compose and Dockerfiles
  - [x] 2.1 Write `docker-compose.yml` with three services: `db` (postgres:15-alpine with healthcheck), `api` (depends on db healthy), `dashboard` (depends on api)
    - Include named volume `pgdata`, environment variable references from `.env`
    - Map ports 8000 (api) and 8501 (dashboard)
    - _Requirements: 13.1, 13.3_
  - [x] 2.2 Write `Dockerfile.api`
    - Base `python:3.11-slim`, copy `requirements.txt`, `pip install`, copy source, CMD runs `alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000`
    - _Requirements: 13.2, 13.3_
  - [x] 2.3 Write `Dockerfile.dashboard`
    - Base `python:3.11-slim`, install streamlit + httpx, copy `dashboard/`, CMD runs `streamlit run dashboard/app.py --server.port 8501 --server.address 0.0.0.0`
    - _Requirements: 13.1, 17.5_

- [x] 3. Implement database models and Alembic migrations
  - [x] 3.1 Write `app/database.py`
    - Create SQLAlchemy `engine` and `SessionLocal` from `DATABASE_URL` env var (fallback to SQLite `./store_intelligence.db` for local dev)
    - Expose `get_db()` dependency and `Base`
    - _Requirements: 6.5_
  - [x] 3.2 Write `app/models.py` — SQLAlchemy ORM models
    - `Event` table: `event_id` (UUID PK), `store_id` (VARCHAR 50, indexed), `camera_id`, `visitor_id` (VARCHAR 12), `event_type` (VARCHAR 30), `timestamp` (TIMESTAMPTZ, indexed), `zone_id`, `dwell_ms`, `is_staff` (BOOLEAN default False, indexed), `confidence` (FLOAT), `metadata_` (JSONB), `ingested_at` (server default NOW())
    - `POSRecord` table: `transaction_id` (UUID PK), `store_id` (indexed), `timestamp` (indexed), `basket_value_inr` (NUMERIC 12,2)
    - Add `UniqueConstraint` on `events.event_id`
    - _Requirements: 6.2, 6.5_
  - [x] 3.3 Write `app/models.py` — Pydantic v2 schemas
    - `EventType` enum: ENTRY, EXIT, ZONE_ENTER, ZONE_EXIT, ZONE_DWELL, BILLING_QUEUE_JOIN, BILLING_QUEUE_ABANDON, REENTRY
    - `EventMetadata` model: `queue_depth: int | None`, `sku_zone: str | None`, `session_seq: int | None`
    - `EventSchema` with `visitor_id: str = Field(pattern=r"^VIS_[a-f0-9]{6}$")`, `confidence: float = Field(ge=0.0, le=1.0)`, all required fields
    - `IngestResponse`, `RejectedEvent`, `MetricsResponse`, `FunnelStage`, `FunnelResponse`, `HeatmapZone`, `HeatmapResponse`, `Anomaly`, `AnomalyResponse`, `StoreHealth`, `HealthResponse`
    - _Requirements: 1.3, 6.1, 6.4, 7.1, 8.1, 9.1, 10.1, 11.1_
  - [x] 3.4 Initialize Alembic and create initial migration
    - Run `alembic init alembic`, configure `alembic.ini` to use `DATABASE_URL` env var
    - Generate migration for `events` and `pos_records` tables
    - _Requirements: 13.3_

- [x] 4. Implement FastAPI application skeleton with all 6 routes
  - [x] 4.1 Write `app/main.py`
    - Create FastAPI app with title "Store Intelligence API"
    - Implement `TraceIDMiddleware`: generate UUID v4 per request, inject into request state, add `X-Trace-ID` response header, bind to structlog context
    - Register global exception handler for unhandled exceptions → HTTP 500 `{"trace_id": ..., "message": "Internal server error"}` (no stack trace)
    - Configure structlog for JSON output with `timestamp`, `level`, `trace_id`, `endpoint`, `method`, `status_code`, `latency_ms`
    - Include routers from `ingestion`, `metrics`, `funnel`, `heatmap`, `anomalies`, `health` modules (stubs returning placeholder data)
    - _Requirements: 12.1, 12.2, 12.3_
  - [x] 4.2 Create stub router files returning placeholder 200 responses
    - `app/ingestion.py`: `POST /events/ingest` → `{"ingested": 0, "rejected": []}`
    - `app/metrics.py`: `GET /stores/{store_id}/metrics` → zero-value MetricsResponse
    - `app/funnel.py`: `GET /stores/{store_id}/funnel` → zero-value FunnelResponse
    - `app/heatmap.py`: `GET /stores/{store_id}/heatmap` → empty HeatmapResponse
    - `app/anomalies.py`: `GET /stores/{store_id}/anomalies` → empty AnomalyResponse
    - `app/health.py`: `GET /health` → `{"status": "ok", "db": "ok", "stores": []}`
    - _Requirements: 6.1, 7.1, 8.1, 9.1, 10.1, 11.1_

- [x] 5. Implement `POST /events/ingest` — idempotent batch ingestion
  - [x] 5.1 Implement ingestion logic in `app/ingestion.py`
    - Accept `list[EventSchema]` (max 500 items; return 422 if exceeded)
    - Validate each event; collect validation failures with `loc` + `msg`
    - For valid events: `INSERT INTO events ... ON CONFLICT (event_id) DO NOTHING`
    - Return `{"ingested": int, "rejected": [{"event_id": str, "reason": str}]}`
    - Never return raw stack traces; all exceptions caught and logged with trace_id
    - _Requirements: 6.1, 6.2, 6.3, 6.4_
  - [x] 5.2 Write property test for idempotent ingestion (Property 8)
    - `# PROMPT:` and `# CHANGES MADE:` blocks at top of `tests/test_ingestion.py`
    - `# Feature: store-intelligence-system, Property 8: Idempotent ingestion`
    - Use `@given` with `st.lists(valid_event_strategy(), min_size=1)` — submit same batch twice, assert DB record count equals unique event_id count
    - _Requirements: 6.2_
  - [x] 5.3 Write property test for validation error format (Property 12)
    - `# Feature: store-intelligence-system, Property 12: Validation error format`
    - Generate invalid payloads (wrong visitor_id pattern, missing fields, out-of-range confidence); assert HTTP 422 with `loc` + `msg` in response, no stack trace
    - _Requirements: 6.4_
  - [x] 5.4 Write property test for Trace ID uniqueness (Property 13)
    - `# Feature: store-intelligence-system, Property 13: Trace ID uniqueness`
    - Generate N requests (N drawn from 2–20); assert all `X-Trace-ID` values are distinct UUID v4 strings
    - _Requirements: 12.3_
  - [x] 5.5 Write unit tests for ingestion edge cases
    - Partial success batch (mix of valid + invalid events)
    - Batch of exactly 500 events (accepted) and 501 events (rejected with 422)
    - Duplicate event_id in same batch → stored once
    - _Requirements: 6.1, 6.2, 6.3_

- [x] 6. Implement `GET /health`
  - [x] 6.1 Implement health logic in `app/health.py`
    - Probe DB with short-timeout `SELECT 1`; catch all exceptions → `db: "unavailable"`, HTTP 503
    - Query last `ingested_at` per `store_id`; mark `STALE_FEED` if > 10 minutes ago
    - Always return valid JSON with `status` field; never propagate unhandled exceptions
    - _Requirements: 11.1, 11.2, 11.3, 11.4_
  - [x] 6.2 Write unit tests for health endpoint
    - DB up → HTTP 200, `status: "ok"`
    - DB down (mock engine to raise) → HTTP 503, `status: "degraded"`, structured body
    - Store with last event > 10 min ago → `feed_status: "STALE_FEED"`
    - _Requirements: 11.1, 11.2, 11.3_

- [x] 7. Implement `GET /stores/{store_id}/metrics`
  - [x] 7.1 Implement metrics computation in `app/metrics.py`
    - All queries include `WHERE is_staff = FALSE`
    - `unique_visitors`: `COUNT(DISTINCT visitor_id)` from ENTRY events
    - `conversion_rate`: `COUNT(DISTINCT pos_records) / COUNT(DISTINCT visitor_id)` in time window; return 0.0 when no visitors (never divide by zero, never null)
    - `avg_dwell_seconds`: mean of `dwell_ms / 1000` from ZONE_DWELL events
    - `queue_depth`: `COUNT(BILLING_QUEUE_JOIN) - COUNT(BILLING_QUEUE_ABANDON)` for active sessions
    - `abandonment_rate`: `COUNT(BILLING_QUEUE_ABANDON) / COUNT(BILLING_QUEUE_JOIN)`; 0.0 when no joins
    - Return HTTP 200 with zeros for unknown store_id (never 404)
    - Accept optional `start` and `end` ISO datetime query params
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5_
  - [x] 7.2 Write property test for staff exclusion (Property 2)
    - `# Feature: store-intelligence-system, Property 2: Staff exclusion`
    - Generate random event batches with mixed `is_staff` values; ingest; assert metrics/funnel/heatmap counts equal counts computed from non-staff events only
    - _Requirements: 3.3, 7.4_
  - [x] 7.3 Write property test for conversion rate bounds (Property 3)
    - `# Feature: store-intelligence-system, Property 3: Conversion rate bounds`
    - Generate random event + POS record sets; assert `conversion_rate` is float in [0.0, 1.0], not null, endpoint returns 200 including when visitor count is zero
    - _Requirements: 7.2, 7.3_
  - [x] 7.4 Write unit tests for metrics edge cases
    - Zero-visitor store returns HTTP 200 with all zeros
    - Queue depth computed correctly from join/abandon sequences
    - _Requirements: 7.2, 7.5_

- [x] 8. Implement `GET /stores/{store_id}/funnel`
  - [x] 8.1 Implement funnel computation in `app/funnel.py`
    - Stage counts (non-staff only):
      - ENTRY: `COUNT(DISTINCT visitor_id)` from ENTRY + REENTRY events, deduplicated per visitor per session
      - ZONE_VISIT: `COUNT(DISTINCT visitor_id)` from ZONE_ENTER events
      - BILLING_QUEUE: `COUNT(DISTINCT visitor_id)` from BILLING_QUEUE_JOIN events
      - PURCHASE: `COUNT(DISTINCT pos_records)` correlated by time window
    - REENTRY deduplication: visitor counted once per stage regardless of ENTRY/REENTRY event count
    - `drop_off_pct` = `(prev_count - curr_count) / prev_count * 100`; null for ENTRY stage
    - Return HTTP 200 with zeros when no ENTRY events
    - _Requirements: 8.1, 8.2, 8.3, 8.4_
  - [x] 8.2 Write property test for funnel monotonicity (Property 4)
    - `# Feature: store-intelligence-system, Property 4: Funnel monotonicity`
    - Generate random event sets; assert `ENTRY >= ZONE_VISIT >= BILLING_QUEUE >= PURCHASE` for all generated inputs
    - _Requirements: 8.3_
  - [x] 8.3 Write property test for re-entry deduplication (Property 9)
    - `# Feature: store-intelligence-system, Property 9: Re-entry deduplication`
    - Generate visitor sequences with multiple ENTRY and REENTRY events for the same visitor_id; assert funnel ENTRY count equals unique visitor count, not event count
    - _Requirements: 8.2_
  - [x] 8.4 Write unit tests for funnel edge cases
    - Zero ENTRY events → HTTP 200 with all zeros
    - Drop-off percentage calculation with known counts
    - _Requirements: 8.3, 8.4_

- [x] 9. Implement `GET /stores/{store_id}/heatmap`
  - [x] 9.1 Implement heatmap computation in `app/heatmap.py`
    - Aggregate `visit_count` and `avg_dwell_seconds` per `zone_id` (non-staff only)
    - Normalize intensity: weighted combination of visit_count and avg_dwell, both scaled to [0,1] relative to store maximum, then × 100
    - When all zones have zero visits: all intensities = 0 (no division by zero)
    - When any zone has visits: max intensity across all zones = exactly 100
    - _Requirements: 9.1, 9.2, 9.3_
  - [x] 9.2 Write property test for heatmap intensity bounds (Property 5)
    - `# Feature: store-intelligence-system, Property 5: Heatmap intensity bounds`
    - Generate random zone visit count distributions; assert all `intensity` values in [0, 100] and max = 100 when any zone has visits
    - _Requirements: 9.2, 9.3_

- [x] 10. Implement `GET /stores/{store_id}/anomalies`
  - [x] 10.1 Implement anomaly detection in `app/anomalies.py`
    - `BILLING_QUEUE_SPIKE`: queue depth > 5 for > 2 consecutive minutes → severity HIGH
    - `CONVERSION_DROP`: current conversion_rate drops > 20 pp below 7-day rolling average → severity MEDIUM
    - `DEAD_ZONE`: zone with zero ZONE_ENTER events during store hours for > 30 continuous minutes → severity LOW
    - Always return `{"anomalies": [...]}` — never null; empty list when no anomalies
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5_
  - [x] 10.2 Write property test for anomalies always returns a list (Property 6)
    - `# Feature: store-intelligence-system, Property 6: Anomalies always a list`
    - Generate random store_ids and event sets (including empty); assert response `anomalies` field is always a list, never null
    - _Requirements: 10.5_
  - [x] 10.3 Write unit tests for anomaly thresholds
    - BILLING_QUEUE_SPIKE: queue depth exactly 5 (no anomaly) vs 6 for 2+ min (anomaly)
    - CONVERSION_DROP: drop of exactly 20 pp (no anomaly) vs 21 pp (anomaly)
    - DEAD_ZONE: zone inactive for exactly 30 min (no anomaly) vs 31 min (anomaly)
    - _Requirements: 10.2, 10.3, 10.4_

- [x] 11. Write full test suite with Hypothesis PBT
  - [x] 11.1 Write `tests/conftest.py`
    - SQLite in-memory test database fixture using `create_engine("sqlite:///:memory:")`
    - `test_client` fixture using FastAPI `TestClient` with DB override
    - Shared Hypothesis strategies: `valid_event_strategy()`, `invalid_event_strategy()`, `store_id_strategy()`, `zone_layout_strategy()`
    - _Requirements: 14.1_
  - [x] 11.2 Write property test for visitor token format (Property 1) in `tests/test_pipeline.py`
    - `# PROMPT:` and `# CHANGES MADE:` blocks at top
    - `# Feature: store-intelligence-system, Property 1: Visitor token format`
    - `@given(st.text(), st.integers(), st.text())` → assert `make_visitor_token(...)` matches `^VIS_[a-f0-9]{6}$`
    - _Requirements: 1.3_
  - [x] 11.3 Write property test for session sequence monotonicity (Property 10) in `tests/test_pipeline.py`
    - `# Feature: store-intelligence-system, Property 10: Session seq monotonicity`
    - Generate random visitor event sequences; assert `session_seq` values are strictly increasing per visitor per session
    - _Requirements: 4.10_
  - [x] 11.4 Write property test for zone mapping correctness (Property 11) in `tests/test_pipeline.py`
    - `# Feature: store-intelligence-system, Property 11: Zone mapping correctness`
    - Generate random points and polygon layouts with overlapping zones and priority assignments; assert `map_to_zone` returns highest-priority zone or None
    - _Requirements: 2.1, 2.3_
  - [x] 11.5 Verify test coverage ≥ 70% across `app/`
    - Run `pytest --cov=app --cov-report=term-missing tests/` and confirm ≥ 70% line coverage
    - Add targeted unit tests to close any gaps below threshold
    - _Requirements: 14.2_

- [x] 12. Checkpoint — all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 13. Implement detection pipeline
  - [x] 13.1 Implement `pipeline/detect.py`
    - `Detection` dataclass: `bbox: tuple[float,float,float,float]`, `confidence: float`, `class_id: int`
    - `detect_persons(frame: np.ndarray, conf_threshold: float = 0.4) -> list[Detection]`
    - Load YOLOv8 model from `YOLO_MODEL_PATH` env var; filter to class 0 (person) above threshold
    - Log detection count per frame via structlog
    - _Requirements: 1.1_
  - [x] 13.2 Implement `pipeline/tracker.py`
    - `TrackedPerson` dataclass: `track_id: int`, `bbox: tuple`, `is_lost: bool`, `frames_lost: int`
    - `update_tracks(detections: list[Detection]) -> list[TrackedPerson]`
    - Wrap ByteTrack (ultralytics); maintain state across calls; handle occlusion up to 60 s (≈ 900 frames at 15 fps)
    - Emit EXIT event when `frames_lost` exceeds occlusion threshold
    - _Requirements: 1.2, 5.3, 5.4_
  - [x] 13.3 Implement `pipeline/zone_mapper.py`
    - `StoreLayout` and `ZoneConfig` dataclasses loaded from `store_layout.json`
    - `load_layout(path: str) -> StoreLayout`: validate all polygons are closed and non-self-intersecting; raise `ConfigurationError` on failure
    - `map_to_zone(point: tuple[float,float], layout: StoreLayout) -> str | None`: return highest-priority zone_id containing point, or None
    - Handle overlapping zones via camera priority order
    - _Requirements: 2.1, 2.2, 2.3, 2.4_
  - [x] 13.4 Implement `pipeline/staff_classifier.py`
    - `HSVConfig` dataclass: `lower: tuple`, `upper: tuple`, `threshold: float`
    - `ClassificationResult` dataclass: `is_staff: bool`, `confidence: float`, `method: str`
    - `classify(frame: np.ndarray, bbox: tuple, hsv_config: HSVConfig) -> ClassificationResult`
    - Stage 1: HSV color detection on bbox region; Stage 2: heuristic fallback if confidence < threshold
    - _Requirements: 3.1, 3.2_
  - [x] 13.5 Implement `pipeline/emit.py`
    - `make_visitor_token(store_id: str, track_id: int, session_start: str) -> str`: `"VIS_" + md5(f"{store_id}_{track_id}_{session_start}").hexdigest()[:6]`
    - `emit_event(event_type, visitor_id, store_id, camera_id, zone_id, **kwargs) -> Event`: assign UUID v4 `event_id`, UTC ISO-8601 `timestamp`, monotonically increasing `session_seq`
    - Write events as newline-delimited JSON to `data/events/{store_id}.jsonl`
    - Handle REENTRY detection: check if visitor_id previously received EXIT event
    - _Requirements: 1.3, 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8, 4.9, 4.10_
  - [x] 13.6 Implement `pipeline/run.sh`
    - Accept `--simulate` flag: replay `data/sample/sample_events.jsonl` at 10× speed via `emit.py`
    - Without `--simulate`: iterate over `data/clips/*.mp4`, process each with detect → track → zone_map → classify → emit pipeline
    - When `data/clips/` is empty and `--simulate` not set: log warning and exit 0
    - POST emitted events to `API_BASE_URL/events/ingest` in batches of up to 500
    - _Requirements: 16.1, 16.2, 16.3_

- [x] 14. Implement Streamlit dashboard
  - [x] 14.1 Implement `dashboard/app.py`
    - Connect to `API_BASE_URL` from env var (no manual URL config)
    - Auto-refresh at configurable interval (default 10 s, minimum 5 s) using `st.rerun()`
    - Metrics panel: display `conversion_rate`, `unique_visitors`, `avg_dwell_seconds`, `queue_depth`, `abandonment_rate` per store
    - Heatmap panel: color-coded grid with zone names and intensity scores (0–100)
    - Funnel panel: sequential stage chart with drop-off percentages
    - Health panel: show STALE_FEED warning indicator per store when API reports it
    - _Requirements: 17.1, 17.2, 17.3, 17.4, 17.5_

- [x] 15. Write AI engineering documentation
  - [x] 15.1 Write `docs/DESIGN.md`
    - Include "AI-Assisted Decisions" section of ≥ 250 words describing how AI tooling was used during development (prompt engineering, code generation, test generation, design review)
    - Cover architecture decisions: idempotency via DB constraint, staff exclusion at query time, conversion rate without customer identity, heatmap normalization, health endpoint resilience
    - _Requirements: 15.1, 15.3_
  - [x] 15.2 Write `docs/CHOICES.md`
    - Cover ≥ 3 key architectural/technical decisions with rationale, total ≥ 250 words
    - Suggested topics: PostgreSQL vs SQLite trade-offs, ByteTrack vs DeepSORT, Hypothesis PBT strategy, structlog vs standard logging, Pydantic v2 validation approach
    - _Requirements: 15.2, 15.3_

- [x] 16. Write `README.md` with 5-command quickstart
  - Include 5-command quickstart: `git clone`, `cp .env.example .env`, `docker compose up --build`, `curl http://localhost:8000/health`, `open http://localhost:8501`
  - Document all API endpoints with example curl commands
  - Document pipeline usage: `--simulate` flag and video file mode
  - Document test execution: `pytest --cov=app tests/`
  - _Requirements: 13.1, 13.4_

- [x] 17. Write `assertions.py` smoke test script
  - Implement 10 behavioral assertions against a live API instance at `API_BASE_URL`
  - Cover: health 200, ingest valid event, ingest duplicate (idempotent), metrics 200 with zeros for unknown store, funnel 200 with zeros, heatmap 200, anomalies returns list, 422 on invalid payload with loc+msg, X-Trace-ID present and UUID v4, health 503 body has status field
  - Script exits 0 if all pass, 1 with failure details if any fail
  - _Requirements: 14.3_

- [x] 18. Final integration checkpoint
  - Ensure all tests pass, ask the user if questions arise.
  - Verify `pytest --cov=app --cov-report=term-missing tests/` reports ≥ 70% coverage
  - Verify `docker compose up --build` starts all three services within 120 s
  - Verify `python assertions.py` passes all 10 assertions against the running stack
  - Verify `docs/DESIGN.md` and `docs/CHOICES.md` each exceed 250 words
  - Verify every test file has `# PROMPT:` and `# CHANGES MADE:` blocks at top
  - _Requirements: 13.3, 14.2, 14.3, 14.4, 15.3_

---

## Notes

- Tasks marked with `*` are optional and can be skipped for a faster MVP
- Each task references specific requirements for traceability
- Property tests use Hypothesis with minimum 100 iterations per property
- All property test files include `# Feature: store-intelligence-system, Property N: <text>` tags
- Staff exclusion (`WHERE is_staff = FALSE`) must be applied in every analytics query
- `POST /events/ingest` uses `INSERT ... ON CONFLICT (event_id) DO NOTHING` — never check-then-insert
- Raw stack traces must never appear in API response bodies; use structlog for server-side logging only
- SQLite is acceptable for local dev/test; PostgreSQL is required for Docker deployment
