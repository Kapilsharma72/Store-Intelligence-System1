from dataclasses import dataclass
import structlog
import numpy as np

logger = structlog.get_logger()


@dataclass
class HSVConfig:
    lower: tuple  # (H, S, V) lower bound
    upper: tuple  # (H, S, V) upper bound
    threshold: float = 0.6  # confidence threshold for HSV method


@dataclass
class ClassificationResult:
    is_staff: bool
    confidence: float
    method: str  # 'hsv' or 'heuristic'


def classify(frame: np.ndarray, bbox: tuple, hsv_config: HSVConfig) -> ClassificationResult:
    """Classify person as staff or visitor using HSV color detection."""
    try:
        import cv2
        x1, y1, x2, y2 = int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])

        # Crop bounding box region
        h, w = frame.shape[:2]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)

        if x2 <= x1 or y2 <= y1:
            return ClassificationResult(is_staff=False, confidence=0.0, method="heuristic")

        roi = frame[y1:y2, x1:x2]

        # Convert to HSV
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)

        # Create mask for staff uniform color range
        lower = np.array(hsv_config.lower)
        upper = np.array(hsv_config.upper)
        mask = cv2.inRange(hsv, lower, upper)

        # Compute ratio of pixels matching staff color
        total_pixels = roi.shape[0] * roi.shape[1]
        matching_pixels = np.sum(mask > 0)
        confidence = matching_pixels / total_pixels if total_pixels > 0 else 0.0

        if confidence >= hsv_config.threshold:
            return ClassificationResult(is_staff=True, confidence=confidence, method="hsv")

        # Heuristic fallback: use confidence as-is, classify as non-staff
        return ClassificationResult(is_staff=False, confidence=confidence, method="heuristic")

    except Exception as e:
        logger.warning("classification_error", error=str(e))
        return ClassificationResult(is_staff=False, confidence=0.0, method="heuristic")
