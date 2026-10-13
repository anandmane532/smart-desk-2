"""
Serial communication and sensor state manager.
Reads USB serial transmissions from Arduino UNO at 9600 baud,
validates format, tracks freshness, and updates physical occupancy states.
Thread-safe and supports dynamic port switching.
"""

import os
import re
import time
import threading
from typing import Optional, Dict, Any, Callable

try:
    import serial
except ImportError:
    serial = None

# Arduino message formats:
# Format 1 (Compact protocol): IR1:1,IR2:1,IR3:0,IR4:1
SERIAL_REGEX = re.compile(
    r"^IR1:([01]),IR2:([01]),IR3:([01]),IR4:([01])$"
)
# Format 2 (Text status): Desk 1: Unoccupied  Desk 2: Unoccupied  Desk 3: Unoccupied  Desk 4: Unoccupied
DESK_TEXT_REGEX = re.compile(
    r"^Desk\s*1:\s*(Occupied|Unoccupied)\s+Desk\s*2:\s*(Occupied|Unoccupied)\s+Desk\s*3:\s*(Occupied|Unoccupied)\s+Desk\s*4:\s*(Occupied|Unoccupied)$",
    re.IGNORECASE
)

STALE_THRESHOLD_SECONDS = 3.0


def parse_serial_message(raw_line: str) -> Optional[Dict[int, int]]:
    """
    Validates and parses a raw serial line from Arduino.
    Supports both:
      1) IR1:1,IR2:1,IR3:0,IR4:1
      2) Desk 1: Unoccupied  Desk 2: Unoccupied  Desk 3: Unoccupied  Desk 4: Unoccupied
    Returns:
        {1: 1, 2: 1, 3: 0, 4: 1} on success (1 = UNOCCUPIED, 0 = OCCUPIED),
        None if line is malformed, partial, or corrupted.
    """
    if not isinstance(raw_line, str):
        return None
    cleaned = raw_line.strip()
    match = SERIAL_REGEX.match(cleaned)
    if match:
        return {
            1: int(match.group(1)),
            2: int(match.group(2)),
            3: int(match.group(3)),
            4: int(match.group(4)),
        }

    match_text = DESK_TEXT_REGEX.match(cleaned)
    if match_text:
        return {
            1: 1 if match_text.group(1).lower() == "unoccupied" else 0,
            2: 1 if match_text.group(2).lower() == "unoccupied" else 0,
            3: 1 if match_text.group(3).lower() == "unoccupied" else 0,
            4: 1 if match_text.group(4).lower() == "unoccupied" else 0,
        }

    return None


def raw_to_physical_state(val: Optional[int], is_stale: bool) -> str:
    """
    Translates raw sensor reading to physical occupancy state.
    1 -> UNOCCUPIED
    0 -> OCCUPIED
    stale or unknown -> UNKNOWN
    """
    if is_stale or val is None:
        return "UNKNOWN"
    if val == 1:
        return "UNOCCUPIED"
    if val == 0:
        return "OCCUPIED"
    return "UNKNOWN"


