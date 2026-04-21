# PROMPT: Generate property-based tests for the store intelligence metrics endpoint
# CHANGES MADE: Added Property 2 (staff exclusion), Property 3 (conversion rate bounds), and unit tests for metrics edge cases

from datetime import timezone

from hypothesis import given, settings
from hypothesis import strategies as st
from fastapi.testclient import TestClient

from app.main import app
from app.database import get_db, Base
from tests.conftest import engine, override_get_db


# Feature: store-intelligence-system, Property 2: Staff exclusion
@given(
    st.lists(
        st.fixed_dictionaries({
            "event_id": st.uuids().map(str),
            "store_id": st.just("STORE_TEST"),
            "camera_id": st.just("CAM_1"),
            "visitor_id": st.from_regex(r"VIS_[a-f0-9]{6}", fullmatch=True),
            "event_type": st.just("ENTRY"),
            "timestamp": st.datetimes(timezones=st.just(timezone.utc)).map(lambda dt: dt.isoformat()),
            "is_staff": st.booleans(),
            "confidence": st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
        }),
        min_size=1,
        max_size=20,
    )
)
@settings(max_examples=50)
def test_staff_exclusion(events):
    """Validates: Requirements 3.3, 7.4"""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as client:
        client.post("/events/ingest", json=events)
        response = client.get("/stores/STORE_TEST/metrics")

    assert response.status_code == 200
    body = response.json()

    # Count non-staff ENTRY events with distinct visitor_ids
    non_staff_visitors = len({e["visitor_id"] for e in events if not e["is_staff"] and e["event_type"] == "ENTRY"})
    assert body["unique_visitors"] == non_staff_visitors


import uuid
from datetime import datetime, timezone

# Feature: store-intelligence-system, Property 3: Conversion rate bounds
@given(
    st.lists(
        st.fixed_dictionaries({
            "event_id": st.uuids().map(str),
            "store_id": st.just("STORE_CONV"),
            "camera_id": st.just("CAM_1"),
            "visitor_id": st.from_regex(r"VIS_[a-f0-9]{6}", fullmatch=True),
            "event_type": st.just("ENTRY"),
            "timestamp": st.datetimes(timezones=st.just(timezone.utc)).map(lambda dt: dt.isoformat()),
            "is_staff": st.just(False),
            "confidence": st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
        }),
        min_size=0,
        max_size=10,
    )
)
@settings(max_examples=50)
def test_conversion_rate_bounds(events):
    """Validates: Requirements 7.2, 7.3"""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as client:
        if events:
            client.post("/events/ingest", json=events)
        response = client.get("/stores/STORE_CONV/metrics")

    assert response.status_code == 200
    body = response.json()
    cr = body["conversion_rate"]
    assert cr is not None
    assert isinstance(cr, float)
    assert 0.0 <= cr <= 1.0


def test_zero_visitor_store_returns_200_with_zeros():
    """Unknown store_id returns HTTP 200 with all zeros."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as client:
        response = client.get("/stores/UNKNOWN_STORE_XYZ/metrics")

    assert response.status_code == 200
    body = response.json()
    assert body["unique_visitors"] == 0
    assert body["conversion_rate"] == 0.0
    assert body["avg_dwell_seconds"] == 0.0
    assert body["queue_depth"] == 0
    assert body["abandonment_rate"] == 0.0


def test_queue_depth_from_join_abandon():
    """3 BILLING_QUEUE_JOIN and 1 BILLING_QUEUE_ABANDON → queue_depth == 2."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db

    now = datetime.now(timezone.utc)
    events = [
        {
            "event_id": str(uuid.uuid4()),
            "store_id": "STORE_QUEUE",
            "camera_id": "CAM_1",
            "visitor_id": f"VIS_{i:06x}",
            "event_type": "BILLING_QUEUE_JOIN",
            "timestamp": now.isoformat(),
            "is_staff": False,
            "confidence": 0.9,
        }
        for i in range(3)
    ] + [
        {
            "event_id": str(uuid.uuid4()),
            "store_id": "STORE_QUEUE",
            "camera_id": "CAM_1",
            "visitor_id": "VIS_000000",
            "event_type": "BILLING_QUEUE_ABANDON",
            "timestamp": now.isoformat(),
            "is_staff": False,
            "confidence": 0.9,
        }
    ]

    with TestClient(app) as client:
        client.post("/events/ingest", json=events)
        response = client.get("/stores/STORE_QUEUE/metrics")

    assert response.status_code == 200
    body = response.json()
    assert body["queue_depth"] == 2
