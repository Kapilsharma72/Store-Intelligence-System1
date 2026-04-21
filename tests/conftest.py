import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from hypothesis import strategies as st
import uuid
from datetime import datetime, timezone

from app.main import app
from app.database import get_db, Base
from app.models import EventType

TEST_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base.metadata.create_all(bind=engine)

def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

app.dependency_overrides[get_db] = override_get_db

@pytest.fixture
def test_client():
    # Reset DB for each test
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with TestClient(app) as client:
        yield client

@pytest.fixture
def db_session():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

def valid_event_strategy():
    return st.fixed_dictionaries({
        "event_id": st.uuids().map(str),
        "store_id": st.text(min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"), whitelist_characters="_")),
        "camera_id": st.text(min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"), whitelist_characters="_")),
        "visitor_id": st.from_regex(r"VIS_[a-f0-9]{6}", fullmatch=True),
        "event_type": st.sampled_from([e.value for e in EventType]),
        "timestamp": st.datetimes(timezones=st.just(timezone.utc)).map(lambda dt: dt.isoformat()),
        "is_staff": st.booleans(),
        "confidence": st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    })

def invalid_event_strategy():
    return st.one_of(
        # Wrong visitor_id pattern
        st.fixed_dictionaries({
            "event_id": st.uuids().map(str),
            "store_id": st.just("STORE_001"),
            "camera_id": st.just("CAM_1"),
            "visitor_id": st.text(min_size=1, max_size=20).filter(lambda s: not s.startswith("VIS_") or len(s) != 10),
            "event_type": st.just("ENTRY"),
            "timestamp": st.just(datetime.now(timezone.utc).isoformat()),
            "is_staff": st.just(False),
            "confidence": st.just(0.9),
        }),
        # Out-of-range confidence
        st.fixed_dictionaries({
            "event_id": st.uuids().map(str),
            "store_id": st.just("STORE_001"),
            "camera_id": st.just("CAM_1"),
            "visitor_id": st.just("VIS_abc123"),
            "event_type": st.just("ENTRY"),
            "timestamp": st.just(datetime.now(timezone.utc).isoformat()),
            "is_staff": st.just(False),
            "confidence": st.floats(min_value=1.001, max_value=100.0, allow_nan=False),
        }),
    )

def store_id_strategy():
    return st.text(min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"), whitelist_characters="_"))

def zone_layout_strategy():
    return st.just({
        "zones": [
            {"zone_id": "ZONE_A", "polygon": [[0,0],[1,0],[1,1],[0,1],[0,0]], "priority": 1},
            {"zone_id": "ZONE_B", "polygon": [[0.5,0.5],[1.5,0.5],[1.5,1.5],[0.5,1.5],[0.5,0.5]], "priority": 2},
        ]
    })
