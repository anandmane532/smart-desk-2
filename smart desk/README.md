# Smart Parking Monitoring & Mobile Booking System

A complete, production-grade IoT smart parking monitoring and desk reservation system built from scratch. Connects an **Arduino UNO** with **4 Infrared (IR) obstacle sensors** over USB serial to a **Python Flask backend** and serves a **responsive mobile-first web interface** with live Server-Sent Events (SSE).

---

## Table of Contents
1. [Project Overview](#1-project-overview)
2. [Hardware and Wiring](#2-hardware-and-wiring)
3. [Python Installation](#3-python-installation)
4. [Virtual Environment Setup](#4-virtual-environment-setup)
5. [Dependency Installation](#5-dependency-installation)
6. [Arduino Sketch Upload Instructions](#6-arduino-sketch-upload-instructions)
7. [COM-Port Identification](#7-com-port-identification)
8. [Backend Startup Commands](#8-backend-startup-commands)
9. [Mobile Wi-Fi Access](#9-mobile-wi-fi-access)
10. [Registration and Booking Instructions](#10-registration-and-booking-instructions)
11. [Cancellation Instructions](#11-cancellation-instructions)
12. [Testing Instructions](#12-testing-instructions)
13. [GitHub Setup](#13-github-setup)
14. [Security Limitations](#14-security-limitations)
15. [Troubleshooting](#15-troubleshooting)

---

## 1. Project Overview

This system monitors physical occupancy across 4 parking spaces (desks) and allows registered users to book and cancel parking slots directly from their smartphones over a local Wi-Fi network.

### Architecture Highlights:
- **Hardware Layer**: Arduino UNO reads 4 digital IR sensors and broadcasts standard machine-readable telemetry packets (`IR1:1,IR2:1,IR3:0,IR4:1`) every 500 ms at 9600 baud.
- **Serial Manager**: Thread-safe background reader using `pyserial` with strict format validation, automatic reconnection, port switching, and 3-second freshness enforcement.
- **Database Layer**: SQLite database (`smart_parking.db`) with foreign keys, WAL mode, transaction isolation, and a **partial unique index** (`WHERE status = 'ACTIVE'`) guaranteeing zero double-bookings under concurrent conditions.
- **Authentication & Security**: Scrypt password hashing (`werkzeug.security`), secure session cookies (`HttpOnly`, `SameSite=Lax`), and strict CSRF protection on all state-changing endpoints.
- **Real-Time Web Interface**: Mobile-first responsive UI built with HTML5, CSS3, and Vanilla JavaScript with Server-Sent Events (SSE) streaming live updates with keep-alive heartbeats and automatic fallback polling.

### State Rules:
| Physical Sensor State | Reservation State | Effective Display State | Booking Permitted? |
| :--- | :--- | :--- | :--- |
| **UNOCCUPIED** (1) | **NOT_BOOKED** | **AVAILABLE** (Green) | **Yes** |
| **UNOCCUPIED** (1) | **BOOKED** | **BOOKED** (Amber) | **No** (Already Reserved) |
| **OCCUPIED** (0) | *Any* | **OCCUPIED** (Red) | **No** (Physically Blocked) |
| **UNKNOWN** / Stale | *Any* | **UNKNOWN** (Grey) | **No** (Sensor Unavailable) |

---

## 2. Hardware and Wiring

### Bill of Materials:
- 1x Arduino UNO (or compatible ATmega328P board)
- 1x USB A-to-B cable (connecting Arduino to Windows laptop)
- 4x Active-Low Infrared (IR) Obstacle Avoidance Sensors (FC-51 or equivalent)
- Breadboard & jumper wires

### Pin Connections:

| Sensor Module | Arduino UNO Pin | Assigned Desk |
| :--- | :--- | :--- |
| **IR1 OUT** | **Digital Pin 2 (D2)** | Desk 1 |
| **IR2 OUT** | **Digital Pin 3 (D3)** | Desk 2 |
| **IR3 OUT** | **Digital Pin 4 (D4)** | Desk 3 |
| **IR4 OUT** | **Digital Pin 5 (D5)** | Desk 4 |
| **VCC (All 4)** | **5V Pin** | Power |
| **GND (All 4)** | **GND Pin** | Ground |

### Sensor Detection Logic:
- **Obstacle detected (Car parked)**: Sensor output drops to digital `0` (`LOW`).
- **No obstacle detected (Empty slot)**: Sensor output remains digital `1` (`HIGH`).
- Adjust the sensitivity potentiometer on each IR module so that the indicator LED lights up only when a car/object enters the bay.

---

## 3. Python Installation

The system requires **Python 3.10+** (tested and verified on Python 3.14).

1. Download the official installer from [python.org](https://www.python.org/downloads/).
2. During installation on Windows, **check "Add python.exe to PATH"**.
3. Verify the installation in PowerShell:
   ```powershell
   python --version
   ```

---

## 4. Virtual Environment Setup

Isolate project dependencies by setting up a Python virtual environment:

```powershell
# Navigate to project folder
cd "d:\smart desk\smart-parking"

# Create virtual environment
python -m venv venv

# Activate on Windows PowerShell
.\venv\Scripts\Activate.ps1
```

*(If PowerShell script execution is restricted, run: `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`)*

---

## 5. Dependency Installation

Install all required packages from `requirements.txt`:

```powershell
pip install -r requirements.txt
```

Required packages:
- `Flask>=3.0.0`: Web framework and REST APIs.
- `pyserial>=3.5`: USB serial communication with Arduino UNO.
- `python-dotenv>=1.0.0`: Environment variable configuration.
- `pytest>=8.0.0`: Automated test suite.

---

## 6. Arduino Sketch Upload Instructions

1. Connect your Arduino UNO to your Windows laptop via USB.
2. Download and open the **Arduino IDE** (or use the Arduino CLI).
3. Open `arduino/smart_parking.ino`.
4. In the Arduino IDE menu:
   - Select **Tools > Board > Arduino Uno**.
   - Select **Tools > Port > COM...** (choose the port detected, e.g. COM8).
5. Click **Upload** (Ctrl + U).
6. Verify the serial output by opening **Tools > Serial Monitor** set to **9600 baud**. You should see lines like:
   ```text
   IR1:1,IR2:1,IR3:0,IR4:1
   ```
   **Important**: Close the Arduino Serial Monitor before starting Flask, as Windows does not permit two applications to open the same serial port simultaneously.

---

## 7. COM-Port Identification

To identify the COM port assigned by Windows:
1. Press `Win + X` and select **Device Manager**.
2. Expand the **Ports (COM & LPT)** section.
3. Look for **Arduino Uno (COMx)** or **USB-SERIAL CH340 (COMx)**.
4. Note the port number (e.g., `COM8` or `COM3`).
5. Configure this port in `.env` (`SERIAL_PORT=COM8`) or dynamically inside the web app's **Connection Settings** tab.

---

## 8. Backend Startup Commands

Configure environment variables:
```powershell
# Copy example environment configuration
copy .env.example .env
```

Start the Flask server:
```powershell
# From the project root or smart-parking directory:
python app.py
```

Console Output:
```text
============================================================
 Smart Parking Monitoring & Mobile Booking System
 Listening on http://0.0.0.0:5000
 SQLite Database: smart_parking.db
 Target Serial Port: COM8
 Accessible on local LAN via your laptop IPv4 address
============================================================
```

---

## 9. Mobile Wi-Fi Access

To access the parking dashboard from your smartphone:

1. **Connect both your laptop and mobile phone to the same Wi-Fi network**.
2. Find your laptop's local IPv4 address in Windows PowerShell:
   ```powershell
   ipconfig
   ```
   Look for the `Wireless LAN adapter Wi-Fi` section and find:
   ```text
   IPv4 Address. . . . . . . . . . . : 192.168.1.45
   ```
3. **Windows Firewall Rule**:
   If your phone cannot load the page, allow inbound traffic on port 5000:
   ```powershell
   # Run in an Administrator PowerShell window:
   New-NetFirewallRule -DisplayName "Smart Parking Flask (Port 5000)" -Direction Inbound -LocalPort 5000 -Protocol TCP -Action Allow
   ```
4. Open the browser on your phone (Chrome / Safari):
   ```text
   http://192.168.1.45:5000
   ```
   *(Replace `192.168.1.45` with your laptop's actual IPv4 address. Do not use `localhost` on your phone!)*

---

## 10. Registration and Booking Instructions

1. On your phone, tap **Account** or **Log In**.
2. Tap **Sign up here** to register a new user:
   - Enter a username (3-30 characters).
   - Enter a secure password (min 6 characters).
   - Submit the form. You are automatically logged in.
3. Return to the **Dashboard** tab:
   - Green cards indicate spaces that are physically unoccupied and unreserved.
   - Tap the large touch-friendly **Book Space** button on an available desk (e.g. Desk 1).
   - The card instantly transitions to **BOOKED** (Amber).
   - The occupancy and booking counts update in real time across all connected devices.

---

## 11. Cancellation Instructions

1. Switch to the **My Bookings** tab or view the card on the **Dashboard**.
2. On any of your active bookings, tap **Cancel Booking** / **Cancel My Booking**.
3. Confirm the cancellation prompt.
4. The system sends `POST /api/bookings/<id>/cancel` with your CSRF token.
5. The reservation is marked `CANCELLED` with a timestamp in SQLite, and the space returns to `AVAILABLE` (if physically unoccupied).
6. Note: Physical sensor state is never altered by booking cancellations.

---

## 12. Testing Instructions

Run the automated test suite using `pytest`:

```powershell
# Run all tests with verbose output
pytest -v
```

### Test Coverage Highlights:
- `test_parser.py`:
  - Valid Arduino message parsing (`IR1:1,IR2:1,IR3:0,IR4:1`).
  - Strict rejection of truncated, malformed, noisy, or partial serial strings.
  - Physical state mappings (1 -> UNOCCUPIED, 0 -> OCCUPIED, None -> UNKNOWN).
  - Stale detection logic (> 3 seconds marks all desks UNKNOWN).
  - Serial disconnection state transitions and port switching.
- `test_booking.py`:
  - User registration validation, uniqueness, and password hashing.
  - Session authentication enforcement on all private endpoints.
  - CSRF token validation and HTTP 403 enforcement.
  - Booking available desks (HTTP 201).
  - Rejecting physically occupied desks (HTTP 409).
  - Rejecting stale/disconnected sensor data (HTTP 503).
  - Rejecting duplicate bookings on already reserved desks (HTTP 409).
  - Concurrent booking race-condition prevention via database partial unique index.
  - Proper ownership authorization on cancellations (HTTP 403 if attempting to cancel another user's reservation).
  - Rejection of double cancellations (HTTP 400).
  - Verification that cancellations never mutate physical sensor readings.
  - Database persistence across connection resets.
  - Dashboard statistics and occupancy percentage math.

---

## 13. GitHub Setup

Initialize and commit your code to Git:

```powershell
# Check git status
git status

# Add files
git add .

# Commit changes
git commit -m "Initial commit: Complete smart parking monitoring and booking system"
```

The repository includes a comprehensive `.gitignore` preventing commit of `.venv`, `__pycache__`, `smart_parking.db`, and local `.env` files.

---

## 14. Security Limitations

- **Local Network Deployment**: The built-in Flask server is designed for local LAN development and trusted testing. It is **not** an enterprise production server.
- **Do not expose directly to the public internet** via port forwarding without a production WSGI server (e.g. Gunicorn/Waitress) and a reverse proxy with TLS/HTTPS (e.g. Nginx/Caddy).
- **Session Security**: Session cookies have `Secure=False` enabled by default to allow local HTTP access over home Wi-Fi. In an HTTPS deployment, set `SESSION_COOKIE_SECURE = True`.
- **Secret Key**: Ensure you set a cryptographically strong `SECRET_KEY` in `.env` instead of relying on ephemeral runtime keys.

---

## 15. Troubleshooting

| Issue | Cause | Solution |
| :--- | :--- | :--- |
| **"Arduino Disconnected on COM8"** | Arduino unplugged or port occupied. | Verify USB connection in Device Manager. If Arduino IDE Serial Monitor is open, close it. |
| **"Sensor data is stale (>3s)"** | Arduino loop halted or loose wiring. | Press the physical Reset button on the Arduino UNO. Verify 5V power LED on IR sensors. |
| **Phone cannot load webpage** | Windows Firewall or wrong IP. | Run `ipconfig` on laptop. Ensure phone and laptop are on same Wi-Fi. Add Windows Firewall inbound rule for port 5000. |
| **"Desk is physically occupied (409)"** | IR sensor detects object or sensitivity too high. | Adjust the onboard sensitivity potentiometer on the IR sensor until the obstacle LED turns off when clear. |
| **"Invalid or missing CSRF token"** | Expired session or missing header. | Refresh the page to obtain a fresh CSRF token. |
