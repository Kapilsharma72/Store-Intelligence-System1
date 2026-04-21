# Requirements Document

## Introduction

The Store Intelligence System converts raw CCTV footage from Apex Retail's physical stores into real-time business analytics. The system processes video feeds through a detection and tracking pipeline, emits structured events, exposes a REST API for querying analytics, and renders a live dashboard. The north star metric is Conversion Rate: unique visitors who made a purchase divided by total unique visitors. The system covers 40 stores, each with multiple camera angles (Entry, Main Floor, Billing), and integrates with POS transaction data to compute purchase-correlated metrics.

The system is composed of four major parts:
- Part A: Detection Pipeline — video ingestion, person detection, tracking, zone mapping, staff classification, event emission
- Part B: Intelligence API — event ingestion, metrics, funnel, heatmap, anomaly detection, health endpoints
- Part C: Production Readiness — containerization, observability, testing
- Part D: AI Engineering Documentation

An optional Part E adds a live Streamlit dashboard with simulation mode.

---

## Glossary

- **System**: The Store Intelligence System as a whole
- **Pipeline**: The video processing component (Part A) responsible for detection, tracking, and event emission
- **API**: The FastAPI-based REST service (Part B) that ingests events and serves analytics
- **Dashboard**: The Streamlit-based live visualization component (Part E)
- **Visitor**: A unique customer detected in a store, identified by a Visitor_Token
- **Visitor_Token**: A unique identifier assigned to a detected person, formatted as `VIS_` followed by 6 alphanumeric characters derived from an MD5 hash
- **Staff**: A store employee identified by uniform color (HSV detection) or rule-based fallback; excluded from all customer metrics
- **Zone**: A named area within a store defined in store_layout.json with polygon boundaries and camera coverage metadata
- **Event**: A structured JSON record emitted by the Pipeline or ingested via the API, representing a discrete visitor action
- **POS_Record**: A point-of-sale transaction record containing store_id, transaction_id, timestamp, and basket_value_inr; contains no customer_id
- **Conversion_Rate**: The ratio of unique visitors who made a purchase to total unique visitors in a given time window
- **Dwell_Time**: The duration in seconds a Visitor spends within a Zone
- **Funnel**: The ordered sequence of visitor stages: ENTRY → ZONE_VISIT → BILLING_QUEUE → PURCHASE
- **Heatmap**: A per-zone summary of visit_count and avg_dwell normalized to a 0–100 scale
- **Anomaly**: A detected deviation from normal store behavior, classified as BILLING_QUEUE_SPIKE, CONVERSION_DROP, or DEAD_ZONE
- **Trace_ID**: A unique identifier attached to every API request for distributed tracing
- **STALE_FEED**: A health condition where no events have been received for a store in more than 10 minutes
- **Simulate_Mode**: A pipeline execution mode that replays sample_events.jsonl at 10× speed without requiring real video input
- **ByteTrack**: The multi-object tracking algorithm used to maintain consistent identities across video frames
- **YOLOv8**: The object detection model used to detect people in video frames

---

## Requirements

### Requirement 1: Person Detection

**User Story:** As a retail analyst, I want the system to detect people in CCTV footage, so that I can track visitor behavior across store zones.

#### Acceptance Criteria

1. WHEN a video frame is processed, THE Pipeline SHALL detect all visible persons using a YOLOv8 model.
2. WHEN a person is detected across consecutive frames, THE Pipeline SHALL assign and maintain a consistent identity using ByteTrack multi-object tracking.
3. WHEN a new unique person is first detected, THE Pipeline SHALL assign a Visitor_Token in the format `VIS_` followed by exactly 6 alphanumeric characters derived from an MD5 hash of the track identity.
4. THE Pipeline SHALL process video input at 1080p resolution and 15 frames per second.
5. WHEN faces are present in video frames, THE Pipeline SHALL process the footage without storing, logging, or transmitting any facial data.

---

### Requirement 2: Zone Mapping

**User Story:** As a store manager, I want visitor positions mapped to named zones, so that I can understand which areas of the store attract the most traffic.

#### Acceptance Criteria

1. WHEN a person's position is determined in a frame, THE Pipeline SHALL map that position to a Zone using the polygon boundaries defined in store_layout.json.
2. THE Pipeline SHALL use the Shapely library to perform point-in-polygon zone membership tests.
3. WHEN a person's position falls within overlapping camera coverage areas, THE Pipeline SHALL resolve the zone assignment using the camera priority order defined in store_layout.json.
4. WHEN store_layout.json is loaded, THE Pipeline SHALL validate that all zone polygon coordinates form closed, non-self-intersecting polygons and raise a configuration error if validation fails.

