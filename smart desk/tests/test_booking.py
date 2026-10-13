"""
Integration and end-to-end tests for Authentication, CSRF Protection,
Booking, Cancellation, Concurrency, Database Persistence, and Dashboard Stats.
"""

import os
import sys
import time
import pytest
import sqlite3
from concurrent.futures import ThreadPoolExecutor

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import app, serial_manager
from database import init_db, get_db_connection


@pytest.fixture
def test_setup(tmp_path):
    """
    Sets up a temporary SQLite database and configures the Flask test client
    in test mode with controlled sensor states.
    """
    db_file = str(tmp_path / "test_parking.db")
    app.config["DATABASE_PATH"] = db_file
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret-key-12345"

    init_db(db_file)
    serial_manager.set_test_mode(True)

    with app.test_client() as client:
        yield client, db_file

    # Teardown
    serial_manager.set_test_mode(False)


def get_session_csrf_token(client):
    """Extracts CSRF token for the active session via /api/csrf-token."""
    res = client.get("/api/csrf-token")
    assert res.status_code == 200
    return res.get_json()["csrf_token"]


def register_and_login(client, username="alice", password="password123"):
    """Helper to register and log in a test user, returning the CSRF token."""
    res = client.post("/api/register", json={
        "username": username,
        "password": password
    })
    assert res.status_code in (200, 201)
    data = res.get_json()
    return data.get("csrf_token") or get_session_csrf_token(client)


# ============================================================================
# Authentication & CSRF Tests
# ============================================================================

def test_user_registration_and_login(test_setup):
    """Tests registration validation, uniqueness, and login flow."""
    client, _ = test_setup

    # 1. Invalid registration (short username)
    res = client.post("/api/register", json={"username": "ab", "password": "validpassword"})
    assert res.status_code == 400

    # 2. Invalid registration (short password)
    res = client.post("/api/register", json={"username": "bob", "password": "123"})
    assert res.status_code == 400

    # 3. Valid registration
    res = client.post("/api/register", json={"username": "bob", "password": "validpassword"})
    assert res.status_code == 201
    assert res.get_json()["user"]["username"] == "bob"

    # 4. Duplicate registration rejected
    res_dup = client.post("/api/register", json={"username": "bob", "password": "anotherpassword"})
    assert res_dup.status_code == 409

    # 5. Logout
    csrf_token = get_session_csrf_token(client)
    res_logout = client.post("/api/logout", headers={"X-CSRF-Token": csrf_token})
    assert res_logout.status_code == 200

    # 6. Login with incorrect password
    res_fail = client.post("/api/login", json={"username": "bob", "password": "wrongpassword"})
    assert res_fail.status_code == 401

    # 7. Login with valid credentials
    res_success = client.post("/api/login", json={"username": "bob", "password": "validpassword"})
    assert res_success.status_code == 200
    assert res_success.get_json()["user"]["username"] == "bob"


def test_auth_enforcement_on_protected_endpoints(test_setup):
    """Ensures unauthenticated requests to bookings endpoints return HTTP 401."""
    client, _ = test_setup

    # Try GET /api/bookings without session
    res = client.get("/api/bookings")
    assert res.status_code == 401

    # Try POST /api/bookings without session
    csrf = get_session_csrf_token(client)
    res_post = client.post("/api/bookings", json={"desk_id": 1}, headers={"X-CSRF-Token": csrf})
    assert res_post.status_code == 401

    # Try POST /api/bookings/1/cancel without session
    res_cancel = client.post("/api/bookings/1/cancel", headers={"X-CSRF-Token": csrf})
    assert res_cancel.status_code == 401


def test_csrf_protection_enforcement(test_setup):
    """Verifies that state-changing requests without or with invalid CSRF token return HTTP 403."""
    client, _ = test_setup
    register_and_login(client, "carol", "secretpass123")

    # Set fresh unoccupied sensor readings
    serial_manager.inject_test_message("IR1:1,IR2:1,IR3:1,IR4:1")

    # Request without CSRF header or body -> Must return 403 Forbidden
    res_no_token = client.post("/api/bookings", json={"desk_id": 1})
    assert res_no_token.status_code == 403
    assert res_no_token.get_json()["code"] == "CSRF_ERROR"

    # Request with forged/invalid CSRF token -> Must return 403 Forbidden
    res_bad_token = client.post(
        "/api/bookings",
        json={"desk_id": 1},
        headers={"X-CSRF-Token": "forged_malicious_token_12345"}
    )
    assert res_bad_token.status_code == 403
    assert res_bad_token.get_json()["code"] == "CSRF_ERROR"


