from fastapi import APIRouter, Depends, Request, HTTPException, Body
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from pydantic import ValidationError
from typing import List, Any
import structlog
import uuid

from app.database import get_db
from app.models import Event as EventModel, EventSchema, IngestResponse, RejectedEvent

router = APIRouter()
logger = structlog.get_logger()

MAX_BATCH_SIZE = 500


@router.post("/events/ingest", response_model=IngestResponse)
def ingest_events(request: Request, events: List[Any] = Body(...), db: Session = Depends(get_db)):
    trace_id = getattr(request.state, "trace_id", str(uuid.uuid4()))

    if len(events) > MAX_BATCH_SIZE:
        raise HTTPException(
            status_code=422,
            detail=f"Batch size {len(events)} exceeds maximum of {MAX_BATCH_SIZE}",
        )

    ingested = 0
    rejected: List[RejectedEvent] = []

    for raw_event in events:
        # Per-event validation — allows partial success in a batch
        try:
            event = EventSchema.model_validate(raw_event)
        except ValidationError as e:
            event_id = raw_event.get("event_id", "unknown") if isinstance(raw_event, dict) else "unknown"
            rejected.append(RejectedEvent(event_id=str(event_id), reason=str(e)))
            continue

        try:
            with db.begin_nested():  # savepoint — isolates each insert
                db_event = EventModel(
                    event_id=str(event.event_id),
                    store_id=event.store_id,
                    camera_id=event.camera_id,
                    visitor_id=event.visitor_id,
                    event_type=event.event_type.value,
                    timestamp=event.timestamp,
                    zone_id=event.zone_id,
                    dwell_ms=event.dwell_ms,
                    is_staff=event.is_staff,
                    confidence=event.confidence,
                    metadata_=event.metadata.model_dump() if event.metadata else None,
                )
                db.add(db_event)
            ingested += 1
        except IntegrityError:
            # Duplicate event_id — already stored; count as ingested (idempotent)
            ingested += 1
        except Exception as e:
            logger.error(
                "event_ingest_error",
                event_id=str(event.event_id),
                error=str(e),
                trace_id=trace_id,
            )
            rejected.append(RejectedEvent(event_id=str(event.event_id), reason=str(e)))

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error("batch_commit_error", error=str(e), trace_id=trace_id)
        raise HTTPException(
            status_code=500,
            detail={"trace_id": trace_id, "message": "Internal server error"},
        )

    return IngestResponse(ingested=ingested, rejected=rejected)
