from dataclasses import dataclass
from typing import List
import os
import structlog

logger = structlog.get_logger()

@dataclass
class Detection:
    bbox: tuple  # (x1, y1, x2, y2)
    confidence: float
    class_id: int

_model = None

def _get_model():
    global _model
    if _model is None:
        model_path = os.getenv("YOLO_MODEL_PATH", "yolov8n.pt")
        try:
            from ultralytics import YOLO
            _model = YOLO(model_path)
        except Exception as e:
            logger.error("model_load_failed", path=model_path, error=str(e))
            raise RuntimeError(f"Failed to load YOLO model from {model_path}: {e}")
    return _model

def detect_persons(frame, conf_threshold: float = 0.4) -> List[Detection]:
    """Detect persons (class 0) in a frame above conf_threshold."""
    model = _get_model()
    results = model(frame, verbose=False)
    detections = []
    for result in results:
        for box in result.boxes:
            if int(box.cls[0]) == 0 and float(box.conf[0]) >= conf_threshold:
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                detections.append(Detection(
                    bbox=(x1, y1, x2, y2),
                    confidence=float(box.conf[0]),
                    class_id=0,
                ))
    logger.info("detections", count=len(detections))
    return detections
