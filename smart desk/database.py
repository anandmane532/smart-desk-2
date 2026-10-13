"""
Database management module for Smart Parking Monitoring System.
Uses SQLite with foreign keys, indexes, and a partial unique index
enforcing at most one active reservation per desk.
"""

import sqlite3
import os
from typing import Optional, Dict, Any, List

DEFAULT_DB_PATH = os.environ.get("DATABASE_PATH", "smart_parking.db")


def get_db_connection(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """
    Returns an SQLite connection configured with Row factory,
    foreign key constraints enabled, and WAL journal mode.
    """
    conn = sqlite3.connect(db_path, timeout=10.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    # WAL mode allows concurrent readers while a writer commits
    conn.execute("PRAGMA journal_mode = WAL;")
    return conn


def init_db(db_path: str = DEFAULT_DB_PATH) -> None:
    """
    Initializes database tables and indexes if they do not already exist.
    """
    conn = get_db_connection(db_path)
    try:
        with conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS bookings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    desk_id INTEGER NOT NULL CHECK(desk_id >= 1 AND desk_id <= 4),
                    status TEXT NOT NULL CHECK(status IN ('ACTIVE', 'CANCELLED')),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    cancelled_at TIMESTAMP NULL
                );

                -- Partial unique index ensures at most ONE active reservation per desk
                CREATE UNIQUE INDEX IF NOT EXISTS idx_active_desk_booking
                ON bookings(desk_id)
                WHERE status = 'ACTIVE';

                -- Indexes for fast query performance
                CREATE INDEX IF NOT EXISTS idx_bookings_user_id ON bookings(user_id);
                CREATE INDEX IF NOT EXISTS idx_bookings_status ON bookings(status);
                CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
                """
            )
    finally:
        conn.close()


def create_user(db_path: str, username: str, password_hash: str) -> Dict[str, Any]:
    """
    Registers a new user. Raises sqlite3.IntegrityError if username exists.
    """
    conn = get_db_connection(db_path)
    try:
        with conn:
            cursor = conn.execute(
                "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                (username.strip(), password_hash)
            )
            user_id = cursor.lastrowid
            row = conn.execute(
                "SELECT id, username, created_at FROM users WHERE id = ?",
                (user_id,)
            ).fetchone()
            return dict(row)
    finally:
        conn.close()


def get_user_by_username(db_path: str, username: str) -> Optional[Dict[str, Any]]:
    """
    Fetches user row by username.
    """
    conn = get_db_connection(db_path)
    try:
        row = conn.execute(
            "SELECT id, username, password_hash, created_at FROM users WHERE username = ?",
            (username.strip(),)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_user_by_id(db_path: str, user_id: int) -> Optional[Dict[str, Any]]:
    """
    Fetches user row by user ID without password hash.
    """
    conn = get_db_connection(db_path)
    try:
        row = conn.execute(
            "SELECT id, username, created_at FROM users WHERE id = ?",
            (user_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_active_bookings(db_path: str) -> Dict[int, Dict[str, Any]]:
    """
    Returns a dictionary mapping desk_id -> active booking details.
    """
    conn = get_db_connection(db_path)
    try:
        rows = conn.execute(
            """
            SELECT b.id, b.user_id, b.desk_id, b.status, b.created_at, u.username
            FROM bookings b
            JOIN users u ON b.user_id = u.id
            WHERE b.status = 'ACTIVE'
            """
        ).fetchall()
        result = {}
        for r in rows:
            result[r["desk_id"]] = dict(r)
        return result
    finally:
        conn.close()


def get_active_booking_for_desk(db_path: str, desk_id: int) -> Optional[Dict[str, Any]]:
    """
    Returns active booking for a specific desk, if any.
    """
    conn = get_db_connection(db_path)
    try:
        row = conn.execute(
            """
            SELECT b.id, b.user_id, b.desk_id, b.status, b.created_at, u.username
            FROM bookings b
            JOIN users u ON b.user_id = u.id
            WHERE b.desk_id = ? AND b.status = 'ACTIVE'
            """,
            (desk_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_booking_by_id(db_path: str, booking_id: int) -> Optional[Dict[str, Any]]:
    """
    Returns a booking row by ID.
    """
    conn = get_db_connection(db_path)
    try:
        row = conn.execute(
            """
            SELECT b.id, b.user_id, b.desk_id, b.status, b.created_at, b.cancelled_at, u.username
            FROM bookings b
            JOIN users u ON b.user_id = u.id
            WHERE b.id = ?
            """,
            (booking_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_user_bookings(db_path: str, user_id: int) -> List[Dict[str, Any]]:
    """
    Returns all bookings (active and cancelled) for a specific user,
    ordered newest first.
    """
    conn = get_db_connection(db_path)
    try:
        rows = conn.execute(
            """
            SELECT b.id, b.user_id, b.desk_id, b.status, b.created_at, b.cancelled_at, u.username
            FROM bookings b
            JOIN users u ON b.user_id = u.id
            WHERE b.user_id = ?
            ORDER BY b.created_at DESC, b.id DESC
            """,
            (user_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def create_booking(db_path: str, user_id: int, desk_id: int) -> Dict[str, Any]:
    """
    Creates an ACTIVE booking inside a database transaction.
    Raises sqlite3.IntegrityError if desk already has an active reservation
    enforced by the partial unique index.
    """
    conn = get_db_connection(db_path)
    try:
        with conn:
            cursor = conn.execute(
                """
                INSERT INTO bookings (user_id, desk_id, status)
                VALUES (?, ?, 'ACTIVE')
                """,
                (user_id, desk_id)
            )
            booking_id = cursor.lastrowid
            row = conn.execute(
                """
                SELECT b.id, b.user_id, b.desk_id, b.status, b.created_at, u.username
                FROM bookings b
                JOIN users u ON b.user_id = u.id
                WHERE b.id = ?
                """,
                (booking_id,)
            ).fetchone()
            return dict(row)
    finally:
        conn.close()


def cancel_booking(db_path: str, booking_id: int, user_id: int) -> Dict[str, Any]:
    """
    Cancels an active booking for a user.
    Enforces authorization: booking must belong to user_id.
    Raises:
      LookupError: booking not found
      PermissionError: booking belongs to another user
      ValueError: booking already cancelled
    """
    conn = get_db_connection(db_path)
    try:
        with conn:
            row = conn.execute(
                "SELECT id, user_id, desk_id, status, created_at, cancelled_at FROM bookings WHERE id = ?",
                (booking_id,)
            ).fetchone()
            if not row:
                raise LookupError(f"Booking {booking_id} does not exist")
            
            if row["user_id"] != user_id:
                raise PermissionError("You do not have permission to cancel this booking")

            if row["status"] != "ACTIVE":
                raise ValueError("This booking is already cancelled")

            conn.execute(
                """
                UPDATE bookings
                SET status = 'CANCELLED', cancelled_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (booking_id,)
            )

            updated = conn.execute(
                """
                SELECT b.id, b.user_id, b.desk_id, b.status, b.created_at, b.cancelled_at, u.username
                FROM bookings b
                JOIN users u ON b.user_id = u.id
                WHERE b.id = ?
                """,
                (booking_id,)
            ).fetchone()
            return dict(updated)
    finally:
        conn.close()


def auto_cancel_booking(db_path: str, booking_id: int) -> Optional[Dict[str, Any]]:
    """
    Cancels an active booking automatically by system (e.g. no-show timeout).
    Does not require a user session.
    Returns the updated booking dict or None if booking was not active.
    """
    conn = get_db_connection(db_path)
    try:
        with conn:
            row = conn.execute(
                "SELECT id, user_id, desk_id, status FROM bookings WHERE id = ?",
                (booking_id,)
            ).fetchone()
            if not row or row["status"] != "ACTIVE":
                return None

            conn.execute(
                """
                UPDATE bookings
                SET status = 'CANCELLED', cancelled_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'ACTIVE'
                """,
                (booking_id,)
            )

            updated = conn.execute(
                """
                SELECT b.id, b.user_id, b.desk_id, b.status, b.created_at, b.cancelled_at, u.username
                FROM bookings b
                JOIN users u ON b.user_id = u.id
                WHERE b.id = ?
                """,
                (booking_id,)
            ).fetchone()
            return dict(updated) if updated else None
    finally:
        conn.close()

