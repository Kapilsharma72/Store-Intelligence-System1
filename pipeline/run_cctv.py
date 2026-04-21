#!/usr/bin/env python3
"""
Process CCTV videos through the Store Intelligence pipeline.
Samples every N frames to keep processing fast.
Emits ENTRY, ZONE_ENTER, EXIT events and POSTs them to the API.
"""
import os
import sys
import uuid
import httpx
import cv2
import structlog
from datetime import datetime, timezone
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline.emit import EventEmitter, make_visitor_token
from pipeline.detect import detect_persons
from pipeline.tracker import ByteTracker

logger = structlog.get_logger()

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
FRAME_SAMPLE_RATE = 15   # process every 15th frame (~2fps for 30fps video)
BATCH_SIZE = 100
CLIPS_DIR = Path("data/clips")

def process_video(video_path: Path, api_url: str) -> dict:
    """Process a single video file. Returns summary stats."""
    store_id = video_path.stem.replace(" ", "_")
    camera_id = video_path.stem
    emitter = EventEmitter(output_dir="data/events")
    tracker = ByteTracker(occlusion_frames=30)

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        logger.error("video_open_failed", path=str(video_path))
        return {"error": "Could not open video"}

    fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration_s = total_frames / fps if fps > 0 else 0

    print(f"\n{'='*60}")
    print(f"Processing: {video_path.name}")
    print(f"  Resolution: {int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))}x{int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))}")
    print(f"  FPS: {fps:.1f}, Frames: {total_frames}, Duration: {duration_s:.0f}s")
    print(f"  Sampling every {FRAME_SAMPLE_RATE} frames")

    batch = []
    frame_count = 0
    processed_frames = 0
    total_detections = 0
    total_events = 0
    active_visitors = set()
    ingested_count = 0

    def flush_batch():
        nonlocal ingested_count
        if not batch:
            return
        try:
            resp = httpx.post(f"{api_url}/events/ingest", json=batch, timeout=30)
            data = resp.json()
            ingested_count += data.get("ingested", 0)
            rejected = data.get("rejected", [])
            if rejected:
                print(f"    [WARN] {len(rejected)} events rejected")
        except Exception as e:
            logger.error("batch_ingest_failed", error=str(e))
        batch.clear()

    session_start = datetime.now(timezone.utc).isoformat()

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        frame_count += 1

        # Sample every N frames
        if frame_count % FRAME_SAMPLE_RATE != 0:
            continue

        processed_frames += 1

        # Detect persons
        try:
            detections = detect_persons(frame, conf_threshold=0.4)
        except Exception as e:
            logger.warning("detection_failed", frame=frame_count, error=str(e))
            continue

        total_detections += len(detections)

        # Update tracker
        tracked = tracker.update_tracks(detections)

        # Emit events for each tracked person
        for tp in tracked:
            if tp.is_lost:
                continue

            visitor_id = make_visitor_token(store_id, tp.track_id, session_start)
            active_visitors.add(visitor_id)

            # Determine event type
            if visitor_id not in emitter._session_seq:
                event_type = "ENTRY"
            else:
                event_type = "ZONE_ENTER"

            event = emitter.emit_event(
                event_type=event_type,
                visitor_id=visitor_id,
                store_id=store_id,
                camera_id=camera_id,
                zone_id=None,
            )
            total_events += 1

            batch.append({
                "event_id": event.event_id,
                "event_type": event.event_type,
                "visitor_id": event.visitor_id,
                "store_id": event.store_id,
                "camera_id": event.camera_id,
                "zone_id": event.zone_id,
                "timestamp": event.timestamp,
                "is_staff": False,
                "confidence": max(tp.bbox[2] - tp.bbox[0], 0.1) / 1920,  # normalized width as proxy
            })

            if len(batch) >= BATCH_SIZE:
                flush_batch()

        # Progress update every 50 processed frames
        if processed_frames % 50 == 0:
            pct = (frame_count / total_frames) * 100
            print(f"  Progress: {pct:.0f}% | Detections so far: {total_detections} | Events: {total_events}")

    cap.release()
    flush_batch()

    # Emit EXIT events for all active visitors
    for visitor_id in active_visitors:
        event = emitter.emit_event(
            event_type="EXIT",
            visitor_id=visitor_id,
            store_id=store_id,
            camera_id=camera_id,
        )
        batch.append({
            "event_id": event.event_id,
            "event_type": event.event_type,
            "visitor_id": event.visitor_id,
            "store_id": event.store_id,
            "camera_id": event.camera_id,
            "zone_id": None,
            "timestamp": event.timestamp,
            "is_staff": False,
            "confidence": 0.9,
        })

    flush_batch()

    summary = {
        "video": video_path.name,
        "store_id": store_id,
        "total_frames": total_frames,
        "processed_frames": processed_frames,
        "total_detections": total_detections,
        "unique_visitors": len(active_visitors),
        "total_events": total_events + len(active_visitors),
        "ingested_to_api": ingested_count,
    }

    print(f"\n  ✅ Done: {summary['unique_visitors']} unique visitors, "
          f"{summary['total_detections']} detections, "
          f"{summary['ingested_to_api']} events ingested to API")

    return summary


