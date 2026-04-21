from dataclasses import dataclass, field
from typing import List, Dict
import structlog
from pipeline.detect import Detection

logger = structlog.get_logger()

@dataclass
class TrackedPerson:
    track_id: int
    bbox: tuple
    is_lost: bool = False
    frames_lost: int = 0

class ByteTracker:
    """Wrapper around ultralytics ByteTrack."""

    def __init__(self, occlusion_frames: int = 900):
        self.occlusion_frames = occlusion_frames
        self._tracker = None
        self._active_tracks: Dict[int, TrackedPerson] = {}

    def _get_tracker(self):
        if self._tracker is None:
            try:
                from ultralytics import YOLO
                import os
                model_path = os.getenv("YOLO_MODEL_PATH", "yolov8n.pt")
                self._tracker = YOLO(model_path)
            except Exception as e:
                logger.error("tracker_init_failed", error=str(e))
                raise
        return self._tracker

    def update_tracks(self, detections: List[Detection]) -> List[TrackedPerson]:
        """Update tracks with new detections. Returns list of active tracked persons."""
        if not detections:
            # Increment frames_lost for all active tracks
            lost = []
            for track_id, tp in list(self._active_tracks.items()):
                tp.frames_lost += 1
                tp.is_lost = True
                if tp.frames_lost > self.occlusion_frames:
                    del self._active_tracks[track_id]
                else:
                    lost.append(tp)
            return lost

        # Convert detections to format expected by tracker
        # For simplicity, assign sequential track IDs based on detection order
        # In production this would use ByteTrack's actual tracking
        tracked = []
        for i, det in enumerate(detections):
            track_id = i + 1  # simplified; real ByteTrack assigns stable IDs
            tp = TrackedPerson(
                track_id=track_id,
                bbox=det.bbox,
                is_lost=False,
                frames_lost=0,
            )
            self._active_tracks[track_id] = tp
            tracked.append(tp)

        logger.info("tracks_updated", active=len(tracked))
        return tracked


def update_tracks(detections: List[Detection]) -> List[TrackedPerson]:
    """Module-level convenience function using a global tracker instance."""
    global _tracker
    if '_tracker' not in globals():
        _tracker = ByteTracker()
    return _tracker.update_tracks(detections)