---

### Requirement 3: Staff Classification

**User Story:** As a retail analyst, I want staff members excluded from customer metrics, so that analytics reflect only genuine customer behavior.

#### Acceptance Criteria

1. WHEN a person is detected, THE Pipeline SHALL classify the person as Staff or Visitor using HSV color range detection on the detected bounding box region.
2. IF HSV color detection produces a confidence score below the configured threshold, THEN THE Pipeline SHALL apply a rule-based fallback classifier using movement pattern heuristics.
3. WHEN a person is classified as Staff, THE Pipeline SHALL exclude all events associated with that person from customer-facing metrics, funnel calculations, heatmap data, and anomaly detection.
4. THE Pipeline SHALL emit events for Staff detections into a separate staff event stream that does not contribute to any customer analytics endpoint.

---

### Requirement 4: Event Emission

**User Story:** As a data engineer, I want the pipeline to emit structured events for every significant visitor action, so that downstream systems can build analytics from a reliable event log.

#### Acceptance Criteria

1. WHEN a Visitor crosses the store entry boundary, THE Pipeline SHALL emit an ENTRY event containing visitor_token, store_id, camera_id, timestamp, and zone_id.
2. WHEN a Visitor's last detection is in the exit zone and no further detections occur within 30 seconds, THE Pipeline SHALL emit an EXIT event.
3. WHEN a Visitor's position transitions from outside a Zone to inside a Zone, THE Pipeline SHALL emit a ZONE_ENTER event.
4. WHEN a Visitor's position transitions from inside a Zone to outside a Zone, THE Pipeline SHALL emit a ZONE_EXIT event.
5. WHEN a Visitor has remained within a single Zone for a duration exceeding the configured dwell threshold, THE Pipeline SHALL emit a ZONE_DWELL event containing the accumulated Dwell_Time in seconds.
6. WHEN a Visitor joins the billing queue zone, THE Pipeline SHALL emit a BILLING_QUEUE_JOIN event.
7. WHEN a Visitor who previously joined the billing queue departs the billing zone without a corresponding POS transaction within the configured timeout window, THE Pipeline SHALL emit a BILLING_QUEUE_ABANDON event.
8. WHEN a Visitor_Token that previously received an EXIT event is detected again in the store, THE Pipeline SHALL emit a REENTRY event.
9. THE Pipeline SHALL emit all events as newline-delimited JSON records conforming to the schema defined in sample_events.jsonl.
10. THE Pipeline SHALL assign a monotonically increasing sequence number to each emitted event within a processing session to support deduplication.

---

### Requirement 5: Edge Case Handling

**User Story:** As a pipeline engineer, I want the system to handle real-world detection edge cases gracefully, so that analytics remain accurate under imperfect video conditions.

#### Acceptance Criteria

1. WHEN multiple persons enter the store simultaneously through the same entry point, THE Pipeline SHALL assign distinct Visitor_Tokens to each person and emit separate ENTRY events for each.
2. WHEN a Staff member moves through customer zones, THE Pipeline SHALL not emit ZONE_ENTER or ZONE_EXIT events attributable to customer metrics for that Staff member.
3. WHEN a Visitor temporarily disappears from all camera frames for fewer than 60 seconds and reappears, THE Pipeline SHALL reassign the same Visitor_Token rather than issuing a new one.
4. WHEN a person is partially occluded in a frame, THE Pipeline SHALL continue tracking using the last known bounding box position and ByteTrack's Kalman filter prediction until the person reappears or the occlusion timeout expires.
5. WHEN more than 5 persons are detected in the billing zone simultaneously, THE Pipeline SHALL emit a BILLING_QUEUE_SPIKE anomaly event.
6. WHEN no persons are detected in any zone for a continuous period exceeding 5 minutes, THE Pipeline SHALL record an empty store period in the event log without emitting spurious events.
7. WHEN the same physical area is covered by more than one camera, THE Pipeline SHALL deduplicate person detections using spatial overlap thresholds to prevent double-counting.

---

### Requirement 6: Event Ingestion API

**User Story:** As a backend engineer, I want a reliable event ingestion endpoint, so that pipeline output can be stored and queried without data loss or duplication.

#### Acceptance Criteria

