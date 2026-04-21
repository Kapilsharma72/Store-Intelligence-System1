#!/usr/bin/env python3
"""Process a single video file through the detection pipeline."""
import argparse
import os
import httpx
import structlog

logger = structlog.get_logger()

BATCH_SIZE = 500


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", required=True)
    parser.add_argument("--api-url", default="http://localhost:8000")
    args = parser.parse_args()

    try:
        import cv2
        from pipeline.detect import detect_persons
        from pipeline.tracker import ByteTracker
        from pipeline.emit import EventEmitter, make_visitor_token

        store_id = os.path.splitext(os.path.basename(args.video))[0]
        emitter = EventEmitter()
        tracker = ByteTracker()

        cap = cv2.VideoCapture(args.video)
        if not cap.isOpened():
            logger.error("video_open_failed", path=args.video)
            return

        batch = []
        frame_count = 0

        while True:
            ret, frame = cap.read()
            if not ret:
                break

            frame_count += 1
            detections = detect_persons(frame)
            tracked = tracker.update_tracks(detections)

            for tp in tracked:
                if not tp.is_lost:
                    visitor_id = make_visitor_token(store_id, tp.track_id, "session_0")
                    event = emitter.emit_event(
                        event_type="ZONE_ENTER",
                        visitor_id=visitor_id,
                        store_id=store_id,
                        camera_id="CAM_1",
                        zone_id=None,
                    )
                    batch.append({
                        "event_id": event.event_id,
                        "event_type": event.event_type,
                        "visitor_id": event.visitor_id,
                        "store_id": event.store_id,
                        "camera_id": event.camera_id,
                        "zone_id": event.zone_id,
                        "timestamp": event.timestamp,
                        "is_staff": False,
                        "confidence": 0.9,
                    })

                    if len(batch) >= BATCH_SIZE:
                        try:
                            resp = httpx.post(f"{args.api_url}/events/ingest", json=batch, timeout=30)
                            logger.info("batch_ingested", status=resp.status_code)
                        except Exception as e:
                            logger.error("batch_ingest_failed", error=str(e))
                        batch = []

        cap.release()

        if batch:
            try:
                resp = httpx.post(f"{args.api_url}/events/ingest", json=batch, timeout=30)
                logger.info("batch_ingested", status=resp.status_code)
            except Exception as e:
                logger.error("batch_ingest_failed", error=str(e))

        logger.info("video_processed", path=args.video, frames=frame_count)

    except Exception as e:
        logger.error("video_processing_failed", path=args.video, error=str(e))


if __name__ == "__main__":
    main()
