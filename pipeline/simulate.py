#!/usr/bin/env python3
"""Replay sample_events.jsonl at N× speed."""
import argparse
import json
import httpx
import structlog

logger = structlog.get_logger()

BATCH_SIZE = 500


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--speed", type=float, default=10.0)
    parser.add_argument("--api-url", default="http://localhost:8000")
    args = parser.parse_args()

    events = []
    with open(args.input) as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))

    if not events:
        logger.warning("no_events_in_sample_file", path=args.input)
        return

    logger.info("simulate_start", events=len(events), speed=args.speed)

    batch = []
    for event in events:
        batch.append(event)
        if len(batch) >= BATCH_SIZE:
            try:
                resp = httpx.post(f"{args.api_url}/events/ingest", json=batch, timeout=30)
                logger.info("batch_ingested", status=resp.status_code, count=len(batch))
            except Exception as e:
                logger.error("batch_ingest_failed", error=str(e))
            batch = []

    if batch:
        try:
            resp = httpx.post(f"{args.api_url}/events/ingest", json=batch, timeout=30)
            logger.info("batch_ingested", status=resp.status_code, count=len(batch))
        except Exception as e:
            logger.error("batch_ingest_failed", error=str(e))

    logger.info("simulate_complete")


if __name__ == "__main__":
    main()