1. THE API SHALL expose a `POST /events/ingest` endpoint that accepts a JSON array of up to 500 events per request.
2. WHEN the same event is submitted more than once with the same event_id, THE API SHALL store the event exactly once and return a success response without error.
3. WHEN a batch contains a mix of valid and invalid events, THE API SHALL ingest all valid events, reject only the invalid ones, and return a partial success response listing the rejected event_ids with reasons.
4. WHEN an event payload fails schema validation, THE API SHALL return HTTP 422 with a structured error body containing the field path and violation description, and SHALL NOT return a raw stack trace.
5. THE API SHALL persist ingested events to PostgreSQL as the primary store, with SQLite accepted as a fallback for local development.

---

### Requirement 7: Store Metrics Endpoint

**User Story:** As a retail analyst, I want to query aggregated store metrics, so that I can monitor store performance in real time.

#### Acceptance Criteria

1. THE API SHALL expose a `GET /stores/{store_id}/metrics` endpoint that returns unique_visitors, conversion_rate, avg_dwell_seconds, queue_depth, and abandonment_rate for the requested store.
2. WHEN the requested store has no recorded events, THE API SHALL return HTTP 200 with all metric fields set to zero or null and SHALL NOT return an error response.
3. WHEN computing conversion_rate, THE API SHALL divide the count of unique Visitor_Tokens with a correlated POS transaction by the total count of unique Visitor_Tokens, excluding Staff.
4. WHEN computing avg_dwell_seconds, THE API SHALL use only ZONE_DWELL events from non-Staff visitors.
5. WHEN computing queue_depth, THE API SHALL return the current count of Visitor_Tokens in the billing zone based on the most recent BILLING_QUEUE_JOIN and BILLING_QUEUE_ABANDON events.

---

### Requirement 8: Conversion Funnel Endpoint

**User Story:** As a retail analyst, I want to see the visitor conversion funnel, so that I can identify where customers drop off before making a purchase.

#### Acceptance Criteria

1. THE API SHALL expose a `GET /stores/{store_id}/funnel` endpoint that returns visitor counts and drop-off percentages for each stage: ENTRY, ZONE_VISIT, BILLING_QUEUE, and PURCHASE.
2. WHEN computing funnel stage counts, THE API SHALL deduplicate Visitor_Tokens so that a Visitor who re-enters the store is counted only once per funnel stage per session.
3. WHEN computing drop-off percentages, THE API SHALL express each stage's drop-off as a percentage of the preceding stage's count.
4. WHEN a store has no ENTRY events, THE API SHALL return HTTP 200 with all funnel stage counts set to zero.

---

### Requirement 9: Zone Heatmap Endpoint

**User Story:** As a store manager, I want a normalized heatmap of zone activity, so that I can identify high-traffic and underperforming areas.

#### Acceptance Criteria

1. THE API SHALL expose a `GET /stores/{store_id}/heatmap` endpoint that returns a list of zones, each with visit_count, avg_dwell_seconds, and a normalized intensity score.
2. WHEN computing the normalized intensity score, THE API SHALL scale visit_count and avg_dwell values to a 0–100 range relative to the maximum observed values across all zones in the store.
3. WHEN all zones have zero visits, THE API SHALL return HTTP 200 with all intensity scores set to 0.

---

### Requirement 10: Anomaly Detection Endpoint

**User Story:** As a store operations manager, I want the system to surface anomalies automatically, so that I can respond to operational issues without manually monitoring dashboards.

#### Acceptance Criteria

1. THE API SHALL expose a `GET /stores/{store_id}/anomalies` endpoint that returns a list of detected anomalies with type, severity, timestamp, and description.
2. WHEN the billing queue depth exceeds 5 persons for more than 2 consecutive minutes, THE API SHALL classify and return a BILLING_QUEUE_SPIKE anomaly.
3. WHEN the conversion_rate for a store drops more than 20 percentage points below the 7-day rolling average, THE API SHALL classify and return a CONVERSION_DROP anomaly.
4. WHEN a Zone records zero ZONE_ENTER events during store open hours for a continuous period exceeding 30 minutes, THE API SHALL classify and return a DEAD_ZONE anomaly for that zone.
5. WHEN no anomalies are detected, THE API SHALL return HTTP 200 with an empty anomalies array.

---

### Requirement 11: Health Endpoint

**User Story:** As a DevOps engineer, I want a health endpoint that reports system and feed status, so that I can detect infrastructure and data pipeline failures quickly.

#### Acceptance Criteria

1. THE API SHALL expose a `GET /health` endpoint that returns HTTP 200 when all subsystems are operational.
2. WHEN the database connection is unavailable, THE API SHALL return HTTP 503 with a structured error body indicating the database connectivity failure.
3. WHEN no events have been received for a store in more than 10 minutes during store open hours, THE API SHALL include a STALE_FEED status for that store in the health response.
4. THE API SHALL report per-store feed status including last_event_timestamp and feed_status for every configured store.