# ============================================================================
# Booking & Physical Sensor Validation Tests
# ============================================================================

def test_booking_available_desk(test_setup):
    """Tests successful booking when desk is physically unoccupied and not booked."""
    client, _ = test_setup
    csrf = register_and_login(client, "dave", "mypassword1")

    # Inject valid, fresh sensor reading (1 = UNOCCUPIED for all desks)
    serial_manager.inject_test_message("IR1:1,IR2:1,IR3:1,IR4:1")

    # Book Desk 1
    res = client.post(
        "/api/bookings",
        json={"desk_id": 1},
        headers={"X-CSRF-Token": csrf}
    )
    assert res.status_code == 201
    data = res.get_json()
    assert data["success"] is True
    assert data["booking"]["desk_id"] == 1
    assert data["booking"]["status"] == "ACTIVE"


def test_reject_booking_physically_occupied_desk(test_setup):
    """Tests that booking an occupied desk (sensor value 0) is rejected with HTTP 409."""
    client, _ = test_setup
    csrf = register_and_login(client, "emma", "password123")

    # Desk 2 is physically OCCUPIED (IR2:0)
    serial_manager.inject_test_message("IR1:1,IR2:0,IR3:1,IR4:1")

    res = client.post(
        "/api/bookings",
        json={"desk_id": 2},
        headers={"X-CSRF-Token": csrf}
    )
    assert res.status_code == 409
    assert "physically occupied" in res.get_json()["error"].lower()


def test_reject_booking_stale_or_unknown_desk(test_setup):
    """Tests that booking is rejected with HTTP 503 if sensor readings are stale or unknown."""
    client, _ = test_setup
    csrf = register_and_login(client, "frank", "password123")

    # 1. Stale readings (> 3 seconds ago)
    stale_time = time.time() - 5.0
    serial_manager.inject_test_message("IR1:1,IR2:1,IR3:1,IR4:1", timestamp=stale_time)

    res = client.post(
        "/api/bookings",
        json={"desk_id": 1},
        headers={"X-CSRF-Token": csrf}
    )
    assert res.status_code == 503
    assert "stale" in res.get_json()["error"].lower()

    # 2. Disconnected state
    serial_manager.inject_test_connection_state(connected=False, status="DISCONNECTED")

    res_disc = client.post(
        "/api/bookings",
        json={"desk_id": 1},
        headers={"X-CSRF-Token": csrf}
    )
    assert res_disc.status_code == 503
    assert "not connected" in res_disc.get_json()["error"].lower()


def test_reject_duplicate_booking(test_setup):
    """Tests that a desk already booked cannot be reserved again (HTTP 409)."""
    client, _ = test_setup
    csrf_user1 = register_and_login(client, "george", "password123")

    serial_manager.inject_test_message("IR1:1,IR2:1,IR3:1,IR4:1")

    # First booking succeeds
    res1 = client.post("/api/bookings", json={"desk_id": 3}, headers={"X-CSRF-Token": csrf_user1})
    assert res1.status_code == 201

    # Second booking attempt for same desk fails with 409
    res2 = client.post("/api/bookings", json={"desk_id": 3}, headers={"X-CSRF-Token": csrf_user1})
    assert res2.status_code == 409
    assert "already" in res2.get_json()["error"].lower()


