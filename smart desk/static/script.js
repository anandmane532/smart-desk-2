/**
 * Smart Parking Monitoring and Mobile Booking System - Frontend Client
 * Handles real-time SSE stream, mobile tab navigation, CSRF headers,
 * dynamic dashboard updates, desk bookings, cancellations, and port settings.
 */

(function () {
  'use strict';

  // Application State
  const state = {
    csrfToken: document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '',
    currentUser: null,
    connection: {
      connected: false,
      port: 'COM8',
      baudRate: 9600,
      status: 'DISCONNECTED',
      isStale: true
    },
    desks: [],
    stats: {},
    activeTab: 'dashboard',
    userBookings: []
  };

  // DOM Elements
  const elements = {
    connectionPill: document.getElementById('connectionPill'),
    connectionDot: document.getElementById('connectionDot'),
    connectionText: document.getElementById('connectionText'),
    warningBanner: document.getElementById('warningBanner'),
    warningMessage: document.getElementById('warningMessage'),
    userDisplay: document.getElementById('userDisplay'),
    toastContainer: document.getElementById('toastContainer'),

    // Stats
    statAvailable: document.getElementById('statAvailable'),
    statOccupied: document.getElementById('statOccupied'),
    statBooked: document.getElementById('statBooked'),
    statTotal: document.getElementById('statTotal'),
    statOccupancyPct: document.getElementById('statOccupancyPct'),
    progressBarFill: document.getElementById('progressBarFill'),

    // Desks Container
    parkingGrid: document.getElementById('parkingGrid'),

    // Tabs
    tabButtons: document.querySelectorAll('.nav-tab'),
    tabPanes: document.querySelectorAll('.tab-pane'),

    // My Bookings
    bookingList: document.getElementById('bookingList'),
    emptyBookings: document.getElementById('emptyBookings'),

    // Settings
    portInput: document.getElementById('portInput'),
    settingsForm: document.getElementById('settingsForm'),
    settingsStatus: document.getElementById('settingsStatus'),
    settingsPort: document.getElementById('settingsPort'),
    settingsLastSeen: document.getElementById('settingsLastSeen'),
    btnSavePort: document.getElementById('btnSavePort')
  };

  // ================= Notification Toast =================
  function showToast(message, type = 'info') {
    if (!elements.toastContainer) return;

    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.innerHTML = `
      <span>${escapeHtml(message)}</span>
    `;

    elements.toastContainer.appendChild(toast);

    setTimeout(() => {
      toast.style.opacity = '0';
      setTimeout(() => toast.remove(), 300);
    }, 4000);
  }

  function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }

  // ================= API Helper with CSRF =================
  async function apiRequest(url, options = {}) {
    options.headers = options.headers || {};
    
    // Always include CSRF token if present
    if (state.csrfToken) {
      options.headers['X-CSRF-Token'] = state.csrfToken;
    }
    
    if (options.body && typeof options.body === 'object' && !(options.body instanceof FormData)) {
      options.headers['Content-Type'] = 'application/json';
      options.body = JSON.stringify(options.body);
    }

    try {
      const response = await fetch(url, options);
      const data = await response.json().catch(() => ({}));

      if (!response.ok) {
        throw new Error(data.error || `Server responded with status ${response.status}`);
      }
      return data;
    } catch (err) {
      throw err;
    }
  }

  // ================= Tab Navigation =================
  function setupNavigation() {
    elements.tabButtons.forEach(button => {
      button.addEventListener('click', () => {
        const targetTab = button.getAttribute('data-tab');
        switchTab(targetTab);
      });
    });
  }

  function switchTab(tabId) {
    state.activeTab = tabId;

    elements.tabButtons.forEach(btn => {
      if (btn.getAttribute('data-tab') === tabId) {
        btn.classList.add('active');
      } else {
        btn.classList.remove('active');
      }
    });

    elements.tabPanes.forEach(pane => {
      if (pane.id === `tab-${tabId}`) {
        pane.classList.add('active');
      } else {
        pane.classList.remove('active');
      }
    });

    if (tabId === 'bookings' && state.currentUser) {
      loadUserBookings();
    }
  }

  // ================= Dashboard Rendering =================
  function updateDashboard(payload) {
    if (!payload) return;

    if (payload.csrf_token) {
      state.csrfToken = payload.csrf_token;
    }
    if (payload.user !== undefined) {
      state.currentUser = payload.user;
    }

    state.connection = payload.connection || state.connection;
    state.desks = payload.desks || [];
    state.stats = payload.stats || {};

    renderConnectionStatus();
    renderStats();
    renderDeskCards();
    renderSettingsInfo();
  }

  function renderConnectionStatus() {
    const conn = state.connection;
    const isOnline = conn.connected && !conn.is_stale;

    if (elements.connectionDot && elements.connectionText) {
      elements.connectionDot.className = 'status-dot';
      
      if (isOnline) {
        elements.connectionDot.classList.add('connected');
        elements.connectionText.textContent = `${conn.port} (9600)`;
      } else if (conn.is_stale && conn.connected) {
        elements.connectionDot.classList.add('stale');
        elements.connectionText.textContent = `${conn.port} (Stale)`;
      } else if (conn.status === 'CONNECTING') {
        elements.connectionDot.classList.add('connecting');
        elements.connectionText.textContent = `Connecting...`;
      } else {
        elements.connectionDot.classList.add('disconnected');
        elements.connectionText.textContent = `${conn.port} (Offline)`;
      }
    }

    if (elements.warningBanner && elements.warningMessage) {
      if (!isOnline) {
        elements.warningBanner.classList.add('active');
        if (!conn.connected) {
          elements.warningMessage.textContent = `Arduino Disconnected on ${conn.port}. Real-time monitoring and new bookings paused.`;
        } else if (conn.is_stale) {
          elements.warningMessage.textContent = `Sensor data is stale (>3s without valid packet). Check Arduino USB connection.`;
        } else {
          elements.warningMessage.textContent = conn.error_message || 'Hardware communication unavailable.';
        }
      } else {
        elements.warningBanner.classList.remove('active');
      }
    }
  }

  function renderStats() {
    const s = state.stats;
    if (elements.statAvailable) elements.statAvailable.textContent = s.available ?? '--';
    if (elements.statOccupied) elements.statOccupied.textContent = s.physically_occupied ?? '--';
    if (elements.statBooked) elements.statBooked.textContent = s.active_bookings ?? '--';
    if (elements.statTotal) elements.statTotal.textContent = s.total ?? '4';
    if (elements.statOccupancyPct) elements.statOccupancyPct.textContent = `${s.occupancy_percentage ?? 0}%`;
    if (elements.progressBarFill) {
      elements.progressBarFill.style.width = `${Math.min(s.occupancy_percentage || 0, 100)}%`;
    }
  }

  function renderDeskCards() {
    if (!elements.parkingGrid) return;
    elements.parkingGrid.innerHTML = '';

    state.desks.forEach(desk => {
      const card = document.createElement('div');
      card.className = `desk-card state-${desk.effective_state.toLowerCase()}`;

      // Badge config
      const badgeClasses = {
        AVAILABLE: 'badge-available',
        OCCUPIED: 'badge-occupied',
        BOOKED: 'badge-booked',
        UNKNOWN: 'badge-unknown'
      };
      const badgeClass = badgeClasses[desk.effective_state] || 'badge-unknown';

      // Physical state text & CSS class
      let physicalText = 'Unknown';
      let physicalValClass = 'val-unknown';
      if (desk.physical_state === 'UNOCCUPIED') {
        physicalText = 'Unoccupied';
        physicalValClass = 'val-unoccupied';
      } else if (desk.physical_state === 'OCCUPIED') {
        physicalText = 'Occupied (Obstacle)';
        physicalValClass = 'val-occupied';
      }

      // Reservation state text & CSS class
      let reservationText = 'Not Booked';
      let reservationValClass = 'val-not-booked';
      if (desk.reservation_state === 'BOOKED') {
        if (desk.booking?.is_owned_by_current_user) {
          reservationText = 'Booked by You';
          reservationValClass = 'val-booked';
        } else if (desk.booking?.username) {
          reservationText = `Booked (${escapeHtml(desk.booking.username)})`;
          reservationValClass = 'val-booked';
        } else {
          reservationText = 'Booked';
          reservationValClass = 'val-booked';
        }
      }

      // Grace period countdown banner (15-second arrival window)
      let graceBannerHtml = '';
      if (desk.reservation_state === 'BOOKED' && desk.booking?.pending_checkin && desk.physical_state !== 'OCCUPIED') {
        const remaining = (desk.booking.grace_remaining_seconds != null) ? Math.ceil(desk.booking.grace_remaining_seconds) : 15;
        graceBannerHtml = `
          <div class="grace-countdown-banner" data-desk-id="${desk.desk_id}" data-remaining="${remaining}">
            <span>&#9201; Occupy seat in <strong class="timer-seconds">${remaining}s</strong> or auto-released</span>
          </div>
        `;
      }

      // Action Button
      let buttonHtml = '';
      const isHardwareReady = state.connection.connected && !state.connection.is_stale;

      if (!state.currentUser) {
        buttonHtml = `
          <a href="/login" class="btn-action btn-book">
            Log In to Book
          </a>
        `;
      } else if (desk.booking?.is_owned_by_current_user) {
        buttonHtml = `
          <button class="btn-action btn-cancel-own" data-booking-id="${desk.booking.id}">
            Cancel My Booking
          </button>
        `;
      } else if (desk.effective_state === 'AVAILABLE' && isHardwareReady) {
        buttonHtml = `
          <button class="btn-action btn-book" data-desk-id="${desk.desk_id}">
            Book Space
          </button>
        `;
      } else if (desk.effective_state === 'OCCUPIED') {
        buttonHtml = `
          <button class="btn-action btn-book" disabled>
            Occupied
          </button>
        `;
      } else if (desk.effective_state === 'BOOKED') {
        buttonHtml = `
          <button class="btn-action btn-book" disabled>
            Reserved
          </button>
        `;
      } else {
        buttonHtml = `
          <button class="btn-action btn-book" disabled>
            Sensor Unavailable
          </button>
        `;
      }

      card.innerHTML = `
        <div class="card-top">
          <span class="desk-title">${escapeHtml(desk.label)}</span>
          <span class="sensor-tag">${escapeHtml(desk.sensor_pin)}</span>
        </div>

        <div class="parking-bay-graphic">
          <svg class="car-icon" viewBox="0 0 24 24">
            <path d="M18.92 6.01C18.72 5.42 18.16 5 17.5 5h-11c-.66 0-1.21.42-1.42 1.01L3 12v8c0 .55.45 1 1 1h1c.55 0 1-.45 1-1v-1h12v1c0 .55.45 1 1 1h1c.55 0 1-.45 1-1v-8l-2.08-5.99zM6.5 16c-.83 0-1.5-.67-1.5-1.5S5.67 13 6.5 13s1.5.67 1.5 1.5S7.33 16 6.5 16zm11 0c-.83 0-1.5-.67-1.5-1.5s.67-1.5 1.5-1.5 1.5.67 1.5 1.5-.67 1.5-1.5 1.5zM5 11l1.5-4.5h11L19 11H5z"/>
          </svg>
        </div>

        <div class="effective-badge ${badgeClass}">
          ${escapeHtml(desk.effective_state)}
        </div>

        ${graceBannerHtml}

        <div class="status-details">
          <div class="status-row">
            <span class="row-label">Physical Sensor:</span>
            <span class="row-val ${physicalValClass}">${escapeHtml(physicalText)}</span>
          </div>
          <div class="status-row">
            <span class="row-label">Reservation:</span>
            <span class="row-val ${reservationValClass}">${escapeHtml(reservationText)}</span>
          </div>
        </div>

        ${buttonHtml}
      `;

      // Attach event listener to Book Space
      const bookBtn = card.querySelector('button.btn-book[data-desk-id]');
      if (bookBtn) {
        bookBtn.addEventListener('click', () => handleBookDesk(desk.desk_id, bookBtn));
      }

      // Attach event listener to Cancel
      const cancelBtn = card.querySelector('button.btn-cancel-own[data-booking-id]');
      if (cancelBtn) {
        cancelBtn.addEventListener('click', () => handleCancelBooking(desk.booking.id, cancelBtn));
      }

      elements.parkingGrid.appendChild(card);
    });
  }

  // ================= Booking Actions =================
  async function handleBookDesk(deskId, buttonElement) {
    if (!state.currentUser) {
      window.location.href = '/login';
      return;
    }

    const originalText = buttonElement.innerHTML;
    buttonElement.disabled = true;
    buttonElement.innerHTML = `<span>Booking...</span>`;

    try {
      const result = await apiRequest('/api/bookings', {
        method: 'POST',
        body: { desk_id: deskId }
      });

      showToast(result.message || `Desk ${deskId} successfully booked!`, 'success');
      // Trigger status refresh
      await loadInitialStatus();
      if (state.activeTab === 'bookings') {
        await loadUserBookings();
      }
    } catch (err) {
      showToast(err.message || 'Booking request failed', 'error');
      buttonElement.disabled = false;
      buttonElement.innerHTML = originalText;
    }
  }

  async function handleCancelBooking(bookingId, buttonElement) {
    if (!confirm('Are you sure you want to cancel this booking?')) {
      return;
    }

    const originalText = buttonElement.innerHTML;
    buttonElement.disabled = true;
    buttonElement.innerHTML = `<span>Cancelling...</span>`;

    try {
      const result = await apiRequest(`/api/bookings/${bookingId}/cancel`, {
        method: 'POST'
      });

      showToast(result.message || 'Booking cancelled', 'success');
      await loadInitialStatus();
      if (state.activeTab === 'bookings') {
        await loadUserBookings();
      }
    } catch (err) {
      showToast(err.message || 'Cancellation failed', 'error');
      buttonElement.disabled = false;
      buttonElement.innerHTML = originalText;
    }
  }

  // ================= My Bookings View =================
  async function loadUserBookings() {
    if (!state.currentUser || !elements.bookingList) return;

    try {
      const res = await apiRequest('/api/bookings');
      state.userBookings = res.bookings || [];
      renderUserBookingsList();
    } catch (err) {
      showToast('Failed to load bookings history', 'error');
    }
  }

  function renderUserBookingsList() {
    if (!elements.bookingList || !elements.emptyBookings) return;

    elements.bookingList.innerHTML = '';

    if (state.userBookings.length === 0) {
      elements.emptyBookings.style.display = 'block';
      return;
    }

    elements.emptyBookings.style.display = 'none';

    state.userBookings.forEach(b => {
      const item = document.createElement('div');
      const isActive = b.status === 'ACTIVE';
      item.className = `booking-item ${isActive ? 'active-item' : 'cancelled-item'}`;

      const createdDate = new Date(b.created_at).toLocaleString();
      const cancelledDate = b.cancelled_at ? new Date(b.cancelled_at).toLocaleString() : null;

      item.innerHTML = `
        <div class="booking-meta">
          <h4>Desk ${b.desk_id}</h4>
          <p>Booked on: ${escapeHtml(createdDate)}</p>
          ${cancelledDate ? `<p>Cancelled on: ${escapeHtml(cancelledDate)}</p>` : ''}
          <span class="booking-tag ${isActive ? 'tag-active' : 'tag-cancelled'}">
            ${escapeHtml(b.status)}
          </span>
        </div>
        ${isActive ? `
          <button class="btn-cancel-sm" data-booking-id="${b.id}">
            Cancel Booking
          </button>
        ` : ''}
      `;

      const cancelBtn = item.querySelector('.btn-cancel-sm');
      if (cancelBtn) {
        cancelBtn.addEventListener('click', () => handleCancelBooking(b.id, cancelBtn));
      }

      elements.bookingList.appendChild(item);
    });
  }

  // ================= Settings Tab =================
  function renderSettingsInfo() {
    if (elements.settingsPort) elements.settingsPort.textContent = state.connection.port || '--';
    if (elements.settingsStatus) elements.settingsStatus.textContent = state.connection.status || '--';
    if (elements.settingsLastSeen) {
      if (state.connection.last_valid_time) {
        const d = new Date(state.connection.last_valid_time * 1000);
        elements.settingsLastSeen.textContent = d.toLocaleTimeString();
      } else {
        elements.settingsLastSeen.textContent = 'Never';
      }
    }
  }

  function setupSettings() {
    if (!elements.settingsForm) return;

    elements.settingsForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const portVal = elements.portInput.value.trim().toUpperCase();
      if (!portVal) {
        showToast('Please enter a valid COM port name', 'warning');
        return;
      }

      elements.btnSavePort.disabled = true;
      elements.btnSavePort.textContent = 'Applying...';

      try {
        const res = await apiRequest('/api/port', {
          method: 'POST',
          body: { port: portVal }
        });

        showToast(res.message || `Switched to ${portVal}`, 'success');
        await loadInitialStatus();
      } catch (err) {
        showToast(err.message || 'Failed to switch port', 'error');
      } finally {
        elements.btnSavePort.disabled = false;
        elements.btnSavePort.textContent = 'Save & Connect';
      }
    });
  }

  // ================= Real-time SSE Connection =================
  function initSSE() {
    let eventSource = null;
    let fallbackPollTimer = null;

    function connect() {
      try {
        eventSource = new EventSource('/api/events');

        eventSource.onopen = () => {
          clearInterval(fallbackPollTimer);
          fallbackPollTimer = null;
        };

        eventSource.onmessage = (event) => {
          try {
            const parsed = JSON.parse(event.data);
            if (parsed.event === 'status' && parsed.data) {
              updateDashboard(parsed.data);
            } else if (parsed.event === 'notification' && parsed.data) {
              showToast(parsed.data.message, parsed.data.type || 'info');
            }
          } catch (e) {
            // Heartbeat or malformed comment
          }
        };

        eventSource.onerror = () => {
          if (eventSource) {
            eventSource.close();
            eventSource = null;
          }
          // Start polling fallback every 2.5 seconds while SSE recovers
          if (!fallbackPollTimer) {
            fallbackPollTimer = setInterval(loadInitialStatus, 2500);
          }
          // Attempt SSE reconnection in 3 seconds
          setTimeout(connect, 3000);
        };
      } catch (err) {
        if (!fallbackPollTimer) {
          fallbackPollTimer = setInterval(loadInitialStatus, 2500);
        }
      }
    }

    // 1-second interval to tick down visible countdown banners
    setInterval(() => {
      const banners = document.querySelectorAll('.grace-countdown-banner');
      banners.forEach(b => {
        let rem = parseInt(b.getAttribute('data-remaining'), 10);
        if (!isNaN(rem) && rem > 0) {
          rem -= 1;
          b.setAttribute('data-remaining', rem);
          const strong = b.querySelector('.timer-seconds');
          if (strong) {
            strong.textContent = `${rem}s`;
          }
        }
      });
    }, 1000);

    connect();
  }

  async function loadInitialStatus() {
    try {
      const data = await apiRequest('/api/status');
      updateDashboard(data);
    } catch (err) {
      console.warn('Status poll failed:', err);
    }
  }

  // ================= Logout Handler =================
  function setupLogout() {
    const logoutBtn = document.getElementById('btnLogout');
    if (!logoutBtn) return;

    logoutBtn.addEventListener('click', async (e) => {
      e.preventDefault();
      try {
        await apiRequest('/api/logout', { method: 'POST' });
        showToast('Logged out successfully', 'success');
        setTimeout(() => window.location.href = '/', 600);
      } catch (err) {
        showToast('Logout failed', 'error');
      }
    });
  }

  // ================= Initialization =================
  document.addEventListener('DOMContentLoaded', () => {
    setupNavigation();
    setupSettings();
    setupLogout();
    loadInitialStatus();
    initSSE();
  });

})();
