"""
Smart Parking Monitoring and Mobile Booking System - Flask Backend.
Provides RESTful APIs, Server-Sent Events (SSE), authentication,
CSRF protection, and coordination with SQLite database and serial manager.
"""

import os
import secrets
import time
import threading
from typing import Dict, Any, Optional, Callable

from flask import (
    Flask,
    request,
    session,
    jsonify,
    render_template,
    Response,
    redirect,
    url_for,
    make_response
)
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3

from database import (
    init_db,
    create_user,
    get_user_by_username,
    get_user_by_id,
    get_active_bookings,
    get_active_booking_for_desk,
    get_booking_by_id,
    get_user_bookings,
    create_booking,
    cancel_booking,
    auto_cancel_booking,
    DEFAULT_DB_PATH
)
from serial_manager import SerialManager
from events import EventManager

# Load environment configuration if available
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = Flask(__name__)

# Application Configuration
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config["DATABASE_PATH"] = os.environ.get("DATABASE_PATH", DEFAULT_DB_PATH)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
# Must be False for local Wi-Fi/LAN HTTP development access
app.config["SESSION_COOKIE_SECURE"] = False

# Initialize database
init_db(app.config["DATABASE_PATH"])

# Initialize SSE Event Manager
event_manager = EventManager()

# Grace period timeout in seconds (default 15s)
BOOKING_GRACE_TIMEOUT_SECONDS = float(os.environ.get("BOOKING_TIMEOUT_SECONDS", 15.0))


class BookingGracePeriodTracker:
    """
    Tracks newly booked desks during a grace window (default 15 seconds).
    If the physical sensor does not switch to OCCUPIED within the grace window,
    the reservation is automatically cancelled and the desk returns to AVAILABLE.
    """

    def __init__(self, timeout_seconds: float = BOOKING_GRACE_TIMEOUT_SECONDS):
        self.timeout_seconds = timeout_seconds
        self._lock = threading.Lock()
        self._pending: Dict[int, Dict[str, Any]] = {}

    def track_booking(
        self,
        booking_id: int,
        desk_id: int,
        on_timeout_callback: Callable[[int, int], None]
    ) -> None:
        """Starts a grace period countdown for a newly created booking."""
        with self._lock:
            # Cancel any existing pending tracker for this desk
            for bid, data in list(self._pending.items()):
                if data["desk_id"] == desk_id:
                    timer = data.get("timer")
                    if timer:
                        timer.cancel()
                    self._pending.pop(bid, None)

            timer = threading.Timer(
                self.timeout_seconds,
                self._handle_timeout,
                args=(booking_id, desk_id, on_timeout_callback)
            )
            timer.daemon = True
            self._pending[booking_id] = {
                "desk_id": desk_id,
                "created_time": time.time(),
                "occupied": False,
                "timer": timer
            }
            timer.start()

    def mark_occupied(self, desk_id: int) -> None:
        """Called when a desk's physical sensor indicates OCCUPIED."""
        with self._lock:
            for bid, data in self._pending.items():
                if data["desk_id"] == desk_id and not data["occupied"]:
                    data["occupied"] = True

    def cancel_tracking(self, booking_id: int) -> None:
        """Stops tracking when booking is cancelled."""
        with self._lock:
            data = self._pending.pop(booking_id, None)
            if data and data.get("timer"):
                data["timer"].cancel()

    def get_remaining_seconds(self, booking_id: int) -> Optional[float]:
        """Returns remaining grace period seconds or None if already checked-in or not pending."""
        with self._lock:
            data = self._pending.get(booking_id)
            if not data or data["occupied"]:
                return None
            elapsed = time.time() - data["created_time"]
            remaining = self.timeout_seconds - elapsed
            return max(0.0, remaining)

    def is_pending_checkin(self, booking_id: int) -> bool:
        """Returns True if booking is within grace period and still awaiting physical arrival."""
        with self._lock:
            data = self._pending.get(booking_id)
            return bool(data and not data["occupied"])

    def _handle_timeout(
        self,
        booking_id: int,
        desk_id: int,
        callback: Callable[[int, int], None]
    ) -> None:
        """Internal timer callback when grace period expires."""
        should_release = False
        with self._lock:
            data = self._pending.get(booking_id)
            if data and not data["occupied"]:
                should_release = True
                self._pending.pop(booking_id, None)
            elif data:
                self._pending.pop(booking_id, None)

        if should_release:
            callback(booking_id, desk_id)


booking_tracker = BookingGracePeriodTracker()