class SerialManager:
    """
    Thread-safe manager for Arduino USB serial communication.
    Runs a single background thread that handles connection, reconnection,
    reading, validating, and freshness tracking.
    """

    def __init__(
        self,
        port: Optional[str] = None,
        baud_rate: int = 9600,
        on_update_callback: Optional[Callable[[], None]] = None,
        test_mode: bool = False
    ):
        self.port = port or os.environ.get("SERIAL_PORT", "COM8")
        self.baud_rate = baud_rate
        self.on_update_callback = on_update_callback
        self.test_mode = test_mode

        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._port_change_event = threading.Event()

        self._ser: Optional[Any] = None
        self._thread: Optional[threading.Thread] = None

        # State attributes
        self.connected = False
        self.status = "DISCONNECTED"  # DISCONNECTED, CONNECTING, CONNECTED, ERROR
        self.error_message: Optional[str] = None
        self.last_valid_time: Optional[float] = None
        self.raw_values: Dict[int, Optional[int]] = {1: None, 2: None, 3: None, 4: None}

    def start(self) -> None:
        """Starts the background serial reader thread."""
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._worker_loop,
                name="SerialReaderThread",
                daemon=True
            )
            self._thread.start()

    def stop(self) -> None:
        """Stops the background reader thread and closes open serial handles."""
        self._stop_event.set()
        self._port_change_event.set()
        with self._lock:
            if self._ser:
                try:
                    self._ser.close()
                except Exception:
                    pass
                self._ser = None
            self.connected = False
            self.status = "DISCONNECTED"

    def switch_port(self, new_port: str) -> None:
        """
        Changes the serial port. Closes existing connection cleanly
        before the background thread opens the new port.
        """
        if not new_port or not isinstance(new_port, str):
            raise ValueError("Invalid port specification")

        cleaned_port = new_port.strip().upper()
        with self._lock:
            if cleaned_port == self.port and self.connected:
                return
            self.port = cleaned_port
            if self._ser:
                try:
                    self._ser.close()
                except Exception:
                    pass
                self._ser = None
            self.connected = False
            self.status = "CONNECTING"
            self.error_message = None
            self.raw_values = {1: None, 2: None, 3: None, 4: None}
            self.last_valid_time = None

        self._port_change_event.set()

    def get_snapshot(self) -> Dict[str, Any]:
        """
        Returns a thread-safe snapshot of connection and physical states.
        Enforces 3-second freshness check: if no valid message arrived within
        3 seconds, all physical states are UNKNOWN.
        """
        with self._lock:
            now = time.time()
            if self.last_valid_time is None:
                is_stale = True
            else:
                is_stale = (now - self.last_valid_time) > STALE_THRESHOLD_SECONDS

            # When disconnected or stale, physical states must be UNKNOWN
            effective_is_stale = is_stale or (not self.connected)

            physical_states = {}
            for desk_id in range(1, 5):
                val = self.raw_values.get(desk_id)
                physical_states[desk_id] = raw_to_physical_state(val, effective_is_stale)

            return {
                "connected": self.connected,
                "port": self.port,
                "baud_rate": self.baud_rate,
                "status": self.status,
                "is_stale": effective_is_stale,
                "last_valid_time": self.last_valid_time,
                "error_message": self.error_message,
                "physical_states": physical_states,
                "raw_values": dict(self.raw_values),
            }

    # ================= Controlled Testing Helpers =================
    def set_test_mode(self, enabled: bool = True) -> None:
        """Enables or disables controlled test mode."""
        with self._lock:
            self.test_mode = enabled
            if enabled and self._ser:
                try:
                    self._ser.close()
                except Exception:
                    pass
                self._ser = None

    def inject_test_message(self, line: str, timestamp: Optional[float] = None) -> bool:
        """
        Injects a serial string for testing purposes only.
        Returns True if message was valid, False otherwise.
        """
        parsed = parse_serial_message(line)
        with self._lock:
            if parsed is not None:
                self.raw_values = parsed
                self.last_valid_time = timestamp if timestamp is not None else time.time()
                self.connected = True
                self.status = "CONNECTED"
                self.error_message = None
                valid = True
            else:
                valid = False

        if valid and self.on_update_callback:
            try:
                self.on_update_callback()
            except Exception:
                pass
        return valid

    def inject_test_connection_state(
        self,
        connected: bool,
        status: str = "DISCONNECTED",
        error_message: Optional[str] = None
    ) -> None:
        """Simulates connection states for testing."""
        with self._lock:
            self.connected = connected
            self.status = status
            self.error_message = error_message
            if not connected:
                self.raw_values = {1: None, 2: None, 3: None, 4: None}
                self.last_valid_time = None

    # ================= Background Worker =================
    def _worker_loop(self) -> None:
        """Background worker handling real serial I/O and reconnection."""
        while not self._stop_event.is_set():
            if self.test_mode:
                self._stop_event.wait(0.5)
                continue

            current_port = self.port
            self._port_change_event.clear()

            with self._lock:
                self.status = "CONNECTING"
                self.connected = False
                self.error_message = None

            try:
                if serial is None:
                    raise RuntimeError("PySerial package is not installed")

                # Open the serial port
                ser = serial.Serial(
                    port=current_port,
                    baudrate=self.baud_rate,
                    timeout=1.0,
                    write_timeout=1.0
                )

                # Reset input buffer if available
                ser.reset_input_buffer()

                with self._lock:
                    self._ser = ser
                    self.connected = True
                    self.status = "CONNECTED"
                    self.error_message = None

                # Notify listeners that we are connected
                if self.on_update_callback:
                    try:
                        self.on_update_callback()
                    except Exception:
                        pass

                # Read loop
                while not self._stop_event.is_set() and not self._port_change_event.is_set():
                    raw_bytes = ser.readline()
                    if not raw_bytes:
                        # Timeout reading line; check staleness and continue
                        continue

                    try:
                        line_str = raw_bytes.decode("utf-8", errors="ignore")
                    except Exception:
                        continue

                    parsed = parse_serial_message(line_str)
                    if parsed is not None:
                        with self._lock:
                            self.raw_values = parsed
                            self.last_valid_time = time.time()
                            self.error_message = None

                        if self.on_update_callback:
                            try:
                                self.on_update_callback()
                            except Exception:
                                pass

            except Exception as ex:
                with self._lock:
                    if self._ser:
                        try:
                            self._ser.close()
                        except Exception:
                            pass
                        self._ser = None
                    self.connected = False
                    self.status = "DISCONNECTED"
                    self.error_message = f"Port {current_port} error: {str(ex)}"
                    # Invalidate readings on error
                    self.raw_values = {1: None, 2: None, 3: None, 4: None}
                    self.last_valid_time = None

                if self.on_update_callback:
                    try:
                        self.on_update_callback()
                    except Exception:
                        pass

                # Wait before retrying reconnection, interruptible by stop or port change
                self._port_change_event.wait(timeout=2.0)
