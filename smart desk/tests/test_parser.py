"""
Automated unit tests for Arduino serial message parser and sensor state management.
Tests valid, invalid, partial, stale, and disconnected sensor states.
"""

import time
import pytest
import os
import sys

# Ensure parent directory is in Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from serial_manager import (
    parse_serial_message,
    raw_to_physical_state,
    SerialManager,
    STALE_THRESHOLD_SECONDS,
)


def test_valid_serial_messages():
    """Validates proper parsing of well-formed Arduino serial strings."""
    # Standard mixed message
    msg = "IR1:1,IR2:1,IR3:0,IR4:1"
    parsed = parse_serial_message(msg)
    assert parsed == {1: 1, 2: 1, 3: 0, 4: 1}

    # All occupied with CRLF
    msg_all_zero = "IR1:0,IR2:0,IR3:0,IR4:0\r\n"
    parsed = parse_serial_message(msg_all_zero)
    assert parsed == {1: 0, 2: 0, 3: 0, 4: 0}

    # All unoccupied with newline
    msg_all_one = "IR1:1,IR2:1,IR3:1,IR4:1\n"
    parsed = parse_serial_message(msg_all_one)
    assert parsed == {1: 1, 2: 1, 3: 1, 4: 1}


def test_invalid_and_partial_serial_messages():
    """Validates that malformed, incomplete, or corrupted messages are strictly rejected."""
    # Missing sensor 4
    assert parse_serial_message("IR1:1,IR2:1,IR3:0") is None

    # Invalid sensor value (not 0 or 1)
    assert parse_serial_message("IR1:1,IR2:2,IR3:0,IR4:1") is None
    assert parse_serial_message("IR1:1,IR2:-1,IR3:0,IR4:1") is None
    assert parse_serial_message("IR1:1,IR2:X,IR3:0,IR4:1") is None

    # Partial / truncated line
    assert parse_serial_message("IR1:1,IR2:") is None
    assert parse_serial_message("IR1:") is None

    # Debugging or extra characters
    assert parse_serial_message("DEBUG: IR1:1,IR2:1,IR3:0,IR4:1") is None
    assert parse_serial_message("IR1:1,IR2:1,IR3:0,IR4:1,EXTRA:0") is None
    assert parse_serial_message("random noise 12345") is None

    # Empty string or non-string
    assert parse_serial_message("") is None
    assert parse_serial_message("   \n") is None
    assert parse_serial_message(None) is None


def test_physical_state_mapping():
    """Validates conversion from raw 0/1 bits to OCCUPIED / UNOCCUPIED / UNKNOWN."""
    # Fresh states
    assert raw_to_physical_state(1, is_stale=False) == "UNOCCUPIED"
    assert raw_to_physical_state(0, is_stale=False) == "OCCUPIED"

    # Stale states must unconditionally map to UNKNOWN
    assert raw_to_physical_state(1, is_stale=True) == "UNKNOWN"
    assert raw_to_physical_state(0, is_stale=True) == "UNKNOWN"
    assert raw_to_physical_state(None, is_stale=False) == "UNKNOWN"
    assert raw_to_physical_state(None, is_stale=True) == "UNKNOWN"


def test_serial_manager_stale_detection():
    """Validates that sensor readings older than 3 seconds become stale and UNKNOWN."""
    sm = SerialManager(port="COM8", baud_rate=9600, test_mode=True)

    # Initial state should be disconnected and stale
    snap = sm.get_snapshot()
    assert snap["connected"] is False
    assert snap["is_stale"] is True
    assert all(state == "UNKNOWN" for state in snap["physical_states"].values())

    # Inject valid message with current timestamp
    now = time.time()
    ok = sm.inject_test_message("IR1:1,IR2:0,IR3:1,IR4:0", timestamp=now)
    assert ok is True

    snap = sm.get_snapshot()
    assert snap["connected"] is True
    assert snap["is_stale"] is False
    assert snap["physical_states"][1] == "UNOCCUPIED"
    assert snap["physical_states"][2] == "OCCUPIED"
    assert snap["physical_states"][3] == "UNOCCUPIED"
    assert snap["physical_states"][4] == "OCCUPIED"

    # Simulate stale reading (> 3 seconds ago)
    stale_time = now - (STALE_THRESHOLD_SECONDS + 1.0)
    sm.inject_test_message("IR1:1,IR2:1,IR3:1,IR4:1", timestamp=stale_time)

    snap_stale = sm.get_snapshot()
    assert snap_stale["is_stale"] is True
    # All physical states must be marked UNKNOWN when data is stale
    for desk_id in range(1, 5):
        assert snap_stale["physical_states"][desk_id] == "UNKNOWN"


def test_serial_manager_disconnected_state():
    """Validates that when disconnected, all physical states are UNKNOWN."""
    sm = SerialManager(port="COM8", baud_rate=9600, test_mode=True)
    sm.inject_test_message("IR1:1,IR2:1,IR3:1,IR4:1")

    # Now disconnect
    sm.inject_test_connection_state(connected=False, status="DISCONNECTED", error_message="Port COM8 unplugged")
    snap = sm.get_snapshot()

    assert snap["connected"] is False
    assert snap["status"] == "DISCONNECTED"
    assert snap["is_stale"] is True
    assert "COM8" in snap["error_message"]
    for desk_id in range(1, 5):
        assert snap["physical_states"][desk_id] == "UNKNOWN"


def test_serial_manager_port_switching():
    """Validates clean port switching and state reset."""
    sm = SerialManager(port="COM8", baud_rate=9600, test_mode=True)
    sm.inject_test_message("IR1:1,IR2:1,IR3:1,IR4:1")
    assert sm.get_snapshot()["port"] == "COM8"

    sm.switch_port("COM3")
    snap = sm.get_snapshot()
    assert snap["port"] == "COM3"
    assert snap["connected"] is False
    assert snap["status"] == "CONNECTING"
    assert snap["is_stale"] is True
