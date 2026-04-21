from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    JSON,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.sql import func

from app.database import Base


class Event(Base):
    __tablename__ = "events"

    event_id = Column(String(36), primary_key=True)
    store_id = Column(String(50), index=True, nullable=False)
    camera_id = Column(String(50))
    visitor_id = Column(String(12), nullable=False)
    event_type = Column(String(30), nullable=False)
    timestamp = Column(DateTime(timezone=True), index=True, nullable=False)
    zone_id = Column(String(50), nullable=True)
    dwell_ms = Column(Integer, nullable=True)
    is_staff = Column(Boolean, default=False, index=True)
    confidence = Column(Float)
    metadata_ = Column("metadata", JSON)
    ingested_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("event_id"),)


class POSRecord(Base):
    __tablename__ = "pos_records"

    transaction_id = Column(String(36), primary_key=True)
    store_id = Column(String(50), index=True, nullable=False)
    timestamp = Column(DateTime(timezone=True), index=True, nullable=False)
    basket_value_inr = Column(Numeric(12, 2))


# ---------------------------------------------------------------------------
# Pydantic v2 schemas
# ---------------------------------------------------------------------------
from enum import Enum
from uuid import UUID
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field


class EventType(str, Enum):
    ENTRY = "ENTRY"
    EXIT = "EXIT"
    ZONE_ENTER = "ZONE_ENTER"
    ZONE_EXIT = "ZONE_EXIT"
    ZONE_DWELL = "ZONE_DWELL"
    BILLING_QUEUE_JOIN = "BILLING_QUEUE_JOIN"
    BILLING_QUEUE_ABANDON = "BILLING_QUEUE_ABANDON"
    REENTRY = "REENTRY"


class EventMetadata(BaseModel):
    queue_depth: Optional[int] = None
    sku_zone: Optional[str] = None
    session_seq: Optional[int] = None


class EventSchema(BaseModel):
    event_id: UUID
    store_id: str
    camera_id: str
    visitor_id: str = Field(pattern=r"^VIS_[a-f0-9]{6}$")
    event_type: EventType
    timestamp: datetime
    zone_id: Optional[str] = None
    dwell_ms: Optional[int] = None
    is_staff: bool = False
    confidence: float = Field(ge=0.0, le=1.0)
    metadata: Optional[EventMetadata] = None


class RejectedEvent(BaseModel):
    event_id: str
    reason: str


class IngestResponse(BaseModel):
    ingested: int
    rejected: List[RejectedEvent]


class MetricsResponse(BaseModel):
    store_id: str
    unique_visitors: int = 0
    conversion_rate: float = 0.0
    avg_dwell_seconds: float = 0.0
    queue_depth: int = 0
    abandonment_rate: float = 0.0


class FunnelStage(BaseModel):
    stage: str
    count: int
    drop_off_pct: Optional[float] = None


class FunnelResponse(BaseModel):
    store_id: str
    stages: List[FunnelStage]


class HeatmapZone(BaseModel):
    zone_id: str
    visit_count: int
    avg_dwell_seconds: float
    intensity: float


class HeatmapResponse(BaseModel):
    store_id: str
    zones: List[HeatmapZone]


class Anomaly(BaseModel):
    type: str
    severity: str
    timestamp: str
    description: str


class AnomalyResponse(BaseModel):
    store_id: str
    anomalies: List[Anomaly]


class StoreHealth(BaseModel):
    store_id: str
    feed_status: str
    last_event_timestamp: Optional[str] = None


class HealthResponse(BaseModel):
    status: str
    db: str
    stores: List[StoreHealth]
