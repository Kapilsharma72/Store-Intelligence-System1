# Store Intelligence System

Real-time retail analytics from CCTV footage. Detects visitors, tracks movement through store zones, computes conversion rates, dwell times, funnel drop-off, heatmaps, and anomalies — all exposed via a REST API and live Streamlit dashboard.

---

## Table of Contents

- [How It Works](#how-it-works)
- [Quick Start](#quick-start)
- [Uploading Videos](#uploading-videos)
- [Getting Analytics Data](#getting-analytics-data)
- [API Reference](#api-reference)
- [Running Tests](#running-tests)
- [Environment Variables](#environment-variables)
- [Project Structure](#project-structure)

---

## How It Works

```
CCTV Videos (.mp4)
      │
      ▼
 Detection Pipeline
 (YOLOv8 + ByteTrack)
      │  detects & tracks persons, maps to zones, classifies staff vs visitor
      ▼
 POST /events/ingest  ──►  FastAPI + PostgreSQL
                                  │
                    ┌─────────────┼─────────────┐
                    ▼             ▼             ▼
               /metrics       /funnel       /heatmap
               /anomalies     /health
                    │
                    ▼
           Streamlit Dashboard  (port 8501)
```

---

## Quick Start

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) and Docker Compose
- Git

### 1. Clone and configure

```bash
git clone https://github.com/Kapilsharma72/Store-Intelligence-System1.git
cd Store-Intelligence-System1
cp .env.example .env
```

The default `.env` works out of the box for local Docker Compose. No changes needed to get started.

### 2. Start the stack

```bash
docker compose up --build
```

This starts three services:
- `db` — PostgreSQL 15 on port 5432
- `api` — FastAPI on **http://localhost:8000**
- `dashboard` — Streamlit on **http://localhost:8501**

Wait ~30 seconds for the database to initialize and migrations to run.

### 3. Verify it's running

```bash
curl http://localhost:8000/health
```

Expected response:
```json
{"status": "ok", "db": "ok", "stores": []}
```

Open the dashboard: **http://localhost:8501**

---

## Uploading Videos

### Option A — Process real CCTV videos (full pipeline)

1. Place your `.mp4` video files in the `data/clips/` directory:

```
data/
  clips/
    CAM_1.mp4
    CAM_2.mp4
    ...
```

2. Run the pipeline (requires the API to be running):

```bash
# With Docker Compose running:
docker compose exec api bash pipeline/run.sh

# Or locally (with Python dependencies installed):
bash pipeline/run.sh
```

The pipeline will:
- Detect persons in each video using YOLOv8
- Track them across frames with ByteTrack
- Emit `ENTRY`, `ZONE_ENTER`, `ZONE_DWELL`, and `EXIT` events
- POST all events to the API in batches of up to 500

The store ID is derived from the video filename (e.g., `CAM_1.mp4` → store `CAM_1`).

### Option B — Simulate with sample events (no video required)

Replay the bundled sample events at 10× speed:

```bash
bash pipeline/run.sh --simulate
```

This replays `data/sample/sample_events.jsonl` and POSTs events to the API. Use this to explore the dashboard without any video files.

### Option C — Ingest events directly via API

POST events directly to the ingest endpoint (useful for testing or custom integrations):

```bash
curl -X POST http://localhost:8000/events/ingest \
  -H "Content-Type: application/json" \
  -d '[{
    "event_id": "550e8400-e29b-41d4-a716-446655440000",
    "store_id": "STORE_001",
    "camera_id": "CAM_ENTRY",
    "visitor_id": "VIS_a1b2c3",
    "event_type": "ENTRY",
    "timestamp": "2024-01-15T10:00:00+00:00",
    "is_staff": false,
    "confidence": 0.92
  }]'
```

Supported `event_type` values: `ENTRY`, `EXIT`, `ZONE_ENTER`, `ZONE_EXIT`, `ZONE_DWELL`, `BILLING_QUEUE_JOIN`, `BILLING_QUEUE_ABANDON`, `REENTRY`

---

## Getting Analytics Data

### Via the Dashboard

Open **http://localhost:8501** in your browser. The dashboard auto-refreshes every 10 seconds and shows:

- System health and feed status per store
- Store metrics (visitors, conversion rate, dwell time, queue depth)
- Conversion funnel with drop-off percentages
- Zone heatmap with intensity visualization
- Active anomalies with severity levels

### Via the API

All endpoints return JSON. Replace `STORE_001` with your actual store ID.

**Store Metrics**
```bash
curl "http://localhost:8000/stores/STORE_001/metrics"
```
```json
{
  "store_id": "STORE_001",
  "unique_visitors": 142,
  "conversion_rate": 0.34,
  "avg_dwell_seconds": 312.5,
  "queue_depth": 3,
  "abandonment_rate": 0.12
}
```

**Visitor Funnel**
```bash
curl "http://localhost:8000/stores/STORE_001/funnel"
```
```json
{
  "store_id": "STORE_001",
  "stages": [
    {"stage": "ENTRY",         "count": 142, "drop_off_pct": null},
    {"stage": "ZONE_VISIT",    "count": 118, "drop_off_pct": 16.9},
    {"stage": "BILLING_QUEUE", "count":  52, "drop_off_pct": 55.93},
    {"stage": "PURCHASE",      "count":  48, "drop_off_pct": 7.69}
  ]
}
```

**Zone Heatmap**
```bash
curl "http://localhost:8000/stores/STORE_001/heatmap"
```
```json
{
  "store_id": "STORE_001",
  "zones": [
    {"zone_id": "MAIN_FLOOR", "visit_count": 98, "avg_dwell_seconds": 245.0, "intensity": 100.0},
    {"zone_id": "ELECTRONICS", "visit_count": 41, "avg_dwell_seconds": 180.0, "intensity": 47.3}
  ]
}
```

**Anomalies**
```bash
curl "http://localhost:8000/stores/STORE_001/anomalies"
```
```json
{
  "store_id": "STORE_001",
  "anomalies": [
    {
      "type": "BILLING_QUEUE_SPIKE",
      "severity": "HIGH",
      "timestamp": "2024-01-15T14:22:10+00:00",
      "description": "Queue depth 8 exceeded 5 for more than 2 minutes"
    }
  ]
}
```

Anomaly types: `BILLING_QUEUE_SPIKE` (HIGH), `CONVERSION_DROP` (MEDIUM), `DEAD_ZONE` (LOW).

**Filter by time window** (supported on metrics, funnel, heatmap, anomalies):
```bash
curl "http://localhost:8000/stores/STORE_001/metrics?start=2024-01-15T09:00:00Z&end=2024-01-15T18:00:00Z"
```

### Via the Smoke Test Script

Run behavioral assertions against a live API:

```bash
python assertions.py
```

Exits 0 if all 10 assertions pass, 1 with failure details otherwise.

---

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | System health + per-store feed status |
| POST | `/events/ingest` | Ingest a batch of events (up to 500) |
| GET | `/stores/{store_id}/metrics` | Visitor count, conversion rate, dwell time, queue |
| GET | `/stores/{store_id}/funnel` | Conversion funnel with drop-off percentages |
| GET | `/stores/{store_id}/heatmap` | Zone visit counts and intensity scores |
| GET | `/stores/{store_id}/anomalies` | Detected anomalies in the time window |

Every response includes an `X-Trace-ID` header (UUID v4) for request tracing.
Unknown store IDs return 200 with zero-value responses (never 404).

---

## Running Tests

```bash
# Install dependencies
pip install -r requirements.txt

# Run all tests
pytest tests/

# With coverage report
pytest --cov=app --cov-report=term-missing tests/
```

The test suite uses both example-based tests and property-based tests (Hypothesis). All 29 tests should pass.

---

## Environment Variables

Copy `.env.example` to `.env` and adjust as needed:

| Variable | Description | Default |
|----------|-------------|---------|
| `POSTGRES_USER` | PostgreSQL username | `store_user` |
| `POSTGRES_PASSWORD` | PostgreSQL password | `store_password` |
| `DATABASE_URL` | Full SQLAlchemy DB URL | SQLite fallback for local dev |
| `API_BASE_URL` | API base URL for pipeline + dashboard | `http://localhost:8000` |
| `YOLO_MODEL_PATH` | Path to YOLOv8 model weights | `yolov8n.pt` |
| `DWELL_THRESHOLD_SECONDS` | Seconds before emitting ZONE_DWELL | `30` |
| `QUEUE_ABANDON_TIMEOUT_SECONDS` | Seconds before marking queue abandon | `120` |

> **Note:** The `yolov8n.pt` model file is not included in the repository (large binary). It will be auto-downloaded by `ultralytics` on first run, or you can download it manually from [Ultralytics](https://github.com/ultralytics/assets/releases).

---

## Project Structure

```
app/              FastAPI application
  main.py         App entry point, middleware, routers
  models.py       SQLAlchemy models + Pydantic schemas
  database.py     DB engine and session factory
  ingestion.py    POST /events/ingest
  metrics.py      GET /stores/{id}/metrics
  funnel.py       GET /stores/{id}/funnel
  heatmap.py      GET /stores/{id}/heatmap
  anomalies.py    GET /stores/{id}/anomalies
  health.py       GET /health

pipeline/         Detection pipeline
  detect.py       YOLOv8 person detection
  tracker.py      ByteTrack multi-object tracking
  zone_mapper.py  Map bounding box centroids to store zones
  staff_classifier.py  HSV-based staff uniform detection
  emit.py         Structured event emission + JSONL writing
  run_cctv.py     Main pipeline runner for video files
  process_video.py  Single-video processing script
  simulate.py     Replay sample events at N× speed
  run.sh          Shell entry point (--simulate flag)

dashboard/
  app.py          Streamlit live dashboard

tests/            pytest + Hypothesis test suite
alembic/          Database migrations
data/
  clips/          Input video files (.mp4) — gitignored
  events/         Emitted event logs (.jsonl)
  sample/         Sample events for simulate mode
docs/             Design rationale and architecture notes
assertions.py     Smoke test script (10 behavioral assertions)
docker-compose.yml
Dockerfile.api
Dockerfile.dashboard
requirements.txt
```
