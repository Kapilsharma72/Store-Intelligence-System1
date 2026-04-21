# PROMPT: Generate property-based tests for the store intelligence ingestion endpoint
# CHANGES MADE: Added Property 8 (idempotent ingestion), Property 12 (validation error format), Property 13 (trace ID uniqueness), and unit tests for edge cases

from hypothesis import given, settings
from hypothesis import strategies as st
from tests.conftest import valid_event_strategy, invalid_event_strategy, engine, TestingSessionLocal, override_get_db
from app.models import Event as EventModel
from app.database import get_db, Base
from app.main import app
from fastapi.testclient import TestClient


# Feature: store-intelligence-system, Property 8: Idempotent ingestion
@given(st.lists(valid_event_strategy(), min_size=1, max_size=10))
@settings(max_examples=50)
def test_idempotent_ingestion(events):
    """Validates: Requirements 6.2"""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as client:
        response1 = client.post("/events/ingest", json=events)
        assert response1.status_code == 200

        response2 = client.post("/events/ingest", json=events)
        assert response2.status_code == 200

        db = TestingSessionLocal()
        unique_ids = len({e["event_id"] for e in events})
        db_count = db.query(EventModel).count()
        db.close()

        assert db_count == unique_ids


# Feature: store-intelligence-system, Property 12: Validation error format
@given(st.lists(invalid_event_strategy(), min_size=1, max_size=5))
@settings(max_examples=50)
def test_validation_error_format(events):
    """Validates: Requirements 6.4
    With per-event validation the endpoint returns HTTP 200 and places invalid
    events in the `rejected` list rather than failing the whole batch with 422.
    """
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as client:
        response = client.post("/events/ingest", json=events)
        # Per-event validation: invalid events are rejected, not the whole batch
        assert response.status_code == 200

        body = response.json()
        assert "rejected" in body
        assert isinstance(body["rejected"], list)
        # All events were invalid, so all should be rejected
        assert len(body["rejected"]) == len(events)
        for entry in body["rejected"]:
            assert "event_id" in entry
            assert "reason" in entry

        # Check no stack trace in response body
        body_str = response.text
        assert "Traceback" not in body_str
        assert 'File "' not in body_str


import re
UUID_PATTERN = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$', re.IGNORECASE)

# Feature: store-intelligence-system, Property 13: Trace ID uniqueness
@given(st.integers(min_value=2, max_value=20))
@settings(max_examples=30)
def test_trace_id_uniqueness(n):
    """Validates: Requirements 12.3"""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as client:
        trace_ids = []
        for _ in range(n):
            response = client.get("/health")
            assert response.status_code in (200, 503)
            trace_id = response.headers.get("X-Trace-ID")
            assert trace_id is not None
            assert UUID_PATTERN.match(trace_id), f"Invalid UUID v4: {trace_id}"
            trace_ids.append(trace_id)

        assert len(trace_ids) == len(set(trace_ids)), "Trace IDs are not unique"


# ---------------------------------------------------------------------------
# Unit tests for ingestion edge cases (Task 5.5)
# Requirements: 6.1, 6.2, 6.3
# ---------------------------------------------------------------------------

import uuid
from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.database import get_db, Base
from app.models import Event as EventModel
from tests.conftest import engine, TestingSessionLocal, override_get_db


def _valid_event(**overrides):
    """Return a minimal valid event dict."""
    base = {
        "event_id": str(uuid.uuid4()),
        "store_id": "STORE_001",
        "camera_id": "CAM_1",
        "visitor_id": "VIS_abc123",
        "event_type": "ENTRY",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "is_staff": False,
        "confidence": 0.9,
    }
    base.update(overrides)
    return base


def test_partial_success_batch():
    """Mix of valid and invalid events: valid ones ingested, invalid ones rejected."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db

    valid1 = _valid_event(event_id=str(uuid.uuid4()))
    valid2 = _valid_event(event_id=str(uuid.uuid4()))
    invalid = _valid_event(event_id=str(uuid.uuid4()), visitor_id="BAD_ID")  # fails pattern

    with TestClient(app) as client:
        response = client.post("/events/ingest", json=[valid1, valid2, invalid])

    assert response.status_code == 200
    body = response.json()
    assert body["ingested"] == 2
    assert len(body["rejected"]) == 1


def test_batch_exactly_500():
    """A batch of exactly 500 events must be accepted (HTTP 200)."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db

    events = [_valid_event(event_id=str(uuid.uuid4())) for _ in range(500)]

    with TestClient(app) as client:
        response = client.post("/events/ingest", json=events)

    assert response.status_code == 200
    assert response.json()["ingested"] == 500


def test_batch_501_rejected():
    """A batch of 501 events must be rejected with HTTP 422."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db

    events = [_valid_event(event_id=str(uuid.uuid4())) for _ in range(501)]

    with TestClient(app) as client:
        response = client.post("/events/ingest", json=events)

    assert response.status_code == 422


def test_duplicate_event_id_in_batch():
    """Two events with the same event_id in one batch → stored exactly once."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db

    shared_id = str(uuid.uuid4())
    event_a = _valid_event(event_id=shared_id)
    event_b = _valid_event(event_id=shared_id)

    with TestClient(app) as client:
        response = client.post("/events/ingest", json=[event_a, event_b])

    assert response.status_code == 200

    db = TestingSessionLocal()
    count = db.query(EventModel).filter(EventModel.event_id == shared_id).count()
    db.close()

    assert count == 1