def handle_booking_timeout(booking_id: int, desk_id: int) -> None:
    """
    Executed when a booking grace period (15s) expires without physical occupation.
    Verifies sensor is still UNOCCUPIED, cancels booking in database,
    and broadcasts state change so desk returns to AVAILABLE.
    """
    with app.app_context():
        try:
            snapshot = serial_manager.get_snapshot()
            physical = snapshot.get("physical_states", {}).get(desk_id, "UNKNOWN")
            raw_val = snapshot.get("raw_values", {}).get(desk_id)

            # If desk physically became occupied, do not cancel
            if physical == "OCCUPIED" or raw_val == 0:
                return

            cancelled = auto_cancel_booking(app.config["DATABASE_PATH"], booking_id)
            if cancelled:
                broadcast_status_change()
                event_manager.broadcast("notification", {
                    "type": "warning",
                    "message": f"Booking #{booking_id} for Desk {desk_id} expired (not occupied within 15 seconds) and is now Available."
                })
        except Exception:
            pass


# Forward declaration of status helper so serial callback can broadcast updates
def broadcast_status_change() -> None:
    """Broadcasts updated system status to SSE clients and checks sensor arrival."""
    with app.app_context():
        try:
            snapshot = serial_manager.get_snapshot()
            for d_id, p_state in snapshot.get("physical_states", {}).items():
                if p_state == "OCCUPIED" or snapshot.get("raw_values", {}).get(d_id) == 0:
                    booking_tracker.mark_occupied(d_id)

            status_payload = build_status_payload()
            event_manager.broadcast("status", status_payload)
        except Exception:
            pass


# Initialize Serial Manager
serial_manager = SerialManager(
    port=os.environ.get("SERIAL_PORT", "COM8"),
    baud_rate=int(os.environ.get("SERIAL_BAUD", 9600)),
    on_update_callback=broadcast_status_change,
    test_mode=os.environ.get("TESTING", "").lower() in ("true", "1")
)
# Start background serial thread unless explicitly testing without hardware
if os.environ.get("TESTING", "").lower() not in ("true", "1"):
    serial_manager.start()


# ============================================================================
# Security & CSRF Helpers
# ============================================================================

def ensure_csrf_token() -> str:
    """Generates and persists a CSRF token in session if not present."""
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(32)
    return session["csrf_token"]


def get_request_csrf_token() -> Optional[str]:
    """Retrieves CSRF token from header, json payload, or form."""
    token = request.headers.get("X-CSRF-Token")
    if token:
        return token
    if request.is_json and request.json:
        return request.json.get("csrf_token")
    if request.form:
        return request.form.get("csrf_token")
    return None


@app.before_request
def csrf_protect() -> Optional[Response]:
    """
    Enforces CSRF protection for sensitive state-changing endpoints.
    Registration and login are exempt as they establish sessions.
    """
    ensure_csrf_token()

    if request.method in ("POST", "PUT", "DELETE", "PATCH"):
        # Exclude endpoints that initialize or authenticate sessions
        exempt_paths = ["/api/login", "/api/register"]
        if request.path in exempt_paths:
            return None

        client_token = get_request_csrf_token()
        session_token = session.get("csrf_token")

        if not client_token or not session_token or not secrets.compare_digest(client_token, session_token):
            return jsonify({
                "error": "Forbidden: Invalid or missing CSRF token",
                "code": "CSRF_ERROR"
            }), 403

    return None


def get_current_user() -> Optional[Dict[str, Any]]:
    """Returns the authenticated user dict from session, or None."""
    user_id = session.get("user_id")
    if not user_id:
        return None
    return get_user_by_id(app.config["DATABASE_PATH"], user_id)


# ============================================================================
# Status & State Aggregator
# ============================================================================

DESK_METADATA = [
    {"desk_id": 1, "label": "Desk 1", "sensor_pin": "IR1 (D2)"},
    {"desk_id": 2, "label": "Desk 2", "sensor_pin": "IR2 (D3)"},
    {"desk_id": 3, "label": "Desk 3", "sensor_pin": "IR3 (D4)"},
    {"desk_id": 4, "label": "Desk 4", "sensor_pin": "IR4 (D5)"},
]


