import json
from dataclasses import dataclass, field
from typing import List, Optional
import structlog

logger = structlog.get_logger()


class ConfigurationError(Exception):
    pass


@dataclass
class ZoneConfig:
    zone_id: str
    polygon: list  # list of [x, y] pairs
    camera_id: str
    priority: int = 1


@dataclass
class StoreLayout:
    store_id: str
    zones: List[ZoneConfig] = field(default_factory=list)


def load_layout(path: str) -> StoreLayout:
    """Load and validate store layout from JSON file."""
    try:
        from shapely.geometry import Polygon
        with open(path, 'r') as f:
            data = json.load(f)

        zones = []
        for z in data.get("zones", []):
            coords = z["polygon"]
            poly = Polygon(coords)
            if not poly.is_valid:
                raise ConfigurationError(f"Zone {z['zone_id']} has invalid polygon")
            if poly.is_empty:
                raise ConfigurationError(f"Zone {z['zone_id']} has empty polygon")
            zones.append(ZoneConfig(
                zone_id=z["zone_id"],
                polygon=coords,
                camera_id=z.get("camera_id", ""),
                priority=z.get("priority", 1),
            ))

        return StoreLayout(store_id=data.get("store_id", ""), zones=zones)

    except ConfigurationError:
        raise
    except Exception as e:
        raise ConfigurationError(f"Failed to load layout from {path}: {e}")


def map_to_zone(point: tuple, layout: StoreLayout) -> Optional[str]:
    """Return zone_id of highest-priority zone containing point, or None."""
    from shapely.geometry import Point, Polygon

    pt = Point(point[0], point[1])

    # Sort zones by priority (lower number = higher priority)
    sorted_zones = sorted(layout.zones, key=lambda z: z.priority)

    for zone in sorted_zones:
        poly = Polygon(zone.polygon)
        if poly.contains(pt) or poly.touches(pt):
            return zone.zone_id

    return None
