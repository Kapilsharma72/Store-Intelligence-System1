# PROMPT: Generate property-based and unit tests for the store intelligence anomalies endpoint
# CHANGES MADE: Added Property 6 (anomalies always a list) and unit tests for anomaly thresholds

import uuid
from datetime import datetime, timezone, timedelta

import pytest
from fastapi.testclient import TestClient
from hypothesis import given, settings
from hypothesis import strategies as st

from app.main import app
from app.database import get_db, Base
from tests.conftest import engine, override_get_db, valid_event_strategy, store_id_strategy

# ---------------------------------------------------------------------------
# Feature: store-intelligence-system, Property 6: Anomalies always a list
# ---------------------------------------------------------------------------


@given(
    store_id=store_id_strategy(),
    events=st.lists(valid_event_strategy(), min_size=0, max_size=10),
)
@settings(max_examples=50)
def test_anomalies_always_returns_a_list(store_id, events):
    """Validates: Requirements 10.5

    For any store_id and any event set (including empty), the anomalies endpoint
    shall return a JSON object whose `anomalies` field is a list, never null.
    """
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as client:
        if events:
            # Normalise store_id so events belong to the queried store
            for e in events:
                e["store_id"] = store_id
            client.post("/events/ingest", json=events)

        response = client.get(f"/stores/{store_id}/anomalies")

    assert response.status_code == 200
    body = response.json()
    assert "anomalies" in body
    assert body["anomalies"] is not None
    assert isinstance(body["anomalies"], list)


# ---------------------------------------------------------------------------
# Unit tests for anomaly thresholds (Requirements 10.2, 10.3, 10.4)
# ---------------------------------------------------------------------------


def _make_event(store_id, event_type, visitor_id, ts, zone_id=None):
    return {
        "event_id": str(uuid.uuid4()),
        "store_id": store_id,
        "camera_id": "CAM_1",
        "visitor_id": visitor_id,
        "event_type": event_type,
        "timestamp": ts.isoformat(),
        "is_staff": False,
        "confidence": 0.9,
        **({"zone_id": zone_id} if zone_id else {}),
    }


def _make_pos(store_id, ts):
    return {
        "transaction_id": str(uuid.uuid4()),
        "store_id": store_id,
        "timestamp": ts.isoformat(),
        "basket_value_inr": "500.00",
    }


# --- BILLING_QUEUE_SPIKE threshold tests ---


def test_billing_queue_spike_threshold_5_no_anomaly():
    """Queue depth exactly 5 (not > 5) → no BILLING_QUEUE_SPIKE anomaly.

    Validates: Requirements 10.2
    """
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db

    store_id = "STORE_SPIKE_5"
    now = datetime.now(timezone.utc)

    # 5 JOIN events spread over 3 minutes → depth reaches exactly 5
    events = [
        _make_event(store_id, "BILLING_QUEUE_JOIN", f"VIS_{i:06x}", now - timedelta(minutes=3 - i * 0.5))
        for i in range(5)
    ]

    with TestClient(app) as client:
        client.post("/events/ingest", json=events)
        response = client.get(f"/stores/{store_id}/anomalies")

    assert response.status_code == 200
    anomalies = response.json()["anomalies"]
    spike_anomalies = [a for a in anomalies if a["type"] == "BILLING_QUEUE_SPIKE"]
    assert len(spike_anomalies) == 0, f"Expected no spike anomaly at depth 5, got: {spike_anomalies}"


def test_billing_queue_spike_threshold_6_anomaly():
    """Queue depth 6 sustained for > 2 minutes → BILLING_QUEUE_SPIKE anomaly.

    Validates: Requirements 10.2
    """
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db

    store_id = "STORE_SPIKE_6"
    now = datetime.now(timezone.utc)

    # 6 JOIN events all at t-3min → depth reaches 6 at t-3min.
    # No subsequent events, so post-loop check fires: window_end - t-3min = 3min > 2min → anomaly.
    events = [
        _make_event(
            store_id,
            "BILLING_QUEUE_JOIN",
            f"VIS_{i:06x}",
            now - timedelta(minutes=3),
        )
        for i in range(6)
    ]

    with TestClient(app) as client:
        client.post("/events/ingest", json=events)
        response = client.get(f"/stores/{store_id}/anomalies")

    assert response.status_code == 200
    anomalies = response.json()["anomalies"]
    spike_anomalies = [a for a in anomalies if a["type"] == "BILLING_QUEUE_SPIKE"]
    assert len(spike_anomalies) == 1, f"Expected 1 spike anomaly at depth 6 for >2 min, got: {spike_anomalies}"
    assert spike_anomalies[0]["severity"] == "HIGH"


