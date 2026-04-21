# PROMPT: Generate property-based tests for the store intelligence heatmap endpoint
# CHANGES MADE: Added Property 5 (heatmap intensity bounds)

import uuid
from datetime import datetime, timezone

from hypothesis import given, settings
from hypothesis import strategies as st
from fastapi.testclient import TestClient

from app.main import app
from app.database import get_db, Base
from tests.conftest import engine, override_get_db


# Feature: store-intelligence-system, Property 5: Heatmap intensity bounds
@given(
    st.lists(
        st.fixed_dictionaries({
            "zone_id": st.sampled_from(["ZONE_A", "ZONE_B", "ZONE_C"]),
            "count": st.integers(min_value=0, max_value=20),
        }),
        min_size=1,
        max_size=5,
    )
)
@settings(max_examples=50)
def test_heatmap_intensity_bounds(zone_configs):
    """Validates: Requirements 9.2, 9.3"""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db

    events = []
    for zc in zone_configs:
        for _ in range(zc["count"]):
            events.append({
                "event_id": str(uuid.uuid4()),
                "store_id": "STORE_HEAT",
                "camera_id": "CAM_1",
                "visitor_id": "VIS_abc123",
                "event_type": "ZONE_ENTER",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "zone_id": zc["zone_id"],
                "is_staff": False,
                "confidence": 0.9,
            })

    with TestClient(app) as client:
        if events:
            client.post("/events/ingest", json=events)
        response = client.get("/stores/STORE_HEAT/heatmap")

    assert response.status_code == 200
    body = response.json()
    zones = body["zones"]

    for z in zones:
        assert 0.0 <= z["intensity"] <= 100.0

    total_visits = sum(zc["count"] for zc in zone_configs)
    if total_visits > 0 and zones:
        max_intensity = max(z["intensity"] for z in zones)
        assert max_intensity == 100.0