---

### Requirement 12: Observability and Error Handling

**User Story:** As a backend engineer, I want structured logging and safe error responses, so that I can diagnose issues in production without exposing internal implementation details.

#### Acceptance Criteria

1. THE API SHALL emit structured JSON log entries using structlog for every request, including trace_id, store_id where applicable, endpoint path, HTTP status code, and latency_ms.
2. WHEN an unhandled exception occurs in any API endpoint, THE API SHALL return HTTP 500 with a structured JSON error body containing a trace_id and a human-readable message, and SHALL NOT include a raw stack trace in the response body.
3. THE API SHALL assign a unique Trace_ID to every incoming request and include it in both the response headers and the structured log entry.

---

### Requirement 13: Containerization

**User Story:** As a DevOps engineer, I want the entire system to start with a single command, so that reviewers and operators can run the system without manual configuration steps.

#### Acceptance Criteria

1. THE System SHALL provide a docker-compose.yml that starts the API, PostgreSQL database, and Dashboard services with no manual steps beyond running `docker compose up`.
2. THE System SHALL provide a Dockerfile.api that builds the API service into a self-contained image.
3. WHEN `docker compose up` is executed in a clean environment, THE System SHALL be fully operational within 120 seconds, including database migrations.
4. THE System SHALL provide a `.env.example` file listing all required environment variables with example values and descriptions.

---

### Requirement 14: Test Coverage

**User Story:** As a quality engineer, I want automated tests covering the core system behaviors, so that regressions are caught before deployment.

#### Acceptance Criteria

1. THE System SHALL include pytest test suites covering event ingestion, metrics computation, funnel calculation, and anomaly detection.
2. THE System SHALL achieve a minimum of 70% line coverage across the `app/` module as measured by pytest-cov.
3. WHEN the assertions defined in assertions.py are executed against a running API instance, THE API SHALL pass all 10 assertions.
4. THE System SHALL include a PROMPT block and a CHANGES MADE block at the top of every test file, documenting the AI-assisted generation process.

---

### Requirement 15: AI Engineering Documentation

**User Story:** As a hiring reviewer, I want documentation of key design decisions and AI-assisted development choices, so that I can evaluate the candidate's engineering judgment.

#### Acceptance Criteria

1. THE System SHALL include a docs/DESIGN.md file containing an AI-Assisted Decisions section of no fewer than 250 words describing how AI tooling was used during development.
2. THE System SHALL include a docs/CHOICES.md file covering at least 3 key architectural or technical decisions, each with rationale, with a total word count of no fewer than 250 words.
3. WHEN docs/DESIGN.md or docs/CHOICES.md is absent or below the minimum word count, THE System SHALL be considered non-compliant with Part D requirements.

---

### Requirement 16: Simulation Mode

**User Story:** As a developer, I want to run the pipeline without real video files, so that I can test the full system end-to-end using sample data.

#### Acceptance Criteria

1. WHEN the Pipeline is invoked with the `--simulate` flag, THE Pipeline SHALL replay events from sample_events.jsonl at 10 times the original event timestamp rate without requiring any video files to be present.
2. WHEN running in Simulate_Mode, THE Pipeline SHALL emit events to the same output stream as normal video processing mode, maintaining schema compatibility.
3. WHEN the CCTV Videos folder is empty and `--simulate` is not specified, THE Pipeline SHALL log a warning and exit gracefully without raising an unhandled exception.

---

### Requirement 17: Live Dashboard

**User Story:** As a store manager, I want a live dashboard that auto-refreshes, so that I can monitor store performance without manually reloading the page.

#### Acceptance Criteria

1. THE Dashboard SHALL display Conversion_Rate, unique_visitors, avg_dwell_seconds, queue_depth, and abandonment_rate for each store, refreshing automatically at a configurable interval of no less than 5 seconds.
2. THE Dashboard SHALL visualize the Zone Heatmap as a color-coded grid with zone names and intensity scores.
3. THE Dashboard SHALL display the Conversion Funnel as a sequential stage chart with drop-off percentages between stages.
4. WHEN the API returns a STALE_FEED status for a store, THE Dashboard SHALL display a visible warning indicator for that store.
5. WHERE the Dashboard is deployed alongside the API via docker-compose, THE Dashboard SHALL connect to the API using the service hostname defined in docker-compose.yml without requiring manual URL configuration.
