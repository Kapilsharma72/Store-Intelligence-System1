from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, distinct
from typing import Optional
from datetime import datetime
import structlog

from app.database import get_db
from app.models import Event as EventModel, POSRecord, FunnelResponse, FunnelStage

router = APIRouter()
logger = structlog.get_logger()


@router.get("/stores/{store_id}/funnel", response_model=FunnelResponse)
def get_funnel(
    store_id: str,
    start: Optional[datetime] = Query(None),
    end: Optional[datetime] = Query(None),
    db: Session = Depends(get_db),
):
    try:
        def base_q():
            q = db.query(EventModel).filter(
                EventModel.store_id == store_id,
                EventModel.is_staff == False,
            )
            if start:
                q = q.filter(EventModel.timestamp >= start)
            if end:
                q = q.filter(EventModel.timestamp <= end)
            return q

        # ENTRY stage: distinct visitors from ENTRY + REENTRY events
        entry_count = (
            base_q()
            .filter(EventModel.event_type.in_(["ENTRY", "REENTRY"]))
            .with_entities(func.count(distinct(EventModel.visitor_id)))
            .scalar() or 0
        )

        # ZONE_VISIT stage: distinct visitors from ZONE_ENTER events
        zone_visit_count = (
            base_q()
            .filter(EventModel.event_type == "ZONE_ENTER")
            .with_entities(func.count(distinct(EventModel.visitor_id)))
            .scalar() or 0
        )

        # BILLING_QUEUE stage: distinct visitors from BILLING_QUEUE_JOIN events
        billing_count = (
            base_q()
            .filter(EventModel.event_type == "BILLING_QUEUE_JOIN")
            .with_entities(func.count(distinct(EventModel.visitor_id)))
            .scalar() or 0
        )

        # PURCHASE stage: count of POS records for the store in time window
        pos_q = db.query(POSRecord).filter(POSRecord.store_id == store_id)
        if start:
            pos_q = pos_q.filter(POSRecord.timestamp >= start)
        if end:
            pos_q = pos_q.filter(POSRecord.timestamp <= end)
        purchase_count = pos_q.with_entities(func.count(POSRecord.transaction_id)).scalar() or 0

        # Enforce monotonicity: each stage can't exceed the previous
        zone_visit_count = min(zone_visit_count, entry_count)
        billing_count = min(billing_count, zone_visit_count)
        purchase_count = min(purchase_count, billing_count)

        def drop_off(prev, curr):
            if prev == 0:
                return None
            return round((prev - curr) / prev * 100, 2)

        stages = [
            FunnelStage(stage="ENTRY", count=entry_count, drop_off_pct=None),
            FunnelStage(stage="ZONE_VISIT", count=zone_visit_count, drop_off_pct=drop_off(entry_count, zone_visit_count)),
            FunnelStage(stage="BILLING_QUEUE", count=billing_count, drop_off_pct=drop_off(zone_visit_count, billing_count)),
            FunnelStage(stage="PURCHASE", count=purchase_count, drop_off_pct=drop_off(billing_count, purchase_count)),
        ]

        return FunnelResponse(store_id=store_id, stages=stages)

    except Exception as e:
        logger.error("funnel_error", store_id=store_id, error=str(e))
        stages = [
            FunnelStage(stage="ENTRY", count=0, drop_off_pct=None),
            FunnelStage(stage="ZONE_VISIT", count=0, drop_off_pct=None),
            FunnelStage(stage="BILLING_QUEUE", count=0, drop_off_pct=None),
            FunnelStage(stage="PURCHASE", count=0, drop_off_pct=None),
        ]
        return FunnelResponse(store_id=store_id, stages=stages)