def test_concurrent_booking_database_constraint(test_setup):
    """
    Simulates concurrent booking attempts on the same desk using the SQLite
    partial unique index. Ensures at most one active booking succeeds.
    """
    _, db_file = test_setup

    # Register two users directly in DB
    conn = get_db_connection(db_file)
    with conn:
        conn.execute("INSERT INTO users (username, password_hash) VALUES ('user1', 'hash')")
        conn.execute("INSERT INTO users (username, password_hash) VALUES ('user2', 'hash')")
    conn.close()

    results = []

    def attempt_booking(user_id, desk_id):
        c = get_db_connection(db_file)
        try:
            with c:
                c.execute(
                    "INSERT INTO bookings (user_id, desk_id, status) VALUES (?, ?, 'ACTIVE')",
                    (user_id, desk_id)
                )
            results.append("SUCCESS")
        except sqlite3.IntegrityError:
            results.append("CONFLICT")
        finally:
            c.close()

    # Run two simultaneous booking inserts for Desk 4
    with ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(attempt_booking, 1, 4)
        f2 = executor.submit(attempt_booking, 2, 4)
        f1.result()
        f2.result()

    # Exactly one must succeed and one must be caught by UNIQUE index constraint
    assert results.count("SUCCESS") == 1
    assert results.count("CONFLICT") == 1


# ============================================================================
# Cancellation Tests
# ============================================================================

def test_cancellation_flow_and_authorization(test_setup):
    """Tests successful cancellation, unauthorized cancellation, and double cancellation."""
    client, _ = test_setup
    csrf_u1 = register_and_login(client, "hannah", "pass12345")

    serial_manager.inject_test_message("IR1:1,IR2:1,IR3:1,IR4:1")

    # Hannah books Desk 1
    res_book = client.post("/api/bookings", json={"desk_id": 1}, headers={"X-CSRF-Token": csrf_u1})
    assert res_book.status_code == 201
    booking_id = res_book.get_json()["booking"]["id"]

    # Register User 2 (Ian)
    csrf_u2 = register_and_login(client, "ian", "pass12345")

    # Ian attempts to cancel Hannah's booking -> HTTP 403 Forbidden
    res_unauth = client.post(
        f"/api/bookings/{booking_id}/cancel",
        headers={"X-CSRF-Token": csrf_u2}
    )
    assert res_unauth.status_code == 403
    assert "permission" in res_unauth.get_json()["error"].lower()

    # Log back in as Hannah
    client.post("/api/login", json={"username": "hannah", "password": "pass12345"})
    csrf_u1_new = get_session_csrf_token(client)

    # Hannah cancels her own booking -> HTTP 200 Success
    snap_before = serial_manager.get_snapshot()
    res_cancel = client.post(
        f"/api/bookings/{booking_id}/cancel",
        headers={"X-CSRF-Token": csrf_u1_new}
    )
    assert res_cancel.status_code == 200
    assert res_cancel.get_json()["booking"]["status"] == "CANCELLED"

    # Cancellation must never change the physical sensor state
    snap_after = serial_manager.get_snapshot()
    assert snap_after["physical_states"][1] == snap_before["physical_states"][1]

    # Hannah attempts to cancel the already cancelled booking -> HTTP 400
    res_double = client.post(
        f"/api/bookings/{booking_id}/cancel",
        headers={"X-CSRF-Token": csrf_u1_new}
    )
    assert res_double.status_code == 400
    assert "already cancelled" in res_double.get_json()["error"].lower()


# ============================================================================
# Database Persistence & Statistics Tests
# ============================================================================

def test_database_persistence(test_setup):
    """Verifies that bookings and cancellation history persist across database connections."""
    client, db_file = test_setup
    csrf = register_and_login(client, "jack", "pass12345")

    serial_manager.inject_test_message("IR1:1,IR2:1,IR3:1,IR4:1")

    res_b = client.post("/api/bookings", json={"desk_id": 2}, headers={"X-CSRF-Token": csrf})
    b_id = res_b.get_json()["booking"]["id"]

    # Query fresh direct DB connection
    conn = get_db_connection(db_file)
    row = conn.execute("SELECT desk_id, status FROM bookings WHERE id = ?", (b_id,)).fetchone()
    conn.close()

    assert row is not None
    assert row["desk_id"] == 2
    assert row["status"] == "ACTIVE"


