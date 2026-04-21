from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, distinct
from typing import Optional, List
from datetime import datetime, timezone, timedelta
import structlog

from app.database import get_db
from app.models import Event as EventModel, POSRecord, AnomalyResponse, Anomaly

router = APIRouter()
logger = structlog.get_logger()

QUEUE_SPIKE_THRESHOLD = 5
QUEUE_SPIKE_DURATION_MINUTES = 2
CONVERSION_DROP_THRESHOLD_PP = 0.20  # 20 percentage points
DEAD_ZONE_MINUTES = 30


@router.get("/stores/{store_id}/anomalies", response_model=AnomalyResponse)
def get_anomalies(
    store_id: str,
    start: Optional[datetime] = Query(None),
    end: Optional[datetime] = Query(None),
    db: Session = Depends(get_db),
):
    anomalies: List[Anomaly] = []

    try:
        now = datetime.now(timezone.utc)
        window_end = end or now
        window_start = start or (window_end - timedelta(hours=24))

        # --- BILLING_QUEUE_SPIKE detection ---
        # Get all BILLING_QUEUE_JOIN and BILLING_QUEUE_ABANDON events ordered by timestamp
        queue_events = (
            db.query(EventModel.event_type, EventModel.timestamp)
            .filter(
                EventModel.store_id == store_id,
                EventModel.is_staff == False,
                EventModel.event_type.in_(["BILLING_QUEUE_JOIN", "BILLING_QUEUE_ABANDON"]),
                EventModel.timestamp >= window_start,
                EventModel.timestamp <= window_end,
            )
            .order_by(EventModel.timestamp)
            .all()
        )

        # Compute running queue depth and check for spike > 5 for > 2 min
        depth = 0
        spike_start_time = None
        spike_detected = False
        for event_type, ts in queue_events:
            if event_type == "BILLING_QUEUE_JOIN":
                depth += 1
            else:
                depth = max(0, depth - 1)

            if depth > QUEUE_SPIKE_THRESHOLD:
                if spike_start_time is None:
                    spike_start_time = ts
                else:
                    ts_aware = ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts
                    spike_start_aware = (
                        spike_start_time.replace(tzinfo=timezone.utc)
                        if spike_start_time.tzinfo is None
                        else spike_start_time
                    )
                    if (ts_aware - spike_start_aware).total_seconds() > QUEUE_SPIKE_DURATION_MINUTES * 60:
                        anomalies.append(
                            Anomaly(
                                type="BILLING_QUEUE_SPIKE",
                                severity="HIGH",
                                timestamp=ts_aware.isoformat(),
                                description=(
                                    f"Queue depth {depth} exceeded {QUEUE_SPIKE_THRESHOLD} "
                                    f"for more than {QUEUE_SPIKE_DURATION_MINUTES} minutes"
                                ),
                            )
                        )
                        spike_start_time = None  # reset to avoid duplicates
                        spike_detected = True
                        break
            else:
                spike_start_time = None

        # Check if spike condition persists until window_end (no subsequent event to trigger check)
        if not spike_detected and spike_start_time is not None and depth > QUEUE_SPIKE_THRESHOLD:
            spike_start_aware = (
                spike_start_time.replace(tzinfo=timezone.utc)
                if spike_start_time.tzinfo is None
                else spike_start_time
            )
            window_end_aware = (
                window_end.replace(tzinfo=timezone.utc) if window_end.tzinfo is None else window_end
            )
            if (window_end_aware - spike_start_aware).total_seconds() > QUEUE_SPIKE_DURATION_MINUTES * 60:
                anomalies.append(
                    Anomaly(
                        type="BILLING_QUEUE_SPIKE",
                        severity="HIGH",
                        timestamp=window_end_aware.isoformat(),
                        description=(
                            f"Queue depth {depth} exceeded {QUEUE_SPIKE_THRESHOLD} "
                            f"for more than {QUEUE_SPIKE_DURATION_MINUTES} minutes"
                        ),
                    )
                )

        # --- CONVERSION_DROP detection ---
        try:
            current_start = window_end - timedelta(hours=24)
            current_visitors = (
                db.query(func.count(distinct(EventModel.visitor_id)))
                .filter(
                    EventModel.store_id == store_id,
                    EventModel.is_staff == False,
                    EventModel.event_type == "ENTRY",
                    EventModel.timestamp >= current_start,
                    EventModel.timestamp <= window_end,
                )
                .scalar() or 0
            )
            current_pos = (
                db.query(func.count(POSRecord.transaction_id))
                .filter(
                    POSRecord.store_id == store_id,
                    POSRecord.timestamp >= current_start,
                    POSRecord.timestamp <= window_end,
                )
                .scalar() or 0
            )
            current_rate = (current_pos / current_visitors) if current_visitors > 0 else 0.0

            seven_days_ago = window_end - timedelta(days=7)
            hist_visitors = (
                db.query(func.count(distinct(EventModel.visitor_id)))
                .filter(
                    EventModel.store_id == store_id,
                    EventModel.is_staff == False,
                    EventModel.event_type == "ENTRY",
                    EventModel.timestamp >= seven_days_ago,
                    EventModel.timestamp <= window_end,
                )
                .scalar() or 0
            )
            hist_pos = (
                db.query(func.count(POSRecord.transaction_id))
                .filter(
                    POSRecord.store_id == store_id,
                    POSRecord.timestamp >= seven_days_ago,
                    POSRecord.timestamp <= window_end,
                )
                .scalar() or 0
            )
            hist_rate = (hist_pos / hist_visitors) if hist_visitors > 0 else 0.0

            if hist_rate > 0 and (hist_rate - current_rate) > CONVERSION_DROP_THRESHOLD_PP:
                anomalies.append(
                    Anomaly(
                        type="CONVERSION_DROP",
                        severity="MEDIUM",
                        timestamp=now.isoformat(),
                        description=(
                            f"Conversion rate dropped {(hist_rate - current_rate) * 100:.1f}pp "
                            f"below 7-day average"
                        ),
                    )
                )
        except Exception as e:
            logger.warning("conversion_drop_check_failed", error=str(e))

        # --- DEAD_ZONE detection ---
        try:
            cutoff = window_end - timedelta(minutes=DEAD_ZONE_MINUTES)

            active_zones = (
                db.query(distinct(EventModel.zone_id))
                .filter(
                    EventModel.store_id == store_id,
                    EventModel.is_staff == False,
                    EventModel.event_type == "ZONE_ENTER",
                    EventModel.timestamp >= window_start,
                    EventModel.timestamp <= window_end,
                    EventModel.zone_id != None,
                )
                .all()
            )

            for (zone_id,) in active_zones:
                last_event = (
                    db.query(func.max(EventModel.timestamp))
                    .filter(
                        EventModel.store_id == store_id,
                        EventModel.is_staff == False,
                        EventModel.event_type == "ZONE_ENTER",
                        EventModel.zone_id == zone_id,
                        EventModel.timestamp >= window_start,
                        EventModel.timestamp <= window_end,
                    )
                    .scalar()
                )

                if last_event is not None:
                    last_event_aware = (
                        last_event.replace(tzinfo=timezone.utc)
                        if last_event.tzinfo is None
                        else last_event
                    )
                    cutoff_aware = (
                        cutoff.replace(tzinfo=timezone.utc) if cutoff.tzinfo is None else cutoff
                    )
                    if last_event_aware < cutoff_aware:
                        anomalies.append(
                            Anomaly(
                                type="DEAD_ZONE",
                                severity="LOW",
                                timestamp=last_event_aware.isoformat(),
                                description=(
                                    f"Zone {zone_id} has had no ZONE_ENTER events "
                                    f"for more than {DEAD_ZONE_MINUTES} minutes"
                                ),
                            )
                        )
        except Exception as e:
            logger.warning("dead_zone_check_failed", error=str(e))

    except Exception as e:
        logger.error("anomalies_error", store_id=store_id, error=str(e))

    return AnomalyResponse(store_id=store_id, anomalies=anomalies)
