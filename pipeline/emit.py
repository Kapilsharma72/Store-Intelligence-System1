import hashlib
import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Dict, Any
import structlog

logger = structlog.get_logger()


def make_visitor_token(store_id: str, track_id: int, session_start: str) -> str:
    """Generate a visitor token: VIS_ + first 6 hex chars of MD5 hash."""
    raw = f"{store_id}_{track_id}_{session_start}"
    return "VIS_" + hashlib.md5(raw.encode()).hexdigest()[:6]


@dataclass
class EmittedEvent:
    event_id: str
    event_type: str
    visitor_id: str
    store_id: str
    camera_id: str
    zone_id: Optional[str]
    timestamp: str
    metadata: Optional[Dict[str, Any]] = None


class EventEmitter:
    """Stateful event emitter that tracks session sequences and exit events."""

    def __init__(self, output_dir: str = "data/events"):
        self.output_dir = output_dir
        self._session_seq: Dict[str, int] = {}  # visitor_id -> last seq
        self._exited_visitors: set = set()  # visitor_ids that received EXIT
        os.makedirs(output_dir, exist_ok=True)

    def emit_event(
        self,
        event_type: str,
        visitor_id: str,
        store_id: str,
        camera_id: str,
        zone_id: Optional[str] = None,
        **kwargs,
    ) -> EmittedEvent:
        """Emit a structured event with UUID event_id, UTC timestamp, and monotonic session_seq."""
        # Monotonically increasing session_seq per visitor
        seq = self._session_seq.get(visitor_id, 0) + 1
        self._session_seq[visitor_id] = seq

        # Track EXIT events for REENTRY detection
        if event_type == "EXIT":
            self._exited_visitors.add(visitor_id)

        event = EmittedEvent(
            event_id=str(uuid.uuid4()),
            event_type=event_type,
            visitor_id=visitor_id,
            store_id=store_id,
            camera_id=camera_id,
            zone_id=zone_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            metadata={"session_seq": seq, **kwargs},
        )

        # Write to JSONL file
        output_path = os.path.join(self.output_dir, f"{store_id}.jsonl")
        try:
            with open(output_path, "a") as f:
                f.write(json.dumps({
                    "event_id": event.event_id,
                    "event_type": event.event_type,
                    "visitor_id": event.visitor_id,
                    "store_id": event.store_id,
                    "camera_id": event.camera_id,
                    "zone_id": event.zone_id,
                    "timestamp": event.timestamp,
                    "metadata": event.metadata,
                }) + "\n")
        except Exception as e:
            logger.error("event_write_failed", error=str(e))

        return event

    def is_reentry(self, visitor_id: str) -> bool:
        """Check if visitor previously received an EXIT event."""
        return visitor_id in self._exited_visitors


# Module-level convenience functions
_emitter = None


def emit_event(
    event_type: str,
    visitor_id: str,
    store_id: str,
    camera_id: str,
    zone_id: Optional[str] = None,
    **kwargs,
) -> EmittedEvent:
    global _emitter
    if _emitter is None:
        _emitter = EventEmitter()
    return _emitter.emit_event(event_type, visitor_id, store_id, camera_id, zone_id, **kwargs)