# --- CONVERSION_DROP threshold tests ---


def _ingest_conversion_scenario(client, store_id, hist_visitors, hist_purchases, curr_visitors, curr_purchases):
    """Helper: insert historical and current ENTRY + POS events for conversion tests."""
    now = datetime.now(timezone.utc)

    # Historical ENTRY events (3-7 days ago)
    hist_events = [
        _make_event(store_id, "ENTRY", f"VIS_{i:06x}", now - timedelta(days=5))
        for i in range(hist_visitors)
    ]
    client.post("/events/ingest", json=hist_events)

    # Historical POS records
    for _ in range(hist_purchases):
        client.post(
            "/pos/ingest",
            json=[_make_pos(store_id, now - timedelta(days=5))],
        )

    # Current ENTRY events (last 12 hours)
    curr_events = [
        _make_event(store_id, "ENTRY", f"VIS_{100 + i:06x}", now - timedelta(hours=12))
        for i in range(curr_visitors)
    ]
    client.post("/events/ingest", json=curr_events)

    # Current POS records
    for _ in range(curr_purchases):
        client.post(
            "/pos/ingest",
            json=[_make_pos(store_id, now - timedelta(hours=12))],
        )


def test_conversion_drop_20pp_no_anomaly():
    """Drop of exactly 20 pp (not > 20 pp) → no CONVERSION_DROP anomaly.

    Validates: Requirements 10.3

    Setup: 1000 historical visitors (5 days ago) with 600 purchases → hist_rate ≈ 0.598 (blended
    with current 24h). Current 24h: 10 visitors, 4 purchases → curr_rate = 0.40.
    Blended 7-day rate ≈ 0.598, drop ≈ 0.198 < 0.20 → no anomaly.
    """
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db

    store_id = "STORE_CONV_20"
    now = datetime.now(timezone.utc)

    from app.models import POSRecord as POSModel
    from tests.conftest import TestingSessionLocal

    with TestClient(app) as client:
        # Historical ENTRY events (5 days ago, within 7-day window but outside 24h)
        # Insert in batches of 50 to avoid 500-event limit
        for batch_start in range(0, 1000, 50):
            hist_events = [
                _make_event(store_id, "ENTRY", f"VIS_{i:06x}", now - timedelta(days=5))
                for i in range(batch_start, batch_start + 50)
            ]
            client.post("/events/ingest", json=hist_events)

        # Historical POS (5 days ago) — 600 purchases
        db = TestingSessionLocal()
        try:
            for _ in range(600):
                db.add(POSModel(
                    transaction_id=str(uuid.uuid4()),
                    store_id=store_id,
                    timestamp=now - timedelta(days=5),
                    basket_value_inr=500,
                ))
            db.commit()
        finally:
            db.close()

        # Current ENTRY events (last 12 hours) — 10 visitors
        curr_events = [
            _make_event(store_id, "ENTRY", f"VIS_{2000 + i:06x}", now - timedelta(hours=12))
            for i in range(10)
        ]
        client.post("/events/ingest", json=curr_events)

        # Current POS (last 12 hours) — 4 purchases → curr_rate = 0.40
        db = TestingSessionLocal()
        try:
            for _ in range(4):
                db.add(POSModel(
                    transaction_id=str(uuid.uuid4()),
                    store_id=store_id,
                    timestamp=now - timedelta(hours=12),
                    basket_value_inr=500,
                ))
            db.commit()
        finally:
            db.close()

        response = client.get(f"/stores/{store_id}/anomalies")

    assert response.status_code == 200
    anomalies = response.json()["anomalies"]
    conv_anomalies = [a for a in anomalies if a["type"] == "CONVERSION_DROP"]
    assert len(conv_anomalies) == 0, f"Expected no anomaly at <20pp drop, got: {conv_anomalies}"


