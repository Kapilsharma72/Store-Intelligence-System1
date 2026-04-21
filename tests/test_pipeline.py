# PROMPT: Generate property-based tests for the store intelligence pipeline components
# CHANGES MADE: Added Property 1 (visitor token format), Property 10 (session seq monotonicity), Property 11 (zone mapping correctness)

import re
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

# Pipeline modules are implemented in Task 13 — skip tests if not yet available
try:
    from pipeline.emit import make_visitor_token, emit_event
    PIPELINE_AVAILABLE = True
except ImportError:
    PIPELINE_AVAILABLE = False

try:
    from pipeline.zone_mapper import map_to_zone, load_layout, StoreLayout, ZoneConfig
    ZONE_MAPPER_AVAILABLE = True
except ImportError:
    ZONE_MAPPER_AVAILABLE = False

VISITOR_TOKEN_PATTERN = re.compile(r"^VIS_[a-f0-9]{6}$")


# Feature: store-intelligence-system, Property 1: Visitor token format
@pytest.mark.skipif(not PIPELINE_AVAILABLE, reason="pipeline.emit not yet implemented")
@given(st.text(), st.integers(), st.text())
@settings(max_examples=100)
def test_visitor_token_format(store_id, track_id, session_start):
    """Validates: Requirements 1.3

    For any combination of store_id, track_id, and session_start,
    the generated Visitor_Token must match ^VIS_[a-f0-9]{6}$
    """
    token = make_visitor_token(store_id, track_id, session_start)
    assert VISITOR_TOKEN_PATTERN.match(token), f"Token {token!r} does not match expected pattern"


# Feature: store-intelligence-system, Property 10: Session seq monotonicity
@pytest.mark.skipif(not PIPELINE_AVAILABLE, reason="pipeline.emit not yet implemented")
@given(
    st.integers(min_value=2, max_value=20),  # number of events per visitor
)
@settings(max_examples=50)
def test_session_seq_monotonicity(num_events):
    """Validates: Requirements 4.10

    For any sequence of events emitted for a single visitor within a single
    processing session, session_seq values shall be strictly increasing.
    """
    from pipeline.emit import EventEmitter

    emitter = EventEmitter()
    store_id = "STORE_001"
    visitor_id = "VIS_abc123"

    seq_values = []
    for i in range(num_events):
        event = emitter.emit_event(
            event_type="ZONE_ENTER",
            visitor_id=visitor_id,
            store_id=store_id,
            camera_id="CAM_1",
            zone_id="ZONE_A",
        )
        if event.metadata and event.metadata.get("session_seq") is not None:
            seq_values.append(event.metadata["session_seq"])

    # Assert strictly increasing
    for i in range(1, len(seq_values)):
        assert seq_values[i] > seq_values[i - 1], f"session_seq not strictly increasing: {seq_values}"


# Feature: store-intelligence-system, Property 11: Zone mapping correctness
@pytest.mark.skipif(not ZONE_MAPPER_AVAILABLE, reason="pipeline.zone_mapper not yet implemented")
@given(
    st.floats(min_value=0.0, max_value=2.0, allow_nan=False, allow_infinity=False),
    st.floats(min_value=0.0, max_value=2.0, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=100, deadline=None)
def test_zone_mapping_correctness(x, y):
    """Validates: Requirements 2.1, 2.3

    For any point and store layout, map_to_zone returns the highest-priority
    zone containing the point, or None if no zone contains it.
    """
    import json
    import tempfile
    import os

    # Create a simple layout with two overlapping zones
    layout_data = {
        "store_id": "STORE_TEST",
        "zones": [
            {
                "zone_id": "ZONE_LOW_PRIORITY",
                "polygon": [[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]],
                "camera_id": "CAM_1",
                "priority": 2
            },
            {
                "zone_id": "ZONE_HIGH_PRIORITY",
                "polygon": [[0.5, 0.5], [1.5, 0.5], [1.5, 1.5], [0.5, 1.5], [0.5, 0.5]],
                "camera_id": "CAM_2",
                "priority": 1
            }
        ]
    }

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(layout_data, f)
        layout_path = f.name

    try:
        layout = load_layout(layout_path)
        result = map_to_zone((x, y), layout)

        # Result must be a zone_id string or None
        assert result is None or isinstance(result, str)

        # If point is in the high-priority zone, result must be ZONE_HIGH_PRIORITY
        if 0.5 <= x <= 1.5 and 0.5 <= y <= 1.5:
            assert result == "ZONE_HIGH_PRIORITY", f"Expected ZONE_HIGH_PRIORITY for ({x},{y}), got {result}"

        # If point is in the outer zone but not inner, result must be ZONE_LOW_PRIORITY
        elif 0.0 <= x <= 2.0 and 0.0 <= y <= 2.0:
            assert result == "ZONE_LOW_PRIORITY", f"Expected ZONE_LOW_PRIORITY for ({x},{y}), got {result}"

        # If point is outside both zones, result must be None
        else:
            assert result is None, f"Expected None for ({x},{y}), got {result}"

    finally:
        os.unlink(layout_path)
