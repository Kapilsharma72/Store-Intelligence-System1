# PROMPT: Generate property-based and unit tests for the store intelligence funnel endpoint
# CHANGES MADE: Added Property 4 (funnel monotonicity), Property 9 (re-entry deduplication), and unit tests for funnel edge cases

import uuid
from datetime import datetime, timezone

from hypothesis import given, settings
from hypothesis import strategies as st
from fastapi.testclient import TestClient

from app.main import app
from app.database import get_db, Base
from tests.conftest import engine, override_get_db


def _make_event(event_type, visitor_id="VIS_abc123", store_id="STORE_FUNNEL", is_staff=False):
    return {
        "event_id": str(uuid.uuid4()),
        "store_id": store_id,
        "camera_id": "CAM_1",
        "visitor_id": visitor_id,
        "event_type": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "is_staff": is_staff,
        "confidence": 0.9,
    }


# Feature: store-intelligence-system, Property 4: Funnel monotonicity
@given(
    st.lists(
        st.fixed_dictionaries({
            "event_id": st.uuids().map(str),
            "store_id": st.just("STORE_MONO"),
            "camera_id": st.just("CAM_1"),
            "visitor_id": st.from_regex(r"VIS_[a-f0-9]{6}", fullmatch=True),
            "event_type": st.sampled_from(["ENTRY", "REENTRY", "ZONE_ENTER", "BILLING_QUEUE_JOIN"]),
            "timestamp": st.datetimes(timezones=st.just(timezone.utc)).map(lambda dt: dt.isoformat()),
            "is_staff": st.just(False),
            "confidence": st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
        }),
        min_size=0,
        max_size=20,
    )
)
@settings(max_examples=50)
def test_funnel_monotonicity(events):
    """Validates: Requirements 8.3 — ENTRY >= ZONE_VISIT >= BILLING_QUEUE >= PURCHASE"""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as client:
        if events:
            client.post("/events/ingest", json=events)
        response = client.get("/stores/STORE_MONO/funnel")

    assert response.status_code == 200
    body = response.json()
    stages = {s["stage"]: s["count"] for s in body["stages"]}

    assert stages["ENTRY"] >= stages["ZONE_VISIT"]
    assert stages["ZONE_VISIT"] >= stages["BILLING_QUEUE"]
    assert stages["BILLING_QUEUE"] >= stages["PURCHASE"]


# Feature: store-intelligence-system, Property 9: Re-entry deduplication
@given(
    st.integers(min_value=1, max_value=5),  # number of distinct visitors
    st.integers(min_value=1, max_value=5),  # number of ENTRY/REENTRY events per visitor
)
@settings(max_examples=50)
def test_reentry_deduplication(num_visitors, events_per_visitor):
    """Validates: Requirements 8.2 — visitor counted once per funnel stage regardless of ENTRY/REENTRY count"""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db

    events = []
    visitor_ids = [f"VIS_{i:06x}" for i in range(num_visitors)]
    for vid in visitor_ids:
        # First event is ENTRY, rest are REENTRY
        events.append(_make_event("ENTRY", visitor_id=vid, store_id="STORE_REENTRY"))
        for _ in range(events_per_visitor - 1):
            events.append(_make_event("REENTRY", visitor_id=vid, store_id="STORE_REENTRY"))

    with TestClient(app) as client:
        client.post("/events/ingest", json=events)
        response = client.get("/stores/STORE_REENTRY/funnel")

    assert response.status_code == 200
    body = response.json()
    stages = {s["stage"]: s["count"] for s in body["stages"]}

    # ENTRY count should equal unique visitor count, not total event count
    assert stages["ENTRY"] == num_visitors


def test_zero_entry_events_returns_200_with_zeros():
    """No ENTRY events → HTTP 200 with all stage counts = 0."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as client:
        response = client.get("/stores/STORE_EMPTY_FUNNEL/funnel")

    assert response.status_code == 200
    body = response.json()
    for stage in body["stages"]:
        assert stage["count"] == 0


def test_drop_off_percentage_calculation():
    """Known counts: 4 ENTRY, 2 ZONE_ENTER → drop_off_pct for ZONE_VISIT = 50.0"""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db

    events = [
        _make_event("ENTRY", visitor_id=f"VIS_{i:06x}", store_id="STORE_DROP") for i in range(4)
    ] + [
        _make_event("ZONE_ENTER", visitor_id=f"VIS_{i:06x}", store_id="STORE_DROP") for i in range(2)
    ]

    with TestClient(app) as client:
        client.post("/events/ingest", json=events)
        response = client.get("/stores/STORE_DROP/funnel")

    assert response.status_code == 200
    body = response.json()
    stages = {s["stage"]: s for s in body["stages"]}

    assert stages["ENTRY"]["count"] == 4
    assert stages["ZONE_VISIT"]["count"] == 2
    assert stages["ZONE_VISIT"]["drop_off_pct"] == 50.0