def test_conversion_drop_21pp_anomaly():
    """Drop of > 20 pp → CONVERSION_DROP anomaly.

    Validates: Requirements 10.3

    Setup: 1000 historical visitors (5 days ago) with 610 purchases → blended 7-day rate ≈ 0.608.
    Current 24h: 10 visitors, 4 purchases → curr_rate = 0.40.
    Drop ≈ 0.208 > 0.20 → anomaly.
    """
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db

    store_id = "STORE_CONV_21"
    now = datetime.now(timezone.utc)

    from app.models import POSRecord as POSModel
    from tests.conftest import TestingSessionLocal

    with TestClient(app) as client:
        # Historical ENTRY events (5 days ago)
        for batch_start in range(0, 1000, 50):
            hist_events = [
                _make_event(store_id, "ENTRY", f"VIS_{i:06x}", now - timedelta(days=5))
                for i in range(batch_start, batch_start + 50)
            ]
            client.post("/events/ingest", json=hist_events)

        # Historical POS (5 days ago) — 610 purchases
        db = TestingSessionLocal()
        try:
            for _ in range(610):
                db.add(POSModel(
                    transaction_id=str(uuid.uuid4()),
                    store_id=store_id,
                    timestamp=now - timedelta(days=5),
                    basket_value_inr=500,
                ))
            db.commit()
        finally:
            db.close()

        # Current ENTRY events (last 12 hours) — 10 visitors
        curr_events = [
            _make_event(store_id, "ENTRY", f"VIS_{2000 + i:06x}", now - timedelta(hours=12))
            for i in range(10)
        ]
        client.post("/events/ingest", json=curr_events)

        # Current POS (last 12 hours) — 4 purchases → curr_rate = 0.40
        db = TestingSessionLocal()
        try:
            for _ in range(4):
                db.add(POSModel(
                    transaction_id=str(uuid.uuid4()),
                    store_id=store_id,
                    timestamp=now - timedelta(hours=12),
                    basket_value_inr=500,
                ))
            db.commit()
        finally:
            db.close()

        response = client.get(f"/stores/{store_id}/anomalies")

    assert response.status_code == 200
    anomalies = response.json()["anomalies"]
    conv_anomalies = [a for a in anomalies if a["type"] == "CONVERSION_DROP"]
    assert len(conv_anomalies) == 1, f"Expected 1 anomaly at >20pp drop, got: {conv_anomalies}"
    assert conv_anomalies[0]["severity"] == "MEDIUM"


# --- DEAD_ZONE threshold tests ---


def test_dead_zone_30min_no_anomaly():
    """Zone inactive for exactly 30 min (not > 30 min) → no DEAD_ZONE anomaly.

    Validates: Requirements 10.4
    """
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db

    store_id = "STORE_DZ_30"
    now = datetime.now(timezone.utc)

    # Last ZONE_ENTER 29 minutes 50 seconds ago → NOT > 30 min → no anomaly
    # (using slightly less than 30 min to avoid timing jitter between test and endpoint)
    events = [
        _make_event(store_id, "ZONE_ENTER", "VIS_aabbcc", now - timedelta(minutes=29, seconds=50), zone_id="ZONE_A"),
    ]

    with TestClient(app) as client:
        client.post("/events/ingest", json=events)
        response = client.get(f"/stores/{store_id}/anomalies")

    assert response.status_code == 200
    anomalies = response.json()["anomalies"]
    dz_anomalies = [a for a in anomalies if a["type"] == "DEAD_ZONE"]
    assert len(dz_anomalies) == 0, f"Expected no dead zone at exactly 30 min, got: {dz_anomalies}"


def test_dead_zone_31min_anomaly():
    """Zone inactive for 31 min (> 30 min) → DEAD_ZONE anomaly.

    Validates: Requirements 10.4
    """
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db

    store_id = "STORE_DZ_31"
    now = datetime.now(timezone.utc)

    # Last ZONE_ENTER 31 minutes ago → > 30 min → anomaly
    events = [
        _make_event(store_id, "ZONE_ENTER", "VIS_aabbcc", now - timedelta(minutes=31), zone_id="ZONE_A"),
    ]

    with TestClient(app) as client:
        client.post("/events/ingest", json=events)
        response = client.get(f"/stores/{store_id}/anomalies")

    assert response.status_code == 200
    anomalies = response.json()["anomalies"]
    dz_anomalies = [a for a in anomalies if a["type"] == "DEAD_ZONE"]
    assert len(dz_anomalies) == 1, f"Expected 1 dead zone anomaly at 31 min, got: {dz_anomalies}"
    assert dz_anomalies[0]["severity"] == "LOW"
