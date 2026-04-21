from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import text, func
from datetime import datetime, timezone, timedelta
import structlog

from app.database import get_db
from app.models import Event as EventModel, HealthResponse, StoreHealth

router = APIRouter()
logger = structlog.get_logger()

STALE_THRESHOLD_MINUTES = 10


@router.get("/health")
def health_check(db: Session = Depends(get_db)):
    try:
        # Probe DB with SELECT 1
        db.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception as e:
        logger.error("health_db_probe_failed", error=str(e))
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "db": "unavailable", "stores": []}
        )

    try:
        now = datetime.now(timezone.utc)
        stale_threshold = now - timedelta(minutes=STALE_THRESHOLD_MINUTES)

        results = (
            db.query(EventModel.store_id, func.max(EventModel.ingested_at).label("last_event"))
            .group_by(EventModel.store_id)
            .all()
        )

        stores = []
        for store_id, last_event in results:
            if last_event is None:
                feed_status = "ok"
                last_ts = None
            else:
                # Handle both timezone-aware and naive datetimes
                if last_event.tzinfo is None:
                    last_event_aware = last_event.replace(tzinfo=timezone.utc)
                else:
                    last_event_aware = last_event
                feed_status = "STALE_FEED" if last_event_aware < stale_threshold else "ok"
                last_ts = last_event.isoformat()

            stores.append(StoreHealth(
                store_id=store_id,
                feed_status=feed_status,
                last_event_timestamp=last_ts,
            ))

        return HealthResponse(status="ok", db=db_status, stores=stores)

    except Exception as e:
        logger.error("health_check_error", error=str(e))
        return JSONResponse(
            status_code=500,
            content={"status": "degraded", "db": db_status, "stores": []}
        )