def main():
    print("Store Intelligence — CCTV Video Pipeline")
    print(f"API: {API_BASE_URL}")

    # Verify API is reachable
    try:
        r = httpx.get(f"{API_BASE_URL}/health", timeout=5)
        print(f"API health: {r.json()['status']}")
    except Exception as e:
        print(f"ERROR: Cannot reach API at {API_BASE_URL}: {e}")
        sys.exit(1)

    # Find video files
    videos = sorted(CLIPS_DIR.glob("*.mp4"))
    if not videos:
        print(f"No .mp4 files found in {CLIPS_DIR}")
        sys.exit(1)

    print(f"\nFound {len(videos)} video(s) to process")

    all_summaries = []
    for video in videos:
        summary = process_video(video, API_BASE_URL)
        all_summaries.append(summary)

    # Final analytics query
    print(f"\n{'='*60}")
    print("PIPELINE COMPLETE — Querying Analytics API")
    print(f"{'='*60}")

    store_ids = list({s["store_id"] for s in all_summaries if "store_id" in s})

    for store_id in store_ids:
        print(f"\nStore: {store_id}")
        try:
            m = httpx.get(f"{API_BASE_URL}/stores/{store_id}/metrics", timeout=10).json()
            print(f"  Unique Visitors:   {m['unique_visitors']}")
            print(f"  Conversion Rate:   {m['conversion_rate']:.1%}")
            print(f"  Avg Dwell (s):     {m['avg_dwell_seconds']:.1f}")
            print(f"  Queue Depth:       {m['queue_depth']}")
            print(f"  Abandonment Rate:  {m['abandonment_rate']:.1%}")
        except Exception as e:
            print(f"  Metrics error: {e}")

        try:
            f = httpx.get(f"{API_BASE_URL}/stores/{store_id}/funnel", timeout=10).json()
            print(f"  Funnel:")
            for stage in f.get("stages", []):
                drop = f"  ↓{stage['drop_off_pct']:.1f}%" if stage['drop_off_pct'] else ""
                print(f"    {stage['stage']:20s}: {stage['count']:4d}{drop}")
        except Exception as e:
            print(f"  Funnel error: {e}")

        try:
            h = httpx.get(f"{API_BASE_URL}/stores/{store_id}/heatmap", timeout=10).json()
            zones = h.get("zones", [])
            if zones:
                print(f"  Heatmap ({len(zones)} zones):")
                for z in sorted(zones, key=lambda x: -x["intensity"])[:5]:
                    print(f"    {z['zone_id']:20s}: intensity={z['intensity']:.0f}, visits={z['visit_count']}")
        except Exception as e:
            print(f"  Heatmap error: {e}")

        try:
            a = httpx.get(f"{API_BASE_URL}/stores/{store_id}/anomalies", timeout=10).json()
            anomalies = a.get("anomalies", [])
            if anomalies:
                print(f"  Anomalies ({len(anomalies)}):")
                for an in anomalies:
                    print(f"    [{an['severity']}] {an['type']}: {an['description']}")
            else:
                print(f"  Anomalies: None detected")
        except Exception as e:
            print(f"  Anomalies error: {e}")

    # Final health check
    print(f"\n{'='*60}")
    print("FINAL HEALTH CHECK")
    try:
        h = httpx.get(f"{API_BASE_URL}/health", timeout=5).json()
        print(f"  Status: {h['status']} | DB: {h['db']}")
        for s in h.get("stores", []):
            icon = "⚠️ " if s["feed_status"] == "STALE_FEED" else "✅"
            print(f"  {icon} {s['store_id']}: {s['feed_status']} (last: {s.get('last_event_timestamp','N/A')})")
    except Exception as e:
        print(f"  Health check error: {e}")

    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    total_visitors = sum(s.get("unique_visitors", 0) for s in all_summaries)
    total_events = sum(s.get("ingested_to_api", 0) for s in all_summaries)
    print(f"  Videos processed:  {len(all_summaries)}")
    print(f"  Total visitors:    {total_visitors}")
    print(f"  Total API events:  {total_events}")


if __name__ == "__main__":
    main()
