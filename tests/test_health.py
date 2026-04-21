# PROMPT: Generate unit tests for the health endpoint
# CHANGES MADE: Added tests for DB up (200), DB down (503), and STALE_FEED detection

import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

from fastapi.testclient import TestClient
from sqlalchemy import update

from app.main import app
from app.database import get_db, Base
from app.models import Event as EventModel
from tests.conftest import engine, TestingSessionLocal, override_get_db


def test_health_db_up():
    """DB is up: GET /health returns HTTP 200 with status ok and db ok."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"


def test_health_db_down():
    """DB is down: GET /health returns HTTP 503 with db unavailable."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    def broken_get_db():
        db = MagicMock()
        db.execute.side_effect = Exception("Connection refused")
        yield db

    app.dependency_overrides[get_db] = broken_get_db

    with TestClient(app) as client:
        response = client.get("/health")

    app.dependency_overrides[get_db] = override_get_db  # restore

    assert response.status_code == 503
    body = response.json()
    assert "status" in body
    assert body["db"] == "unavailable"


def test_health_stale_feed():
    """Store with last event > 10 min ago appears with feed_status STALE_FEED."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db

    db = TestingSessionLocal()
    stale_time = datetime.now(timezone.utc) - timedelta(minutes=15)
    event = EventModel(
        event_id=str(uuid.uuid4()),
        store_id="STORE_STALE",
        camera_id="CAM_1",
        visitor_id="VIS_abc123",
        event_type="ENTRY",
        timestamp=stale_time,
        is_staff=False,
        confidence=0.9,
    )
    db.add(event)
    db.commit()

    # Manually update ingested_at to be stale
    db.execute(
        update(EventModel)
        .where(EventModel.store_id == "STORE_STALE")
        .values(ingested_at=stale_time)
    )
    db.commit()
    db.close()

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    stores = body.get("stores", [])
    stale_store = next((s for s in stores if s["store_id"] == "STORE_STALE"), None)
    assert stale_store is not None
    assert stale_store["feed_status"] == "STALE_FEED"
