from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, distinct
from typing import Optional
from datetime import datetime
import structlog

from app.database import get_db
from app.models import Event as EventModel, POSRecord, MetricsResponse

router = APIRouter()
logger = structlog.get_logger()


@router.get("/stores/{store_id}/metrics", response_model=MetricsResponse)
def get_metrics(
    store_id: str,
    start: Optional[datetime] = Query(None),
    end: Optional[datetime] = Query(None),
    db: Session = Depends(get_db),
):
    try:
        base_query = db.query(EventModel).filter(
            EventModel.store_id == store_id,
            EventModel.is_staff == False,
        )
        if start:
            base_query = base_query.filter(EventModel.timestamp >= start)
        if end:
            base_query = base_query.filter(EventModel.timestamp <= end)

        # unique_visitors: COUNT(DISTINCT visitor_id) from ENTRY events
        unique_visitors = (
            base_query.filter(EventModel.event_type == "ENTRY")
            .with_entities(func.count(distinct(EventModel.visitor_id)))
            .scalar() or 0
        )

        # conversion_rate: COUNT(DISTINCT pos_records) / COUNT(DISTINCT visitor_id)
        pos_query = db.query(POSRecord).filter(POSRecord.store_id == store_id)
        if start:
            pos_query = pos_query.filter(POSRecord.timestamp >= start)
        if end:
            pos_query = pos_query.filter(POSRecord.timestamp <= end)
        pos_count = pos_query.with_entities(func.count(POSRecord.transaction_id)).scalar() or 0
        conversion_rate = (pos_count / unique_visitors) if unique_visitors > 0 else 0.0
        conversion_rate = min(1.0, max(0.0, conversion_rate))

        # avg_dwell_seconds: mean of dwell_ms / 1000 from ZONE_DWELL events
        avg_dwell_ms = (
            base_query.filter(EventModel.event_type == "ZONE_DWELL")
            .with_entities(func.avg(EventModel.dwell_ms))
            .scalar()
        )
        avg_dwell_seconds = (avg_dwell_ms / 1000.0) if avg_dwell_ms is not None else 0.0

        # queue_depth: COUNT(BILLING_QUEUE_JOIN) - COUNT(BILLING_QUEUE_ABANDON), min 0
        queue_joins = (
            base_query.filter(EventModel.event_type == "BILLING_QUEUE_JOIN")
            .with_entities(func.count(EventModel.event_id))
            .scalar() or 0
        )
        queue_abandons = (
            base_query.filter(EventModel.event_type == "BILLING_QUEUE_ABANDON")
            .with_entities(func.count(EventModel.event_id))
            .scalar() or 0
        )
        queue_depth = max(0, queue_joins - queue_abandons)

        # abandonment_rate: COUNT(BILLING_QUEUE_ABANDON) / COUNT(BILLING_QUEUE_JOIN); 0.0 when no joins
        abandonment_rate = (queue_abandons / queue_joins) if queue_joins > 0 else 0.0

        return MetricsResponse(
            store_id=store_id,
            unique_visitors=unique_visitors,
            conversion_rate=conversion_rate,
            avg_dwell_seconds=avg_dwell_seconds,
            queue_depth=queue_depth,
            abandonment_rate=abandonment_rate,
        )
    except Exception as e:
        logger.error("metrics_error", store_id=store_id, error=str(e))
        return MetricsResponse(store_id=store_id)
