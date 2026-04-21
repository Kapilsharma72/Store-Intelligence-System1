from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import Optional
from datetime import datetime
import structlog

from app.database import get_db
from app.models import Event as EventModel, HeatmapResponse, HeatmapZone

router = APIRouter()
logger = structlog.get_logger()


@router.get("/stores/{store_id}/heatmap", response_model=HeatmapResponse)
def get_heatmap(
    store_id: str,
    start: Optional[datetime] = Query(None),
    end: Optional[datetime] = Query(None),
    db: Session = Depends(get_db),
):
    try:
        q = db.query(
            EventModel.zone_id,
            func.count(EventModel.event_id).label("visit_count"),
            func.avg(EventModel.dwell_ms).label("avg_dwell_ms"),
        ).filter(
            EventModel.store_id == store_id,
            EventModel.is_staff == False,
            EventModel.zone_id != None,
        )
        if start:
            q = q.filter(EventModel.timestamp >= start)
        if end:
            q = q.filter(EventModel.timestamp <= end)

        results = q.group_by(EventModel.zone_id).all()

        if not results:
            return HeatmapResponse(store_id=store_id, zones=[])

        # Compute raw values
        zones_data = []
        for zone_id, visit_count, avg_dwell_ms in results:
            avg_dwell_s = (avg_dwell_ms / 1000.0) if avg_dwell_ms else 0.0
            zones_data.append({
                "zone_id": zone_id,
                "visit_count": visit_count,
                "avg_dwell_seconds": avg_dwell_s,
            })

        max_visits = max(z["visit_count"] for z in zones_data)
        max_dwell = max(z["avg_dwell_seconds"] for z in zones_data)

        zones = []
        for z in zones_data:
            if max_visits == 0:
                intensity = 0.0
            else:
                norm_visits = z["visit_count"] / max_visits if max_visits > 0 else 0.0
                norm_dwell = z["avg_dwell_seconds"] / max_dwell if max_dwell > 0 else 0.0
                # Weighted combination: 60% visits, 40% dwell
                raw_intensity = (0.6 * norm_visits + 0.4 * norm_dwell) * 100
                intensity = raw_intensity

            zones.append(HeatmapZone(
                zone_id=z["zone_id"],
                visit_count=z["visit_count"],
                avg_dwell_seconds=z["avg_dwell_seconds"],
                intensity=intensity,
            ))

        # Normalize so max intensity = 100 when any zone has visits
        if zones and max_visits > 0:
            max_intensity = max(z.intensity for z in zones)
            if max_intensity > 0:
                for z in zones:
                    z.intensity = round(z.intensity / max_intensity * 100, 2)

        return HeatmapResponse(store_id=store_id, zones=zones)

    except Exception as e:
        logger.error("heatmap_error", store_id=store_id, error=str(e))
        return HeatmapResponse(store_id=store_id, zones=[])