def test_dashboard_statistics(test_setup):
    """Validates accurate calculations of occupied, available, booked, and occupancy percentage."""
    client, _ = test_setup
    csrf = register_and_login(client, "kate", "pass12345")

    # Desk 1: Unoccupied (1), Desk 2: Occupied (0), Desk 3: Unoccupied (1), Desk 4: Occupied (0)
    serial_manager.inject_test_message("IR1:1,IR2:0,IR3:1,IR4:0")

    # Book Desk 1
    client.post("/api/bookings", json={"desk_id": 1}, headers={"X-CSRF-Token": csrf})

    # Fetch status
    res = client.get("/api/status")
    assert res.status_code == 200
    stats = res.get_json()["stats"]

    # Desks state summary:
    # Desk 1: Unoccupied + Booked = BOOKED
    # Desk 2: Occupied (0) = OCCUPIED
    # Desk 3: Unoccupied + Not Booked = AVAILABLE
    # Desk 4: Occupied (0) = OCCUPIED
    assert stats["total"] == 4
    assert stats["physically_occupied"] == 2  # Desks 2 and 4
    assert stats["physically_unoccupied"] == 2  # Desks 1 and 3
    assert stats["active_bookings"] == 1  # Desk 1
    assert stats["available"] == 1  # Desk 3 only (unoccupied & not booked)
    # Occupancy % = 2 / 4 = 50.0%
    assert stats["occupancy_percentage"] == 50.0


def test_auto_release_unoccupied_booking(test_setup):
    """Validates that if a desk is booked and does not change to occupied within grace window, it returns to available."""
    client, db_file = test_setup
    from app import booking_tracker
    old_timeout = booking_tracker.timeout_seconds
    booking_tracker.timeout_seconds = 0.3  # fast test timeout

    try:
        csrf = register_and_login(client, "timer_user", "pass12345")
        serial_manager.inject_test_message("IR1:1,IR2:1,IR3:1,IR4:1")

        # Book Desk 1
        res = client.post("/api/bookings", json={"desk_id": 1}, headers={"X-CSRF-Token": csrf})
        assert res.status_code == 201

        # Immediately check status: Desk 1 is BOOKED
        res_status = client.get("/api/status")
        desk1 = [d for d in res_status.get_json()["desks"] if d["desk_id"] == 1][0]
        assert desk1["effective_state"] == "BOOKED"
        assert desk1["reservation_state"] == "BOOKED"
        assert desk1["booking"]["pending_checkin"] is True

        # Wait for grace period timeout (0.5s > 0.3s)
        time.sleep(0.5)

        # Status must now be AVAILABLE and booking CANCELLED in DB
        res_after = client.get("/api/status")
        desk1_after = [d for d in res_after.get_json()["desks"] if d["desk_id"] == 1][0]
        assert desk1_after["effective_state"] == "AVAILABLE"
        assert desk1_after["reservation_state"] == "NOT_BOOKED"

        # Check DB status
        conn = get_db_connection(db_file)
        row = conn.execute("SELECT status FROM bookings WHERE desk_id = 1").fetchone()
        conn.close()
        assert row["status"] == "CANCELLED"
    finally:
        booking_tracker.timeout_seconds = old_timeout


def test_no_auto_release_when_physically_occupied(test_setup):
    """Validates that when a booked desk changes to OCCUPIED within the grace window, it is NOT released."""
    client, db_file = test_setup
    from app import booking_tracker
    old_timeout = booking_tracker.timeout_seconds
    booking_tracker.timeout_seconds = 0.4

    try:
        csrf = register_and_login(client, "occupy_user", "pass12345")
        serial_manager.inject_test_message("IR1:1,IR2:1,IR3:1,IR4:1")

        # Book Desk 2
        res = client.post("/api/bookings", json={"desk_id": 2}, headers={"X-CSRF-Token": csrf})
        assert res.status_code == 201

        # Simulate vehicle/person occupying Desk 2 before timeout
        time.sleep(0.1)
        serial_manager.inject_test_message("IR1:1,IR2:0,IR3:1,IR4:1")

        # Wait past grace period
        time.sleep(0.5)

        # Desk 2 should remain OCCUPIED and booking ACTIVE
        res_after = client.get("/api/status")
        desk2_after = [d for d in res_after.get_json()["desks"] if d["desk_id"] == 2][0]
        assert desk2_after["physical_state"] == "OCCUPIED"
        assert desk2_after["reservation_state"] == "BOOKED"
        assert desk2_after["effective_state"] == "OCCUPIED"

        conn = get_db_connection(db_file)
        row = conn.execute("SELECT status FROM bookings WHERE desk_id = 2").fetchone()
        conn.close()
        assert row["status"] == "ACTIVE"
    finally:
        booking_tracker.timeout_seconds = old_timeout