def build_status_payload(current_user_id: Optional[int] = None) -> Dict[str, Any]:
    """
    Assembles real sensor states, reservation states, effective display states,
    and summary statistics.
    Rules:
    - UNOCCUPIED + NOT_BOOKED = AVAILABLE
    - UNOCCUPIED + BOOKED = BOOKED
    - OCCUPIED = OCCUPIED (regardless of reservation state)
    - UNKNOWN = UNKNOWN
    """
    serial_snapshot = serial_manager.get_snapshot()
    active_bookings = get_active_bookings(app.config["DATABASE_PATH"])

    desks = []
    occupied_count = 0
    unoccupied_count = 0
    available_count = 0
    active_booking_count = 0

    for meta in DESK_METADATA:
        desk_id = meta["desk_id"]
        physical = serial_snapshot["physical_states"].get(desk_id, "UNKNOWN")
        booking = active_bookings.get(desk_id)

        has_booking = booking is not None
        reservation_state = "BOOKED" if has_booking else "NOT_BOOKED"

        if physical == "OCCUPIED":
            occupied_count += 1
            effective = "OCCUPIED"
        elif physical == "UNOCCUPIED":
            unoccupied_count += 1
            if has_booking:
                effective = "BOOKED"
            else:
                effective = "AVAILABLE"
                available_count += 1
        else:
            effective = "UNKNOWN"

        if has_booking:
            active_booking_count += 1

        booking_summary = None
        if booking:
            rem_sec = booking_tracker.get_remaining_seconds(booking["id"])
            is_pending = booking_tracker.is_pending_checkin(booking["id"])
            booking_summary = {
                "id": booking["id"],
                "username": booking["username"],
                "created_at": booking["created_at"],
                "is_owned_by_current_user": bool(
                    current_user_id and booking["user_id"] == current_user_id
                ),
                "pending_checkin": is_pending,
                "grace_remaining_seconds": round(rem_sec, 1) if rem_sec is not None else None,
            }

        desks.append({
            "desk_id": desk_id,
            "label": meta["label"],
            "sensor_pin": meta["sensor_pin"],
            "physical_state": physical,
            "reservation_state": reservation_state,
            "effective_state": effective,
            "booking": booking_summary,
        })

    # Occupancy percentage calculated strictly from valid sensor data
    valid_sensors = occupied_count + unoccupied_count
    if valid_sensors > 0:
        occupancy_pct = round((occupied_count / valid_sensors) * 100.0, 1)
    else:
        occupancy_pct = 0.0

    return {
        "connection": {
            "connected": serial_snapshot["connected"],
            "port": serial_snapshot["port"],
            "baud_rate": serial_snapshot["baud_rate"],
            "status": serial_snapshot["status"],
            "is_stale": serial_snapshot["is_stale"],
            "last_valid_time": serial_snapshot["last_valid_time"],
            "error_message": serial_snapshot["error_message"],
        },
        "desks": desks,
        "stats": {
            "total": 4,
            "physically_occupied": occupied_count,
            "physically_unoccupied": unoccupied_count,
            "available": available_count,
            "active_bookings": active_booking_count,
            "occupancy_percentage": occupancy_pct,
        },
        "user": (
            {"id": current_user_id, "username": session.get("username")}
            if current_user_id
            else None
        ),
        "csrf_token": session.get("csrf_token"),
    }


# ============================================================================
# Page Routes
# ============================================================================

@app.route("/")
def index():
    """Serves the main mobile-first dashboard."""
    ensure_csrf_token()
    user = get_current_user()
    return render_template(
        "index.html",
        user=user,
        csrf_token=session.get("csrf_token")
    )


@app.route("/login")
def login_page():
    """Serves the login page."""
    if session.get("user_id"):
        return redirect(url_for("index"))
    ensure_csrf_token()
    return render_template("login.html", csrf_token=session.get("csrf_token"))


@app.route("/register")
def register_page():
    """Serves the registration page."""
    if session.get("user_id"):
        return redirect(url_for("index"))
    ensure_csrf_token()
    return render_template("register.html", csrf_token=session.get("csrf_token"))


# ============================================================================
# API Endpoints
# ============================================================================

@app.route("/api/status", methods=["GET"])
def api_status():
    """Returns live sensor states, bookings, stats, and connection status."""
    ensure_csrf_token()
    user_id = session.get("user_id")
    payload = build_status_payload(user_id)
    return jsonify(payload), 200


