#!/usr/bin/env python3
"""Smoke test assertions against a live Store Intelligence API instance."""
import os
import re
import sys
import uuid
from datetime import datetime, timezone

import httpx

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
UUID_V4_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

failures = []

def assert_that(name: str, condition: bool, detail: str = ""):
    if condition:
        print(f"  ✅ {name}")
    else:
        print(f"  ❌ {name}: {detail}")
        failures.append(f"{name}: {detail}")

def main():
    print(f"Running assertions against {API_BASE_URL}\n")
    client = httpx.Client(base_url=API_BASE_URL, timeout=10)
    
    # 1. Health endpoint returns 200
    print("1. Health endpoint returns 200")
    try:
        r = client.get("/health")
        assert_that("health returns 200", r.status_code == 200, f"got {r.status_code}")
    except Exception as e:
        assert_that("health returns 200", False, str(e))
    
    # 2. Ingest a valid event
    print("2. Ingest valid event")
    test_event_id = str(uuid.uuid4())
    valid_event = {
        "event_id": test_event_id,
        "store_id": "STORE_ASSERT_001",
        "camera_id": "CAM_1",
        "visitor_id": "VIS_abc123",
        "event_type": "ENTRY",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "is_staff": False,
        "confidence": 0.9,
    }
    try:
        r = client.post("/events/ingest", json=[valid_event])
        assert_that("ingest returns 200", r.status_code == 200, f"got {r.status_code}")
        body = r.json()
        assert_that("ingested count is 1", body.get("ingested") == 1, f"got {body.get('ingested')}")
    except Exception as e:
        assert_that("ingest valid event", False, str(e))
    
    # 3. Ingest same event again (idempotent)
    print("3. Ingest duplicate event (idempotent)")
    try:
        r = client.post("/events/ingest", json=[valid_event])
        assert_that("duplicate ingest returns 200", r.status_code == 200, f"got {r.status_code}")
        body = r.json()
        assert_that("duplicate ingested count is 1", body.get("ingested") == 1, f"got {body.get('ingested')}")
    except Exception as e:
        assert_that("ingest duplicate event", False, str(e))
    
    # 4. Metrics for unknown store returns 200 with zeros
    print("4. Metrics for unknown store")
    try:
        r = client.get("/stores/UNKNOWN_STORE_ASSERT_XYZ/metrics")
        assert_that("metrics returns 200", r.status_code == 200, f"got {r.status_code}")
        body = r.json()
        assert_that("metrics unique_visitors is 0", body.get("unique_visitors") == 0, f"got {body.get('unique_visitors')}")
    except Exception as e:
        assert_that("metrics for unknown store", False, str(e))
    
    # 5. Funnel for unknown store returns 200
    print("5. Funnel for unknown store")
    try:
        r = client.get("/stores/UNKNOWN_STORE_ASSERT_XYZ/funnel")
        assert_that("funnel returns 200", r.status_code == 200, f"got {r.status_code}")
    except Exception as e:
        assert_that("funnel for unknown store", False, str(e))
    
    # 6. Heatmap for unknown store returns 200
    print("6. Heatmap for unknown store")
    try:
        r = client.get("/stores/UNKNOWN_STORE_ASSERT_XYZ/heatmap")
        assert_that("heatmap returns 200", r.status_code == 200, f"got {r.status_code}")
        body = r.json()
        assert_that("heatmap zones is list", isinstance(body.get("zones"), list), f"got {type(body.get('zones'))}")
    except Exception as e:
        assert_that("heatmap for unknown store", False, str(e))
    
    # 7. Anomalies for unknown store returns 200 with empty list
    print("7. Anomalies for unknown store")
    try:
        r = client.get("/stores/UNKNOWN_STORE_ASSERT_XYZ/anomalies")
        assert_that("anomalies returns 200", r.status_code == 200, f"got {r.status_code}")
        body = r.json()
        assert_that("anomalies field is list", isinstance(body.get("anomalies"), list), f"got {type(body.get('anomalies'))}")
    except Exception as e:
        assert_that("anomalies for unknown store", False, str(e))
    
    # 8. Invalid payload returns 422 with loc+msg
    print("8. Invalid payload returns 422")
    invalid_event = {"event_id": "not-a-uuid", "visitor_id": "INVALID"}
    try:
        r = client.post("/events/ingest", json=[invalid_event])
        # With per-event validation, invalid events go to rejected list (200)
        # or the whole batch fails with 422 if the body itself is malformed
        body = r.json()
        if r.status_code == 422:
            assert_that("invalid payload 422 has detail", "detail" in body, f"body: {body}")
        else:
            # Per-event validation: check rejected list
            assert_that("invalid payload rejected", len(body.get("rejected", [])) > 0, f"body: {body}")
    except Exception as e:
        assert_that("invalid payload handling", False, str(e))
    
    # 9. X-Trace-ID header present and valid UUID v4
    print("9. X-Trace-ID header is UUID v4")
    try:
        r = client.get("/health")
        trace_id = r.headers.get("X-Trace-ID", "")
        assert_that("X-Trace-ID present", bool(trace_id), "header missing")
        assert_that("X-Trace-ID is UUID v4", bool(UUID_V4_PATTERN.match(trace_id)), f"got {trace_id!r}")
    except Exception as e:
        assert_that("X-Trace-ID check", False, str(e))
    
    # 10. Health response body has status field
    print("10. Health response body has status field")
    try:
        r = client.get("/health")
        body = r.json()
        assert_that("health body has status field", "status" in body, f"body keys: {list(body.keys())}")
    except Exception as e:
        assert_that("health body structure", False, str(e))
    
    print(f"\n{'='*50}")
    if failures:
        print(f"FAILED: {len(failures)} assertion(s) failed:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print(f"All 10 assertions passed.")
        sys.exit(0)

if __name__ == "__main__":
    main()