@app.route("/api/events", methods=["GET"])
def api_events():
    """Streams live parking updates and heartbeats via Server-Sent Events."""
    user_id = session.get("user_id")
    initial_payload = build_status_payload(user_id)
    q = event_manager.subscribe()
    return Response(
        event_manager.stream(q, initial_payload),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


@app.route("/api/register", methods=["POST"])
def api_register():
    """Registers a new user and creates an authenticated session."""
    data = request.get_json(silent=True) or request.form
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()

    if not username or len(username) < 3 or len(username) > 30:
        return jsonify({
            "error": "Username must be between 3 and 30 characters",
            "code": "INVALID_USERNAME"
        }), 400

    if not password or len(password) < 6:
        return jsonify({
            "error": "Password must be at least 6 characters long",
            "code": "INVALID_PASSWORD"
        }), 400

    existing = get_user_by_username(app.config["DATABASE_PATH"], username)
    if existing:
        return jsonify({
            "error": "Username is already taken",
            "code": "USER_EXISTS"
        }), 409

    pwd_hash = generate_password_hash(password)
    try:
        new_user = create_user(app.config["DATABASE_PATH"], username, pwd_hash)
    except sqlite3.IntegrityError:
        return jsonify({
            "error": "Username is already taken",
            "code": "USER_EXISTS"
        }), 409

    # Establish session
    session["user_id"] = new_user["id"]
    session["username"] = new_user["username"]
    token = ensure_csrf_token()

    return jsonify({
        "success": True,
        "message": f"Welcome, {username}! Registration successful.",
        "user": {"id": new_user["id"], "username": new_user["username"]},
        "csrf_token": token,
    }), 201


@app.route("/api/login", methods=["POST"])
def api_login():
    """Authenticates user credentials and starts an authorized session."""
    data = request.get_json(silent=True) or request.form
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()

    if not username or not password:
        return jsonify({
            "error": "Username and password are required",
            "code": "MISSING_CREDENTIALS"
        }), 400

    user = get_user_by_username(app.config["DATABASE_PATH"], username)
    if not user or not check_password_hash(user["password_hash"], password):
        return jsonify({
            "error": "Invalid username or password",
            "code": "AUTH_FAILED"
        }), 401

    session["user_id"] = user["id"]
    session["username"] = user["username"]
    token = ensure_csrf_token()

    return jsonify({
        "success": True,
        "message": "Login successful",
        "user": {"id": user["id"], "username": user["username"]},
        "csrf_token": token,
    }), 200


@app.route("/api/logout", methods=["POST"])
def api_logout():
    """Terminates user session."""
    session.clear()
    ensure_csrf_token()
    return jsonify({
        "success": True,
        "message": "Logged out successfully"
    }), 200


@app.route("/api/bookings", methods=["GET"])
def api_get_bookings():
    """Returns booking history for the authenticated user."""
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({
            "error": "Authentication required",
            "code": "UNAUTHORIZED"
        }), 401

    bookings = get_user_bookings(app.config["DATABASE_PATH"], user_id)
    return jsonify({"bookings": bookings}), 200


@app.route("/api/bookings/<int:booking_id>", methods=["GET"])
def api_get_booking_detail(booking_id: int):
    """Returns details for a specific authorized booking."""
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({
            "error": "Authentication required",
            "code": "UNAUTHORIZED"
        }), 401

    booking = get_booking_by_id(app.config["DATABASE_PATH"], booking_id)
    if not booking:
        return jsonify({
            "error": "Booking not found",
            "code": "NOT_FOUND"
        }), 404

    if booking["user_id"] != user_id:
        return jsonify({
            "error": "Forbidden: You do not own this booking",
            "code": "FORBIDDEN"
        }), 403

    return jsonify({"booking": booking}), 200


@app.route("/api/bookings", methods=["POST"])
def api_create_booking():
    """
    Creates a parking reservation following all hardware and database validation steps.
    """
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({
            "error": "Authentication required to book a space",
            "code": "UNAUTHORIZED"
        }), 401

    data = request.get_json(silent=True)
    if not data or "desk_id" not in data:
        return jsonify({
            "error": "desk_id is required in JSON payload",
            "code": "INVALID_INPUT"
        }), 400

    try:
        desk_id = int(data["desk_id"])
    except (ValueError, TypeError):
        return jsonify({
            "error": "desk_id must be an integer",
            "code": "INVALID_INPUT"
        }), 400

    if desk_id < 1 or desk_id > 4:
        return jsonify({
            "error": "desk_id must be between 1 and 4",
            "code": "OUT_OF_RANGE"
        }), 400

    # 1. Thread-safe snapshot & connection verification
    snapshot = serial_manager.get_snapshot()

    if not snapshot["connected"]:
        return jsonify({
            "error": "Hardware unavailable: Arduino is not connected",
            "code": "HARDWARE_DISCONNECTED"
        }), 503

    if snapshot["is_stale"]:
        return jsonify({
            "error": "Sensor data is stale or unavailable. Reconnecting to Arduino...",
            "code": "SENSOR_STALE"
        }), 503

    # 2. Check completeness of sensor snapshot
    raw_val = snapshot["raw_values"].get(desk_id)
    physical_state = snapshot["physical_states"].get(desk_id, "UNKNOWN")

    if raw_val is None or physical_state == "UNKNOWN":
        return jsonify({
            "error": f"Sensor readings for Desk {desk_id} are currently unavailable",
            "code": "SENSOR_UNAVAILABLE"
        }), 503

    # 3. Check actual physical sensor value (0 = OCCUPIED, 1 = UNOCCUPIED)
    if physical_state == "OCCUPIED" or raw_val == 0:
        return jsonify({
            "error": f"Desk {desk_id} is physically occupied and cannot be booked",
            "code": "DESK_OCCUPIED"
        }), 409

    # 4. Check for existing active reservation
    existing_booking = get_active_booking_for_desk(app.config["DATABASE_PATH"], desk_id)
    if existing_booking:
        return jsonify({
            "error": f"Desk {desk_id} already has an active reservation",
            "code": "DESK_ALREADY_BOOKED"
        }), 409

    # 5. Insert reservation into database inside a transaction
    # The partial unique index ensures no race condition can double-book
    try:
        booking = create_booking(app.config["DATABASE_PATH"], user_id, desk_id)
    except sqlite3.IntegrityError:
        return jsonify({
            "error": f"Desk {desk_id} was just booked by another user",
            "code": "CONCURRENT_BOOKING_CONFLICT"
        }), 409

    # Start 15-second grace period tracker (auto-releases if not occupied in 15s)
    booking_tracker.track_booking(booking["id"], desk_id, handle_booking_timeout)

    # 6. Broadcast updated state to all connected SSE clients
    broadcast_status_change()

    return jsonify({
        "success": True,
        "message": f"Desk {desk_id} successfully booked! Please occupy within 15 seconds.",
        "booking": booking
    }), 201


@app.route("/api/bookings/<int:booking_id>/cancel", methods=["POST"])
def api_cancel_booking(booking_id: int):
    """
    Cancels an active booking owned by the authenticated user.
    Preserves historical database records and never changes physical sensor state.
    """
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({
            "error": "Authentication required",
            "code": "UNAUTHORIZED"
        }), 401

    try:
        cancelled = cancel_booking(app.config["DATABASE_PATH"], booking_id, user_id)
    except LookupError as err:
        return jsonify({
            "error": str(err),
            "code": "NOT_FOUND"
        }), 404
    except PermissionError as err:
        return jsonify({
            "error": str(err),
            "code": "FORBIDDEN"
        }), 403
    except ValueError as err:
        return jsonify({
            "error": str(err),
            "code": "ALREADY_CANCELLED"
        }), 400

    # Stop grace period tracking
    booking_tracker.cancel_tracking(booking_id)

    # Broadcast updated state to all clients
    broadcast_status_change()

    return jsonify({
        "success": True,
        "message": f"Booking #{booking_id} cancelled successfully",
        "booking": cancelled
    }), 200


@app.route("/api/port", methods=["POST"])
def api_change_port():
    """
    Changes Arduino serial port dynamically.
    Closes previous serial connection cleanly before opening the new port.
    """
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({
            "error": "Authentication required to change serial configuration",
            "code": "UNAUTHORIZED"
        }), 401

    data = request.get_json(silent=True) or request.form
    new_port = data.get("port", "").strip()

    if not new_port:
        return jsonify({
            "error": "Port name is required (e.g., COM8)",
            "code": "INVALID_PORT"
        }), 400

    try:
        serial_manager.switch_port(new_port)
    except Exception as ex:
        return jsonify({
            "error": f"Failed to switch port: {str(ex)}",
            "code": "PORT_ERROR"
        }), 500

    broadcast_status_change()

    snapshot = serial_manager.get_snapshot()
    return jsonify({
        "success": True,
        "message": f"Serial port switched to {new_port}",
        "connection": snapshot
    }), 200


@app.route("/api/csrf-token", methods=["GET"])
def api_csrf_token():
    """Returns current CSRF token for the session."""
    token = ensure_csrf_token()
    return jsonify({"csrf_token": token}), 200


# ============================================================================
# Main Entry Point
# ============================================================================

if __name__ == "__main__":
    host = os.environ.get("FLASK_HOST", "0.0.0.0")
    port = int(os.environ.get("FLASK_PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"

    print("=" * 60)
    print(" Smart Parking Monitoring & Mobile Booking System")
    print(f" Listening on http://{host}:{port}")
    print(f" SQLite Database: {app.config['DATABASE_PATH']}")
    print(f" Target Serial Port: {serial_manager.port}")
    print(" Accessible on local LAN via your laptop IPv4 address")
    print("=" * 60)

    # Threaded mode ensures SSE streams and API calls do not block each other
    app.run(host=host, port=port, debug=debug, threaded=True)
