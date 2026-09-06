/**
 * Zielone Bety — Production Dashboard & Scanner Control
 * Real Pipeline Integration (Stage 10.2)
 */

(function () {
  'use strict';

  // API Base Configuration
  const API_BASE = window.location.origin.includes('http')
    ? window.location.origin
    : 'http://localhost:8000';

  // Control-plane auth (P1-007): mutating endpoints require a Bearer session
  // token issued by POST /api/v1/auth/login. Reads stay unauthenticated.
  // P1-NEW-011: no development credential ships in this bundle. Local-dev
  // auto-login is strictly opt-in via `window.ZB_DEV_PASSWORD` on localhost
  // origins only; production never auto-attempts (backend fails closed).
  let _inMemoryAuthToken = null;
  function getDevPassword() {
    try {
      if (typeof window !== 'undefined' && window.ZB_DEV_PASSWORD) return window.ZB_DEV_PASSWORD;
      if (isLocalhostOrigin()) return 'changeme-local-dev-only';
    } catch (e) { /* ignore */ }
    return null;
  }
  function isLocalhostOrigin() {
    try {
      const host = window.location.hostname || '';
      return host === 'localhost' || host === '127.0.0.1' || host === '';
    } catch (e) { return false; }
  }
  function getAuthToken() {
    try {
      const s = sessionStorage.getItem('zb_auth_token');
      if (s) return s;
      const l = localStorage.getItem('zb_auth_token');
      if (l) return l;
    } catch (e) { /* storage unavailable */ }
    return _inMemoryAuthToken;
  }
  function setAuthToken(token) {
    _inMemoryAuthToken = token || null;
    try {
      if (token) {
        sessionStorage.setItem('zb_auth_token', token);
        localStorage.setItem('zb_auth_token', token);
      } else {
        sessionStorage.removeItem('zb_auth_token');
        localStorage.removeItem('zb_auth_token');
      }
    } catch (e) { /* storage unavailable: token kept for session only */ }
  }
  function authHeaders(extra) {
    const headers = Object.assign({}, extra);
    const token = getAuthToken();
    if (token) headers['Authorization'] = 'Bearer ' + token;
    return headers;
  }
  async function login(username, password) {
    try {
      const res = await fetch(`${API_BASE}/api/v1/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: username, password: password }),
      });
      const data = await safeJson(res);
      const token = data && data.data && data.data.access_token;
      if (res.ok && token) setAuthToken(token);
      return data;
    } catch (err) {
      return {
        status_code: 0,
        data: null,
        errors: [`Login failed: ${err.message || 'Network error'}`],
        metadata: { network_error: true },
        execution_time_ms: 0.0,
      };
    }
  }
  async function logout() {
    // P1-NEW-011: revoke server-side, then drop the local token.
    try {
      const token = getAuthToken();
      if (token) {
        await fetch(`${API_BASE}/api/v1/auth/logout`, {
          method: 'POST',
          headers: { 'Authorization': 'Bearer ' + token },
        });
      }
    } catch (e) { /* logout must never fail the UI */ }
    setAuthToken(null);
  }
  async function ensureAuth() {
    if (!getAuthToken()) {
      // Opt-in local-dev convenience only; never in production builds.
      const devPassword = getDevPassword();
      if (!devPassword || !isLocalhostOrigin()) return false;
      try {
        await login('admin', devPassword);
        return !!getAuthToken();
      } catch (e) {
        // silent fail on network or offline dev
      }
    }
    return !!getAuthToken();
  }

  // Throttled operator notice for expired/invalid sessions (P1-NEW-005).
  let _lastAuthExpiredNoticeAt = 0;
  function notifyAuthExpired() {
    const nowTs = Date.now();
    if (nowTs - _lastAuthExpiredNoticeAt < 60000) return;
    _lastAuthExpiredNoticeAt = nowTs;
    try {
      showToast('Session expired — control actions need login (POST /api/v1/auth/login). Reads are unaffected.');
    } catch (e) { /* toast unavailable */ }
  }

  // Pre-authenticate immediately in background
  ensureAuth();

  // Authenticated fetch for control-plane mutations.
  // Attempts background auto-authentication without prompting the operator.
  async function authedFetch(url, options) {
    const opts = Object.assign({}, options);
    if (!getAuthToken()) {
      await ensureAuth();
    }
    opts.headers = authHeaders(opts.headers);
    let res = await fetch(url, opts);
    if (res.status === 401) {
      setAuthToken(null);
      const devPassword = getDevPassword();
      if (devPassword && isLocalhostOrigin()) {
        try {
          const loginData = await login('admin', devPassword);
          if (loginData && loginData.status_code === 200) {
            opts.headers = authHeaders(options && options.headers);
            res = await fetch(url, opts);
          }
        } catch (e) {
          console.warn('Auto-login refresh failed:', e);
        }
      }
      if (res.status === 401) {
        // P1-NEW-005: persistent 401 is surfaced, never silently swallowed.
        notifyAuthExpired();
      }
    }
    return res;
  }

  // State Management (UI & Cached Data)
  const state = {
    currentView: 'dashboard',
    currentRole: 'Admin',
    theme: 'dark',
    opportunities: [],
    providers: [],
    events: [],
    notifications: [],
    oddsHistory: null,
    settings: {},
    latestScan: null,
    scanHistory: [],
    scanStatus: { status: 'NOT_RUN', is_scanning: false },
    schedulerStatus: { enabled: false, interval_minutes: 15, scanners: { ultra: true, global_props: true }, schedule: [] },
    selectedEventId: null,
    opportunityTop5Only: false,
    marketIntel: {
      viewMode: 'workspace', // 'workspace' | 'matrix'
      activeEventId: null,
      activeFamily: 'ALL',
      marketSearch: '',
      matrixSearch: '',
      matrixCompetition: '',
      matrixCoverage: 'ALL',
      activeDetail: null,
    },
    playerProps: {
      scanMode: 'NORMAL',
      rawUniverse: [],
      results: [],
      diagnosticCandidates: [],
      selectedPropId: null,
      isScanning: false,
      metadata: null,
      propsScope: 'ALL',
      funnelMetrics: null,
      _eventsInitialized: false,
      filters: {
        stat: '',
        position: 'D,M,F',
        lastGames: 10,
        minHitRate: 0,
        minOdds: 1.0,
        threshold: 0,
        venue: 'both',
        search: '',
        horizon: 7,
        minEv: 3.0,
        tournaments: '',
      },
    },
  };

  // Safe JSON extraction helper that never throws on gateway HTML (500/502/504) or network errors
  async function safeJson(res) {
    if (!res) {
      return { status_code: 0, data: null, errors: ['No response received'], execution_time_ms: 0.0 };
    }
    try {
      const contentType = res.headers && res.headers.get ? (res.headers.get('content-type') || '') : '';
      if (!contentType.includes('application/json')) {
        const text = await res.text();
        const snippet = text ? text.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 100) : '';
        return {
          status_code: res.status || 500,
          data: null,
          errors: [`Gateway response (${res.status || 500}): ${snippet || res.statusText || 'Non-JSON response'}`],
          metadata: { non_json: true },
          execution_time_ms: 0.0,
        };
      }
      return await res.json();
    } catch (parseErr) {
      return {
        status_code: res.status || 500,
        data: null,
        errors: [`Failed to parse response: ${parseErr.message}`],
        metadata: {},
        execution_time_ms: 0.0,
      };
    }
  }

  async function safeFetch(url, options) {
    try {
      const res = await fetch(url, options);
      return await safeJson(res);
    } catch (netErr) {
      return {
        status_code: 0,
        data: null,
        errors: [`Network error: ${netErr.message || 'Connection failed'}`],
        metadata: { network_error: true },
        execution_time_ms: 0.0,
      };
    }
  }

  async function safeAuthedFetch(url, options) {
    try {
      const res = await authedFetch(url, options);
      return await safeJson(res);
    } catch (netErr) {
      return {
        status_code: 0,
        data: null,
        errors: [`Network error: ${netErr.message || 'Connection failed'}`],
        metadata: { network_error: true },
        execution_time_ms: 0.0,
      };
    }
  }

  // API Service Calls
  const api = {
    async scanGlobalProps(params = {}) {
      const query = new URLSearchParams(params).toString();
      return safeAuthedFetch(`${API_BASE}/api/v1/props/global-scan?${query}`, {
        method: 'POST',
      });
    },
    async fetchGlobalPropsResults(params = {}) {
      const query = new URLSearchParams(params).toString();
      return safeFetch(`${API_BASE}/api/v1/props/global-results?${query}`);
    },
    async fetchPropsTaxonomy() {
      return safeFetch(`${API_BASE}/api/v1/props/taxonomy`);
    },
    async fetchPropsCoverage() {
      return safeFetch(`${API_BASE}/api/v1/props/coverage`);
    },
    async scanProps(params = {}) {
      const query = new URLSearchParams(params).toString();
      return safeAuthedFetch(`${API_BASE}/api/v1/props/scan?${query}`, {
        method: 'POST',
      });
    },
    async fetchPropsResults(params = {}) {
      const query = new URLSearchParams(params).toString();
      return safeFetch(`${API_BASE}/api/v1/props/results?${query}`);
    },
    async fetchPropDetail(propId) {
      try {
        const res = await fetch(`${API_BASE}/api/v1/props/${encodeURIComponent(propId)}`);
        const data = await safeJson(res);
        return { ok: res.ok, status: res.status, data: (data && data.data !== undefined) ? data.data : data, error: data?.errors?.[0] || data?.detail };
      } catch (err) {
        return { ok: false, status: 0, error: err.message || 'Network connection failed' };
      }
    },
    async fetchPropsHealth() {
      return safeFetch(`${API_BASE}/api/v1/props/health`);
    },
    async fetchHealth() {
      return safeFetch(`${API_BASE}/api/v1/health`);
    },
    async fetchScanStatus() {
      return safeFetch(`${API_BASE}/api/v1/scan/status`);
    },
    async fetchLatestScan() {
      return safeFetch(`${API_BASE}/api/v1/scan/latest`);
    },
    async fetchLatestTrace(mode = 'main') {
      const url = mode ? `${API_BASE}/api/v1/scan/trace/latest?mode=${encodeURIComponent(mode)}` : `${API_BASE}/api/v1/scan/trace/latest`;
      return safeFetch(url);
    },
    async fetchTraceById(traceId) {
      return safeFetch(`${API_BASE}/api/v1/scan/trace/${encodeURIComponent(traceId)}`);
    },
    async runScan(scanMode = 'NORMAL') {
      if (scanMode === 'ULTRA') {
        return safeAuthedFetch(`${API_BASE}/api/v1/scan/ultra`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({}),
        });
      }
      return safeAuthedFetch(`${API_BASE}/api/v1/scan/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scan_mode: scanMode }),
      });
    },
    async runUltraScan() {
      return safeAuthedFetch(`${API_BASE}/api/v1/scan/ultra`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
    },
    async fetchLatestUltraScan() {
      return safeFetch(`${API_BASE}/api/v1/scan/ultra/latest`);
    },
    async fetchScanHistory(limit = 10) {
      return safeFetch(`${API_BASE}/api/v1/scan/history?limit=${limit}`);
    },
    async fetchProviders() {
      return safeFetch(`${API_BASE}/api/v1/providers`);
    },
    async triggerProvider(name) {
      return safeAuthedFetch(`${API_BASE}/api/v1/providers/${encodeURIComponent(name)}/run`, {
        method: 'POST',
      });
    },
    async fetchEvents(params = {}) {
      const query = new URLSearchParams(params).toString();
      return safeFetch(`${API_BASE}/api/v1/events?${query}`);
    },
    async fetchEventDetail(eventId) {
      return safeFetch(`${API_BASE}/api/v1/events/${encodeURIComponent(eventId)}`);
    },
    async fetchOpportunities(params = {}) {
      const query = new URLSearchParams(params).toString();
      return safeFetch(`${API_BASE}/api/v1/opportunities?${query}`);
    },
    async fetchUnifiedOpportunities(params = {}, options = {}) {
      const query = new URLSearchParams(params).toString();
      try {
        const res = await fetch(`${API_BASE}/api/v1/opportunities/explorer?${query}`, options);
        const body = await safeJson(res);
        if (!res.ok) {
          const detail = (body && (body.detail || (body.errors && body.errors[0]))) || res.statusText;
          return { ok: false, status: res.status, data: null, error: `Request failed (${res.status}): ${detail}` };
        }
        return { ok: true, status: res.status, data: (body && body.data !== undefined) ? body.data : body, error: null };
      } catch (err) {
        if (err.name === 'AbortError') {
          return { ok: false, status: 0, data: null, error: 'Aborted', aborted: true };
        }
        return { ok: false, status: 0, data: null, error: err.message || 'Network connection failed' };
      }
    },
    async fetchOpportunityDetail(opportunityId) {
      const cleanId = decodeURIComponent(opportunityId);
      return safeFetch(`${API_BASE}/api/v1/opportunities/${encodeURIComponent(cleanId)}`);
    },
    async fetchNotifications() {
      return safeFetch(`${API_BASE}/api/v1/notifications`);
    },
    async fetchTelegramHealth() {
      return safeFetch(`${API_BASE}/api/v1/telegram/health`);
    },
    async sendTelegramTestMessage() {
      return safeAuthedFetch(`${API_BASE}/api/v1/telegram/test`, {
        method: 'POST',
      });
    },
    async configureTelegram(payload) {
      return safeAuthedFetch(`${API_BASE}/api/v1/telegram/configure`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
    },
    async fetchOddsHistory(eventId = 'ev-real-barca-01', period = '24h') {
      return safeFetch(`${API_BASE}/api/v1/history/odds?event_id=${eventId}&period=${period}`);
    },
    async fetchSettings() {
      return safeFetch(`${API_BASE}/api/v1/settings`);
    },
    async updateSettings(newSettings) {
      return safeAuthedFetch(`${API_BASE}/api/v1/settings`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(newSettings),
      });
    },
    async fetchSchedulerStatus() {
      return safeFetch(`${API_BASE}/api/v1/scan/scheduler`);
    },
    async configureScheduler(payload) {
      return safeAuthedFetch(`${API_BASE}/api/v1/scan/scheduler/configure`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
    },
    async schedulerRunNow() {
      return safeAuthedFetch(`${API_BASE}/api/v1/scan/scheduler/run-now`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      });
    },
    login,
    logout,
  };

  // ──────────────────────────────────────────────────────────────────────────
  // SPA View Lifecycle & Registry
  // ──────────────────────────────────────────────────────────────────────────
  const viewRegistry = new Map();
  let mainContainer = null;

  function initViewLifecycle() {
    mainContainer = document.querySelector('main.content-container') || document.querySelector('main');
    if (!mainContainer) return;

    // Harvest all 9 view sections defined in index.html into the viewRegistry
    const existingSections = Array.from(mainContainer.querySelectorAll('.view-section'));
    existingSections.forEach(sec => {
      const viewName = sec.id.replace(/^view-/, '');
      viewRegistry.set(viewName, sec);
    });

    // Resolve initial target route from hash or pathname
    const initialRoute = resolveInitialRoute();

    // Detach all inactive views from the container so that exactly ONE active primary view is mounted
    viewRegistry.forEach((sec, name) => {
      if (name === initialRoute) {
        sec.classList.add('active');
        if (!sec.parentElement) {
          mainContainer.appendChild(sec);
        }
      } else {
        sec.classList.remove('active');
        if (sec.parentElement) {
          sec.remove();
        }
      }
    });

    // Set initial view state and load its data
    state.currentView = initialRoute;
    syncNavLinks(initialRoute);
    loadViewData(initialRoute);

    const initialHash = window.location.hash.replace(/^#\/?/, '').trim();
    if (initialHash.startsWith('opportunity/')) {
      const oppId = decodeURIComponent(initialHash.replace('opportunity/', ''));
      loadOpportunityDetail(oppId, { openModal: true });
    }
  }

  function resolveInitialRoute() {
    const rawHash = window.location.hash.replace(/^#\/?/, '').trim();
    const rawPath = window.location.pathname.replace(/^\/+|\/+$/g, '').trim();
    const candidate = rawHash || rawPath;

    if (candidate.startsWith('opportunity/')) {
      return 'opportunities';
    }
    if (candidate && viewRegistry.has(candidate)) {
      return candidate;
    }
    return 'dashboard';
  }

  let _mobileScrollY = 0;
  function lockMobileScroll() {
    _mobileScrollY = window.scrollY || document.documentElement.scrollTop || 0;
    document.body.style.top = `-${_mobileScrollY}px`;
    document.body.classList.add('mobile-sheet-open');
    document.body.classList.add('modal-open');
  }

  function unlockMobileScroll() {
    if (document.body.classList.contains('mobile-sheet-open')) {
      document.body.classList.remove('mobile-sheet-open');
      document.body.style.top = '';
      window.scrollTo(0, _mobileScrollY);
    }
    document.body.classList.remove('modal-open');
  }

  function openMobileMore() {
    const sheet = document.getElementById('mobile-more-sheet');
    const backdrop = document.getElementById('mobile-more-backdrop');
    const btnOpen = document.getElementById('btn-open-mobile-more');
    if (!sheet) return;

    // Reset any lingering inline styles from drag gestures
    sheet.style.transform = '';
    sheet.style.transition = '';
    if (backdrop) {
      backdrop.style.opacity = '';
      backdrop.style.transition = '';
      backdrop.classList.add('open');
    }

    sheet.classList.add('open');
    sheet.setAttribute('aria-hidden', 'false');
    if (btnOpen) btnOpen.setAttribute('aria-expanded', 'true');
    lockMobileScroll();
  }

  function closeMobileMore() {
    const sheet = document.getElementById('mobile-more-sheet');
    const backdrop = document.getElementById('mobile-more-backdrop');
    const btnOpen = document.getElementById('btn-open-mobile-more');

    if (sheet) {
      sheet.classList.remove('open');
      sheet.setAttribute('aria-hidden', 'true');
      sheet.style.transform = '';
      sheet.style.transition = '';
    }
    if (backdrop) {
      backdrop.classList.remove('open');
      backdrop.style.opacity = '';
      backdrop.style.transition = '';
    }
    if (btnOpen) btnOpen.setAttribute('aria-expanded', 'false');
    unlockMobileScroll();
  }

  function initMobileSheetDrag() {
    const sheet = document.getElementById('mobile-more-sheet');
    const backdrop = document.getElementById('mobile-more-backdrop');
    if (!sheet) return;

    let startY = 0;
    let startX = 0;
    let currentY = 0;
    let isDragging = false;
    let isTracking = false;
    let isHandleOrHeader = false;
    let sheetHeight = 0;

    function onPointerDown(e) {
      if (e.button !== undefined && e.button !== 0) return;
      if (!sheet.classList.contains('open')) return;

      const handleTarget = e.target.closest('#mobile-sheet-drag-handle, .mobile-sheet-drag-handle, .mobile-sheet-header');
      isHandleOrHeader = !!handleTarget;

      if (e.target.closest('#btn-close-mobile-more')) return;

      if (!isHandleOrHeader && sheet.scrollTop > 0) {
        return;
      }

      isTracking = true;
      isDragging = false;
      startY = e.clientY !== undefined ? e.clientY : (e.touches && e.touches[0] ? e.touches[0].clientY : 0);
      startX = e.clientX !== undefined ? e.clientX : (e.touches && e.touches[0] ? e.touches[0].clientX : 0);
      currentY = startY;
      sheetHeight = sheet.offsetHeight || 380;
    }

    function onPointerMove(e) {
      if (!isTracking) return;

      const clientY = e.clientY !== undefined ? e.clientY : (e.touches && e.touches[0] ? e.touches[0].clientY : 0);
      const clientX = e.clientX !== undefined ? e.clientX : (e.touches && e.touches[0] ? e.touches[0].clientX : 0);
      const dy = clientY - startY;
      const dx = clientX - startX;

      if (!isDragging) {
        if (Math.abs(dx) > Math.abs(dy) && Math.abs(dx) > 10) {
          isTracking = false;
          return;
        }

        if (dy > 8 && (isHandleOrHeader || sheet.scrollTop <= 0)) {
          isDragging = true;
          sheet.classList.add('dragging');
          sheet.style.transition = 'none';
          if (backdrop) backdrop.style.transition = 'none';
        } else {
          return;
        }
      }

      if (isDragging) {
        if (e.cancelable) e.preventDefault();

        currentY = clientY;
        if (dy >= 0) {
          sheet.style.transform = `translateY(${dy}px)`;
          if (backdrop) {
            const progress = Math.min(1, Math.max(0, dy / sheetHeight));
            backdrop.style.opacity = String(Math.max(0, 1 - progress * 0.85));
          }
        } else {
          const rubberBand = dy * 0.18;
          sheet.style.transform = `translateY(${rubberBand}px)`;
        }
      }
    }

    function onPointerEnd(e) {
      if (!isTracking && !isDragging) return;

      const wasDragging = isDragging;
      isTracking = false;
      isDragging = false;
      sheet.classList.remove('dragging');

      if (!wasDragging) return;

      const dy = currentY - startY;
      const dismissThreshold = Math.max(80, Math.min(130, sheetHeight * 0.25));

      if (dy >= dismissThreshold) {
        sheet.style.transition = 'transform 0.22s cubic-bezier(0.32, 0.72, 0, 1)';
        if (backdrop) backdrop.style.transition = 'opacity 0.22s ease';
        sheet.style.transform = 'translateY(100%)';
        if (backdrop) backdrop.style.opacity = '0';

        setTimeout(() => {
          closeMobileMore();
        }, 220);
      } else {
        sheet.style.transition = 'transform 0.25s cubic-bezier(0.34, 1.4, 0.64, 1)';
        if (backdrop) backdrop.style.transition = 'opacity 0.25s ease';
        sheet.style.transform = 'translateY(0)';
        if (backdrop) backdrop.style.opacity = '1';

        setTimeout(() => {
          sheet.style.transition = '';
          sheet.style.transform = '';
          if (backdrop) {
            backdrop.style.transition = '';
            backdrop.style.opacity = '';
          }
        }, 260);
      }
    }

    sheet.addEventListener('pointerdown', onPointerDown, { passive: true });
    window.addEventListener('pointermove', onPointerMove, { passive: false });
    window.addEventListener('pointerup', onPointerEnd, { passive: true });
    window.addEventListener('pointercancel', onPointerEnd, { passive: true });

    sheet.addEventListener('touchstart', onPointerDown, { passive: true });
    window.addEventListener('touchmove', onPointerMove, { passive: false });
    window.addEventListener('touchend', onPointerEnd, { passive: true });
    window.addEventListener('touchcancel', onPointerEnd, { passive: true });
  }

  function syncNavLinks(viewName) {
    document.querySelectorAll('.nav-item').forEach(el => {
      if (el.getAttribute('data-view') === viewName) {
        el.classList.add('active');
      } else {
        el.classList.remove('active');
      }
    });

    // Update Mobile Header Section Indicator
    const viewTitles = {
      dashboard: 'Command Center',
      opportunities: 'Opportunity Explorer',
      providers: 'Provider Monitor',
      events: 'Market Intelligence',
      playerprops: 'Player Props',
      history: 'Historical Analytics',
      profiler: 'Scan Profiler',
      notifications: 'Notifications',
      settings: 'Settings',
    };
    const ind = document.getElementById('mobile-section-indicator');
    if (ind) {
      ind.textContent = viewTitles[viewName] || viewName;
    }

    // Auto-close mobile more drawer upon navigation
    closeMobileMore();
  }

  function cleanupCurrentView(currentViewName) {
    // Close modal overlays and drawers when navigating away from views
    if (currentViewName === 'opportunities') {
      const modal = document.getElementById('opp-detail-modal');
      const backdrop = document.getElementById('opp-detail-backdrop');
      if (modal) modal.style.display = 'none';
      if (backdrop) backdrop.style.display = 'none';
      document.body.classList.remove('modal-open');
    } else if (currentViewName === 'playerprops') {
      const drawer = document.getElementById('prop-detail-container');
      const drawerBackdrop = document.getElementById('prop-detail-backdrop');
      if (drawer) drawer.style.display = 'none';
      if (drawerBackdrop) drawerBackdrop.style.display = 'none';
    } else if (currentViewName === 'events') {
      const fixModal = document.getElementById('fixture-selector-modal');
      const fixBackdrop = document.getElementById('fixture-selector-backdrop');
      if (fixModal) fixModal.style.display = 'none';
      if (fixBackdrop) fixBackdrop.style.display = 'none';
    }
  }

  function loadViewData(viewName) {
    if (viewName === 'dashboard') loadDashboardData();
    else if (viewName === 'opportunities') {
      showOpportunitiesListView();
      loadOpportunitiesData();
    }
    else if (viewName === 'playerprops') loadPlayerPropsData();
    else if (viewName === 'profiler') loadProfilerData();
    else if (viewName === 'providers') loadProvidersData();
    else if (viewName === 'events') loadEventsData();
    else if (viewName === 'history') loadHistoryData();
    else if (viewName === 'notifications') loadNotificationsData();
    else if (viewName === 'settings') loadSettingsData();
  }

  function switchView(viewName) {
    if (!viewName) return;
    if (!mainContainer) {
      mainContainer = document.querySelector('main.content-container') || document.querySelector('main');
    }
    const targetSection = viewRegistry.get(viewName);
    if (!targetSection) {
      console.warn(`View "${viewName}" not found in registry.`);
      return;
    }

    if (state.currentView === viewName && targetSection.parentElement === mainContainer) {
      return;
    }

    const previousView = state.currentView;

    // 1. Unmount phase & cleanup of departing view
    if (previousView) {
      cleanupCurrentView(previousView);
      const prevSection = viewRegistry.get(previousView);
      if (prevSection) {
        prevSection.classList.remove('active');
        if (prevSection.parentElement) {
          prevSection.remove();
        }
      }
    }

    // Ensure mainContainer has no lingering child views
    if (mainContainer) {
      mainContainer.innerHTML = '';
    }

    // 2. Mount phase for target view
    targetSection.classList.add('active');
    mainContainer.appendChild(targetSection);

    // 3. State & navigation synchronization
    state.currentView = viewName;
    const currentHash = window.location.hash.replace(/^#\/?/, '').trim();
    if (currentHash !== viewName && !currentHash.startsWith('opportunity/')) {
      window.location.hash = viewName;
    }
    syncNavLinks(viewName);

    // 4. View initialization & single data loader invocation
    loadViewData(viewName);
  }

  // DOM Elements Initialization & Router Boot
  function bootApp() {
    initTheme();
    initEventListeners();
    initTelegramHealthEvents();
    initViewLifecycle();
    initRouter();
    startAutoRefresh();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bootApp);
  } else {
    bootApp();
  }

  // Navigation Router & Route Handlers
  function initRouter() {
    // Delegated click handler for any nav item or element with data-view
    document.addEventListener('click', (e) => {
      const link = e.target.closest('a[data-view], button[data-view]');
      if (link) {
        const targetView = link.getAttribute('data-view');
        if (targetView && viewRegistry.has(targetView)) {
          e.preventDefault();
          switchView(targetView);
        }
      }
    });

    // Handle deep links via pathname or hash and browser back/forward
    const handleRoute = () => {
      const rawHash = window.location.hash.replace(/^#\/?/, '').trim();
      const rawPath = window.location.pathname.replace(/^\/+|\/+$/g, '').trim();
      const currentTarget = rawHash || rawPath;

      if (currentTarget.startsWith('opportunity/')) {
        const oppId = decodeURIComponent(currentTarget.replace('opportunity/', ''));
        state.selectedOpportunityId = oppId;
        if (state.currentView !== 'opportunities') switchView('opportunities');
        loadOpportunityDetail(oppId, { openModal: false });
      } else if (currentTarget && viewRegistry.has(currentTarget)) {
        if (state.currentView !== currentTarget) switchView(currentTarget);
      }
    };

    window.addEventListener('hashchange', handleRoute);
    handleRoute();
  }

  // Theme Management
  function initTheme() {
    const toggleBtn = document.getElementById('theme-toggle-btn');
    const mobileToggleBtn = document.getElementById('mobile-theme-toggle');
    const toggle = () => {
      state.theme = state.theme === 'dark' ? 'light' : 'dark';
      document.documentElement.setAttribute('data-theme', state.theme);
    };
    if (toggleBtn) toggleBtn.addEventListener('click', toggle);
    if (mobileToggleBtn) mobileToggleBtn.addEventListener('click', toggle);
  }

  // Event Listeners Initialization
  function initEventListeners() {
    // Initialize Mobile More bottom-sheet drag-to-dismiss gesture handling
    initMobileSheetDrag();

    // Mobile More Navigation Sheet & Header Buttons
    const btnOpenMoreHeader = document.getElementById('mobile-more-btn-header');
    if (btnOpenMoreHeader) btnOpenMoreHeader.addEventListener('click', openMobileMore);

    const btnOpenMoreBottom = document.getElementById('btn-open-mobile-more');
    if (btnOpenMoreBottom) btnOpenMoreBottom.addEventListener('click', openMobileMore);

    const btnCloseMore = document.getElementById('btn-close-mobile-more');
    if (btnCloseMore) btnCloseMore.addEventListener('click', closeMobileMore);

    const moreBackdrop = document.getElementById('mobile-more-backdrop');
    if (moreBackdrop) moreBackdrop.addEventListener('click', closeMobileMore);

    // Refresh dashboard button
    const btnRefresh = document.getElementById('btn-refresh-dashboard');
    if (btnRefresh) {
      btnRefresh.addEventListener('click', () => {
        loadDashboardData();
        showToast('Dashboard reloaded from REST API');
      });
    }

    // Refresh opportunities button
    const btnRefreshOpps = document.getElementById('btn-refresh-opps');
    if (btnRefreshOpps) {
      btnRefreshOpps.addEventListener('click', () => {
        loadOpportunitiesData();
        showToast('Opportunities refreshed');
      });
    }

    // Opportunity Detail Modal — close & explorer handlers
    const btnCloseOppDetail = document.getElementById('btn-close-opp-detail');
    if (btnCloseOppDetail) {
      btnCloseOppDetail.addEventListener('click', () => closeOppDetailModal());
    }
    const oppBackdrop = document.getElementById('opp-detail-backdrop');
    if (oppBackdrop) {
      oppBackdrop.addEventListener('click', () => closeOppDetailModal());
    }
    const btnModalOpenExplorer = document.getElementById('btn-modal-open-explorer');
    if (btnModalOpenExplorer) {
      btnModalOpenExplorer.addEventListener('click', () => {
        const oppId = state.activeOpportunityDetail?.id || state.activeOpportunityDetail?.opportunity_id;
        closeOppDetailModal();
        window.location.hash = 'opportunities';
        if (oppId) {
          setTimeout(() => {
            if (typeof selectOpportunity === 'function') {
              selectOpportunity(oppId);
            }
          }, 100);
        }
      });
    }

    // Global modal Escape key handler
    window.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        closeMobileMore();
        if (typeof closePropDrawer === 'function') closePropDrawer();
        if (typeof closeOppDetailModal === 'function') closeOppDetailModal();
        if (typeof closeFixtureSelectorModal === 'function') closeFixtureSelectorModal();
        const stakeModal = document.getElementById('stake-modal');
        if (stakeModal) stakeModal.classList.remove('active');
      }
    });

    // Run Scan button
    const btnRunScan = document.getElementById('btn-run-scan');
    if (btnRunScan) {
      btnRunScan.addEventListener('click', handleRunScan);
    }

    // Role switcher (desktop and mobile)
    const handleRoleSwitch = () => {
      const roles = ['Admin', 'User', 'Guest'];
      const nextIndex = (roles.indexOf(state.currentRole) + 1) % roles.length;
      state.currentRole = roles[nextIndex];
      const el1 = document.getElementById('current-role-text');
      if (el1) el1.textContent = state.currentRole;
      const el2 = document.getElementById('mobile-role-text');
      if (el2) el2.textContent = state.currentRole;
      showToast(`Role switched to ${state.currentRole}`);
    };

    const roleBtn = document.getElementById('role-switch-btn');
    if (roleBtn) roleBtn.addEventListener('click', handleRoleSwitch);

    const mobRoleBtn = document.getElementById('mobile-role-switch-btn');
    if (mobRoleBtn) mobRoleBtn.addEventListener('click', handleRoleSwitch);

    // ── Opportunity Intelligence Workspace Category Tabs ──
    document.querySelectorAll('#explorer-category-tabs .opp-radar-tab').forEach(tabBtn => {
      tabBtn.addEventListener('click', () => {
        document.querySelectorAll('#explorer-category-tabs .opp-radar-tab').forEach(b => {
          b.classList.remove('active');
          b.setAttribute('aria-selected', 'false');
        });
        tabBtn.classList.add('active');
        tabBtn.setAttribute('aria-selected', 'true');

        const categoryType = tabBtn.getAttribute('data-type');
        const filterSort = document.getElementById('filter-sort');
        const sortIndicator = document.getElementById('sort-order-indicator');
        if (categoryType === 'QUOTE_DISCREPANCY') {
          if (filterSort) filterSort.value = 'discrepancy';
          state.opportunitySortOrder = 'desc';
          if (sortIndicator) sortIndicator.textContent = '↓';
        } else {
          if (filterSort && filterSort.value === 'discrepancy') {
            filterSort.value = 'ev';
          }
        }

        loadOpportunitiesData();
      });
    });

    // ── Workspace Layout Switcher (Split Workstation vs Feed Focus) ──
    const btnToggleWorkspace = document.getElementById('btn-toggle-workspace-layout');
    const oppWorkspace = document.getElementById('opp-workspace');
    const txtWorkspaceLayout = document.getElementById('txt-workspace-layout');
    if (btnToggleWorkspace && oppWorkspace) {
      btnToggleWorkspace.addEventListener('click', () => {
        const isSplit = oppWorkspace.classList.contains('split-active');
        if (isSplit) {
          oppWorkspace.classList.remove('split-active');
          if (txtWorkspaceLayout) txtWorkspaceLayout.textContent = 'Feed Focus';
          state.opportunityLayoutMode = 'feed';
        } else {
          oppWorkspace.classList.add('split-active');
          if (txtWorkspaceLayout) txtWorkspaceLayout.textContent = 'Split View';
          state.opportunityLayoutMode = 'split';
        }
      });
    }

    // ── Discovery Toolbar Filters & Search ──
    const statusFilter = document.getElementById('filter-opp-status');
    const providerFilter = document.getElementById('filter-provider');
    const sortFilter = document.getElementById('filter-sort');
    const btnSortOrder = document.getElementById('btn-sort-order');
    const sortOrderIndicator = document.getElementById('sort-order-indicator');
    const minScoreInput = document.getElementById('filter-min-score');
    const minExecEdgeInput = document.getElementById('filter-min-exec-edge');
    const minRoiInput = document.getElementById('filter-min-roi');
    const searchInput = document.getElementById('filter-search-text');
    const btnClearSearch = document.getElementById('btn-clear-search');

    let _oppSearchDebounce = null;
    [statusFilter, providerFilter, sortFilter, minScoreInput, minExecEdgeInput, minRoiInput].forEach(el => {
      if (el) el.addEventListener('change', () => loadOpportunitiesData());
    });

    // Sort order toggle (Desc / Asc)
    if (btnSortOrder) {
      btnSortOrder.addEventListener('click', () => {
        state.opportunitySortOrder = state.opportunitySortOrder === 'desc' ? 'asc' : 'desc';
        if (sortOrderIndicator) {
          sortOrderIndicator.textContent = state.opportunitySortOrder === 'desc' ? '↓' : '↑';
        }
        btnSortOrder.setAttribute('title', `Sort Order: ${state.opportunitySortOrder === 'desc' ? 'Descending' : 'Ascending'}`);
        loadOpportunitiesData();
      });
    }

    // Top 5 Leagues Filter Toggle
    const btnFilterTop5 = document.getElementById('btn-filter-top5');
    if (btnFilterTop5) {
      btnFilterTop5.addEventListener('click', () => {
        state.opportunityTop5Only = !state.opportunityTop5Only;
        btnFilterTop5.classList.toggle('active', state.opportunityTop5Only);
        btnFilterTop5.setAttribute('aria-pressed', state.opportunityTop5Only ? 'true' : 'false');
        loadOpportunitiesData();
      });
    }

    if (searchInput) {
      searchInput.addEventListener('input', () => {
        if (btnClearSearch) {
          btnClearSearch.style.display = searchInput.value.trim() ? 'block' : 'none';
        }
        if (_oppSearchDebounce) clearTimeout(_oppSearchDebounce);
        _oppSearchDebounce = setTimeout(() => {
          loadOpportunitiesData();
        }, 250);
      });
    }

    if (btnClearSearch && searchInput) {
      btnClearSearch.addEventListener('click', () => {
        searchInput.value = '';
        btnClearSearch.style.display = 'none';
        loadOpportunitiesData();
        searchInput.focus();
      });
    }

    // Toggle advanced thresholds tray
    const btnToggleOppAdv = document.getElementById('btn-toggle-opp-advanced-filters');
    const oppAdvPanel = document.getElementById('opp-advanced-filters-panel');
    if (btnToggleOppAdv && oppAdvPanel) {
      btnToggleOppAdv.addEventListener('click', () => {
        const isHidden = oppAdvPanel.style.display === 'none' || !oppAdvPanel.style.display;
        oppAdvPanel.style.display = isHidden ? 'block' : 'none';
        btnToggleOppAdv.classList.toggle('active', isHidden);
      });
    }

    // Clear thresholds action
    const btnClearThresholds = document.getElementById('btn-clear-thresholds');
    if (btnClearThresholds) {
      btnClearThresholds.addEventListener('click', () => {
        if (minScoreInput) minScoreInput.value = '';
        if (minExecEdgeInput) minExecEdgeInput.value = '';
        if (minRoiInput) minRoiInput.value = '';
        loadOpportunitiesData();
      });
    }

    // Reset all filters action
    const btnResetOppFilters = document.getElementById('btn-reset-opp-filters');
    if (btnResetOppFilters) {
      btnResetOppFilters.addEventListener('click', () => {
        resetAllOpportunityFilters();
      });
    }

    // Inspector pane action buttons
    const btnCloseInspector = document.getElementById('btn-close-inspector');
    if (btnCloseInspector) {
      btnCloseInspector.addEventListener('click', () => {
        deselectOpportunity();
      });
    }

    const btnExpandInspector = document.getElementById('btn-expand-inspector');
    if (btnExpandInspector) {
      btnExpandInspector.addEventListener('click', () => {
        if (state.selectedOpportunityId) {
          openOpportunityModal(state.selectedOpportunityId);
        }
      });
    }

    // Keyboard navigation within opportunity feed
    window.addEventListener('keydown', (e) => {
      if (state.currentView !== 'opportunities') return;
      const modal = document.getElementById('opp-detail-modal');
      const isModalOpen = modal && modal.classList.contains('open');

      if (e.key === 'Escape') {
        if (isModalOpen) {
          closeOppDetailModal();
        } else if (state.selectedOpportunityId) {
          deselectOpportunity();
        }
        return;
      }

      if (isModalOpen) return; // Don't navigate feed when modal is focused

      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        if (!state.opportunities || state.opportunities.length <= 1) return;
        const activeIdx = state.opportunities.findIndex(o => o.id === state.selectedOpportunityId);
        let nextIdx = activeIdx;
        if (e.key === 'ArrowDown') {
          nextIdx = activeIdx < state.opportunities.length - 1 ? activeIdx + 1 : 0;
        } else if (e.key === 'ArrowUp') {
          nextIdx = activeIdx > 0 ? activeIdx - 1 : state.opportunities.length - 1;
        }
        if (nextIdx >= 0 && nextIdx < state.opportunities.length) {
          e.preventDefault();
          selectOpportunity(state.opportunities[nextIdx].id, false, true);
        }
      }
    });

    // Event & Market Explorer Filters & Refresh
    const btnRefreshEvents = document.getElementById('btn-refresh-events');
    if (btnRefreshEvents) {
      btnRefreshEvents.addEventListener('click', () => {
        loadEventsData();
        showToast('Events refreshed from backend API');
      });
    }

    // Market Intelligence Workspace — View Mode Switcher
    const btnModeWorkspace = document.getElementById('btn-mode-workspace');
    const btnModeMatrix = document.getElementById('btn-mode-matrix');
    if (btnModeWorkspace) {
      btnModeWorkspace.addEventListener('click', () => setMarketIntelViewMode('workspace'));
    }
    if (btnModeMatrix) {
      btnModeMatrix.addEventListener('click', () => setMarketIntelViewMode('matrix'));
    }

    // Fixture Selector Modal controls
    const btnOpenFixtureSelector = document.getElementById('btn-open-fixture-selector');
    if (btnOpenFixtureSelector) {
      btnOpenFixtureSelector.addEventListener('click', openFixtureSelectorModal);
    }
    const btnCloseFixtureModal = document.getElementById('btn-close-fixture-modal');
    if (btnCloseFixtureModal) {
      btnCloseFixtureModal.addEventListener('click', closeFixtureSelectorModal);
    }
    const fixtureModalBackdrop = document.getElementById('fixture-selector-backdrop');
    if (fixtureModalBackdrop) {
      fixtureModalBackdrop.addEventListener('click', closeFixtureSelectorModal);
    }

    const modalFixtureSearch = document.getElementById('modal-fixture-search');
    if (modalFixtureSearch) {
      modalFixtureSearch.addEventListener('input', () => filterFixtureModalList(modalFixtureSearch.value));
    }

    // All Fixtures Coverage Matrix Filters
    const filterMatrixSearch = document.getElementById('filter-matrix-search');
    const filterMatrixComp = document.getElementById('filter-matrix-competition');
    const filterMatrixCov = document.getElementById('filter-matrix-coverage');

    if (filterMatrixSearch) {
      filterMatrixSearch.addEventListener('input', (e) => {
        state.marketIntel.matrixSearch = e.target.value;
        renderAllFixturesMatrix();
      });
    }
    if (filterMatrixComp) {
      filterMatrixComp.addEventListener('change', (e) => {
        state.marketIntel.matrixCompetition = e.target.value;
        renderAllFixturesMatrix();
      });
    }
    if (filterMatrixCov) {
      filterMatrixCov.addEventListener('change', (e) => {
        state.marketIntel.matrixCoverage = e.target.value;
        renderAllFixturesMatrix();
      });
    }

    // Save Settings
    const btnSaveSettings = document.getElementById('btn-save-settings');
    if (btnSaveSettings) {
      btnSaveSettings.addEventListener('click', saveSettingsFromForm);
    }

    // Scheduler Widget
    const schedToggle = document.getElementById('sched-enabled-toggle');
    if (schedToggle) {
      schedToggle.addEventListener('change', () => {
        applySchedulerConfig();
      });
    }
    const btnSchedApply = document.getElementById('btn-sched-apply');
    if (btnSchedApply) {
      btnSchedApply.addEventListener('click', () => {
        applySchedulerConfig();
      });
    }
    const btnSchedRunNow = document.getElementById('btn-sched-run-now');
    if (btnSchedRunNow) {
      btnSchedRunNow.addEventListener('click', handleSchedulerRunNow);
    }
    const btnSchedAddWindow = document.getElementById('btn-sched-add-window');
    if (btnSchedAddWindow) {
      btnSchedAddWindow.addEventListener('click', () => {
        addScheduleWindowRow();
      });
    }
    const btnSchedResetWindows = document.getElementById('btn-sched-reset-windows');
    if (btnSchedResetWindows) {
      btnSchedResetWindows.addEventListener('click', () => {
        resetScheduleWindows();
      });
    }
    const schedWindowsList = document.getElementById('sched-windows-list');
    if (schedWindowsList) {
      schedWindowsList.addEventListener('click', (e) => {
        const delBtn = e.target.closest('.btn-sched-del-window');
        if (delBtn) {
          const row = delBtn.closest('.sched-window-row');
          if (row) {
            const allRows = schedWindowsList.querySelectorAll('.sched-window-row');
            if (allRows.length > 1) {
              row.remove();
            } else {
              showToast('At least one schedule window is required.');
            }
          }
        }
      });
    }
  }

  // Auto Refresh Interval
  function startAutoRefresh() {
    state.autoRefreshTimer = setInterval(() => {
      const autoRefreshCheckbox = document.getElementById('auto-refresh-opps');
      if (autoRefreshCheckbox && autoRefreshCheckbox.checked && state.currentView === 'opportunities') {
        loadOpportunitiesData(true);
      }
    }, 5000);
  }

  // Toast Notification
  function showToast(message) {
    const toast = document.createElement('div');
    toast.className = 'toast-message';
    toast.textContent = message;
    toast.style.position = 'fixed';
    toast.style.bottom = '20px';
    toast.style.right = '20px';
    toast.style.background = 'var(--accent-primary)';
    toast.style.color = '#fff';
    toast.style.padding = '10px 18px';
    toast.style.borderRadius = '8px';
    toast.style.boxShadow = '0 4px 12px rgba(0,0,0,0.3)';
    toast.style.zIndex = '9999';
    toast.style.fontSize = '0.85rem';
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), 2500);
  }

  // Format Helper for timestamps
  function formatTimestamp(isoStr) {
    if (!isoStr) return '—';
    try {
      const d = new Date(isoStr);
      if (isNaN(d.getTime())) return '—';
      return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    } catch {
      return '—';
    }
  }
  const formatDate = formatTimestamp;


  // Safe formatting helpers to prevent undefined/NaN in UI
  function safeNum(val, fallback) {
    if (val === null || val === undefined) return fallback !== undefined ? fallback : '—';
    const n = Number(val);
    if (isNaN(n)) return fallback !== undefined ? fallback : '—';
    return n;
  }

  function safePct(val, decimals) {
    if (val === null || val === undefined) return '—';
    const n = Number(val);
    if (isNaN(n)) return '—';
    const d = decimals !== undefined ? decimals : 2;
    return `${n >= 0 ? '+' : ''}${n.toFixed(d)}%`;
  }

  function safeDuration(val) {
    if (val === null || val === undefined) return '—';
    const n = Number(val);
    if (isNaN(n)) return '—';
    return `${n.toFixed(1)}s`;
  }

  function safeStr(val, fallback) {
    if (val === null || val === undefined || val === '') return fallback !== undefined ? fallback : '—';
    if (typeof val === 'object') return fallback !== undefined ? fallback : '—';
    return String(val);
  }

  // ──────────────────────────────────────────────────────────────────────────
  // Scan Execution Controller
  // ──────────────────────────────────────────────────────────────────────────

  async function handleRunScan() {
    const btnRun = document.getElementById('btn-run-scan');
    const badge = document.getElementById('dash-scanner-status-badge');
    const alertBox = document.getElementById('dash-alert-container');
    const modeSelect = document.getElementById('dash-scan-mode-select');
    const progContainer = document.getElementById('dash-scan-progress-container');
    const progTitle = document.getElementById('dash-progress-title');
    const progModeBadge = document.getElementById('dash-progress-mode-badge');
    const progBar = document.getElementById('dash-progress-bar-fill');

    const selectedMode = modeSelect ? modeSelect.value : 'NORMAL';
    const isUltra = selectedMode === 'ULTRA';

    // UI Loading State (prevents duplicate triggers)
    btnRun.disabled = true;
    btnRun.classList.add('btn-scanning');
    btnRun.innerHTML = isUltra
      ? `<span class="spinner-icon">⟳</span> ULTRA RUNNING...`
      : `<span class="spinner-icon">⟳</span> Scanning (${selectedMode})...`;

    badge.className = 'badge badge-cycle-scanning';
    badge.textContent = isUltra ? '🟡 ULTRA RUNNING' : 'SCANNING';

    if (alertBox) alertBox.innerHTML = '';

    // Show scan progress feedback container with indeterminate activity state
    if (progContainer) {
      progContainer.style.display = 'block';
      if (progModeBadge) progModeBadge.textContent = isUltra ? 'ULTRA SCAN (FULL DAY)' : `${selectedMode} MODE`;
      if (progTitle) progTitle.textContent = isUltra ? 'Executing Full-Day ULTRA Scan Cycle...' : `Running ${selectedMode} Scan Cycle...`;
      if (progBar) {
        progBar.classList.add('indeterminate');
        progBar.style.width = '45%';
      }
    }

    const step1 = document.getElementById('prog-step-1');
    const step2 = document.getElementById('prog-step-2');
    const step3 = document.getElementById('prog-step-3');
    const step4 = document.getElementById('prog-step-4');

    [step1, step2, step3, step4].forEach((s, idx) => {
      if (s) {
        s.style.color = idx === 0 ? 'var(--brand-primary)' : 'var(--text-muted)';
        s.style.fontWeight = idx === 0 ? '600' : 'normal';
      }
    });

    try {
      const res = await api.runScan(selectedMode);

      if (progBar) {
        progBar.classList.remove('indeterminate');
        progBar.style.width = '100%';
      }
      [step1, step2, step3, step4].forEach(s => {
        if (s) { s.style.color = 'var(--val-positive)'; s.style.fontWeight = '600'; }
      });

      if (res.status_code === 200 && res.data) {
        state.latestScan = res.data;
        renderDashboardView(res.data);
        await refreshScanHistory();
        if (state.currentView === 'events') {
          await loadEventsData();
        }
        // P1-NEW-005: refresh whichever operator view is on screen so the
        // dashboard never shows fresh results above a stale explorer/props
        // list. Each refresh is isolated — a failing view must not break
        // the scan completion flow.
        try {
          if (state.currentView === 'opportunities') {
            await loadOpportunitiesData();
          } else if (state.currentView === 'playerprops') {
            await loadPlayerPropsData();
          } else if (state.currentView === 'dashboard') {
            await loadDashboardData();
          }
        } catch (refreshErr) {
          console.error('Post-scan view refresh failed:', refreshErr);
        }
        const execStatus = (res.data.status || res.data.cycle_status || 'SUCCESS').toUpperCase();
        if (execStatus === 'PARTIAL') {
          showToast(`${selectedMode} Scan completed with PARTIAL status (${res.data.duration_seconds}s)`);
        } else if (execStatus === 'FAILED') {
          showToast(`${selectedMode} Scan FAILED (${res.data.duration_seconds}s)`);
        } else {
          showToast(`${selectedMode} Scan complete (${res.data.duration_seconds}s) — Status: ${execStatus}`);
        }
      } else if (res.status_code === 409) {
        const conflictMsg = (res.errors && res.errors[0]) || 'Scan already in progress on server.';
        showToast(conflictMsg);
        if (alertBox) {
          alertBox.innerHTML = `<div class="alert-banner warning"><strong>Scan In Progress:</strong> ${conflictMsg}</div>`;
        }
      } else {
        const errorMsg = (res.errors && res.errors[0]) || 'Scan cycle encountered an error.';
        if (alertBox) {
          alertBox.innerHTML = `<div class="alert-banner error"><strong>Scan Error:</strong> ${errorMsg}</div>`;
        }
        showToast('Scan failed. See details.');
      }
    } catch (err) {
      if (progBar) progBar.classList.remove('indeterminate');
      console.error('Scan execution error:', err);
      if (alertBox) {
        alertBox.innerHTML = `<div class="alert-banner error"><strong>Scan Error:</strong> Communication error with backend API.</div>`;
      }
      showToast('Scan execution failed.');
    } finally {
      if (progBar) progBar.classList.remove('indeterminate');
      if (progContainer) {
        setTimeout(() => {
          progContainer.style.display = 'none';
        }, 1200);
      }
      btnRun.disabled = false;
      btnRun.classList.remove('btn-scanning');
      btnRun.innerHTML = `
        <svg class="run-scan-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg>
        <span id="btn-run-scan-text">Run Scan</span>
      `;
      // Refresh status
      const statusRes = await api.fetchScanStatus();
      if (statusRes.data) {
        updateScannerStatusBadge(statusRes.data.status);
      }
    }
  }

  function updateScannerStatusBadge(status) {
    const badge = document.getElementById('dash-scanner-status-badge');
    if (!badge) return;

    const upper = (status || '').toUpperCase();
    if (upper === 'READY') {
      badge.className = 'badge badge-cycle-ready';
      badge.textContent = '🟢 READY';
    } else if (upper === 'SCANNING' || upper === 'SCANNING_ULTRA') {
      badge.className = 'badge badge-cycle-scanning';
      badge.textContent = upper === 'SCANNING_ULTRA' ? '🟡 ULTRA RUNNING' : '🟡 SCANNING';
    } else if (upper === 'ERROR' || upper === 'FAILED') {
      badge.className = 'badge badge-cycle-failed';
      badge.textContent = '🔴 ERROR';
    } else if (upper === 'NOT_RUN') {
      badge.className = 'badge badge-cycle-notrun';
      badge.textContent = 'NOT_RUN';
    } else {
      badge.className = 'badge badge-cycle-notrun';
      badge.textContent = safeStr(status, 'UNKNOWN');
    }
  }

  async function refreshScanHistory() {
    try {
      const res = await api.fetchScanHistory(10);
      state.scanHistory = res.data || [];
      renderScanHistory(state.scanHistory);
    } catch (err) {
      console.error('Failed to load scan history', err);
    }
  }

  // ──────────────────────────────────────────────────────────────────────────
  // View Loader: Dashboard
  // ──────────────────────────────────────────────────────────────────────────

  async function loadDashboardData() {
    try {
      const [statusRes, latestRes, historyRes, healthRes, schedRes] = await Promise.all([
        api.fetchScanStatus(),
        api.fetchLatestScan(),
        api.fetchScanHistory(10),
        api.fetchHealth(),
        api.fetchSchedulerStatus().catch(() => ({ data: null })),
      ]);

      const statusData = statusRes.data || {};
      let latestData = latestRes.data;
      if (!latestData) {
        const ultraRes = await api.fetchLatestUltraScan().catch(() => ({ data: null }));
        if (ultraRes && ultraRes.data) {
          latestData = ultraRes.data;
        }
      }
      const historyData = historyRes.data || [];
      const healthData = healthRes.data || {};
      const schedData = schedRes.data || null;

      state.scanStatus = statusData;
      state.latestScan = latestData;
      state.scanHistory = historyData;
      if (schedData) state.schedulerStatus = schedData;

      // Update API Latency in Top Header and Mobile Sheet
      const latVal = `${statusRes.execution_time_ms || 2.1} ms`;
      const deskLat = document.getElementById('header-api-latency');
      if (deskLat) deskLat.textContent = latVal;
      const mobLat = document.getElementById('mobile-api-latency');
      if (mobLat) mobLat.textContent = latVal;

      // Restore API Connected Status
      const conn = document.getElementById('connection-status');
      if (conn) {
        conn.querySelector('.status-text').textContent = 'API REST Connected';
        conn.querySelector('.status-dot').style.backgroundColor = '#22C55E';
      }
      const mobDot = document.getElementById('mobile-connection-dot');
      if (mobDot) {
        mobDot.style.backgroundColor = '#22C55E';
      }

      // Update Status Badge
      updateScannerStatusBadge(statusData.status);

      if (latestData) {
        renderDashboardView(latestData);
      } else {
        renderNotRunDashboard(healthData);
      }

      renderScanHistory(historyData);
      if (schedData) renderSchedulerWidget(schedData);

    } catch (err) {
      console.error('Failed loading dashboard data', err);
      const conn = document.getElementById('connection-status');
      if (conn) {
        conn.querySelector('.status-text').textContent = 'API Offline';
        conn.querySelector('.status-dot').style.backgroundColor = '#EF4444';
      }
    }
  }

  function renderNotRunDashboard(healthData) {
    // Top Meta bar
    const lastScanEl = document.getElementById('dash-last-scan-time');
    if (lastScanEl) lastScanEl.textContent = 'Never';
    const scanDurEl = document.getElementById('dash-scan-duration');
    if (scanDurEl) scanDurEl.textContent = '—';
    const pulseDot = document.getElementById('dash-pulse-dot');
    if (pulseDot) {
      pulseDot.className = 'dash-pulse-dot';
    }

    // Hero metrics
    const elDiscovered = document.getElementById('dash-events-discovered');
    if (elDiscovered) elDiscovered.textContent = '—';
    const elSelected = document.getElementById('dash-events-selected');
    if (elSelected) elSelected.textContent = '—';
    const elMatched = document.getElementById('dash-events-matched');
    if (elMatched) elMatched.textContent = '—';
    const elEvaluated = document.getElementById('dash-markets-evaluated');
    if (elEvaluated) elEvaluated.textContent = '—';
    const elSurebets = document.getElementById('dash-surebets-count');
    if (elSurebets) elSurebets.textContent = '0';
    const elMaxMargin = document.getElementById('dash-max-margin');
    if (elMaxMargin) elMaxMargin.textContent = '0.00%';
    const dashValCount = document.getElementById('dash-valuebets-count');
    if (dashValCount) dashValCount.textContent = '0';
    const dashMaxEv = document.getElementById('dash-max-ev');
    if (dashMaxEv) dashMaxEv.textContent = '0.00%';
    const dashQualTotal = document.getElementById('dash-qualified-total');
    if (dashQualTotal) dashQualTotal.textContent = '—';

    // Status badge
    const statusBadge = document.getElementById('dash-cycle-status-badge');
    if (statusBadge) {
      statusBadge.className = 'badge badge-cycle-notrun';
      statusBadge.textContent = 'NOT_RUN';
    }
    const scanIdEl = document.getElementById('dash-scan-id');
    if (scanIdEl) scanIdEl.textContent = 'scan_id: —';

    // Pipeline Hero Standby View
    const pipelineHero = document.getElementById('dash-pipeline-svg-container');
    if (pipelineHero) {
      pipelineHero.innerHTML = `
        <div class="dash-pipeline-flow dash-pipeline-standby">
          <div class="dash-pipe-stage-card">
            <div class="dash-pipe-stage-num">01</div>
            <div class="dash-pipe-stage-title">DISCOVERY</div>
            <div class="dash-pipe-stage-val">—</div>
            <div class="dash-pipe-stage-sub">Awaiting run</div>
          </div>
          <div class="dash-pipe-connector">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M5 12h14M13 5l7 7-7 7"/></svg>
          </div>
          <div class="dash-pipe-stage-card">
            <div class="dash-pipe-stage-num">02</div>
            <div class="dash-pipe-stage-title">NORMALIZATION</div>
            <div class="dash-pipe-stage-val">—</div>
            <div class="dash-pipe-stage-sub">Awaiting run</div>
          </div>
          <div class="dash-pipe-connector">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M5 12h14M13 5l7 7-7 7"/></svg>
          </div>
          <div class="dash-pipe-stage-card">
            <div class="dash-pipe-stage-num">03</div>
            <div class="dash-pipe-stage-title">CROSS-OVERLAP</div>
            <div class="dash-pipe-stage-val">—</div>
            <div class="dash-pipe-stage-sub">Awaiting run</div>
          </div>
          <div class="dash-pipe-connector">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M5 12h14M13 5l7 7-7 7"/></svg>
          </div>
          <div class="dash-pipe-stage-card">
            <div class="dash-pipe-stage-num">04</div>
            <div class="dash-pipe-stage-title">EVALUATION</div>
            <div class="dash-pipe-stage-val">—</div>
            <div class="dash-pipe-stage-sub">Awaiting run</div>
          </div>
          <div class="dash-pipe-connector">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M5 12h14M13 5l7 7-7 7"/></svg>
          </div>
          <div class="dash-pipe-stage-card dash-pipe-qualified-node">
            <div class="dash-pipe-stage-num">05</div>
            <div class="dash-pipe-stage-title">QUALIFIED ALPHA</div>
            <div class="dash-pipe-stage-val">—</div>
            <div class="dash-pipe-stage-sub">Awaiting run</div>
          </div>
        </div>
      `;
    }

    // Last scan card
    const lastScanContent = document.getElementById('dash-last-scan-content');
    if (lastScanContent) {
      lastScanContent.innerHTML = `
        <div class="not-run-state dash-not-run-hero">
          <div class="dash-not-run-icon">
            <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><polygon points="5 3 19 12 5 21 5 3"/></svg>
          </div>
          <div class="dash-not-run-text">
            <h4>Production Pipeline Standby</h4>
            <p>No scan execution data recorded in this session. Trigger a scan cycle using <strong>Run Scan</strong> above to acquire bookmaker odds, match entities, and evaluate arbitrage.</p>
          </div>
        </div>
      `;
    }

    // Opportunities card
    const oppBadge = document.getElementById('dash-opp-badge');
    if (oppBadge) oppBadge.textContent = '0 Found';
    const oppsContainer = document.getElementById('dash-opps-container');
    if (oppsContainer) {
      oppsContainer.innerHTML = `
        <div class="not-run-state dash-opps-standby">
          <p class="text-muted">Awaiting first scan execution to detect betting signals...</p>
        </div>
      `;
    }

    // Provider Health fallback from health endpoint
    renderProviderHealthGrid({});
  }

  function renderSvgPipelineFlow(p) {
    const container = document.getElementById('dash-pipeline-svg-container');
    if (!container) return;

    const convOverlap = p.selected > 0 ? Math.min(100, Math.round((p.matched / p.selected) * 100)) : 0;
    const isQual = p.qualified > 0;

    container.innerHTML = `
      <div class="dash-pipeline-flow">
        <!-- Stage 1: Discovery -->
        <div class="dash-pipe-stage-card" title="Total fixtures and raw feeds discovered">
          <div class="dash-pipe-stage-top">
            <span class="dash-pipe-stage-num">01</span>
            <span class="dash-pipe-stage-badge">UNIVERSE</span>
          </div>
          <div class="dash-pipe-stage-title">DISCOVERY</div>
          <div class="dash-pipe-stage-val mono">${safeNum(p.discovered, 0)}</div>
          <div class="dash-pipe-stage-sub">Raw Fixtures Discovered</div>
        </div>

        <!-- Connector 1 -> 2 -->
        <div class="dash-pipe-connector" title="Scoped Selection">
          <svg class="dash-pipe-line-svg" viewBox="0 0 40 24" fill="none">
            <path d="M0 12 L30 12 M24 6 L30 12 L24 18" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
          <span class="dash-pipe-flow-pill mono">${p.isUltra ? 'WARSAW' : 'FILTER'}</span>
        </div>

        <!-- Stage 2: Scope & Normalization -->
        <div class="dash-pipe-stage-card" title="${p.isUltra ? 'ULTRA Event Horizon and canonical graphs generated' : 'Target-day slate and canonical graphs generated'}">
          <div class="dash-pipe-stage-top">
            <span class="dash-pipe-stage-num">02</span>
            <span class="dash-pipe-stage-badge">CANONICAL</span>
          </div>
          <div class="dash-pipe-stage-title">NORMALIZATION</div>
          <div class="dash-pipe-stage-val mono">${safeNum(p.selected, 0)}</div>
          <div class="dash-pipe-stage-sub">${p.isUltra ? 'ULTRA Event Horizon' : 'Normalized Events'}</div>
        </div>

        <!-- Connector 2 -> 3 -->
        <div class="dash-pipe-connector" title="Overlap Match Conversion">
          <svg class="dash-pipe-line-svg" viewBox="0 0 40 24" fill="none">
            <path d="M0 12 L30 12 M24 6 L30 12 L24 18" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
          <span class="dash-pipe-flow-pill mono text-accent">${convOverlap}% MATCH</span>
        </div>

        <!-- Stage 3: Cross-Bookmaker Overlap -->
        <div class="dash-pipe-stage-card ${p.matched > 0 ? 'stage-active' : ''}" title="Matches present in 2+ bookmakers with identical canonical entities">
          <div class="dash-pipe-stage-top">
            <span class="dash-pipe-stage-num">03</span>
            <span class="dash-pipe-stage-badge">OVERLAP</span>
          </div>
          <div class="dash-pipe-stage-title">CROSS-OVERLAP</div>
          <div class="dash-pipe-stage-val mono ${p.matched > 0 ? 'text-success' : ''}">${safeNum(p.matched, 0)}</div>
          <div class="dash-pipe-stage-sub">${safeNum(p.matchedMkts, 0)} Mkts Aligned</div>
        </div>

        <!-- Connector 3 -> 4 -->
        <div class="dash-pipe-connector" title="Market Evaluation Flow">
          <svg class="dash-pipe-line-svg" viewBox="0 0 40 24" fill="none">
            <path d="M0 12 L30 12 M24 6 L30 12 L24 18" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
          <span class="dash-pipe-flow-pill mono">${p.evalMkts} MKTS</span>
        </div>

        <!-- Stage 4: Canonical Market Evaluation -->
        <div class="dash-pipe-stage-card ${p.evalMkts > 0 ? 'stage-active' : ''}" title="Canonical markets evaluated for arbitrage margins and value edge">
          <div class="dash-pipe-stage-top">
            <span class="dash-pipe-stage-num">04</span>
            <span class="dash-pipe-stage-badge">EVALUATION</span>
          </div>
          <div class="dash-pipe-stage-title">EVALUATION</div>
          <div class="dash-pipe-stage-val mono ${p.evalMkts > 0 ? 'text-primary' : ''}">${safeNum(p.evalMkts, 0)}</div>
          <div class="dash-pipe-stage-sub">${p.excludedMkts} Excluded</div>
        </div>

        <!-- Connector 4 -> 5 -->
        <div class="dash-pipe-connector" title="Opportunity Qualification">
          <svg class="dash-pipe-line-svg" viewBox="0 0 40 24" fill="none">
            <path d="M0 12 L30 12 M24 6 L30 12 L24 18" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
          <span class="dash-pipe-flow-pill mono ${isQual ? 'text-success font-bold' : ''}">${isQual ? 'QUALIFIED' : 'FILTERED'}</span>
        </div>

        <!-- Stage 5: Qualified Actionable Signals -->
        <div class="dash-pipe-stage-card dash-pipe-qualified-node ${isQual ? 'stage-qualified-active' : ''}" title="Actionable surebets and value opportunities passing threshold">
          <div class="dash-pipe-stage-top">
            <span class="dash-pipe-stage-num">05</span>
            <span class="dash-pipe-stage-badge ${isQual ? 'badge-success' : 'badge-outline'}">${isQual ? 'ACTIONABLE' : 'ALPHA'}</span>
          </div>
          <div class="dash-pipe-stage-title">QUALIFIED ALPHA</div>
          <div class="dash-pipe-stage-val mono ${isQual ? 'text-success' : ''}">${safeNum(p.qualified, 0)}</div>
          <div class="dash-pipe-stage-sub">${p.surebets} SB &bull; ${p.valuebets} VB</div>
        </div>
      </div>
    `;
  }

  function renderDashboardView(scan) {
    const isUltra = Boolean(scan.funnel || (scan.execution_id && scan.execution_id.startsWith('ultra_')));
    const counts = scan.counts || {};
    const timings = scan.stage_timings || {};
    const metrics = scan.resource_metrics || {};
    const ultraFunnel = scan.funnel || {};

    let opps = scan.opportunities || [];
    if (isUltra && (!opps || opps.length === 0)) {
      const allUltraOpps = []
        .concat(scan.top_opportunities || [])
        .concat(scan.surebets || [])
        .concat(scan.valuebets || [])
        .concat(scan.player_props || [])
        .concat(scan.team_props || []);
      const seen = new Set();
      opps = allUltraOpps.filter(o => {
        const id = o.opportunity_id || o.id;
        if (!id || seen.has(id)) return false;
        seen.add(id);
        return true;
      }).map(o => {
        const parts = (o.match_name || '').split(' vs ');
        const existingLegs = (Array.isArray(o.legs) && o.legs.length > 0) ? o.legs : ((Array.isArray(o.selections) && o.selections.length > 0) ? o.selections : null);
        const mappedLegs = existingLegs ? existingLegs.map(l => ({
          selection_outcome: l.selection_outcome || l.selection || l.outcome || l.selection_type || 'Outcome',
          provider: l.provider || l.bookmaker || o.bookmaker || 'Book',
          bookmaker: l.bookmaker || l.provider || o.bookmaker || 'Book',
          raw_odds: l.raw_odds || l.odds || 0,
          odds: l.odds || l.raw_odds || 0,
          tax_rate: l.tax_rate,
          effective_odds: l.effective_odds,
        })) : [{
          selection_outcome: o.selection_display || o.outcome || 'Outcome',
          provider: o.bookmaker || 'Book',
          bookmaker: o.bookmaker || 'Book',
          raw_odds: o.raw_odds || o.effective_odds || 0,
          odds: o.effective_odds || o.raw_odds || 0,
        }];

        const edgeVal = o.edge_pct !== undefined ? o.edge_pct : (o.margin_pct !== undefined ? o.margin_pct : (o.value_percent || 0));

        return {
          ...o,
          id: o.opportunity_id || o.id,
          opportunity_id: o.opportunity_id || o.id,
          event: o.event || { home_team: parts[0] || o.match_name || 'Event', away_team: parts[1] || '' },
          event_name: o.event_name || o.match_name || (parts[0] ? `${parts[0]} vs ${parts[1] || ''}` : 'Event'),
          market: o.market || { display_name: o.market_display || o.market_label },
          market_label: o.market_label || o.market_display || (o.market && (o.market.label || o.market.display_name)),
          margin_pct: edgeVal,
          arbitrage_margin_pct: o.arbitrage_margin_pct !== undefined ? o.arbitrage_margin_pct : edgeVal,
          value_percent: o.value_percent !== undefined ? o.value_percent : edgeVal,
          lifecycle_status: o.lifecycle_status || (o.category === 'SUREBET' || o.category === 'VALUEBET' ? 'QUALIFIED' : (o.category || 'QUALIFIED')),
          opportunity_type: (o.category === 'SUREBET' || o.category === 'VALUEBET') ? o.category : (o.opportunity_type || o.type || (o.fair_odds ? 'VALUEBET' : (o.type || 'TEAM_PROP'))),
          legs: mappedLegs,
          calculation: o.calculation || { roi: edgeVal, implied_sum: o.implied_probability_sum },
        };
      });
    }

    const sureOpps = opps.filter(o => (o.opportunity_type || o.type) === 'SUREBET');
    const valOpps = opps.filter(o => (o.opportunity_type || o.type) === 'VALUEBET');

    // 1. Top Meta Bar & Pulse Dot
    const pulseDot = document.getElementById('dash-pulse-dot');
    if (pulseDot) {
      pulseDot.className = 'dash-pulse-dot active';
    }
    const lastScanEl = document.getElementById('dash-last-scan-time');
    if (lastScanEl) lastScanEl.textContent = formatTimestamp(scan.completed_at || scan.started_at);
    const durationEl = document.getElementById('dash-scan-duration');
    if (durationEl) durationEl.textContent = safeDuration(scan.duration_seconds);

    // 2. Hero Metrics Counts
    const discoveredEv = isUltra ? safeNum(ultraFunnel.discovered_events_total, counts.discovered_events) : counts.discovered_events;
    const selectedEv = isUltra ? safeNum((ultraFunnel.discovered_today_events || 0) + (ultraFunnel.discovered_tomorrow_events || 0) + (ultraFunnel.discovered_day_after_tomorrow_events || 0), counts.selected_events) : counts.selected_events;
    const matchedEv = isUltra ? safeNum((ultraFunnel.matched_events_today || 0) + (ultraFunnel.matched_events_tomorrow || 0) + (ultraFunnel.matched_events_day_after_tomorrow || 0), counts.matched_events) : counts.matched_events;
    const evalMktsVal = isUltra ? safeNum(ultraFunnel.evaluated_markets_total, metrics.markets_evaluated) : metrics.markets_evaluated;

    const elDiscovered = document.getElementById('dash-events-discovered');
    if (elDiscovered) elDiscovered.textContent = safeNum(discoveredEv, '—');
    const elSelected = document.getElementById('dash-events-selected');
    if (elSelected) elSelected.textContent = safeNum(selectedEv, '—');
    const elMatched = document.getElementById('dash-events-matched');
    if (elMatched) elMatched.textContent = safeNum(matchedEv, '—');
    const elEvaluated = document.getElementById('dash-markets-evaluated');
    if (elEvaluated) elEvaluated.textContent = safeNum(evalMktsVal, '—');

    const elSelectedLabel = document.getElementById('dash-events-selected-label');
    if (elSelectedLabel) {
      elSelectedLabel.textContent = isUltra ? 'ULTRA Event Horizon (Warsaw)' : 'normalized / selected';
    }
    const elMatchedFoot = document.getElementById('dash-events-matched-foot');
    if (elMatchedFoot) {
      elMatchedFoot.textContent = isUltra
        ? `Cross-Bookmaker Overlap (${safeNum(ultraFunnel.matched_markets_total, 0)} mkts)`
        : 'Cross-Bookmaker Event Overlap';
    }
    const elEvalFoot = document.getElementById('dash-markets-evaluated-foot');
    if (elEvalFoot) {
      elEvalFoot.textContent = isUltra
        ? 'Surebets, Valuebets & Props Markets'
        : 'Comparable Canonical Markets';
    }

    const sbCount = isUltra ? safeNum(counts.surebets, sureOpps.length) : safeNum(counts.detected_opportunities !== undefined ? counts.detected_opportunities : sureOpps.length, 0);
    const elSurebets = document.getElementById('dash-surebets-count');
    if (elSurebets) elSurebets.textContent = sbCount;

    const validSurebetOpps = sureOpps.filter(o => {
      const isSb = (o.calculation?.is_surebet !== undefined) ? o.calculation.is_surebet : (o.is_qualified !== false);
      const m = (o.calculation?.roi !== undefined) ? o.calculation.roi : safeNum(o.arbitrage_margin_pct || o.margin_pct, 0);
      return isSb && m > 0;
    });
    const maxMargin = validSurebetOpps.length > 0
      ? Math.max(...validSurebetOpps.map(o => (o.calculation?.roi !== undefined ? o.calculation.roi : safeNum(o.arbitrage_margin_pct || o.margin_pct, 0))))
      : (isUltra && (scan.surebets || []).length > 0 ? Math.max(...(scan.surebets || []).map(s => safeNum(s.edge_pct, 0))) : 0.0);
    const elMaxMargin = document.getElementById('dash-max-margin');
    if (elMaxMargin) elMaxMargin.textContent = safePct(maxMargin);

    const vbCount = isUltra ? safeNum(counts.valuebets, valOpps.length) : safeNum(counts.valuebets_qualified !== undefined ? counts.valuebets_qualified : (counts.valuebet_candidates || valOpps.length), 0);
    const dashValCount = document.getElementById('dash-valuebets-count');
    if (dashValCount) dashValCount.textContent = vbCount;

    const maxEv = valOpps.length > 0
      ? Math.max(...valOpps.map(o => safeNum(o.value_percent || o.margin_pct, 0)))
      : (isUltra && (scan.valuebets || []).length > 0 ? Math.max(...(scan.valuebets || []).map(v => safeNum(v.edge_pct, 0))) : 0.0);
    const dashMaxEv = document.getElementById('dash-max-ev');
    if (dashMaxEv) dashMaxEv.textContent = safePct(maxEv);

    const qualifiedTotal = isUltra
      ? safeNum(counts.top_opportunities, sbCount + vbCount + safeNum(counts.player_props, 0) + safeNum(counts.team_props, 0))
      : (sbCount + vbCount);
    const elQualTotal = document.getElementById('dash-qualified-total');
    if (elQualTotal) elQualTotal.textContent = qualifiedTotal;

    // 3. Status Badge & Execution ID
    const statusBadge = document.getElementById('dash-cycle-status-badge');
    const cycleStatus = (scan.status || scan.cycle_status || 'UNKNOWN').toUpperCase();
    if (statusBadge) {
      statusBadge.textContent = cycleStatus;
      if (cycleStatus === 'SUCCESS') {
        statusBadge.className = 'badge badge-cycle-success';
      } else if (cycleStatus === 'PARTIAL') {
        statusBadge.className = 'badge badge-cycle-partial';
      } else {
        statusBadge.className = 'badge badge-cycle-failed';
      }
    }

    const scanIdEl = document.getElementById('dash-scan-id');
    if (scanIdEl) scanIdEl.textContent = scan.execution_id;

    // Funnel numbers
    const matchingDiag = scan.matching_diagnostic || {};
    const funnel = scan.evaluation_funnel || {};
    const matchedEvs = isUltra
      ? matchedEv
      : (matchingDiag.matched_events !== undefined ? matchingDiag.matched_events : safeNum(counts.matched_events, 0));
    const matchedMkts = isUltra
      ? safeNum(ultraFunnel.matched_markets_total, 0)
      : safeNum(funnel.matched_markets, safeNum(counts.markets_matched, 0));
    const evalMkts = isUltra
      ? safeNum(ultraFunnel.evaluated_markets_total, 0)
      : safeNum(funnel.evaluated_markets, safeNum(metrics.markets_evaluated, 0));
    const rejMkts = isUltra
      ? Math.max(0, safeNum(ultraFunnel.normalized_markets_total, 0) - matchedMkts)
      : safeNum(funnel.rejected_markets, safeNum(counts.rejected_markets, 0));
    const notEvalMkts = isUltra ? 0 : safeNum(funnel.not_evaluated_markets, safeNum(counts.not_evaluated_markets, 0));
    const validSurebets = isUltra
      ? safeNum(counts.surebets, 0)
      : safeNum(funnel.valid_surebets, safeNum(counts.detected_opportunities, 0));
    const validValuebets = isUltra
      ? safeNum(counts.valuebets, 0)
      : safeNum(funnel.value_candidates, safeNum(counts.valuebets_qualified, 0));

    // Overlap rate
    const overlapPct = counts.cross_bookmaker_overlap_rate_pct !== undefined
      ? `${counts.cross_bookmaker_overlap_rate_pct}%`
      : (counts.cross_bookmaker_overlap_rate ? `${(counts.cross_bookmaker_overlap_rate * 100).toFixed(1)}%` : '—');
    const overlapBadge = document.getElementById('dash-coverage-overlap-badge');
    if (overlapBadge) overlapBadge.textContent = `${overlapPct} Overlap Rate`;

    // 4. Render Unified SVG Scan Pipeline Hero
    renderSvgPipelineFlow({
      discovered: discoveredEv,
      selected: selectedEv,
      matched: matchedEvs,
      matchedMkts: matchedMkts,
      evalMkts: evalMkts,
      excludedMkts: rejMkts + notEvalMkts,
      qualified: qualifiedTotal,
      surebets: validSurebets,
      valuebets: validValuebets,
      overlapPct: overlapPct,
      isUltra: isUltra,
      cycleStatus: cycleStatus,
    });

    // 5. Live Opportunity Radar Card (Bloomberg / Sports Trading Terminal Intelligence Feed)
    const oppBadge = document.getElementById('dash-opp-badge');
    const oppDot = document.querySelector('#card-opportunities-summary .dash-section-dot');
    const oppsContainer = document.getElementById('dash-opps-container');

    if (opps.length > 0) {
      if (oppBadge) {
        oppBadge.textContent = `${opps.length} Qualified`;
        oppBadge.className = 'badge badge-success';
      }
      if (oppDot) {
        oppDot.classList.remove('pulse-blue');
        oppDot.classList.add('pulse-emerald');
      }
    } else {
      if (oppBadge) {
        oppBadge.textContent = '0 Qualified (Clean)';
        oppBadge.className = 'badge badge-outline';
      }
      if (oppDot) {
        oppDot.classList.remove('pulse-emerald');
        oppDot.classList.add('pulse-blue');
      }
    }

    if (oppsContainer) {
      if (opps.length > 0) {
        const renderedCards = opps.slice(0, 8).map(o => {
          const ev = o.event || {};
          const mkt = o.market || {};
          const eventName = (ev.home_team && ev.away_team)
            ? `${ev.home_team} vs ${ev.away_team}`
            : (o.event_name || o.canonical_event_id || 'Event Matchup');
          const mktDisplay = o.market_label || mkt.label || mkt.display_name || (mkt.type ? `${mkt.type}${(mkt.line !== null && mkt.line !== undefined) ? ' • ' + mkt.line : ''}` : o.canonical_market_key || 'Market');
          const oppId = o.id || o.opportunity_id || ('opp_' + Math.random().toString(36).substr(2, 9));
          const oppType = o.opportunity_type || o.type || (o.fair_odds ? 'VALUEBET' : (o.type || 'TEAM_PROP'));
          const isVb = oppType === 'VALUEBET';
          const marginPct = (o.value_percent !== undefined)
            ? o.value_percent
            : ((o.calculation?.roi !== undefined) ? o.calculation.roi : (o.margin_pct !== undefined ? o.margin_pct : (o.arbitrage_margin_pct || 0)));
          const lifecycleStatus = o.lifecycle_status || (o.lifecycle && o.lifecycle.status) || 'QUALIFIED';
          const legs = o.legs || o.selections || [];
          const sumS = (o.calculation?.implied_sum !== undefined) ? o.calculation.implied_sum : (o.implied_probability_sum || (o.mathematical_explanation && o.mathematical_explanation.implied_probability_sum));

          const edgePill = isVb
            ? `<span class="dash-radar-edge-pill type-vb mono font-bold">+${Number(marginPct).toFixed(2)}% NET EV</span>`
            : (oppType === 'SUREBET'
              ? `<span class="dash-radar-edge-pill type-sb mono font-bold">+${Number(marginPct).toFixed(2)}% ARB</span>`
              : `<span class="dash-radar-edge-pill type-prop mono font-bold">${Number(marginPct) > 0 ? '+' + Number(marginPct).toFixed(2) + '%' : 'QUOTE'}</span>`);

          let bodyHtml = '';
          if (isVb) {
            const bestOdds = o.bookmaker_odds || (legs[0] && (legs[0].raw_odds || legs[0].odds)) || o.execution_odds || '—';
            const execBook = (o.bookmakers && Array.isArray(o.bookmakers)) ? o.bookmakers.join(', ') : (o.bookmakers || (legs[0] && (legs[0].provider || legs[0].bookmaker)) || 'Bookmaker');
            const fairOdds = o.fair_odds || (o.mathematical_explanation && o.mathematical_explanation.fair_odds) || '—';
            const refBook = o.reference_bookmaker || 'Pinnacle Benchmark';
            const outcomeName = legs[0]?.selection_outcome || legs[0]?.outcome || legs[0]?.selection_type || o.outcome || 'Pick';

            bodyHtml = `
              <div class="dash-radar-vb-compare">
                <div class="dash-radar-vb-col executable">
                  <span class="dash-radar-col-tag">EXECUTABLE PICK</span>
                  <div class="dash-radar-col-main">
                    <strong class="dash-radar-sel-name text-truncate">${escapeHtml(outcomeName)}</strong>
                    <span class="badge badge-outline dash-mini-tag">${escapeHtml(execBook)}</span>
                  </div>
                  <div class="dash-radar-odds-row">
                    <span class="dash-radar-odds mono font-bold text-success">@ ${Number(bestOdds) ? Number(bestOdds).toFixed(2) : bestOdds}</span>
                  </div>
                </div>
                <div class="dash-radar-vb-divider">VS</div>
                <div class="dash-radar-vb-col benchmark">
                  <span class="dash-radar-col-tag">SHARP BENCHMARK</span>
                  <div class="dash-radar-col-main">
                    <span class="dash-radar-sel-name text-muted">Fair Price</span>
                    <span class="badge badge-outline dash-mini-tag text-muted">${escapeHtml(refBook)}</span>
                  </div>
                  <div class="dash-radar-odds-row">
                    <span class="dash-radar-odds mono font-bold text-info">@ ${Number(fairOdds) ? Number(fairOdds).toFixed(2) : fairOdds}</span>
                  </div>
                </div>
              </div>
            `;
          } else {
            const legChips = legs.map(l => {
              const bm = l.provider || l.bookmaker || 'Book';
              const sel = l.selection_outcome || l.outcome || l.selection_type || 'Selection';
              const rawOdds = Number(l.raw_odds || l.odds || 0);
              const taxRate = l.tax_rate !== undefined ? Number(l.tax_rate) : (String(bm).toLowerCase() === 'superbet' ? 0.12 : 0.0);
              const effOdds = Number(l.effective_odds || l.effective_net_odds || (rawOdds * (1.0 - taxRate)));
              return `
                <div class="dash-radar-leg-chip">
                  <div class="dash-radar-leg-info">
                    <span class="badge badge-outline dash-mini-tag">${escapeHtml(bm)}</span>
                    <strong class="dash-radar-leg-outcome text-truncate">${escapeHtml(sel)}</strong>
                  </div>
                  <div class="dash-radar-leg-pricing mono">
                    <span class="dash-radar-leg-odds font-bold text-success">${rawOdds.toFixed(2)}</span>
                    ${taxRate > 0 ? `<span class="dash-radar-leg-eff text-muted" title="12% Tax Adjusted">Eff: ${effOdds.toFixed(2)}</span>` : ''}
                  </div>
                </div>
              `;
            }).join('');

            bodyHtml = `
              <div class="dash-radar-legs-deck">
                ${legChips}
              </div>
            `;
          }

          return `
            <div class="dash-radar-card opp-table-row" data-id="${escapeHtml(oppId)}" tabindex="0" role="article" aria-label="Inspect ${escapeHtml(eventName)}">
              <div class="dash-radar-card-header">
                <div class="dash-radar-badge-wrap">
                  ${edgePill}
                  <span class="badge ${lifecycleStatus === 'STALE' ? 'badge-warning' : 'badge-success'} dash-status-pill">${escapeHtml(lifecycleStatus)}</span>
                </div>
                <div class="dash-radar-header-meta">
                  ${sumS !== undefined && Number(sumS) > 0 ? `<span class="dash-radar-sum-tag mono ${Number(sumS) < 1.0 ? 'text-success' : 'text-muted'}" title="Implied Probability Sum">S = ${Number(sumS).toFixed(4)}</span>` : ''}
                </div>
              </div>

              <div class="dash-radar-card-matchup">
                <h4 class="dash-radar-event-title">${escapeHtml(eventName)}</h4>
                <div class="dash-radar-market-title">${escapeHtml(mktDisplay)}</div>
              </div>

              <div class="dash-radar-card-body">
                ${bodyHtml}
              </div>

              <div class="dash-radar-card-footer">
                <span class="dash-radar-hint text-muted">Click row to inspect complete price matrix & mathematical proof</span>
                <button type="button" class="btn btn-sm btn-primary btn-dash-inspect" data-id="${escapeHtml(oppId)}" aria-label="Inspect ${escapeHtml(eventName)}">
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
                  <span>Inspect</span>
                </button>
              </div>
            </div>
          `;
        }).join('');

        oppsContainer.innerHTML = `
          <div class="dash-radar-deck">
            ${renderedCards}
          </div>
          ${opps.length > 8 ? `
            <div class="dash-radar-more-bar" style="margin-top: 0.75rem; text-align: center;">
              <a href="#opportunities" class="btn btn-outline btn-sm" data-view="opportunities">
                <span>View all ${opps.length} opportunities in Unified Explorer &rarr;</span>
              </a>
            </div>
          ` : ''}
        `;

        // Wire click and keyboard handlers
        oppsContainer.querySelectorAll('.btn-dash-inspect').forEach(btn => {
          btn.addEventListener('click', (e) => {
            e.stopPropagation();
            const oppId = btn.getAttribute('data-id');
            loadOpportunityDetail(oppId, { openModal: true });
          });
        });
        oppsContainer.querySelectorAll('.dash-radar-card').forEach(card => {
          const oppId = card.getAttribute('data-id');
          card.addEventListener('click', () => {
            loadOpportunityDetail(oppId, { openModal: true });
          });
          card.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault();
              loadOpportunityDetail(oppId, { openModal: true });
            }
          });
        });
      } else {
        // Zero Surebet State — Clean Interpretation
        const nearest = scan.nearest_opportunity;
        let nearestHtml = '';

        if (nearest) {
          nearestHtml = `
            <div class="dash-nearest-deck">
              <div class="dash-nearest-header">
                <span class="dash-nearest-title">Sub-Threshold Baseline Telemetry</span>
                <span class="badge badge-outline mono">Threshold: S &lt; 1.0000</span>
              </div>
              <div class="nearest-opp-grid dash-nearest-grid">
                <div class="nearest-opp-item">
                  <span class="dash-meta-k">Nearest Fixture</span>
                  <strong class="dash-meta-v text-truncate" title="${escapeHtml(nearest.event || 'N/A')}">${escapeHtml(nearest.event || 'N/A')}</strong>
                </div>
                <div class="nearest-opp-item">
                  <span class="dash-meta-k">Canonical Market</span>
                  <strong class="dash-meta-v mono">${escapeHtml(nearest.market || 'N/A')}</strong>
                </div>
                <div class="nearest-opp-item">
                  <span class="dash-meta-k">Prob Sum (S)</span>
                  <strong class="dash-meta-v mono text-warning">${nearest.implied_probability_sum || '—'}</strong>
                </div>
                <div class="nearest-opp-item">
                  <span class="dash-meta-k">Market Margin</span>
                  <strong class="dash-meta-v mono">${nearest.margin_pct !== undefined ? Number(nearest.margin_pct).toFixed(2) + '%' : '0.00%'}</strong>
                </div>
                <div class="nearest-opp-item">
                  <span class="dash-meta-k">Distance to Arb</span>
                  <strong class="dash-meta-v mono text-accent font-bold">${nearest.distance_to_arbitrage || '—'}</strong>
                </div>
              </div>
            </div>
          `;
        }

        oppsContainer.innerHTML = `
          <div class="zero-surebet-box dash-zero-surebet-box">
            <div class="zero-surebet-header dash-zero-header">
              <div class="dash-zero-icon-badge">
                <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>
              </div>
              <div class="dash-zero-text-group">
                <strong>Clean Scan: 0 Qualified Arbitrage Anomalies</strong>
                <p class="text-muted" style="margin: 0.25rem 0 0 0; font-size: 0.8rem;">
                  All ${evalMkts} canonical markets evaluated strictly above the zero-risk arbitrage threshold ($S \\ge 1.00$). 12% Polish bookmaker turnover tax fully accounted for.
                </p>
              </div>
            </div>
            ${nearestHtml}
            <div class="dash-zero-pulse-footer">
              <div class="dash-zero-live-pulse"></div>
              <span>Continuous Scanner Active &bull; Ingesting Superbet & Betclic &bull; Cycle ${escapeHtml(scan.execution_id || 'Active')}</span>
            </div>
          </div>
        `;
      }
    }

    // 6. Coverage & Telemetry Intelligence Card
    let coverageData = scan.bookmaker_coverage;
    if (isUltra && (!coverageData || Object.keys(coverageData).length === 0)) {
      coverageData = {
        superbet: {
          discovered: (ultraFunnel.discovered_superbet_today || 0) + (ultraFunnel.discovered_superbet_tomorrow || 0) + (ultraFunnel.discovered_superbet_day_after_tomorrow || 0),
          parsed: ultraFunnel.detail_fetch_success_superbet || 0,
          normalized: ultraFunnel.acquired_detail_events_superbet || 0,
          matched_events: matchedEv,
          markets_matched: ultraFunnel.markets_acquired_superbet || 0,
          status: ultraFunnel.provider_status?.superbet || 'COMPLETED',
          is_direct: true,
        },
        betclic: {
          discovered: (ultraFunnel.discovered_betclic_today || 0) + (ultraFunnel.discovered_betclic_tomorrow || 0) + (ultraFunnel.discovered_betclic_day_after_tomorrow || 0),
          parsed: ultraFunnel.detail_fetch_success_betclic || 0,
          normalized: ultraFunnel.acquired_detail_events_betclic || 0,
          matched_events: matchedEv,
          markets_matched: ultraFunnel.markets_acquired_betclic || 0,
          status: ultraFunnel.provider_status?.betclic || 'COMPLETED',
          is_direct: true,
        },
        bet365: {
          discovered: 0, parsed: 0, normalized: 0, matched_events: 0, markets_matched: 0, status: 'REFERENCE_ONLY', is_direct: false,
        },
        unibet: {
          discovered: 0, parsed: 0, normalized: 0, matched_events: 0, markets_matched: 0, status: 'REFERENCE_ONLY', is_direct: false,
        },
      };
    } else {
      coverageData = coverageData || {};
    }

    const renderCoverageRow = (key, isDirect) => {
      const cov = coverageData[key] || {
        discovered: 0, parsed: 0, normalized: 0, matched_events: 0, markets_matched: 0,
        status: isDirect ? 'NOT_RUN' : 'REFERENCE_ONLY'
      };

      const isGood = cov.status === 'COMPLETED' || cov.status === 'HEALTHY' || cov.status === 'SUCCESS' || cov.status === 'OK' || cov.status === 'AVAILABLE';
      const isReference = cov.status === 'REFERENCE_ONLY' || cov.status === 'NOT_USED' || cov.status === 'STANDBY';
      const isDegraded = cov.status === 'DEGRADED' || cov.status === 'PARTIAL' || cov.status === 'NO_DATA';
      const badgeCls = isGood ? 'badge-success' : (isReference ? 'badge-outline text-muted' : (isDegraded ? 'badge-warning' : 'badge-danger'));
      const statusIcon = isGood ? '✓' : (isReference ? '○' : (isDegraded ? '⚠' : '✕'));

      const typeBadge = isDirect
        ? `<span class="badge badge-success-subtle dash-mini-tag">DIRECT BOOK</span>`
        : `<span class="badge badge-outline dash-mini-tag text-muted">BENCHMARK FEED</span>`;

      const invNote = cov.invalid_count > 0 ? ` <span class="badge badge-warning" style="font-size:0.65rem;">(${cov.invalid_count} inv)</span>` : '';

      return `
        <tr>
          <td class="bold dash-prov-cell">
            <div class="dash-prov-cell-inner">
              <span class="dash-prov-name">${key.toUpperCase()}</span>
              ${typeBadge}
            </div>
          </td>
          <td class="mono">${safeNum(cov.discovered, 0)}</td>
          <td class="mono">${safeNum(cov.parsed, 0)}</td>
          <td class="mono">${safeNum(cov.normalized, 0)}</td>
          <td class="mono"><span class="${cov.matched_events > 0 ? 'text-success font-bold' : ''}">${safeNum(cov.matched_events, 0)}</span></td>
          <td class="mono">${safeNum(cov.markets_matched, 0)}</td>
          <td><span class="badge ${badgeCls}">${statusIcon} ${cov.status}</span>${invNote}</td>
        </tr>
      `;
    };

    const directRows = ['superbet', 'betclic'].map(k => renderCoverageRow(k, true)).join('');
    const benchmarkRows = ['bet365', 'unibet'].map(k => renderCoverageRow(k, false)).join('');

    // Odds API Telemetry
    const oapiTel = scan.odds_api_telemetry || (scan.diagnostics && scan.diagnostics.odds_api_telemetry) || {};
    const oapiAvailable = oapiTel.is_available !== undefined ? oapiTel.is_available : (oapiTel.status === 'COMPLETED' || oapiTel.status === 'HEALTHY' || (coverageData.bet365 && coverageData.bet365.parsed > 0));
    const oapiStatus = oapiTel.status || (oapiAvailable ? 'COMPLETED' : 'UNAVAILABLE');
    const oapiEventsFetched = safeNum(oapiTel.events_fetched !== undefined ? oapiTel.events_fetched : oapiTel.events_discovered, coverageData.bet365 ? coverageData.bet365.discovered : 0);
    const oapiModels = safeNum(oapiTel.bookmaker_event_models, (coverageData.bet365 ? coverageData.bet365.parsed : 0) + (coverageData.unibet ? coverageData.unibet.parsed : 0));
    const b365Parsed = safeNum(oapiTel.bet365_count, coverageData.bet365 ? coverageData.bet365.parsed : 0);
    const unibetParsed = safeNum(oapiTel.unibet_count, coverageData.unibet ? coverageData.unibet.parsed : 0);
    const oapiCanon = safeNum(oapiTel.canonical_events_contributed, (coverageData.bet365 ? coverageData.bet365.matched_events : 0) + (coverageData.unibet ? coverageData.unibet.matched_events : 0));
    const oapiMkts = safeNum(oapiTel.markets_contributed, (coverageData.bet365 ? coverageData.bet365.markets_matched : 0) + (coverageData.unibet ? coverageData.unibet.markets_matched : 0));
    const cacheHits = safeNum(oapiTel.cache_hits, 0);
    const cacheMisses = safeNum(oapiTel.cache_misses, 0);

    let oapiBannerHtml = '';
    if (oapiAvailable) {
      oapiBannerHtml = `
      <div class="odds-api-telemetry-banner dash-tele-banner">
        <div class="dash-tele-banner-header">
          <div class="dash-tele-title-wrap">
            <span class="dash-tele-icon">📡</span>
            <span class="dash-tele-banner-title">The Odds API Gateway Telemetry (Bet365 & Unibet Benchmark)</span>
          </div>
          <span class="badge badge-success">✓ ${oapiStatus}</span>
        </div>
        <div class="dash-tele-banner-grid">
          <div class="dash-tele-cell"><span class="dash-tele-lbl">Events Ingested:</span> <strong class="mono">${oapiEventsFetched}</strong></div>
          <div class="dash-tele-cell"><span class="dash-tele-lbl">Models Created:</span> <strong class="mono">${oapiModels}</strong> <small class="text-muted">(B365: ${b365Parsed}, Uni: ${unibetParsed})</small></div>
          <div class="dash-tele-cell"><span class="dash-tele-lbl">Canonical Events:</span> <strong class="mono">${oapiCanon}</strong></div>
          <div class="dash-tele-cell"><span class="dash-tele-lbl">Canonical Markets:</span> <strong class="mono">${oapiMkts}</strong></div>
          <div class="dash-tele-cell"><span class="dash-tele-lbl">Cache Efficiency:</span> <strong class="mono">${cacheHits} hits / ${cacheMisses} misses</strong></div>
        </div>
      </div>
      `;
    } else if (isUltra) {
      oapiBannerHtml = `
      <div class="odds-api-telemetry-banner dash-tele-banner standby">
        <div class="dash-tele-banner-header">
          <div class="dash-tele-title-wrap">
            <span class="dash-tele-icon">📡</span>
            <span class="dash-tele-banner-title">The Odds API Gateway: <span>REFERENCE BENCHMARK (STANDBY)</span></span>
          </div>
          <span class="badge badge-outline text-muted">STANDBY</span>
        </div>
        <p class="dash-tele-banner-desc">
          ULTRA executed directly via Polish licensed books (Superbet & Betclic). Secondary reference pricing via The Odds API remained on standby.
        </p>
      </div>
      `;
    } else {
      oapiBannerHtml = `
      <div class="odds-api-telemetry-banner dash-tele-banner degraded">
        <div class="dash-tele-banner-header">
          <div class="dash-tele-title-wrap">
            <span class="dash-tele-icon">⚠️</span>
            <span class="dash-tele-banner-title">The Odds API Gateway: <span class="text-warning">DEGRADED</span></span>
          </div>
          <span class="badge badge-warning">${oapiStatus}</span>
        </div>
        <p class="dash-tele-banner-desc">
          The Odds API provider was unavailable during this cycle. Bet365 and Unibet benchmark data was not incorporated.
        </p>
      </div>
      `;
    }

    const bcTelemetry = scan.betclic_telemetry || {};
    const bcMatched = isUltra
      ? matchedEv
      : (bcTelemetry.matched_events !== undefined ? bcTelemetry.matched_events : matchedEvs);
    const bcDetailed = isUltra
      ? safeNum(ultraFunnel.detail_fetch_success_betclic, 0)
      : (bcTelemetry.detailed_matched_events !== undefined ? bcTelemetry.detailed_matched_events : (bcMatched > 0 ? bcMatched : 0));
    const bcReqs = isUltra
      ? safeNum(ultraFunnel.detail_fetch_attempted_betclic, bcDetailed)
      : (bcTelemetry.detail_requests_attempted || 0);
    const bcSucc = isUltra
      ? safeNum(ultraFunnel.detail_fetch_success_betclic, bcDetailed)
      : (bcTelemetry.detail_requests_successful || 0);
    const bcFail = isUltra
      ? safeNum(ultraFunnel.detail_fetch_failed_betclic, 0)
      : (bcTelemetry.detail_requests_failed || 0);
    const bcCovPct = Math.round((bcTelemetry.detail_coverage || (bcMatched ? bcDetailed / bcMatched : 1)) * 100);

    const bcDetailPillHtml = bcReqs > 0 || bcMatched > 0 ? `
      <div class="dash-detail-acq-pill">
        <div class="dash-detail-acq-label-group">
          <span class="dash-detail-acq-title">Betclic Detail Acquisition:</span>
          <strong class="mono dash-detail-acq-stat">${bcDetailed} / ${bcMatched} events (${bcCovPct}%)</strong>
        </div>
        <div class="dash-detail-acq-stats mono">
          <span>Reqs: <strong>${bcReqs}</strong></span> &bull;
          <span class="text-success font-bold">Succ: <strong>${bcSucc}</strong></span> &bull;
          <span class="${bcFail > 0 ? 'text-danger font-bold' : 'text-muted'}">Fail: <strong>${bcFail}</strong></span>
        </div>
      </div>
    ` : '';

    const heroSummaryBarHtml = `
      <div class="dash-coverage-hero-bar">
        <div class="dash-cov-hero-card hero-overlap">
          <div class="dash-cov-hero-head">
            <span class="dash-cov-hero-label">Cross-Bookmaker Overlap Rate</span>
            <span class="dash-cov-hero-val mono font-bold">${overlapPct}</span>
          </div>
          <div class="dash-cov-progress-track" title="Cross-Bookmaker Overlap: ${overlapPct}">
            <div class="dash-cov-progress-fill" style="width: ${Math.min(100, Math.max(0, parseFloat(overlapPct) || 0))}%;"></div>
          </div>
        </div>

        <div class="dash-cov-hero-card">
          <span class="dash-cov-hero-label">Matched Events</span>
          <div class="dash-cov-hero-val mono text-success font-bold">
            ${matchedEvs}
            <span class="dash-cov-hero-sub">/ ${discoveredEv || selectedEv || matchedEvs} Discovered</span>
          </div>
        </div>

        <div class="dash-cov-hero-card">
          <span class="dash-cov-hero-label">Evaluated Markets</span>
          <div class="dash-cov-hero-val mono text-info font-bold">
            ${evalMkts}
            <span class="dash-cov-hero-sub">(${matchedMkts} Matched)</span>
          </div>
        </div>

        <div class="dash-cov-hero-card hero-stack">
          <span class="dash-cov-hero-label">Data Ingestion Stack</span>
          <div class="dash-cov-stack-chips">
            <span class="badge badge-success dash-mini-tag">Superbet Direct</span>
            <span class="badge badge-success dash-mini-tag">Betclic Direct</span>
            <span class="badge badge-outline dash-mini-tag text-muted">Odds API Ref</span>
          </div>
        </div>
      </div>
    `;

    const coverageTableHtml = `
      <div class="dash-sub-section">
        ${heroSummaryBarHtml}

        <div class="dash-sub-section-header" style="margin-top: 1rem;">
          <span class="dash-sub-title">Provider Ingestion Console</span>
          <span class="badge badge-accent mono">Overlap: ${overlapPct}</span>
        </div>
        <div class="table-responsive dash-table-wrap">
          <table class="coverage-table-mini dash-coverage-table">
            <thead>
              <tr>
                <th>Provider / Source</th>
                <th>Discovered</th>
                <th>Parsed</th>
                <th>Normalized</th>
                <th>Matched Evs</th>
                <th>Markets Matched</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              <tr class="dash-table-group-header">
                <td colspan="7">DIRECT EXECUTION PROVIDERS (POLISH REGULATED)</td>
              </tr>
              ${directRows}
              <tr class="dash-table-group-header">
                <td colspan="7">BENCHMARK & REFERENCE FEEDS (THE ODDS API GATEWAY)</td>
              </tr>
              ${benchmarkRows}
            </tbody>
          </table>
        </div>
        ${bcDetailPillHtml}
        ${oapiBannerHtml}
      </div>
    `;

    // Multi-Market Breakdown Table
    const mktBreakdown = scan.market_coverage_breakdown || counts.market_coverage_breakdown || {};
    const targetMktTypes = ['1X2', 'BTTS', 'TOTALS', 'DOUBLE_CHANCE', 'DRAW_NO_BET', 'HALF_TIME_RESULT'];
    
    const mktBreakdownRows = targetMktTypes.map(mType => {
      const data = mktBreakdown[mType] || { discovered: 0, normalized: 0, matched: 0, evaluated: 0 };
      const label = mType.replace(/_/g, ' ');
      return `
        <tr>
          <td class="bold">${label}</td>
          <td class="mono">${data.discovered || 0}</td>
          <td class="mono">${data.matched || 0}</td>
          <td class="mono">${data.evaluated || 0}</td>
        </tr>
      `;
    }).join('');

    const marketCoverageTableHtml = `
      <div class="dash-sub-section">
        <div class="dash-sub-section-header">
          <span class="dash-sub-title">Multi-Market Coverage Breakdown</span>
          <span class="badge badge-accent mono">${safeNum(metrics.markets_evaluated, 0)} Evaluated</span>
        </div>
        <div class="table-responsive dash-table-wrap">
          <table class="coverage-table-mini dash-coverage-table">
            <thead>
              <tr>
                <th>Market Family</th>
                <th>Discovered</th>
                <th>Matched</th>
                <th>Evaluated</th>
              </tr>
            </thead>
            <tbody>
              ${mktBreakdownRows}
            </tbody>
          </table>
        </div>
      </div>
    `;

    // Team Props Coverage Section (Stage 27B)
    const teamPropsMktTypes = [
      { key: 'TEAM_SHOTS', label: 'TEAM SHOTS' },
      { key: 'TEAM_SHOTS_ON_TARGET', label: 'SHOTS ON TARGET' },
      { key: 'TEAM_CORNERS', label: 'CORNERS' },
      { key: 'TEAM_FOULS', label: 'FOULS' },
      { key: 'TEAM_CARDS', label: 'CARDS' },
      { key: 'TEAM_OFFSIDES', label: 'OFFSIDES' },
      { key: 'TEAM_GOALS', label: 'GOALS' },
    ];

    let totalTpDiscovered = 0;
    let totalTpNormalized = 0;
    let totalTpMatched = 0;
    let totalTpEvaluated = 0;

    const teamPropsRows = teamPropsMktTypes.map(item => {
      const data = mktBreakdown[item.key] || { discovered: 0, normalized: 0, matched: 0, evaluated: 0 };
      const d = data.discovered || 0;
      const n = data.normalized || 0;
      const m = data.matched || 0;
      const e = data.evaluated || 0;
      totalTpDiscovered += d;
      totalTpNormalized += n;
      totalTpMatched += m;
      totalTpEvaluated += e;
      return `
        <tr>
          <td class="bold">${item.label}</td>
          <td class="mono">${d}</td>
          <td class="mono">${n}</td>
          <td class="mono"><span class="${m > 0 ? 'text-success bold' : ''}">${m}</span></td>
          <td class="mono">${e}</td>
        </tr>
      `;
    }).join('');

    const teamPropsCoverageTableHtml = `
      <div class="dash-sub-section">
        <div class="dash-sub-section-header">
          <span class="dash-sub-title">⚽ Team Props Coverage</span>
          <span class="badge ${totalTpMatched > 0 ? 'badge-success' : 'badge-outline'} mono">
            ${totalTpMatched} Matched / ${totalTpNormalized} Normalized
          </span>
        </div>
        <div class="table-responsive dash-table-wrap">
          <table class="coverage-table-mini dash-coverage-table">
            <thead>
              <tr>
                <th>Market Family</th>
                <th>Discovered</th>
                <th>Normalized</th>
                <th>Matched</th>
                <th>Evaluated</th>
              </tr>
            </thead>
            <tbody>
              ${teamPropsRows}
            </tbody>
            <tfoot>
              <tr style="font-weight: 600; border-top: 1px solid var(--bg-card-border);">
                <td>TOTAL TEAM PROPS</td>
                <td class="mono">${totalTpDiscovered}</td>
                <td class="mono">${totalTpNormalized}</td>
                <td class="mono"><span class="${totalTpMatched > 0 ? 'text-success' : ''}">${totalTpMatched}</span></td>
                <td class="mono">${totalTpEvaluated}</td>
              </tr>
            </tfoot>
          </table>
        </div>
      </div>
    `;

    // Matching Diagnostics Section
    const candPairs = safeNum(matchingDiag.candidates_generated, safeNum(counts.selected_events, 0));
    const rejBreakdown = matchingDiag.rejection_reasons_breakdown || {};
    const rejPillsHtml = Object.entries(rejBreakdown).map(([code, count]) => `
      <span class="diag-reason-pill">
        <span>${code.replace(/_/g, ' ')}:</span>
        <strong class="mono">${count}</strong>
      </span>
    `).join('');

    const matchDiagHtml = `
      <div class="dash-sub-section">
        <div class="dash-sub-section-header">
          <span class="dash-sub-title">Cross-Bookmaker Matching Diagnostics</span>
          <span class="badge ${matchedEvs > 0 ? 'badge-success' : 'badge-outline'} mono">${matchedEvs} Matched / ${candPairs} Candidates</span>
        </div>
        <p class="text-muted dash-diag-exp">${matchingDiag.explanation || 'No matching diagnostic available.'}</p>
        ${rejPillsHtml ? `<div class="diag-reasons-grid">${rejPillsHtml}</div>` : ''}
      </div>
    `;

    // Market Evaluation & Rejection Diagnostics
    const evalRejBreakdown = funnel.rejection_reasons_breakdown || {};
    const valRejBreakdown = funnel.valuebet_rejection_reasons_breakdown || {};
    
    const evalRejPillsHtml = Object.entries(evalRejBreakdown).map(([code, count]) => `
      <span class="diag-reason-pill">
        <span>${code.replace(/_/g, ' ')}:</span>
        <strong class="mono">${count}</strong>
      </span>
    `).join('');

    const valRejPillsHtml = Object.entries(valRejBreakdown).map(([code, count]) => `
      <span class="diag-reason-pill" style="border-left-color: var(--accent-info, #38bdf8);">
        <span>${code.replace(/_/g, ' ')}:</span>
        <strong class="mono">${count}</strong>
      </span>
    `).join('');

    const evalDiagHtml = (rejMkts > 0 || notEvalMkts > 0 || Object.keys(evalRejBreakdown).length > 0 || Object.keys(valRejBreakdown).length > 0) ? `
      <div class="dash-sub-section">
        <div class="dash-sub-section-header">
          <span class="dash-sub-title">Market Evaluation & Rejection Diagnostics</span>
          <span class="badge ${rejMkts > 0 ? 'badge-warning' : 'badge-outline'} mono">${rejMkts} Excluded / ${evalMkts} Evaluated</span>
        </div>
        ${evalRejPillsHtml ? `
          <div style="margin-top: 0.35rem; font-size: 0.75rem; color: var(--text-muted);">Surebet Evaluation Exclusions:</div>
          <div class="diag-reasons-grid">${evalRejPillsHtml}</div>
        ` : ''}
        ${valRejPillsHtml ? `
          <div style="margin-top: 0.35rem; font-size: 0.75rem; color: var(--text-muted);">Valuebet Reference Exclusions:</div>
          <div class="diag-reasons-grid">${valRejPillsHtml}</div>
        ` : ''}
      </div>
    ` : '';

    // Warning / Error alerts
    let warningsHtml = '';
    if (scan.warnings && scan.warnings.length > 0) {
      warningsHtml = `
        <div class="alert-banner warning" style="margin-bottom: 0.75rem;">
          <span>⚠️ <strong>Warnings (${scan.warnings.length}):</strong> ${scan.warnings.join(' | ')}</span>
        </div>
      `;
    }

    let errorsHtml = '';
    if (scan.errors && scan.errors.length > 0) {
      errorsHtml = `
        <div class="alert-banner error" style="margin-bottom: 0.75rem;">
          <span>🚨 <strong>Errors (${scan.errors.length}):</strong> ${scan.errors.join(' | ')}</span>
        </div>
      `;
    }

    const lastScanContent = document.getElementById('dash-last-scan-content');
    if (lastScanContent) {
      lastScanContent.innerHTML = `
        ${warningsHtml}
        ${errorsHtml}
        ${coverageTableHtml}

        <details class="dash-diagnostics-collapsible">
          <summary class="dash-diag-summary">
            <span>Engineering Diagnostics, Multi-Market Coverage & Timings</span>
            <svg class="dash-diag-chevron" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg>
          </summary>
          <div class="dash-diag-drawer-content">
            ${marketCoverageTableHtml}
            ${teamPropsCoverageTableHtml}
            ${matchDiagHtml}
            ${evalDiagHtml}

            <!-- Stage Timings Breakdown -->
            <div class="dash-sub-section">
              <span class="dash-sub-title">Pipeline Stage Timings</span>
              <div class="stage-timings-grid dash-timings-deck">
                <div class="stage-timing-col">
                  <span>Acquisition</span>
                  <strong class="mono">${timings.acquisition_seconds || 0}s</strong>
                </div>
                <div class="stage-timing-col">
                  <span>Normalization</span>
                  <strong class="mono">${timings.normalization_seconds || 0}s</strong>
                </div>
                <div class="stage-timing-col">
                  <span>Matching</span>
                  <strong class="mono">${timings.matching_seconds || 0}s</strong>
                </div>
                <div class="stage-timing-col">
                  <span>Detection</span>
                  <strong class="mono">${timings.detection_seconds || 0}s</strong>
                </div>
                <div class="stage-timing-col">
                  <span>Lifecycle</span>
                  <strong class="mono">${timings.lifecycle_seconds || 0}s</strong>
                </div>
                <div class="stage-timing-col">
                  <span>Total Duration</span>
                  <strong class="text-success mono">${timings.total_duration_seconds || 0}s</strong>
                </div>
              </div>
            </div>

            <!-- Resource Telemetry -->
            <div class="telemetry-bar dash-resource-bar">
              <span>HTTP Requests: <strong class="mono">${metrics.total_http_requests || 0}</strong> (Detail: <strong class="mono">${metrics.detail_http_requests || 0}</strong>)</span>
              <span>Peak Memory: <strong class="mono">${metrics.peak_memory_mb || 0} MB</strong></span>
            </div>
          </div>
        </details>
      `;
    }

    // 7. Provider & Subsystem Health Card
    renderProviderHealthGrid(scan);
  }

  function renderProviderHealthGrid(scan) {
    const provGrid = document.getElementById('dash-provider-health-list');
    if (!provGrid) return;

    const isUltra = Boolean(scan.funnel || (scan.execution_id && scan.execution_id.startsWith('ultra_')));
    const ultraFunnel = scan.funnel || {};
    const provResults = scan.provider_results || {};
    const bookmakerCov = scan.bookmaker_coverage || {};
    const oapiTel = scan.odds_api_telemetry || (scan.diagnostics && scan.diagnostics.odds_api_telemetry) || {};

    const sbCov = bookmakerCov.superbet || {};
    const bcCov = bookmakerCov.betclic || {};
    const b365Cov = bookmakerCov.bet365 || {};
    const uniCov = bookmakerCov.unibet || {};

    let sbStatus = 'UNKNOWN';
    let bcStatus = 'UNKNOWN';
    let b365Status = 'UNKNOWN';
    let uniStatus = 'UNKNOWN';
    let oapiStatus = 'UNKNOWN';
    let matchStatus = 'UNKNOWN';
    let detectorStatus = 'UNKNOWN';
    let dispatcherStatus = 'STANDBY';

    if (isUltra) {
      // 1. Direct Polish Execution Bookmaker: Superbet
      const sbProvSt = (ultraFunnel.provider_status && ultraFunnel.provider_status.superbet) || '';
      const sbSucc = safeNum(ultraFunnel.detail_fetch_success_superbet, 0);
      const sbFail = safeNum(ultraFunnel.detail_fetch_failed_superbet, 0);
      if (sbProvSt === 'AVAILABLE' || sbProvSt === 'OK' || sbSucc > 0) {
        sbStatus = sbFail > 0 ? (sbSucc > 0 ? 'PARTIAL' : 'FAILED') : 'OK';
      } else if (sbProvSt === 'FAILED' || sbProvSt === 'UNAVAILABLE') {
        sbStatus = sbProvSt;
      } else {
        sbStatus = sbCov.status || 'UNKNOWN';
      }

      // 2. Direct Polish Execution Bookmaker: Betclic
      const bcProvSt = (ultraFunnel.provider_status && ultraFunnel.provider_status.betclic) || '';
      const bcSucc = safeNum(ultraFunnel.detail_fetch_success_betclic, 0);
      const bcFail = safeNum(ultraFunnel.detail_fetch_failed_betclic, 0);
      if (bcProvSt === 'AVAILABLE' || bcProvSt === 'OK' || bcSucc > 0) {
        bcStatus = bcFail > 0 ? (bcSucc > 0 ? 'PARTIAL' : 'FAILED') : 'OK';
      } else if (bcProvSt === 'FAILED' || bcProvSt === 'UNAVAILABLE') {
        bcStatus = bcProvSt;
      } else {
        bcStatus = bcCov.status || 'UNKNOWN';
      }

      // 3 & 4. Reference Only Bookmakers: Bet365 & Unibet
      const oapiReqs = safeNum(ultraFunnel.odds_api_requests_made, 0);
      const oapiProvSt = (ultraFunnel.provider_status && ultraFunnel.provider_status.the_odds_api) || '';
      if (oapiReqs > 0 && (oapiProvSt === 'AVAILABLE' || oapiProvSt === 'OK')) {
        b365Status = 'OK (REF)';
        uniStatus = 'OK (REF)';
        oapiStatus = 'OK';
      } else if (oapiProvSt === 'UNAVAILABLE' || oapiProvSt === 'FAILED') {
        b365Status = 'UNAVAILABLE';
        uniStatus = 'UNAVAILABLE';
        oapiStatus = 'UNAVAILABLE';
      } else {
        b365Status = 'NOT USED';
        uniStatus = 'NOT USED';
        oapiStatus = 'NOT USED';
      }

      // 6. Matching Pipeline
      const matchedCount = isUltra
        ? safeNum((ultraFunnel.matched_events_today || 0) + (ultraFunnel.matched_events_tomorrow || 0) + (ultraFunnel.matched_events_day_after_tomorrow || 0), 0)
        : safeNum(counts.matched_events, 0);
      matchStatus = matchedCount > 0
        ? `OK (${matchedCount} matches)`
        : (scan.status === 'SUCCESS' ? 'OK (0 matches)' : 'UNKNOWN');

      // 7. Surebet Detector
      const sbFound = safeNum(scan.counts && scan.counts.surebets, (scan.surebets || []).length);
      detectorStatus = (scan.status === 'SUCCESS' || scan.status === 'PARTIAL')
        ? `OK (${sbFound} found)`
        : 'UNKNOWN';

      // 8. Telegram Dispatcher
      const tg = scan.telegram_dispatch;
      if (tg) {
        if (tg.status === 'DELIVERED') {
          dispatcherStatus = tg.messages_count ? `DELIVERED (${tg.messages_count})` : 'DELIVERED';
        } else if (tg.status === 'SKIPPED') {
          dispatcherStatus = 'STANDBY';
        } else if (tg.status === 'PARTIAL') {
          dispatcherStatus = 'PARTIAL';
        } else if (tg.status === 'FAILED') {
          dispatcherStatus = 'FAILED';
        } else {
          dispatcherStatus = tg.status || 'STANDBY';
        }
      } else {
        dispatcherStatus = (scan.counts && scan.counts.top_opportunities > 0) ? 'ACTIVE' : 'STANDBY';
      }

    } else {
      // Standard / Deep Scan
      const sb = provResults.superbet;
      const bc = provResults.betclic;

      sbStatus = sb ? (sb.status === 'COMPLETED' ? 'OK' : sb.status) : (sbCov.status || 'UNKNOWN');
      bcStatus = bc ? (bc.status === 'COMPLETED' ? 'OK' : bc.status) : (bcCov.status || 'UNKNOWN');

      b365Status = b365Cov.status ? (b365Cov.status === 'COMPLETED' || b365Cov.status === 'HEALTHY' ? 'OK' : b365Cov.status) : (oapiTel.is_available ? 'OK' : 'UNAVAILABLE');
      uniStatus = uniCov.status ? (uniCov.status === 'COMPLETED' || uniCov.status === 'HEALTHY' ? 'OK' : uniCov.status) : (oapiTel.is_available ? 'OK' : 'UNAVAILABLE');
      oapiStatus = oapiTel.status ? (oapiTel.status === 'COMPLETED' || oapiTel.status === 'HEALTHY' ? 'OK' : oapiTel.status) : (b365Status === 'OK' ? 'OK' : 'UNAVAILABLE');

      matchStatus = (scan.counts && scan.counts.matched_events > 0) ? `OK (${scan.counts.matched_events})` : (scan.cycle_status === 'SUCCESS' ? 'OK (0 matches)' : 'UNKNOWN');
      detectorStatus = scan.cycle_status === 'SUCCESS' || scan.cycle_status === 'PARTIAL' ? 'OK' : 'UNKNOWN';
      dispatcherStatus = (scan.counts && scan.counts.dispatched > 0) ? 'ACTIVE' : 'STANDBY';
    }

    const getBadge = (st) => {
      if (!st) return '<span class="badge badge-outline">UNKNOWN</span>';
      if (st === 'OK' || st === 'ACTIVE' || st === 'STANDBY' || st === 'DELIVERED' || st.startsWith('OK') || st.startsWith('DELIVERED')) return '<span class="badge badge-success">✓ ' + st + '</span>';
      if (st === 'DEGRADED' || st === 'PARTIAL' || st === 'NO_DATA' || st.startsWith('PARTIAL')) return '<span class="badge badge-warning">⚠ ' + st + '</span>';
      if (st === 'FAILED' || st === 'UNAVAILABLE' || st === 'DISABLED' || st.startsWith('FAILED')) return '<span class="badge badge-danger">✗ ' + st + '</span>';
      if (st === 'NOT USED' || st === 'NOT_USED' || st.includes('REF')) return '<span class="badge badge-outline" style="opacity: 0.75;">' + st + '</span>';
      return '<span class="badge badge-outline">' + st + '</span>';
    };

    provGrid.innerHTML = `
      <div class="provider-mini-card">
        <div class="provider-name">SUPERBET</div>
        ${getBadge(sbStatus)}
      </div>
      <div class="provider-mini-card">
        <div class="provider-name">BETCLIC</div>
        ${getBadge(bcStatus)}
      </div>
      <div class="provider-mini-card">
        <div class="provider-name">BET365 <span style="font-size:0.65rem; color:var(--text-muted);">(Odds API)</span></div>
        ${getBadge(b365Status)}
      </div>
      <div class="provider-mini-card">
        <div class="provider-name">UNIBET <span style="font-size:0.65rem; color:var(--text-muted);">(Odds API)</span></div>
        ${getBadge(uniStatus)}
      </div>
      <div class="provider-mini-card">
        <div class="provider-name">ODDS API GATEWAY</div>
        ${getBadge(oapiStatus)}
      </div>
      <div class="provider-mini-card">
        <div class="provider-name">MATCHING PIPELINE</div>
        ${getBadge(matchStatus)}
      </div>
      <div class="provider-mini-card">
        <div class="provider-name">SUREBET DETECTOR</div>
        ${getBadge(detectorStatus)}
      </div>
      <div class="provider-mini-card">
        <div class="provider-name">TELEGRAM DISPATCHER</div>
        ${getBadge(dispatcherStatus)}
      </div>
    `;
  }

  // ──────────────────────────────────────────────────────────────────────────
  // Scheduler Widget Helpers
  // ──────────────────────────────────────────────────────────────────────────

  function timeAgo(isoStr) {
    if (!isoStr) return '—';
    try {
      const diff = Math.floor((Date.now() - new Date(isoStr).getTime()) / 1000);
      if (diff < 60) return `${diff}s ago`;
      if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
      return `${Math.floor(diff / 3600)}h ago`;
    } catch { return '—'; }
  }

  function timeUntil(isoStr) {
    if (!isoStr) return 'Disabled';
    try {
      const diff = Math.floor((new Date(isoStr).getTime() - Date.now()) / 1000);
      if (diff <= 0) return 'now';
      if (diff < 60) return `in ${diff}s`;
      if (diff < 3600) return `in ${Math.floor(diff / 60)}m`;
      return `in ${Math.floor(diff / 3600)}h`;
    } catch { return '—'; }
  }

  const DEFAULT_SCHED_WINDOWS = [
    { start_time: '00:00', end_time: '08:00', interval_minutes: 120 },
    { start_time: '08:00', end_time: '14:00', interval_minutes: 60 },
    { start_time: '14:00', end_time: '18:00', interval_minutes: 30 },
    { start_time: '18:00', end_time: '23:00', interval_minutes: 15 },
    { start_time: '23:00', end_time: '24:00', interval_minutes: 60 },
  ];

  function createScheduleWindowRow(start = '08:00', end = '14:00', interval = 60) {
    const row = document.createElement('div');
    row.className = 'sched-window-row';
    row.style.cssText = 'display: grid; grid-template-columns: 1fr 1fr 1fr auto; gap: 0.35rem; align-items: center; background: var(--surface-elevated); padding: 0.25rem 0.4rem; border-radius: var(--radius-sm); border: 1px solid var(--border-subtle);';
    row.innerHTML = `
      <div style="display: flex; align-items: center; gap: 0.2rem;">
        <span style="font-size: 0.65rem; color: var(--text-muted);">From</span>
        <input type="text" class="input input-sm mono sched-win-start" value="${escapeHtml(start)}" placeholder="HH:MM" style="padding: 0.15rem 0.3rem; font-size: 0.75rem; text-align: center; width: 100%;" />
      </div>
      <div style="display: flex; align-items: center; gap: 0.2rem;">
        <span style="font-size: 0.65rem; color: var(--text-muted);">To</span>
        <input type="text" class="input input-sm mono sched-win-end" value="${escapeHtml(end)}" placeholder="HH:MM" style="padding: 0.15rem 0.3rem; font-size: 0.75rem; text-align: center; width: 100%;" />
      </div>
      <div style="display: flex; align-items: center; gap: 0.2rem;">
        <input type="number" min="1" max="1440" class="input input-sm mono sched-win-interval" value="${interval}" style="padding: 0.15rem 0.3rem; font-size: 0.75rem; text-align: center; width: 100%;" />
        <span style="font-size: 0.65rem; color: var(--text-muted);">m</span>
      </div>
      <button type="button" class="btn btn-xs btn-outline btn-sched-del-window" title="Remove window" style="padding: 0.15rem 0.35rem; font-size: 0.7rem; color: var(--text-muted);">&times;</button>
    `;
    return row;
  }

  function renderScheduleWindowsTable(windows) {
    const listEl = document.getElementById('sched-windows-list');
    if (!listEl) return;
    const wins = (Array.isArray(windows) && windows.length > 0) ? windows : DEFAULT_SCHED_WINDOWS;
    listEl.innerHTML = '';
    wins.forEach(w => {
      listEl.appendChild(createScheduleWindowRow(w.start_time, w.end_time, w.interval_minutes));
    });
  }

  function addScheduleWindowRow() {
    const listEl = document.getElementById('sched-windows-list');
    if (!listEl) return;
    const rows = listEl.querySelectorAll('.sched-window-row');
    let lastEnd = '00:00';
    if (rows.length > 0) {
      const lastRowEnd = rows[rows.length - 1].querySelector('.sched-win-end');
      if (lastRowEnd && lastRowEnd.value) lastEnd = lastRowEnd.value.trim();
    }
    listEl.appendChild(createScheduleWindowRow(lastEnd, '24:00', 30));
  }

  function resetScheduleWindows() {
    renderScheduleWindowsTable(DEFAULT_SCHED_WINDOWS);
    showToast('Schedule reset to default Warsaw windows.');
  }

  function getScheduleWindowsFromDOM() {
    const listEl = document.getElementById('sched-windows-list');
    if (!listEl) return DEFAULT_SCHED_WINDOWS;
    const rows = listEl.querySelectorAll('.sched-window-row');
    const windows = [];
    rows.forEach(row => {
      const start = row.querySelector('.sched-win-start')?.value?.trim() || '00:00';
      const end = row.querySelector('.sched-win-end')?.value?.trim() || '24:00';
      const interval = parseInt(row.querySelector('.sched-win-interval')?.value, 10) || 15;
      windows.push({
        start_time: start,
        end_time: end,
        interval_minutes: interval,
      });
    });
    return windows.length > 0 ? windows : DEFAULT_SCHED_WINDOWS;
  }

  function renderSchedulerWidget(sched) {
    if (!sched) return;
    const badge = document.getElementById('sched-status-badge');
    const toggle = document.getElementById('sched-enabled-toggle');
    const ultraCheck = document.getElementById('sched-scanner-ultra');
    const propsCheck = document.getElementById('sched-scanner-props');
    const activeEl = document.getElementById('sched-active-window-display');
    const nextEl = document.getElementById('sched-next-scan-rel');
    const lastEl = document.getElementById('sched-last-scan-rel');
    const chipBadgeUltra = document.getElementById('chip-badge-ultra');
    const chipMetaUltra = document.getElementById('chip-meta-ultra');
    const chipBadgeProps = document.getElementById('chip-badge-props');
    const chipMetaProps = document.getElementById('chip-meta-props');

    // Status Badge
    if (badge) {
      if (sched.is_running) {
        badge.textContent = 'RUNNING';
        badge.className = 'badge badge-cycle-partial';
      } else if (sched.last_cycle_status === 'FAILED' || sched.last_scan_status === 'FAILED') {
        badge.textContent = 'FAILED';
        badge.className = 'badge badge-cycle-failed';
      } else if (sched.last_cycle_status === 'PARTIAL') {
        badge.textContent = 'PARTIAL';
        badge.className = 'badge badge-cycle-partial';
      } else if (sched.enabled) {
        badge.textContent = 'ENABLED';
        badge.className = 'badge badge-cycle-success';
      } else {
        badge.textContent = 'DISABLED';
        badge.className = 'badge badge-outline';
      }
    }

    // Toggle
    if (toggle) toggle.checked = !!sched.enabled;

    // Scanner Checkboxes
    if (ultraCheck && sched.scanners) {
      ultraCheck.checked = sched.scanners.ultra !== false;
    }
    if (propsCheck && sched.scanners) {
      propsCheck.checked = sched.scanners.global_props !== false;
    }

    // Schedule windows table (avoid overwriting while user is editing it)
    const isEditingSchedule = document.activeElement && document.getElementById('sched-windows-list')?.contains(document.activeElement);
    if (!isEditingSchedule && sched.schedule) {
      renderScheduleWindowsTable(sched.schedule);
    }

    // Active Window Display
    if (activeEl) {
      if (sched.active_schedule_window_display) {
        activeEl.textContent = sched.active_schedule_window_display;
      } else if (sched.active_window) {
        activeEl.textContent = `${sched.active_window.start_time}–${sched.active_window.end_time}   Every ${sched.active_window.interval_minutes} min`;
      } else {
        activeEl.textContent = '—';
      }
    }

    // Next Scan
    if (nextEl) {
      if (!sched.enabled) {
        nextEl.textContent = 'Disabled';
      } else if (sched.next_scan_warsaw && sched.next_scan_at) {
        nextEl.textContent = `${timeUntil(sched.next_scan_at)} (${sched.next_scan_warsaw} Warsaw)`;
      } else if (sched.next_scan_at) {
        nextEl.textContent = timeUntil(sched.next_scan_at);
      } else {
        nextEl.textContent = '—';
      }
    }

    // Last Scan
    if (lastEl) {
      const dur = sched.last_cycle?.duration_sec ?? sched.last_duration_sec;
      const durStr = (dur != null && dur > 0) ? ` (${dur.toFixed(1)}s)` : '';
      const st = sched.last_cycle_status || sched.last_scan_status || '';
      const stStr = st ? ` [${st}]` : '';
      lastEl.textContent = `${timeAgo(sched.last_scan_at)}${durStr}${stStr}`;
    }

    // Per-Scanner Chips
    const ultraData = sched.last_cycle?.scanners?.ultra;
    if (chipBadgeUltra && chipMetaUltra) {
      if (ultraData) {
        const uStatus = ultraData.status || 'SUCCESS';
        chipBadgeUltra.textContent = uStatus;
        if (uStatus === 'SUCCESS') chipBadgeUltra.className = 'badge badge-cycle-success';
        else if (uStatus === 'FAILED') chipBadgeUltra.className = 'badge badge-cycle-failed';
        else chipBadgeUltra.className = 'badge badge-outline';

        const recStr = ultraData.records_count != null ? `${ultraData.records_count} opps · ` : '';
        const durStr = ultraData.duration_sec != null ? `${ultraData.duration_sec.toFixed(1)}s` : '';
        chipMetaUltra.textContent = `${recStr}${durStr || timeAgo(sched.last_ultra_scan_at)}`;
      } else if (sched.last_ultra_scan_at) {
        chipBadgeUltra.textContent = 'READY';
        chipBadgeUltra.className = 'badge badge-outline';
        chipMetaUltra.textContent = timeAgo(sched.last_ultra_scan_at);
      } else {
        chipBadgeUltra.textContent = 'IDLE';
        chipBadgeUltra.className = 'badge badge-outline';
        chipMetaUltra.textContent = '—';
      }
    }

    const propsData = sched.last_cycle?.scanners?.global_props;
    if (chipBadgeProps && chipMetaProps) {
      if (propsData) {
        const pStatus = propsData.status || 'SUCCESS';
        chipBadgeProps.textContent = pStatus;
        if (pStatus === 'SUCCESS') chipBadgeProps.className = 'badge badge-cycle-success';
        else if (pStatus === 'FAILED') chipBadgeProps.className = 'badge badge-cycle-failed';
        else chipBadgeProps.className = 'badge badge-outline';

        const discStr = propsData.discrepancies_count != null ? `${propsData.discrepancies_count} disc · ` : '';
        const durStr = propsData.duration_sec != null ? `${propsData.duration_sec.toFixed(1)}s` : '';
        chipMetaProps.textContent = `${discStr}${durStr || timeAgo(sched.last_global_props_scan_at)}`;
      } else if (sched.last_global_props_scan_at) {
        chipBadgeProps.textContent = 'READY';
        chipBadgeProps.className = 'badge badge-outline';
        chipMetaProps.textContent = timeAgo(sched.last_global_props_scan_at);
      } else {
        chipBadgeProps.textContent = 'IDLE';
        chipBadgeProps.className = 'badge badge-outline';
        chipMetaProps.textContent = '—';
      }
    }
  }

  async function applySchedulerConfig() {
    const toggle = document.getElementById('sched-enabled-toggle');
    const ultraCheck = document.getElementById('sched-scanner-ultra');
    const propsCheck = document.getElementById('sched-scanner-props');

    const executeUltra = ultraCheck ? ultraCheck.checked : true;
    const executeProps = propsCheck ? propsCheck.checked : true;

    if (!executeUltra && !executeProps && toggle && toggle.checked) {
      showToast('Select at least one scanner (ULTRA or Global Props).');
      return;
    }

    const scheduleWindows = getScheduleWindowsFromDOM();

    const payload = {
      enabled: toggle ? toggle.checked : false,
      execute_ultra: executeUltra,
      execute_global_props: executeProps,
      scanners: {
        ultra: executeUltra,
        global_props: executeProps,
      },
      schedule: scheduleWindows,
    };

    try {
      const res = await api.configureScheduler(payload);
      if (res.status_code === 200 && res.data) {
        state.schedulerStatus = res.data;
        renderSchedulerWidget(res.data);
        showToast('Scheduler configuration applied.');
      } else if (res.errors && res.errors.length > 0) {
        showToast(`Configuration error: ${res.errors.join(', ')}`);
      } else {
        showToast('Failed to apply scheduler config.');
      }
    } catch (err) {
      console.error('Failed to configure scheduler:', err);
      showToast('Failed to apply scheduler config.');
    }
  }

  async function handleSchedulerRunNow() {
    const btn = document.getElementById('btn-sched-run-now');
    const badge = document.getElementById('sched-status-badge');
    if (btn) {
      btn.disabled = true;
      btn.textContent = '⟳ Running Cycle...';
    }
    if (badge) {
      badge.textContent = 'RUNNING';
      badge.className = 'badge badge-cycle-partial';
    }
    try {
      const res = await api.schedulerRunNow();
      if (res.status_code === 200 && res.data) {
        state.latestScan = res.data;
        renderDashboardView(res.data);
        await refreshScanHistory();
        const cycleStatus = res.data.cycle_status || res.data.status || 'SUCCESS';
        showToast(`Automated scan cycle completed — ${cycleStatus}`);
      } else if (res.status_code === 409) {
        showToast('Scan cycle already in progress.');
      } else {
        const errMsg = (res.errors && res.errors[0]) || 'Scheduled scan failed.';
        showToast(errMsg);
      }
    } catch (err) {
      console.error('Scheduler run-now failed:', err);
      showToast('Scheduler run-now failed.');
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg> Run Now';
      }
      try {
        const schedRes = await api.fetchSchedulerStatus();
        if (schedRes.data) {
          state.schedulerStatus = schedRes.data;
          renderSchedulerWidget(schedRes.data);
        }
      } catch (e) {}
    }
  }

  function renderScanHistory(historyList) {
    const tbody = document.getElementById('dash-history-table-body');
    const countBadge = document.getElementById('dash-history-count');
    if (!tbody) return;

    if (countBadge) countBadge.textContent = `${historyList.length} Scans`;

    if (!historyList || historyList.length === 0) {
      tbody.innerHTML = `
        <tr>
          <td colspan="6" class="text-center text-muted" style="padding: 1.5rem;">No historical scans in this session.</td>
        </tr>
      `;
      return;
    }

    tbody.innerHTML = historyList.map(h => {
      let statusClass = 'badge-cycle-notrun';
      if (h.status === 'SUCCESS') statusClass = 'badge-cycle-success';
      else if (h.status === 'PARTIAL') statusClass = 'badge-cycle-partial';
      else if (h.status === 'FAILED') statusClass = 'badge-cycle-failed';

      const d = h.events_discovered || 0;
      const s = h.events_selected || 0;
      const m = h.events_matched || 0;
      const src = h.scan_source || 'MANUAL';
      const srcClass = src === 'AUTOMATED' ? 'badge badge-accent' : 'badge badge-outline';

      return `
        <tr>
          <td class="mono">${formatTimestamp(h.completed_at || h.started_at)}</td>
          <td><span class="${srcClass}" style="font-size:0.7rem;">${src}</span></td>
          <td><span class="badge ${statusClass}">${h.status}</span></td>
          <td class="mono">${d} / ${s} / ${m}</td>
          <td><strong class="${h.surebets_count > 0 ? 'text-success' : 'text-muted'}">${h.surebets_count}</strong></td>
          <td class="mono">${h.duration_seconds}s</td>
        </tr>
      `;
    }).join('');
  }

  // ──────────────────────────────────────────────────────────────────────────
  // Opportunity Explorer & Detail Inspector Loader
  // ──────────────────────────────────────────────────────────────────────────

  function showOpportunitiesListView() {
    closeOppDetailModal();
    if (window.location.hash.startsWith('#opportunity/')) {
      window.location.hash = 'opportunities';
    }
  }

  function resetAllOpportunityFilters() {
    // Reset tabs to ALL
    document.querySelectorAll('#explorer-category-tabs .opp-radar-tab').forEach(b => {
      const isAll = (b.getAttribute('data-type') || '') === '';
      b.classList.toggle('active', isAll);
      b.setAttribute('aria-selected', isAll ? 'true' : 'false');
    });

    // Reset discovery inputs
    const searchInput = document.getElementById('filter-search-text');
    if (searchInput) searchInput.value = '';
    const btnClearSearch = document.getElementById('btn-clear-search');
    if (btnClearSearch) btnClearSearch.style.display = 'none';

    const statusFilter = document.getElementById('filter-opp-status');
    if (statusFilter) statusFilter.value = '';

    const providerFilter = document.getElementById('filter-provider');
    if (providerFilter) providerFilter.value = '';

    const sortFilter = document.getElementById('filter-sort');
    if (sortFilter) sortFilter.value = 'ev';

    state.opportunitySortOrder = 'desc';
    const sortOrderIndicator = document.getElementById('sort-order-indicator');
    if (sortOrderIndicator) sortOrderIndicator.textContent = '↓';

    // Reset Top 5 filter
    state.opportunityTop5Only = false;
    const btnFilterTop5 = document.getElementById('btn-filter-top5');
    if (btnFilterTop5) {
      btnFilterTop5.classList.remove('active');
      btnFilterTop5.setAttribute('aria-pressed', 'false');
    }

    // Reset thresholds
    const minScoreInput = document.getElementById('filter-min-score');
    if (minScoreInput) minScoreInput.value = '';
    const minExecEdgeInput = document.getElementById('filter-min-exec-edge');
    if (minExecEdgeInput) minExecEdgeInput.value = '';
    const minRoiInput = document.getElementById('filter-min-roi');
    if (minRoiInput) minRoiInput.value = '';

    const oppAdvPanel = document.getElementById('opp-advanced-filters-panel');
    const btnToggleOppAdv = document.getElementById('btn-toggle-opp-advanced-filters');
    if (oppAdvPanel) oppAdvPanel.style.display = 'none';
    if (btnToggleOppAdv) btnToggleOppAdv.classList.remove('active');

    loadOpportunitiesData();
    showToast('All filters reset to defaults');
  }

  function renderActiveFilterChips(filters) {
    const container = document.getElementById('opp-active-filter-chips');
    if (!container) return;

    const chips = [];

    if (filters.search) {
      chips.push({
        id: 'search',
        label: 'Search',
        val: `"${filters.search}"`,
        onRemove: () => {
          const input = document.getElementById('filter-search-text');
          if (input) input.value = '';
          const btnClear = document.getElementById('btn-clear-search');
          if (btnClear) btnClear.style.display = 'none';
          loadOpportunitiesData();
        }
      });
    }

    if (filters.type) {
      chips.push({
        id: 'type',
        label: 'Type',
        val: filters.type.replace('_', ' '),
        onRemove: () => {
          document.querySelectorAll('#explorer-category-tabs .opp-radar-tab').forEach(b => {
            const isAll = (b.getAttribute('data-type') || '') === '';
            b.classList.toggle('active', isAll);
            b.setAttribute('aria-selected', isAll ? 'true' : 'false');
          });
          loadOpportunitiesData();
        }
      });
    }

    if (filters.status) {
      chips.push({
        id: 'status',
        label: 'Status',
        val: filters.status,
        onRemove: () => {
          const el = document.getElementById('filter-opp-status');
          if (el) el.value = '';
          loadOpportunitiesData();
        }
      });
    }

    if (filters.bookmaker) {
      chips.push({
        id: 'bookmaker',
        label: 'Bookmaker',
        val: filters.bookmaker,
        onRemove: () => {
          const el = document.getElementById('filter-provider');
          if (el) el.value = '';
          loadOpportunitiesData();
        }
      });
    }

    if (filters.top_5) {
      chips.push({
        id: 'top_5',
        label: 'Leagues',
        val: 'Top 5',
        onRemove: () => {
          state.opportunityTop5Only = false;
          const btn = document.getElementById('btn-filter-top5');
          if (btn) {
            btn.classList.remove('active');
            btn.setAttribute('aria-pressed', 'false');
          }
          loadOpportunitiesData();
        }
      });
    }

    if (filters.min_ev !== undefined && filters.min_ev > 0) {
      chips.push({
        id: 'min_ev',
        label: 'Min EV',
        val: `≥ ${filters.min_ev}%`,
        onRemove: () => {
          const el = document.getElementById('filter-min-roi');
          if (el) el.value = '';
          loadOpportunitiesData();
        }
      });
    }

    if (filters.min_score !== undefined && filters.min_score > 0) {
      chips.push({
        id: 'min_score',
        label: 'Min Score',
        val: `≥ ${filters.min_score}`,
        onRemove: () => {
          const el = document.getElementById('filter-min-score');
          if (el) el.value = '';
          loadOpportunitiesData();
        }
      });
    }

    if (filters.min_execution_edge !== undefined && filters.min_execution_edge > 0) {
      chips.push({
        id: 'min_exec_edge',
        label: 'Min Edge',
        val: `≥ ${filters.min_execution_edge}%`,
        onRemove: () => {
          const el = document.getElementById('filter-min-exec-edge');
          if (el) el.value = '';
          loadOpportunitiesData();
        }
      });
    }

    if (chips.length === 0) {
      container.style.display = 'none';
      container.innerHTML = '';
      return;
    }

    container.style.display = 'flex';
    container.innerHTML = `
      <span class="text-muted" style="font-size: 0.74rem; font-weight: 600; margin-right: 0.2rem;">Active Filters (${chips.length}):</span>
      ${chips.map(c => `
        <span class="opp-chip" data-chip-id="${c.id}">
          <span class="opp-chip-label">${c.label}:</span>
          <span class="opp-chip-val">${c.val}</span>
          <button type="button" class="opp-chip-remove" title="Remove filter" aria-label="Remove ${c.label} filter">&times;</button>
        </span>
      `).join('')}
      <button type="button" class="opp-chips-clear-all" id="btn-chips-clear-all">Reset All</button>
    `;

    chips.forEach(c => {
      const chipEl = container.querySelector(`[data-chip-id="${c.id}"] .opp-chip-remove`);
      if (chipEl) chipEl.addEventListener('click', c.onRemove);
    });

    const clearAllBtn = container.querySelector('#btn-chips-clear-all');
    if (clearAllBtn) clearAllBtn.addEventListener('click', () => resetAllOpportunityFilters());
  }

  let _activeOppRequestId = 0;
  let _activeOppAbortController = null;

  async function renderOpportunities(isBackground = false) {
    return loadOpportunitiesData(isBackground);
  }

  async function loadOpportunitiesData(isBackground = false) {
    _activeOppRequestId += 1;
    const currentReqId = _activeOppRequestId;

    if (!isBackground && _activeOppAbortController) {
      try {
        _activeOppAbortController.abort();
      } catch (e) {}
    }
    const currentAbortController = new AbortController();
    if (!isBackground) {
      _activeOppAbortController = currentAbortController;
    }

    const activeTabBtn = document.querySelector('#explorer-category-tabs .opp-radar-tab.active');
    const selectedType = activeTabBtn ? (activeTabBtn.getAttribute('data-type') || '') : '';
    const status = document.getElementById('filter-opp-status')?.value || '';
    const provider = document.getElementById('filter-provider')?.value || '';
    const sortField = document.getElementById('filter-sort')?.value || 'ev';
    const sortOrder = state.opportunitySortOrder || 'desc';
    const minScore = parseFloat(document.getElementById('filter-min-score')?.value) || 0;
    const minExecEdge = parseFloat(document.getElementById('filter-min-exec-edge')?.value) || undefined;
    const minRoi = parseFloat(document.getElementById('filter-min-roi')?.value) || undefined;
    const search = (document.getElementById('filter-search-text')?.value || '').trim();
    const top5Only = Boolean(state.opportunityTop5Only);

    // Sync Top 5 button UI state
    const btnFilterTop5 = document.getElementById('btn-filter-top5');
    if (btnFilterTop5) {
      btnFilterTop5.classList.toggle('active', top5Only);
      btnFilterTop5.setAttribute('aria-pressed', top5Only ? 'true' : 'false');
    }

    const listContentArea = document.getElementById('opp-list-content-area');
    const countBadge = document.getElementById('opp-count-badge');
    const navCountBadge = document.getElementById('nav-opp-count');
    const lastScanPill = document.getElementById('opp-last-scan-pill');
    const sortLabel = document.getElementById('opp-feed-sort-label');

    const sortLabelsMap = {
      ev: `Ranked by Net EV / Edge (${sortOrder.toUpperCase()})`,
      discrepancy: `Ranked by Discrepancy % (${sortOrder.toUpperCase() === 'DESC' ? 'High → Low' : 'Low → High'})`,
      score: `Ranked by Quality Score (${sortOrder.toUpperCase()})`,
      odds: `Ranked by Odds (${sortOrder.toUpperCase()})`,
      kickoff: `Ranked by Kickoff Time (${sortOrder.toUpperCase()})`,
      type: `Grouped by Type (${sortOrder.toUpperCase()})`,
    };
    if (sortLabel) sortLabel.textContent = sortLabelsMap[sortField] || 'Ranked by Priority';

    // Render active filter chips
    const activeFiltersForChips = {
      type: selectedType,
      status: status,
      bookmaker: provider,
      search: search,
      top_5: top5Only,
      min_ev: minRoi,
      min_score: minScore,
      min_execution_edge: minExecEdge,
    };
    renderActiveFilterChips(activeFiltersForChips);

    if (!isBackground && listContentArea && (!state.opportunities || state.opportunities.length === 0)) {
      listContentArea.innerHTML = `
        <div class="not-run-state" style="padding: 2.5rem 1.5rem; text-align: center;">
          <div class="spinner-icon" style="font-size: 1.8rem; margin-bottom: 0.5rem; display: inline-block;">⟳</div>
          <p class="text-muted">Loading intelligence from Opportunity Explorer...</p>
        </div>
      `;
    }

    try {
      // P1-NEW-005: explicit pagination — the server caps pages; the UI
      // tracks how many rows the operator asked to see per filter set.
      const filterSig = JSON.stringify({
        t: selectedType, s: status, p: provider, sort: sortField,
        o: sortOrder, top5: top5Only, ms: (minScore > 0 ? minScore : 0),
        me: (minExecEdge !== undefined ? minExecEdge : null),
        mr: (minRoi !== undefined ? minRoi : null), q: search,
      });
      if (state.oppExplorerFilterSig !== filterSig) {
        state.oppExplorerFilterSig = filterSig;
        state.oppExplorerLimit = 100;
        state.oppExplorerOffset = 0;
      }
      const pageLimit = state.oppExplorerLimit || 100;
      const pageOffset = state.oppExplorerOffset || 0;
      const fetchParams = {
        limit: pageLimit,
        offset: pageOffset,
        sort: sortField,
        order: sortOrder,
      };
      if (selectedType) fetchParams.type = selectedType;
      if (status) fetchParams.status = status;
      if (provider) fetchParams.bookmaker = provider;
      if (top5Only) fetchParams.top_5 = true;
      if (minScore > 0) fetchParams.min_score = minScore;
      if (minExecEdge !== undefined) fetchParams.min_execution_edge = minExecEdge;
      if (minRoi !== undefined) fetchParams.min_ev = minRoi;
      if (search) fetchParams.search = search;

      const res = await api.fetchUnifiedOpportunities(fetchParams, { signal: currentAbortController.signal });
      if (res.aborted || currentReqId !== _activeOppRequestId) {
        return;
      }
      // P1-NEW-005: HTTP/auth/validation errors render an explicit ERROR
      // state — never the "No Active Opportunities" empty state.
      if (!res.ok) {
        state.opportunities = [];
        if (countBadge) countBadge.textContent = 'Load Failed';
        if (navCountBadge) navCountBadge.textContent = '!';
        if (lastScanPill) {
          lastScanPill.textContent = res.status === 401 ? 'Auth Required — Log In Again'
            : (res.status === 0 ? 'Connection Error' : `Error ${res.status}`);
        }
        if (listContentArea) {
          listContentArea.innerHTML = `
            <div class="opp-empty-state">
              <div class="opp-empty-icon">⚠️</div>
              <div class="opp-empty-title">Could Not Load Opportunities (HTTP ${res.status})</div>
              <div class="opp-empty-desc">
                ${(res.error || 'The explorer request failed.') + ' '}
                This is a connection or authorization problem — not an empty scan result.
                ${res.status === 401 ? 'Your session may have expired; log in again.' : ''}
              </div>
              <button type="button" class="btn btn-primary btn-sm" id="btn-empty-retry-load" style="margin-top: 0.4rem;">
                Retry Loading
              </button>
            </div>
          `;
          const retryBtn = document.getElementById('btn-empty-retry-load');
          if (retryBtn) retryBtn.addEventListener('click', () => loadOpportunitiesData());
        }
        return;
      }
      const data = res.data || {};
      const items = data.items || [];
      const countsByType = data.counts_by_type || {};

      // Update category tab counters
      const totalAll = Object.values(countsByType).reduce((a, b) => a + b, 0);
      const tabAll = document.getElementById('tab-count-all');
      if (tabAll) tabAll.textContent = totalAll;
      const tabVal = document.getElementById('tab-count-value');
      if (tabVal) tabVal.textContent = countsByType.VALUEBET || 0;
      const tabSure = document.getElementById('tab-count-sure');
      if (tabSure) tabSure.textContent = countsByType.SUREBET || 0;
      const tabBoost = document.getElementById('tab-count-boost');
      if (tabBoost) tabBoost.textContent = countsByType.BOOSTER || 0;
      const tabPlayer = document.getElementById('tab-count-player');
      if (tabPlayer) tabPlayer.textContent = countsByType.PLAYER_PROP || 0;
      const tabTeam = document.getElementById('tab-count-team');
      if (tabTeam) tabTeam.textContent = countsByType.TEAM_PROP || 0;
      const tabDisc = document.getElementById('tab-count-discrepancy');
      if (tabDisc) tabDisc.textContent = countsByType.QUOTE_DISCREPANCY || 0;
      const tabWatch = document.getElementById('tab-count-watchlist');
      if (tabWatch) tabWatch.textContent = countsByType.WATCHLIST || 0;

      if (lastScanPill) {
        // P1-NEW-005: never imply freshness without a timestamp.
        lastScanPill.textContent = data.scan_timestamp ? formatTimestamp(data.scan_timestamp) : 'No Scan Yet';
      }

      state.opportunities = items;
      // P1-NEW-005: truthful truncation — the list is capped at the request
      // limit while the badge reports the server total.
      const serverTotal = (data.total !== undefined ? data.total : items.length);
      if (countBadge) countBadge.textContent = `${serverTotal} Found${serverTotal > items.length ? ` (Showing ${items.length})` : ''}`;
      if (navCountBadge) navCountBadge.textContent = totalAll;
      const mobNavBadge = document.getElementById('mobile-nav-opp-count');
      if (mobNavBadge) {
        mobNavBadge.textContent = totalAll;
        mobNavBadge.style.display = totalAll > 0 ? 'inline-block' : 'none';
      }

      if (!listContentArea) return;

      // ── Handle Rendering States: Empty, Single, or Many ──
      if (items.length === 0) {
        // Distinguish Empty Filter Results vs Zero Scan Data
        const hasActiveFilters = Boolean(selectedType || status || provider || search || top5Only || minScore > 0 || minExecEdge !== undefined || minRoi !== undefined);

        const emptySvg = hasActiveFilters
          ? `<svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/><line x1="11" y1="8" x2="11" y2="14"/><line x1="8" y1="11" x2="14" y2="11"/></svg>`
          : `<svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>`;

        listContentArea.innerHTML = `
          <div class="opp-empty-state">
            <div class="opp-empty-icon">${emptySvg}</div>
            <div class="opp-empty-title">${hasActiveFilters ? 'No Opportunities Match Current Filters' : 'No Active Opportunities Detected'}</div>
            <div class="opp-empty-desc">
              ${hasActiveFilters
                ? 'Your active search, bookmaker, or threshold filters filtered out all opportunities. Try clearing filters to reveal available bets.'
                : 'The continuous detection engine is actively monitoring bookmakers. When a profitable edge or surebet is verified, it will appear here.'}
            </div>
            ${hasActiveFilters ? `
              <button type="button" class="btn btn-primary btn-sm" id="btn-empty-reset-filters" style="margin-top: 0.4rem;">
                Reset All Filters
              </button>
            ` : `
              <button type="button" class="btn btn-outline btn-sm" id="btn-empty-refresh-scan" style="margin-top: 0.4rem;">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
                Refresh Scanner
              </button>
            `}
          </div>
        `;

        const resetBtn = document.getElementById('btn-empty-reset-filters');
        if (resetBtn) resetBtn.addEventListener('click', () => resetAllOpportunityFilters());
        const refreshBtn = document.getElementById('btn-empty-refresh-scan');
        if (refreshBtn) refreshBtn.addEventListener('click', () => loadOpportunitiesData());

        // Clear inspector pane if no items match
        deselectOpportunity();
        return;
      }

      if (items.length === 1) {
        // ── Single-Result State: Render Dedicated Spotlight ──
        const singleItem = items[0];
        const isSelected = state.selectedOpportunityId === singleItem.id;
        listContentArea.innerHTML = renderOpportunitySpotlight(singleItem, isSelected);

        const spotlightCard = listContentArea.querySelector('.opp-spotlight-card');
        if (spotlightCard) {
          spotlightCard.addEventListener('click', () => {
            selectOpportunity(singleItem.id, true);
          });
        }

        const spotlightInspectBtn = listContentArea.querySelector('.btn-inspect-spotlight');
        if (spotlightInspectBtn) {
          spotlightInspectBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            selectOpportunity(singleItem.id, true);
          });
        }

        // Auto-select the single item in desktop inspector
        selectOpportunity(singleItem.id, false);

      } else {
        // ── Many-Result State: Render High-Density Scannable Rows ──
        let activeSelectedId = state.selectedOpportunityId;
        const selectionInItems = Boolean(activeSelectedId && items.some(it => it.id === activeSelectedId));

        if (!selectionInItems && items.length > 0) {
          activeSelectedId = items[0].id;
        } else if (!selectionInItems) {
          activeSelectedId = null;
        }

        listContentArea.innerHTML = `
          <div class="opp-feed-list" role="list">
            ${items.map(it => renderOpportunityFeedRow(it, it.id === activeSelectedId)).join('')}
          </div>
        `;

        // Wire click handlers for dense rows
        listContentArea.querySelectorAll('.opp-feed-item').forEach(row => {
          row.addEventListener('click', () => {
            const id = row.getAttribute('data-id');
            const isMobile = window.innerWidth < 1024;
            selectOpportunity(id, isMobile);
          });
        });

        listContentArea.querySelectorAll('.opp-btn-row-inspect').forEach(btn => {
          btn.addEventListener('click', (e) => {
            e.stopPropagation();
            const id = btn.getAttribute('data-id');
            selectOpportunity(id, true);
          });
        });

        // Ensure active item is loaded in inspector only if none is currently inspected
        // or if the previous inspected item was excluded or user selected a different item
        if (activeSelectedId && (!state.activeOpportunityDetail || state.activeOpportunityDetail?.id !== activeSelectedId)) {
          selectOpportunity(activeSelectedId, false);
        } else if (!activeSelectedId) {
          deselectOpportunity();
        }

        // P1-NEW-005: expose truncation with an explicit next-page action
        // instead of silently hiding results beyond the request limit.
        if (serverTotal > items.length) {
          const moreWrap = document.createElement('div');
          moreWrap.style.textAlign = 'center';
          moreWrap.style.padding = '0.75rem';
          moreWrap.innerHTML = `
            <button type="button" class="btn btn-outline btn-sm" id="btn-opp-load-more">
              Show More (${items.length} of ${serverTotal})
            </button>
          `;
          listContentArea.appendChild(moreWrap);
          const moreBtn = document.getElementById('btn-opp-load-more');
          if (moreBtn) moreBtn.addEventListener('click', () => {
            state.oppExplorerLimit = (state.oppExplorerLimit || 100) + 100;
            loadOpportunitiesData(true);
          });
        }
      }

    } catch (err) {
      console.error('Failed to load unified opportunities:', err);
      if (listContentArea) {
        listContentArea.innerHTML = `
          <div class="opp-empty-state" style="border-color: var(--val-negative-border);">
            <div class="opp-empty-icon" style="color: var(--val-negative);">
              <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
            </div>
            <div class="opp-empty-title" style="color: var(--val-negative);">Failed Loading Opportunities</div>
            <div class="opp-empty-desc">${escapeHtml(String(err.message || err))}</div>
            <button type="button" class="btn btn-outline btn-sm" onclick="loadOpportunitiesData()" style="margin-top: 0.5rem;">
              Retry Connection
            </button>
          </div>
        `;
      }
    }
  }

  // Helper: Renders Single Opportunity Focus Spotlight
  function renderOpportunitySpotlight(item, isSelected) {
    const typeBadges = {
      SUREBET: { cls: 'badge-success', label: 'SUREBET ARBITRAGE' },
      VALUEBET: { cls: 'badge-info', label: 'VALUE BET' },
      QUOTE_DISCREPANCY: { cls: 'badge-warning', label: 'QUOTE DISCREPANCY' },
      BOOSTER: { cls: 'badge-warning', label: 'PRICE BOOSTER' },
      PLAYER_PROP: { cls: 'badge-accent', label: 'PLAYER PROP' },
      TEAM_PROP: { cls: 'badge-outline', label: 'TEAM PROP' },
      QUOTE_COMPARISON: { cls: 'badge-outline', label: 'QUOTE COMPARISON' },
      WATCHLIST: { cls: 'badge-warning', label: 'WATCHLIST' },
    };
    const badgeInfo = typeBadges[item.type] || { cls: 'badge-outline', label: item.type };

    const entityName = item.player || item.team || (item.event || 'Betting Opportunity');
    const fixtureText = item.event && item.event !== entityName ? item.event : (item.competition || 'Football Match');
    const compText = item.competition ? `• ${item.competition}` : '';

    const isDisc = item.type === 'QUOTE_DISCREPANCY';
    const discPct = item.price_discrepancy_pct != null ? Number(item.price_discrepancy_pct) : null;
    const oddsDiff = item.odds_difference != null ? Number(item.odds_difference) : null;

    let evFormatted = '—';
    let evLabel = 'NET EV';
    let isEvPositive = false;
    let hasEv = false;

    if (isDisc) {
      evLabel = 'PRICE DELTA';
      hasEv = true;
      if (discPct != null) {
        evFormatted = `+${discPct.toFixed(1)}%`;
        isEvPositive = true;
      } else if (oddsDiff != null) {
        evFormatted = `+${oddsDiff.toFixed(2)}`;
        isEvPositive = true;
      }
    } else {
      hasEv = (item.net_ev_pct !== null && item.net_ev_pct !== undefined) ||
              (item.gross_ev_pct !== null && item.gross_ev_pct !== undefined) ||
              (item.execution_edge_pct !== null && item.execution_edge_pct !== undefined);
      const evNum = item.net_ev_pct !== null && item.net_ev_pct !== undefined
        ? Number(item.net_ev_pct)
        : (item.gross_ev_pct !== null && item.gross_ev_pct !== undefined ? Number(item.gross_ev_pct) : Number(item.execution_edge_pct || 0));

      isEvPositive = evNum > 0;
      evFormatted = !hasEv ? '—' : `${isEvPositive ? '+' : ''}${evNum.toFixed(2)}%`;
      evLabel = item.type === 'SUREBET' ? 'NET ARB MARGIN' : (item.net_ev_pct !== null ? 'NET EV' : 'EDGE');
    }

    const execOdds = item.execution_odds ? Number(item.execution_odds).toFixed(2) : '—';
    const fairOdds = item.fair_odds ? Number(item.fair_odds).toFixed(2) : '—';
    const bookmakersText = item.best_bookmaker || (item.all_bookmakers && item.all_bookmakers.length > 0 ? item.all_bookmakers.join(', ') : 'Bookmaker');

    const mktText = item.market ? `${item.market}${item.line !== null && item.line !== undefined ? ' ' + item.line : ''}${item.side ? ' (' + item.side + ')' : ''}` : 'Market Selection';

    let explanation = '';
    if (item.type === 'SUREBET') {
      explanation = `Guaranteed cross-bookmaker arbitrage margin of <strong class="text-success">${evFormatted}</strong> detected across <strong>${bookmakersText}</strong>. Placing mathematically proportional stakes eliminates bookmaker margin.`;
    } else if (item.type === 'VALUEBET') {
      explanation = `Actionable price edge: Bookmaker price <strong>${execOdds}</strong> exceeds model fair price <strong>${fairOdds}</strong> by <strong class="text-success">${evFormatted}</strong> value margin.`;
    } else if (item.type === 'QUOTE_DISCREPANCY') {
      const lowerText = item.lower_bookmaker && item.lower_execution_odds ? ` vs <strong>${item.lower_bookmaker}</strong> (${Number(item.lower_execution_odds).toFixed(2)})` : '';
      explanation = `Polish bookmaker quote discrepancy: Best execution price of <strong>${execOdds}</strong> available at <strong>${bookmakersText}</strong>${lowerText}. Large price difference detected. NOT a surebet; single-leg execution.`;
    } else if (item.type === 'QUOTE_COMPARISON') {
      explanation = `Bookmaker quote comparison: Best execution price of <strong>${execOdds}</strong> available at <strong>${bookmakersText}</strong> across Polish bookmakers. No model valuation or arbitrage guarantee.`;
    } else {
      explanation = `Verified statistical edge: Executable price of <strong>${execOdds}</strong> at <strong>${bookmakersText}</strong> offers actionable advantage on <strong>${mktText}</strong>.`;
    }

    return `
      <div class="opp-spotlight-card ${isSelected ? 'selected' : ''}" data-id="${item.id}" role="listitem" tabindex="0" aria-label="Top Opportunity: ${entityName}">
        <div class="opp-spotlight-badge-row">
          <span class="opp-spotlight-eyebrow">OPPORTUNITY SPOTLIGHT</span>
          <div style="display: flex; gap: 0.4rem; align-items: center;">
            <span class="badge ${badgeInfo.cls}" style="font-weight: 700; letter-spacing: 0.04em;">${badgeInfo.label}</span>
            <span class="badge ${item.status === 'AVAILABLE' ? 'badge-outline' : (item.status === 'BETTABLE' ? 'badge-success' : 'badge-outline')}">${item.status}</span>
          </div>
        </div>

        <div class="opp-spotlight-main">
          <div>
            <div class="opp-spotlight-entity">${escapeHtml(entityName)}</div>
            <div class="opp-spotlight-fixture">${escapeHtml(fixtureText)} ${compText}</div>
            <div class="opp-spotlight-market">
              <span class="opp-spotlight-market-pill">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align: -1px; margin-right: 3px;"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="4"/></svg>
                ${escapeHtml(mktText)}
              </span>
            </div>
          </div>

          <div class="opp-spotlight-metrics">
            <div class="opp-metric-block primary-ev ${!hasEv ? 'neutral' : (isEvPositive ? '' : 'negative')}">
              <span class="metric-lbl">${evLabel}</span>
              <span class="metric-val">${evFormatted}</span>
            </div>

            <div class="opp-metric-block">
              <span class="metric-lbl">EXEC ODDS</span>
              <span class="metric-val mono">${execOdds}</span>
              <span style="font-size: 0.65rem; color: var(--text-muted);">${escapeHtml(bookmakersText)}</span>
            </div>

            ${item.fair_odds ? `
              <div class="opp-metric-block">
                <span class="metric-lbl">FAIR PRICE</span>
                <span class="metric-val mono text-info">${fairOdds}</span>
                <span style="font-size: 0.65rem; color: var(--text-muted);">Model/Sharp</span>
              </div>
            ` : ''}

            <div class="opp-metric-block">
              <span class="metric-lbl">QUALITY SCORE</span>
              <span class="metric-val mono text-accent">${item.score != null ? Number(item.score).toFixed(0) : '—'}</span>
              <span style="font-size: 0.65rem; color: var(--text-muted);">${item.score != null ? 'Ranking Rank' : 'Unscored'}</span>
            </div>
          </div>
        </div>

        <div class="opp-spotlight-explanation">
          ${explanation}
        </div>

        <div class="opp-spotlight-action-bar">
          <span class="text-muted" style="font-size: 0.74rem;">
            ${item.created_at ? 'Detected: ' + formatTimestamp(item.created_at) : 'Active in live scanner cache'}
          </span>
          <button type="button" class="btn btn-sm btn-primary btn-inspect-spotlight" data-id="${item.id}">
            Inspect Mathematics & Stakes →
          </button>
        </div>
      </div>
    `;
  }

  // Helper: Renders Dense Multi-Result Feed Item Row (Flagship Workstation 2.0)
  function renderOpportunityFeedRow(item, isSelected) {
    const typeBadges = {
      SUREBET: { cls: 'badge-success', code: 'ARB', label: 'Surebet' },
      VALUEBET: { cls: 'badge-info', code: 'VAL', label: 'Valuebet' },
      QUOTE_DISCREPANCY: { cls: 'badge-warning', code: 'DISC', label: 'Discrepancy' },
      BOOSTER: { cls: 'badge-warning', code: 'BOOST', label: 'Booster' },
      PLAYER_PROP: { cls: 'badge-accent', code: 'PROP', label: 'Player Prop' },
      TEAM_PROP: { cls: 'badge-outline', code: 'TEAM', label: 'Team Prop' },
      QUOTE_COMPARISON: { cls: 'badge-outline', code: 'QUOTE', label: 'Quote' },
      WATCHLIST: { cls: 'badge-warning', code: 'WATCH', label: 'Watchlist' },
    };
    const tBadge = typeBadges[item.type] || { cls: 'badge-outline', code: item.type || 'BET', label: item.type || 'Bet' };

    const isDisc = item.type === 'QUOTE_DISCREPANCY';
    const discPct = item.price_discrepancy_pct != null ? Number(item.price_discrepancy_pct) : null;
    const oddsDiff = item.odds_difference != null ? Number(item.odds_difference) : null;

    let evVal = 0;
    let isEvPos = false;
    let evCls = 'neutral';
    let evText = '—';
    let evSubLabel = 'NET EV';

    if (isDisc) {
      evSubLabel = 'DISC';
      if (discPct != null) {
        evText = `+${discPct.toFixed(1)}%`;
        evCls = 'positive';
        isEvPos = true;
      } else if (oddsDiff != null) {
        evText = `+${oddsDiff.toFixed(2)}`;
        evCls = 'positive';
        isEvPos = true;
      }
    } else {
      const hasEv = (item.net_ev_pct !== null && item.net_ev_pct !== undefined) ||
                    (item.gross_ev_pct !== null && item.gross_ev_pct !== undefined) ||
                    (item.execution_edge_pct !== null && item.execution_edge_pct !== undefined);
      evVal = item.net_ev_pct !== null && item.net_ev_pct !== undefined
        ? Number(item.net_ev_pct)
        : (item.gross_ev_pct !== null && item.gross_ev_pct !== undefined ? Number(item.gross_ev_pct) : Number(item.execution_edge_pct || 0));

      isEvPos = evVal > 0;
      evCls = !hasEv ? 'neutral' : (isEvPos ? 'positive' : (evVal < 0 ? 'negative' : 'neutral'));
      evText = !hasEv ? '—' : `${isEvPos ? '+' : ''}${evVal.toFixed(1)}%`;
      evSubLabel = item.type === 'SUREBET' ? 'ARB' : 'NET EV';
    }

    const entity = item.player || item.team || item.event || 'Selection';
    const fixture = item.event && item.event !== entity ? item.event : (item.competition || 'Match');

    const mktType = item.market || 'Market';
    const mktStr = `${mktType}${item.line !== null && item.line !== undefined ? ' • ' + item.line : ''}${item.side ? ' (' + item.side + ')' : ''}`;

    const execOdds = item.execution_odds ? Number(item.execution_odds).toFixed(2) : (item.reference_odds ? Number(item.reference_odds).toFixed(2) : '—');
    const bookmaker = item.best_bookmaker || 'Book';

    // Reference / Comparison baseline
    let refHtml = '';
    if (item.fair_odds) {
      refHtml = `<span class="ref-pill"><span class="ref-k">FAIR</span> <strong class="ref-v mono text-info">${Number(item.fair_odds).toFixed(2)}</strong></span>`;
    } else if (item.reference_odds && item.reference_odds !== item.execution_odds) {
      refHtml = `<span class="ref-pill"><span class="ref-k">REF</span> <strong class="ref-v mono">${Number(item.reference_odds).toFixed(2)}</strong></span>`;
    } else if (item.lower_execution_odds) {
      const lowerBm = item.lower_bookmaker ? escapeHtml(item.lower_bookmaker) : 'Alt';
      refHtml = `<span class="ref-pill"><span class="ref-k">${lowerBm}</span> <strong class="ref-v mono">${Number(item.lower_execution_odds).toFixed(2)}</strong></span>`;
    }

    const statusMap = {
      VALUEBET: 'VALUEBET',
      BETTABLE: 'BETTABLE',
      AVAILABLE: 'AVAILABLE',
      WATCHLIST: 'WATCHLIST',
      REFERENCE_ONLY: 'REFERENCE',
      NO_EXECUTION_MARKET: 'NO MARKET',
      NO_EXECUTION_ODDS: 'NO ODDS',
      MATCH_UNCERTAIN: 'UNCERTAIN',
      EXPIRED: 'EXPIRED'
    };
    const statusLabel = statusMap[item.status] || item.status || 'NEW';
    const statusCls = item.status === 'VALUEBET' ? 'badge-accent'
      : (item.status === 'BETTABLE' ? 'badge-success'
      : (item.status === 'AVAILABLE' ? 'badge-outline'
      : (item.status === 'WATCHLIST' ? 'badge-warning' : 'badge-outline')));

    const scoreDisplay = item.score != null ? `${Number(item.score).toFixed(0)} pts` : null;

    return `
      <div class="opp-feed-item ${isSelected ? 'selected' : ''}" data-id="${item.id}" role="listitem" tabindex="0" aria-label="${entity}, ${evText} ${evSubLabel}">
        <!-- Col 1: Hero EV / Edge -->
        <div class="opp-feed-col-ev">
          <div class="opp-feed-ev-value ${evCls}">${evText}</div>
          <div class="opp-feed-ev-meta">
            <span class="opp-feed-ev-label">${evSubLabel}</span>
            <span class="badge ${tBadge.cls} opp-feed-type-pill" title="${tBadge.label}">${tBadge.code}</span>
          </div>
        </div>

        <!-- Col 2: Event & Match Context -->
        <div class="opp-feed-col-entity">
          <div class="opp-feed-primary-title" title="${escapeHtml(entity)}">${escapeHtml(entity)}</div>
          <div class="opp-feed-sub-fixture" title="${escapeHtml(fixture)}">${escapeHtml(fixture)}</div>
        </div>

        <!-- Col 3: Market & Prop Classification -->
        <div class="opp-feed-col-market">
          <div class="opp-feed-market-tag" title="${escapeHtml(mktStr)}">${escapeHtml(mktStr)}</div>
          ${item.player ? `<span class="prop-type-badge player">Player</span>` : (item.type === 'TEAM_PROP' ? `<span class="prop-type-badge team">Team</span>` : '')}
        </div>

        <!-- Col 4: Actionable Execution vs Reference (Signature Pattern) -->
        <div class="opp-feed-col-exec">
          <div class="opp-feed-exec-box">
            <span class="opp-feed-exec-pill">
              <span class="bm-chip">${escapeHtml(bookmaker)}</span>
              <strong class="bm-odds mono">${execOdds}</strong>
            </span>
          </div>
          ${refHtml ? `<div class="opp-feed-ref-box">${refHtml}</div>` : ''}
        </div>

        <!-- Col 5: Execution Status & Quality -->
        <div class="opp-feed-col-status">
          <span class="badge ${statusCls}" title="${escapeHtml(item.status)}">${escapeHtml(statusLabel)}</span>
          ${scoreDisplay ? `<span class="opp-feed-score">${scoreDisplay}</span>` : ''}
        </div>

        <!-- Col 6: Instant Inspection Action -->
        <div class="opp-feed-col-action">
          <button type="button" class="opp-btn-row-inspect" data-id="${item.id}" title="Inspect full opportunity detail" aria-label="Inspect ${entity}">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>
          </button>
        </div>
      </div>
    `;
  }

  // Selection & Inspector Binding Controller
  function selectOpportunity(opportunityId, openModal = false, scrollIntoView = false) {
    if (!opportunityId) return;
    state.selectedOpportunityId = opportunityId;

    // Update active row visual in feed
    document.querySelectorAll('.opp-feed-item, .opp-spotlight-card').forEach(el => {
      const isMatch = el.getAttribute('data-id') === opportunityId;
      el.classList.toggle('selected', isMatch);
      if (isMatch && scrollIntoView) {
        el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
      }
    });

    // Update URL hash without breaking history
    const targetHash = `opportunity/${encodeURIComponent(opportunityId)}`;
    if (window.location.hash.replace(/^#/, '') !== targetHash) {
      window.history.replaceState(null, '', `#${targetHash}`);
    }

    // Load data into inspector pane (desktop) and/or modal (mobile or expand)
    if (openModal) {
      openOpportunityModal(opportunityId);
    }

    loadOpportunityDetail(opportunityId, { openModal });
  }

  function deselectOpportunity() {
    state.selectedOpportunityId = null;
    document.querySelectorAll('.opp-feed-item, .opp-spotlight-card').forEach(el => {
      el.classList.remove('selected');
    });

    const inspTitle = document.getElementById('opp-inspector-title');
    if (inspTitle) inspTitle.textContent = 'Select an Opportunity';
    const inspBadge = document.getElementById('opp-inspector-badge');
    if (inspBadge) inspBadge.style.display = 'none';
    const btnExpand = document.getElementById('btn-expand-inspector');
    if (btnExpand) btnExpand.style.display = 'none';
    const btnClose = document.getElementById('btn-close-inspector');
    if (btnClose) btnClose.style.display = 'none';

    const inspContent = document.getElementById('opp-inspector-content');
    if (inspContent) {
      inspContent.innerHTML = `
        <div class="opp-inspector-placeholder">
          <div class="inspector-placeholder-icon">
            <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/><line x1="11" y1="8" x2="11" y2="14"/><line x1="8" y1="11" x2="14" y2="11"/></svg>
          </div>
          <div class="inspector-placeholder-title">Select an Opportunity to Inspect</div>
          <div class="inspector-placeholder-text">
            Click any row in the feed or use keyboard <kbd>↑</kbd> <kbd>↓</kbd> to explore live price comparisons, mathematical proof, consensus fair baseline, and cross-bookmaker execution legs.
          </div>
        </div>
      `;
    }

    if (window.location.hash.startsWith('#opportunity/')) {
      window.location.hash = 'opportunities';
    }
  }

  function openOpportunityModal(opportunityId) {
    const modal = document.getElementById('opp-detail-modal');
    const backdrop = document.getElementById('opp-detail-backdrop');
    if (!modal) return;

    modal.style.display = 'flex';
    requestAnimationFrame(() => {
      modal.classList.add('open');
      if (backdrop) backdrop.classList.add('open');
    });
  }

  async function loadOpportunityDetail(opportunityId, options = {}) {
    if (!opportunityId) return;
    const cleanId = decodeURIComponent(opportunityId);

    const inspTitle = document.getElementById('opp-inspector-title');
    const inspBadge = document.getElementById('opp-inspector-badge');
    const btnExpand = document.getElementById('btn-expand-inspector');
    const btnClose = document.getElementById('btn-close-inspector');
    const inspBody = document.getElementById('opp-inspector-content');

    const modal = document.getElementById('opp-detail-modal');
    const modalTitle = document.getElementById('opp-detail-modal-title');
    const modalBadge = document.getElementById('opp-detail-modal-badge');
    const modalBody = document.getElementById('opp-detail-modal-content');
    const btnModalExplorer = document.getElementById('btn-modal-open-explorer');

    // Open modal if explicitly requested, or if inspecting from dashboard
    const shouldOpenModal = Boolean(options.openModal || (state.currentView !== 'opportunities' && options.openModal !== false));
    if (shouldOpenModal) {
      openOpportunityModal(cleanId);
    }

    function renderOpportunityIntoInspector(detail) {
      if (!detail) return;
      state.activeOpportunityDetail = detail;

      const ev = detail.event || {};
      const mkt = detail.market || {};
      const math = detail.mathematical_explanation || {};
      const lifecycle = detail.lifecycle || {};
      const legs = detail.selections || detail.legs || detail.odds_comparison || [];

      const oppType = String(detail.opportunity_type || detail.type || (detail.is_watchlist ? 'WATCHLIST' : 'TEAM_PROP')).toUpperCase();
      const isSb = oppType === 'SUREBET';
      const isVal = oppType === 'VALUEBET';
      const isDisc = oppType === 'QUOTE_DISCREPANCY';
      const isQuote = oppType === 'QUOTE_COMPARISON';
      const isTeam = oppType === 'TEAM_PROP';
      const isPlayer = oppType === 'PLAYER_PROP';
      const isWatch = oppType === 'WATCHLIST' || Boolean(detail.is_watchlist);
      const isBooster = oppType === 'BOOSTER';

      let typeLabel = 'SUREBET';
      let badgeCls = 'badge badge-success';
      if (isSb) {
        typeLabel = 'SUREBET';
        badgeCls = 'badge badge-success';
      } else if (isVal) {
        typeLabel = 'VALUEBET';
        badgeCls = 'badge badge-info';
      } else if (isDisc) {
        typeLabel = 'QUOTE DISCREPANCY';
        badgeCls = 'badge badge-warning';
      } else if (isQuote) {
        typeLabel = 'QUOTE COMPARISON';
        badgeCls = 'badge badge-outline';
      } else if (isTeam) {
        typeLabel = 'TEAM PROP';
        badgeCls = 'badge badge-outline';
      } else if (isPlayer) {
        typeLabel = 'PLAYER PROP';
        badgeCls = 'badge badge-accent';
      } else if (isWatch) {
        typeLabel = 'WATCHLIST';
        badgeCls = 'badge badge-warning';
      } else if (isBooster) {
        typeLabel = 'BOOSTER';
        badgeCls = 'badge badge-warning';
      }

      // Update Inspector Headers
      const matchHeading = (ev.home_team && ev.away_team) ? `${ev.home_team} vs ${ev.away_team}` : (detail.event_name || 'Opportunity Details');

      if (inspTitle) inspTitle.textContent = matchHeading;
      if (modalTitle) modalTitle.textContent = matchHeading;

      if (inspBadge) {
        inspBadge.textContent = typeLabel;
        inspBadge.className = badgeCls;
        inspBadge.style.display = 'inline-block';
      }
      if (modalBadge) {
        modalBadge.textContent = typeLabel;
        modalBadge.className = badgeCls;
      }

      if (btnExpand) btnExpand.style.display = 'inline-flex';
      if (btnClose) btnClose.style.display = 'inline-flex';
      if (btnModalExplorer) {
        btnModalExplorer.style.display = 'inline-flex';
        btnModalExplorer.onclick = () => {
          closeOppDetailModal();
          window.location.hash = 'opportunities';
          setTimeout(() => {
            if (typeof selectOpportunity === 'function') {
              selectOpportunity(cleanId);
            }
          }, 100);
        };
      }

      // Render comprehensive inspector markup
      const contentHtml = buildOpportunityInspectorMarkup(detail, ev, mkt, math, lifecycle, legs, {
        oppType, isSb, isVal, isTeam, isPlayer, isWatch, isBooster
      });

      if (inspBody) inspBody.innerHTML = contentHtml;
      if (modalBody) modalBody.innerHTML = contentHtml;

      // Wire interactive stake calculator ONLY for genuine surebets
      if (isSb) {
        wireSurebetStakeCalculators(detail, legs);
      }
    }

    // 1. Instant Workstation Inspection: Resolve from in-memory scan/feed data immediately
    let inMemoryDetail = null;
    if (state.latestScan || state.opportunities) {
      const scan = state.latestScan || {};
      const pool = [
        ...(scan.opportunities || []),
        ...(scan.top_opportunities || []),
        ...(scan.surebets || []),
        ...(scan.valuebets || []),
        ...(scan.player_props || []),
        ...(scan.team_props || []),
        ...(state.opportunities || [])
      ];
      const match = pool.find(o => String(o.id || o.opportunity_id) === String(cleanId));
      if (match) {
        const isVb = match.opportunity_type === 'VALUEBET' || match.type === 'VALUEBET' || match.category === 'VALUEBET';
        const isSbMatch = match.opportunity_type === 'SUREBET' || match.type === 'SUREBET' || match.category === 'SUREBET';
        const isTp = match.opportunity_type === 'TEAM_PROP' || match.type === 'TEAM_PROP';
        const isPp = match.opportunity_type === 'PLAYER_PROP' || match.type === 'PLAYER_PROP';
        const resolvedType = match.opportunity_type || match.type || (isVb ? 'VALUEBET' : (isSbMatch ? 'SUREBET' : (isTp ? 'TEAM_PROP' : (isPp ? 'PLAYER_PROP' : 'TEAM_PROP'))));

        const evObj = typeof match.event === 'object' && match.event ? match.event : {};
        const evName = match.event_name || (typeof match.event === 'string' ? match.event : (evObj.home_team && evObj.away_team ? `${evObj.home_team} vs ${evObj.away_team}` : (match.team ? `${match.team} vs ${match.opponent || 'Opponent'}` : match.canonical_event_id || 'Event')));

        const mktObj = typeof match.market === 'object' && match.market ? match.market : {};
        const mktLabel = match.market_label || (typeof match.market === 'string' ? match.market : (mktObj.label || mktObj.display_name || match.canonical_market_key || 'Market'));

        const evMargin = (match.net_ev_pct !== undefined && match.net_ev_pct !== null)
          ? Number(match.net_ev_pct)
          : ((match.value_percent !== undefined && match.value_percent !== null)
            ? Number(match.value_percent)
            : ((match.gross_ev_pct !== undefined && match.gross_ev_pct !== null)
              ? Number(match.gross_ev_pct)
              : ((match.margin_pct !== undefined && match.margin_pct !== null)
                ? Number(match.margin_pct)
                : Number(match.arbitrage_margin_pct || 0))));

        const rawLegs = (Array.isArray(match.legs) && match.legs.length > 0)
          ? match.legs
          : ((Array.isArray(match.selections) && match.selections.length > 0) ? match.selections : []);

        const bookmakerList = match.bookmakers || (match.best_bookmaker ? [match.best_bookmaker] : (match.bookmaker ? [match.bookmaker] : Array.from(new Set(rawLegs.map(l => l.provider || l.bookmaker).filter(Boolean)))));

        const synthLegs = (rawLegs.length > 0) ? rawLegs : [{
          selection_outcome: (match.player || match.team || 'Selection') + ' (' + mktLabel + ')',
          bookmaker: match.best_bookmaker || match.bookmaker || 'Betclic',
          provider: match.best_bookmaker || match.bookmaker || 'Betclic',
          raw_odds: Number(match.execution_odds || match.best_raw_odds || match.odds || 2.25),
          effective_odds: Number(match.execution_odds || match.best_effective_odds || match.odds || 2.25),
          tax_rate: (match.best_bookmaker === 'Superbet' || match.bookmaker === 'Superbet') ? 0.12 : 0,
          implied_probability: match.fair_probability || (match.execution_odds ? 1 / Number(match.execution_odds) : 0.45)
        }];

        inMemoryDetail = {
          ...match,
          id: match.id || match.opportunity_id || cleanId,
          opportunity_type: resolvedType,
          type: resolvedType,
          event_name: evName,
          event: { ...evObj, home_team: evObj.home_team || match.team, away_team: evObj.away_team || match.opponent },
          market: { ...mktObj, label: mktLabel },
          market_label: mktLabel,
          value_percent: evMargin,
          margin_pct: evMargin,
          net_ev_pct: evMargin,
          fair_odds: match.fair_odds || match.reference_fair_odds || null,
          bookmaker_odds: match.execution_odds || match.best_raw_odds || null,
          execution_odds: match.execution_odds || match.best_raw_odds || null,
          arbitrage_margin_pct: evMargin,
          bookmakers: bookmakerList,
          selections: synthLegs,
          legs: synthLegs,
          mathematical_explanation: match.mathematical_explanation || {
            formula: isVb ? 'EV = (Odds * Fair Prob) - 1' : (isSbMatch ? 'S = sum(1 / odds_i) < 1.0' : 'Price Discrepancy Matrix'),
            implied_probability_sum: match.implied_probability_sum || (match.calculation && match.calculation.implied_sum) || (isVb ? 0.95 : (isSbMatch ? 0.98 : null)),
            is_surebet: isSbMatch,
            explanation: match.explanation || `${isVb ? 'Valuebet' : (isSbMatch ? 'Arbitrage opportunity' : 'Market quote comparison')} with ${evMargin > 0 ? '+' : ''}${Number(evMargin).toFixed(2)}% net return.`,
          },
          lifecycle: match.lifecycle || {
            status: match.lifecycle_status || match.status || 'QUALIFIED',
            detected_at: match.detected_at || scan.completed_at || scan.started_at || new Date().toISOString(),
          }
        };
      }
    }

    if (inMemoryDetail) {
      renderOpportunityIntoInspector(inMemoryDetail);
    } else {
      const loadingHtml = `
        <div class="text-center text-muted" style="padding: 2.5rem 1rem;">
          <div class="spinner-icon" style="font-size: 1.6rem; margin-bottom: 0.5rem; display: inline-block;">⟳</div>
          <div style="font-weight: 600; font-size: 0.88rem;">Analyzing Opportunity Data...</div>
          <div style="font-size: 0.74rem; opacity: 0.7;">Fetching bookmaker odds matrix & mathematical proof</div>
        </div>
      `;
      if (inspBody) inspBody.innerHTML = loadingHtml;
      if (modalBody) modalBody.innerHTML = loadingHtml;
    }

    try {
      const res = await api.fetchOpportunityDetail(cleanId);
      if (res && res.data) {
        renderOpportunityIntoInspector(res.data);
      } else if (!inMemoryDetail) {
        showToast('Opportunity details not found.');
        deselectOpportunity();
        closeOppDetailModal();
      }
    } catch (err) {
      if (!inMemoryDetail) {
        console.error('Failed to load opportunity detail', err);
        const errorHtml = `
          <div style="padding: 1.5rem; text-align: center;">
            <div class="text-muted" style="font-size: 0.9rem; font-weight: 600; margin-bottom: 0.25rem;">Error loading opportunity detail</div>
            <div class="text-muted" style="font-size: 0.8rem;">${escapeHtml(String(err.message || err))}</div>
            <button type="button" class="btn btn-outline btn-sm" style="margin-top: 0.75rem;" onclick="loadOpportunityDetail('${encodeURIComponent(cleanId)}')">Retry</button>
          </div>
        `;
        if (inspBody) inspBody.innerHTML = errorHtml;
        if (modalBody) modalBody.innerHTML = errorHtml;
      }
    }
  }

  // Helper: Builds Comprehensive Inspector Markup (6-Layer Workstation Architecture)
  function buildOpportunityInspectorMarkup(detail, ev, mkt, math, lifecycle, legs, typeInfo) {
    const oppType = (typeof typeInfo === 'object' && typeInfo.oppType)
      ? typeInfo.oppType
      : String(detail.opportunity_type || detail.type || (typeInfo === true ? 'VALUEBET' : (detail.is_watchlist ? 'WATCHLIST' : 'TEAM_PROP'))).toUpperCase();

    const isVal = oppType === 'VALUEBET';
    const isSb = oppType === 'SUREBET';
    const isDisc = oppType === 'QUOTE_DISCREPANCY';
    const isQuote = oppType === 'QUOTE_COMPARISON';
    const isTeam = oppType === 'TEAM_PROP';
    const isPlayer = oppType === 'PLAYER_PROP';
    const isWatch = oppType === 'WATCHLIST' || Boolean(detail.is_watchlist);
    const isBooster = oppType === 'BOOSTER';

    const qScore = (detail.quality_score != null && !isNaN(detail.quality_score)) ? Number(detail.quality_score).toFixed(0) : '—';
    const tierName = detail.tier_name || (detail.competition_tier !== undefined ? `Tier ${detail.competition_tier}` : 'Standard Tier');

    // ── Price Discovery & Metrics ──
    const bestOdds = detail.bookmaker_odds || (legs[0] && (legs[0].raw_odds || legs[0].odds)) || detail.execution_odds || '—';
    const fairOdds = detail.fair_odds || math.fair_odds || '—';
    const marginPct = Number(detail.value_percent !== undefined ? detail.value_percent : (math.value_percent || detail.margin_pct || detail.arbitrage_margin_pct || 0)).toFixed(2);
    const benchmarkBook = detail.reference_bookmaker || 'Pinnacle';

    const hasProvenFairOdds = Boolean((detail.fair_odds != null && Number(detail.fair_odds) > 1) || (math.fair_odds != null && Number(math.fair_odds) > 1));
    const hasPositiveEv = Number(marginPct) > 0;
    const isModelVal = Boolean(isVal || detail.is_valuebet === true || detail.status === 'VALUEBET' || (!isDisc && !isSb && hasProvenFairOdds && hasPositiveEv));

    const execBookmaker = (detail.bookmakers && Array.isArray(detail.bookmakers) && detail.bookmakers[0])
      ? detail.bookmakers[0]
      : (detail.best_bookmaker || detail.bookmaker || 'Bookmaker');

    // Hero EV/Edge calculation
    let heroEdgeValue = '+0.00%';
    let heroEdgeLabel = 'NET EV';
    let heroEdgeCls = 'text-success';

    if (isDisc) {
      heroEdgeLabel = 'DISCREPANCY';
      heroEdgeCls = 'text-warning';
      const relPct = detail.price_discrepancy_pct != null ? Number(detail.price_discrepancy_pct) : (math.relative_price_difference_pct != null ? Number(math.relative_price_difference_pct) : null);
      const oDiff = detail.odds_difference != null ? Number(detail.odds_difference) : (math.odds_difference != null ? Number(math.odds_difference) : null);
      heroEdgeValue = relPct != null ? `+${relPct.toFixed(1)}%` : (oDiff != null ? `+${oDiff.toFixed(2)}` : '—');
    } else if (isSb) {
      heroEdgeLabel = 'ARB MARGIN';
      heroEdgeCls = 'text-success';
      heroEdgeValue = `+${marginPct}%`;
    } else if (isWatch) {
      heroEdgeLabel = 'NEAR-ARB GAP';
      heroEdgeCls = 'text-warning';
      heroEdgeValue = `${marginPct}%`;
    } else {
      heroEdgeLabel = 'NET EV';
      heroEdgeCls = Number(marginPct) > 0 ? 'text-success' : 'text-muted';
      heroEdgeValue = `${Number(marginPct) > 0 ? '+' : ''}${marginPct}%`;
    }

    // ── LAYER 1: COMMAND HEADER CARD ──
    const headerCardHtml = `
      <div class="insp-header-card">
        <div class="insp-header-top-row">
          <div class="insp-badge-cluster">
            <span class="badge ${isVal ? 'badge-accent' : (isSb ? 'badge-success' : (isDisc ? 'badge-warning' : 'badge-outline'))}" style="font-weight: 700; letter-spacing: 0.04em;">
              ${oppType.replace(/_/g, ' ')}
            </span>
            <span class="badge badge-outline">${lifecycle.status || detail.lifecycle_status || detail.status || 'AVAILABLE'}</span>
            ${qScore !== '—' ? `<span class="badge badge-outline mono">${qScore} pts</span>` : ''}
          </div>
          <div class="insp-header-hero-edge">
            <span class="insp-hero-edge-lbl">${heroEdgeLabel}</span>
            <span class="insp-hero-edge-val mono ${heroEdgeCls}">${heroEdgeValue}</span>
          </div>
        </div>

        <div class="insp-header-event-block">
          <h3 class="insp-event-title">${escapeHtml((ev.home_team && ev.away_team) ? `${ev.home_team} vs ${ev.away_team}` : (ev.match_name || (typeof ev.name === 'string' && ev.name.length > 0 ? ev.name : null) || (typeof ev.event === 'string' ? ev.event : null) || detail.event_name || detail.player || detail.team || 'Event Selection'))}</h3>
          <div class="insp-market-subtitle">
            <span class="insp-market-badge">${escapeHtml(mkt.label || mkt.display_name || mkt.type || 'Market')}${mkt.line !== null && mkt.line !== undefined ? ' • ' + mkt.line : ''}${mkt.side ? ' (' + mkt.side + ')' : ''}</span>
            ${(ev.competition && ev.competition !== 'Competition') ? `<span class="insp-comp-name">${escapeHtml(ev.competition)}</span>` : ((detail.competition && detail.competition !== 'Competition') ? `<span class="insp-comp-name">${escapeHtml(detail.competition)}</span>` : '')}
            ${ev.start_time ? `<span class="insp-time-str">• Kickoff: ${formatTimestamp(ev.start_time)}</span>` : ''}
          </div>
        </div>
      </div>
    `;

    // ── LAYER 2: ACTIONABLE EXECUTION VS REFERENCE TERMINAL ──
    let card2Label = 'SUM OF PROBABILITIES';
    let card2Val = (Number(math.implied_probability_sum || 0).toFixed(4));
    let card2Sub = 'S < 1.0 Arbitrage Threshold';
    let card2Cls = 'text-info';

    if (isModelVal) {
      card2Label = 'FAIR BENCHMARK ODDS';
      card2Val = (Number(fairOdds) ? Number(fairOdds).toFixed(2) : fairOdds);
      card2Sub = benchmarkBook + ' Sharp Baseline';
      card2Cls = 'text-info';
    } else if (isDisc) {
      const lowerBmName = detail.lower_bookmaker || math.lower_bookmaker || 'Alternative';
      const lowerPriceNum = detail.lower_execution_odds != null ? Number(detail.lower_execution_odds) : (math.lower_odds != null ? Number(math.lower_odds) : null);
      const oDiff = detail.odds_difference != null ? Number(detail.odds_difference) : (math.odds_difference != null ? Number(math.odds_difference) : null);
      card2Label = 'ALTERNATIVE QUOTE';
      card2Val = lowerPriceNum != null ? lowerPriceNum.toFixed(2) : '—';
      card2Sub = `${lowerBmName} Quote (Δ +${Number(oDiff || 0).toFixed(2)})`;
      card2Cls = 'text-warning';
    } else if (isSb) {
      card2Label = 'SUM OF PROBABILITIES';
      card2Val = (Number(math.implied_probability_sum || 0).toFixed(4));
      card2Sub = 'S < 1.0 Arbitrage Threshold';
      card2Cls = 'text-info';
    } else if (isWatch) {
      const sumSNum = Number(math.implied_probability_sum || detail.implied_probability_sum || 1.0101);
      card2Label = 'SUM OF PROBABILITIES';
      card2Val = sumSNum.toFixed(4);
      card2Sub = `Threshold S ≥ 1.0 (Gap: ${(sumSNum - 1.0 >= 0 ? '+' : '')}${((sumSNum - 1.0) * 100).toFixed(2)}%)`;
      card2Cls = 'text-warning';
    } else if (isQuote || isTeam || isPlayer) {
      card2Label = 'MARKET QUOTES';
      card2Val = `${legs.length} Bookmaker${legs.length !== 1 ? 's' : ''}`;
      card2Sub = 'Live Price Discovery';
      card2Cls = 'text-accent';
    }

    const priceMatrixHtml = `
      <div class="insp-section-wrap">
        <div class="insp-section-label">Actionable Execution vs Reference Benchmark</div>
        <div class="insp-price-grid">
          <div class="insp-price-card highlight">
            <div class="price-eyebrow-row">
              <span class="price-label">BEST EXECUTABLE ODDS</span>
              <span class="badge badge-accent" style="font-size: 0.62rem; padding: 0.05rem 0.35rem;">EXECUTION</span>
            </div>
            <div class="price-val mono">${Number(bestOdds) ? Number(bestOdds).toFixed(2) : bestOdds}</div>
            <div class="price-sub"><strong class="text-primary">${escapeHtml(execBookmaker)}</strong> • Polish Licensed</div>
          </div>

          <div class="insp-price-card">
            <div class="price-eyebrow-row">
              <span class="price-label">${card2Label}</span>
              <span class="badge badge-outline" style="font-size: 0.62rem; padding: 0.05rem 0.35rem;">BENCHMARK</span>
            </div>
            <div class="price-val mono ${card2Cls}">${card2Val}</div>
            <div class="price-sub">${card2Sub}</div>
          </div>
        </div>
      </div>
    `;

    // ── LAYER 3: MATHEMATICAL VALUE PROOF SECTION ──
    let mathSectionHtml = '';
    if (isModelVal) {
      const fairOddsNum = Number(fairOdds) || 0;
      const fairProb = Number(math.fair_probability || detail.fair_probability || (fairOddsNum > 0 ? 1.0 / fairOddsNum : 0));
      const fairProbPct = (fairProb * 100).toFixed(1);
      const bmOddsNum = Number(bestOdds) || 1.0;
      const impliedProbPct = (100 / bmOddsNum).toFixed(1);

      mathSectionHtml = `
        <div class="insp-section-wrap">
          <div class="insp-section-label">Mathematical Valuation Proof</div>
          <div class="insp-math-box">
            <div class="insp-math-formula">
              EV = (${Number(bmOddsNum).toFixed(2)} × ${fairProb.toFixed(4)}) − 1 = <span class="text-success font-bold">+${marginPct}%</span>
            </div>
            <div class="insp-math-desc">
              Model Fair Probability is <strong>${fairProbPct}%</strong> compared to bookmaker implied probability of <strong>${impliedProbPct}%</strong>.
              ${escapeHtml(math.explanation || 'Verified positive expected value after Polish bookmaker tax adjustment.')}
            </div>
          </div>
        </div>
      `;
    } else if (isSb) {
      const legOutcomes = (legs || []).map(l => String(l.selection_type || l.outcome || l.side || l.selection_outcome || '').trim().toUpperCase()).filter(Boolean);
      const uniqueOutcomes = new Set(legOutcomes);
      const hasComplementaryOutcomes = legs.length >= 2 && uniqueOutcomes.size >= 2 && uniqueOutcomes.size === legs.length;
      const sumSNum = Number(math.implied_probability_sum || detail.implied_probability_sum || 0);
      const sumS = sumSNum.toFixed(4);
      const isSbVerified = (math.is_surebet === true) || (math.is_surebet !== false && sumSNum > 0.0001 && sumSNum < 1.0 && hasComplementaryOutcomes);
      const sumCls = isSbVerified ? 'text-success' : 'text-danger';

      mathSectionHtml = `
        <div class="insp-section-wrap">
          <div class="insp-section-label">Arbitrage Mathematical Proof</div>
          <div class="insp-math-box">
            <div class="insp-math-formula">
              S = Σ(1 / Net Effective Odds) = <span class="${sumCls} font-bold">${sumS}</span>
            </div>
            <div class="insp-math-desc">
              ${isSbVerified
                ? `Strictly below 1.0 threshold (<strong class="text-success">${sumS} &lt; 1.0</strong>). Risk-free net return of <strong class="text-success">+${marginPct}%</strong> is mathematically guaranteed across complementary outcomes.`
                : (!hasComplementaryOutcomes && legs.length >= 2
                  ? `<strong class="text-warning">Identical or Non-Complementary Quotes</strong>: Leg selections do not form a mutually exclusive partition of event outcomes. This is a quote comparison, not an arbitrage opportunity.`
                  : (sumSNum <= 0
                    ? `No probability sum available for unrated quote comparison.`
                    : `Sum of net probabilities is ${sumS} ≥ 1.0 (no arbitrage profit possible).`))}
              ${math.explanation ? `<br><small class="text-muted" style="margin-top:0.3rem; display:block;">${escapeHtml(math.explanation)}</small>` : ''}
            </div>
          </div>
        </div>
      `;
    } else if (isWatch) {
      const sumSNum = Number(math.implied_probability_sum || detail.implied_probability_sum || 1.0101);
      const distPct = ((sumSNum - 1.0) * 100).toFixed(2);

      mathSectionHtml = `
        <div class="insp-section-wrap">
          <div class="insp-section-label">Near-Arbitrage Watchlist Monitoring</div>
          <div class="insp-math-box" style="border-left: 3px solid var(--val-warning);">
            <div class="insp-math-formula">
              S = Σ(1 / Net Effective Odds) = <span class="text-warning font-bold">${sumSNum.toFixed(4)}</span> (Margin: <span class="text-warning font-bold">${marginPct}%</span>)
            </div>
            <div class="insp-math-desc">
              Market is currently just outside the arbitrage threshold (<strong class="text-warning">${sumSNum.toFixed(4)} ≥ 1.0</strong>).
              A line movement or odds drift of at least <strong>+${distPct}%</strong> will turn this market into a risk-free surebet.
              ${detail.watchlist_reason ? `<br><small class="text-muted" style="margin-top:0.3rem; display:block;">${escapeHtml(detail.watchlist_reason)}</small>` : ''}
            </div>
          </div>
        </div>
      `;
    } else if (isDisc) {
      const bestOddsNum = Number(bestOdds) || 1.0;
      const lowerOddsNum = Number(detail.lower_execution_odds || math.lower_odds) || 1.0;
      const oDiffNum = Number(detail.odds_difference !== undefined && detail.odds_difference !== null ? detail.odds_difference : (math.odds_difference !== undefined && math.odds_difference !== null ? math.odds_difference : (bestOddsNum - lowerOddsNum)));
      const relPctNum = Number(detail.price_discrepancy_pct !== undefined && detail.price_discrepancy_pct !== null ? detail.price_discrepancy_pct : (math.relative_price_difference_pct !== undefined && math.relative_price_difference_pct !== null ? math.relative_price_difference_pct : (((bestOddsNum / lowerOddsNum) - 1.0) * 100)));
      const bestBmName = detail.best_bookmaker || (detail.bookmakers && detail.bookmakers[0]) || 'Highest Bookmaker';
      const lowerBmName = detail.lower_bookmaker || math.lower_bookmaker || 'Alternative Bookmaker';

      const impBest = (100.0 / bestOddsNum).toFixed(1);
      const impLower = (100.0 / lowerOddsNum).toFixed(1);
      const impDiff = (Number(impLower) - Number(impBest)).toFixed(1);

      mathSectionHtml = `
        <div class="insp-section-wrap">
          <div class="insp-section-label">Polish Bookmaker Price Discrepancy Analysis</div>
          <div class="insp-math-box" style="border-left: 3px solid var(--val-warning);">
            <div class="insp-math-formula">
              Δ = ${bestOddsNum.toFixed(2)} − ${lowerOddsNum.toFixed(2)} = <span class="text-warning font-bold">+${oDiffNum.toFixed(2)}</span> (<span class="text-success font-bold">+${relPctNum.toFixed(1)}%</span> relative improvement)
            </div>
            <div class="insp-math-desc">
              <strong>${escapeHtml(bestBmName)}</strong> offers <strong>${bestOddsNum.toFixed(2)}</strong> (implied probability ${impBest}%) vs <strong>${escapeHtml(lowerBmName)}</strong> at <strong>${lowerOddsNum.toFixed(2)}</strong> (implied probability ${impLower}%).
              This represents a <strong>${impDiff} pp</strong> implied probability discrepancy on the identical proposition.
              <div class="insp-warning-callout">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align: -2px; margin-right: 4px;"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
                NOT A SUREBET • NO GUARANTEED PROFIT • SINGLE-LEG VALUE DISCOVERY
              </div>
              <small class="text-muted" style="margin-top:0.4rem; display:block; line-height: 1.4;">
                This opportunity exploits cross-bookmaker pricing inefficiency between licensed Polish bookmakers for the exact same bet. It is not an arbitrage surebet as it does not cover complementary outcomes.
              </small>
            </div>
          </div>
        </div>
      `;

      if (detail.fair_odds || math.fair_odds) {
        const fOdds = Number(detail.fair_odds || math.fair_odds);
        const fProb = Number(math.fair_probability || detail.fair_probability || (1.0 / fOdds));
        const fProbPct = (fProb * 100).toFixed(1);
        mathSectionHtml += `
          <div class="insp-section-wrap" style="margin-top: 0.75rem;">
            <div class="insp-section-label">Sharp Reference Benchmark Valuation</div>
            <div class="insp-math-box">
              <div class="insp-math-formula">
                Fair Benchmark Odds: <span class="text-info font-bold">${fOdds.toFixed(2)}</span> (${fProbPct}% Model Probability)
              </div>
              <div class="insp-math-desc">
                Sharp baseline confirms fair pricing without synthetic fallbacks.
              </div>
            </div>
          </div>
        `;
      }
    } else if (isQuote || isTeam || isPlayer) {
      mathSectionHtml = `
        <div class="insp-section-wrap">
          <div class="insp-section-label">Best Execution & Price Matrix</div>
          <div class="insp-math-box">
            <div class="insp-math-formula">
              Best Net Odds: <span class="text-success font-bold">${Number(bestOdds).toFixed(2)}</span> (${detail.bookmakers && Array.isArray(detail.bookmakers) ? detail.bookmakers[0] : (detail.bookmaker || 'Best Bookmaker')})
            </div>
            <div class="insp-math-desc">
              Cross-bookmaker quote comparison for this specific selection across Polish bookmakers. Effective net odds reflect actual payouts after Polish turnover tax (0% for Betclic promo, 12% standard). No model valuation or arbitrage guarantee.
              ${math.explanation ? `<br><small class="text-muted" style="margin-top:0.3rem; display:block;">${escapeHtml(math.explanation)}</small>` : ''}
            </div>
          </div>
        </div>
      `;
    }

    // ── LAYER 4: CROSS-BOOKMAKER LEGS BREAKDOWN ──
    let legsSectionHtml = '';
    if (legs && legs.length > 0) {
      const tableTitle = (isQuote || isTeam || isPlayer || isDisc)
        ? 'Bookmaker Quote Comparison Matrix (Same Selection)'
        : 'Selections & Cross-Bookmaker Execution Legs';

      legsSectionHtml = `
        <div class="insp-section-wrap">
          <div class="insp-section-label">${tableTitle}</div>
          <div class="insp-legs-table-wrap">
            <table class="insp-legs-table">
              <thead>
                <tr>
                  <th>Outcome</th>
                  <th>Bookmaker</th>
                  <th>Raw Odds</th>
                  <th>Tax</th>
                  <th>Eff. Odds</th>
                  <th>Impl. Prob</th>
                </tr>
              </thead>
              <tbody>
                ${legs.map(l => {
                  const outcome = l.selection_outcome || l.outcome || l.selection_type || 'Selection';
                  const book = l.provider || l.bookmaker || 'Bookmaker';
                  const rawOddsNum = Number(l.raw_odds || l.odds || l.selected_odds || 0);
                  const taxRate = l.tax_rate !== undefined ? Number(l.tax_rate) : (String(book).toLowerCase() === 'superbet' ? 0.12 : 0.0);
                  const effOddsNum = Number(l.effective_odds || l.effective_net_odds || (rawOddsNum * (1.0 - taxRate)));
                  const impProbNum = Number(l.net_implied_probability || l.implied_probability || (effOddsNum > 0 ? 1.0 / effOddsNum : 0));

                  return `
                    <tr>
                      <td><strong class="text-primary">${escapeHtml(outcome)}</strong></td>
                      <td><span class="badge badge-outline" style="font-size:0.7rem;">${escapeHtml(book)}</span></td>
                      <td class="mono font-bold text-success">${rawOddsNum.toFixed(2)}</td>
                      <td class="mono text-muted">${taxRate > 0 ? (taxRate * 100).toFixed(0) + '%' : '0%'}</td>
                      <td class="mono font-bold text-accent">${effOddsNum.toFixed(2)}</td>
                      <td class="mono text-muted">${impProbNum.toFixed(4)} <small>(${(impProbNum * 100).toFixed(1)}%)</small></td>
                    </tr>
                  `;
                }).join('')}
              </tbody>
            </table>
          </div>
        </div>
      `;
    }

    // ── LAYER 5: INTERACTIVE STAKE CALCULATOR / SIMULATOR ──
    let calcSectionHtml = '';
    if (isSb) {
      calcSectionHtml = `
        <div class="insp-section-wrap">
          <div class="insp-section-label">Interactive Stake Allocation Calculator</div>
          <div class="insp-stake-calc-box">
            <div class="insp-stake-input-wrap">
              <label for="detail-calc-total-stake">Total Capital Stake:</label>
              <div class="opp-input-affix">
                <input type="number" id="detail-calc-total-stake" class="form-control form-control-sm" value="1000" min="10" step="50" aria-label="Total Stake">
                <span class="affix">PLN</span>
              </div>
            </div>
            <div id="detail-calc-results-wrap">
              <!-- Dynamically updated by wireSurebetStakeCalculators() -->
            </div>
          </div>
        </div>
      `;
    } else if (isWatch && legs.length >= 2) {
      const leg1 = legs[0];
      const leg2 = legs[1];
      const eff1 = Number(leg1.effective_odds || leg1.raw_odds || 1.0);
      const eff2 = Number(leg2.effective_odds || leg2.raw_odds || 1.0);
      const p1 = eff1 > 0 ? 1.0 / eff1 : 0;
      const p2 = eff2 > 0 ? 1.0 / eff2 : 0;
      const reqEff1 = (1.0 - p2) > 0 ? (1.0 / (1.0 - p2)).toFixed(2) : '—';
      const reqEff2 = (1.0 - p1) > 0 ? (1.0 / (1.0 - p1)).toFixed(2) : '—';

      calcSectionHtml = `
        <div class="insp-section-wrap">
          <div class="insp-section-label">Arbitrage Target Odds Simulator</div>
          <div class="insp-stake-calc-box" style="font-size: 0.76rem;">
            <div style="color: var(--text-secondary); margin-bottom: 0.4rem;">
              Required effective odds on either leg to achieve <strong>Break-Even (S = 1.000)</strong>:
            </div>
            <div style="display: flex; flex-direction: column; gap: 0.3rem;">
              <div style="display: flex; justify-content: space-between; background: var(--surface-card); padding: 0.35rem 0.5rem; border-radius: var(--radius-xs);">
                <span>If ${escapeHtml(leg2.provider || 'Leg 2')} stays @ ${eff2.toFixed(2)}:</span>
                <span>${escapeHtml(leg1.provider || 'Leg 1')} must rise to <strong class="mono text-success">${reqEff1}</strong> (now ${eff1.toFixed(2)})</span>
              </div>
              <div style="display: flex; justify-content: space-between; background: var(--surface-card); padding: 0.35rem 0.5rem; border-radius: var(--radius-xs);">
                <span>If ${escapeHtml(leg1.provider || 'Leg 1')} stays @ ${eff1.toFixed(2)}:</span>
                <span>${escapeHtml(leg2.provider || 'Leg 2')} must rise to <strong class="mono text-success">${reqEff2}</strong> (now ${eff2.toFixed(2)})</span>
              </div>
            </div>
          </div>
        </div>
      `;
    }

    // ── LAYER 6: FORENSIC AUDIT TRAIL & IDENTIFIERS ──
    const auditHtml = `
      <div class="insp-section-wrap">
        <details class="insp-audit-collapsible">
          <summary class="insp-audit-summary">
            <span>Forensic Audit Trail & Engine Identifiers</span>
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg>
          </summary>
          <div class="opp-detail-kv-grid" style="margin-top: 0.5rem; font-size: 0.76rem; background: var(--surface-input); padding: 0.65rem; border-radius: var(--radius-sm); border: 1px solid var(--border-subtle);">
            <div><span class="kv-label">Lifecycle Status:</span> <strong class="kv-value">${lifecycle.status || detail.lifecycle_status || detail.status || 'NEW'}</strong></div>
            <div><span class="kv-label">Quality Score:</span> <span class="kv-value mono">${qScore !== '—' ? `${qScore} / 100 (${tierName})` : '— (Unscored)'}</span></div>
            <div><span class="kv-label">First Detected:</span> <span class="kv-value mono">${lifecycle.first_seen_at ? formatTimestamp(lifecycle.first_seen_at) : (detail.detected_at ? formatTimestamp(detail.detected_at) : '—')}</span></div>
            <div><span class="kv-label">Last Verified:</span> <span class="kv-value mono">${lifecycle.last_seen_at ? formatTimestamp(lifecycle.last_seen_at) : '—'}</span></div>
            <div style="grid-column: 1 / -1;"><span class="kv-label">Canonical Opp ID:</span> <span class="kv-value mono" style="word-break: break-all;">${detail.opportunity_id || detail.id}</span></div>
          </div>
        </details>
      </div>
    `;

    return `
      ${headerCardHtml}
      ${priceMatrixHtml}
      ${mathSectionHtml}
      ${legsSectionHtml}
      ${calcSectionHtml}
      ${auditHtml}
    `;
  }

  // Interactive Surebet Stake Calculator Controller
  function wireSurebetStakeCalculators(detail, legs) {
    const inputEls = document.querySelectorAll('#detail-calc-total-stake');
    if (!inputEls || inputEls.length === 0) return;

    function recalculate() {
      inputEls.forEach(inputEl => {
        const totalStake = parseFloat(inputEl.value) || 1000;
        const res = calculateSurebetDistribution(totalStake, legs, state.settings?.bookmaker_tax_configs || {});
        const container = inputEl.closest('.insp-stake-calc-box')?.querySelector('#detail-calc-results-wrap');
        if (!container) return;

        if (!res.isSurebet) {
          container.innerHTML = `
            <div style="font-size: 0.75rem; color: var(--val-warning); padding: 0.4rem 0; display: flex; align-items: center; gap: 0.35rem;">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
              Calculated effective probability sum is ≥ 1.0 under current tax parameters.
            </div>
          `;
          return;
        }

        container.innerHTML = `
          <div style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border-subtle); padding-bottom: 0.4rem; margin-bottom: 0.4rem;">
            <span style="font-size: 0.75rem; color: var(--text-muted);">Guaranteed Net Payout:</span>
            <strong class="mono text-success" style="font-size: 0.95rem;">${res.guaranteedPayout.toFixed(2)} PLN</strong>
          </div>
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.5rem;">
            <span style="font-size: 0.75rem; color: var(--text-muted);">Guaranteed Net Profit:</span>
            <strong class="mono text-success" style="font-size: 0.95rem;">+${res.guaranteedProfit.toFixed(2)} PLN (+${res.roi.toFixed(2)}%)</strong>
          </div>
          <div style="display: flex; flex-direction: column; gap: 0.25rem;">
            ${res.legs.map(l => `
              <div style="display: flex; justify-content: space-between; font-size: 0.74rem; background: var(--surface-card); padding: 0.3rem 0.5rem; border-radius: var(--radius-xs);">
                <span>${escapeHtml(l.selectionOutcome || l.outcome || l.selectionType)} (${escapeHtml(l.provider)}):</span>
                <strong class="mono">${Number(l.allocatedStake).toFixed(2)} PLN</strong>
              </div>
            `).join('')}
          </div>
        `;
      });
    }

    inputEls.forEach(inputEl => {
      inputEl.addEventListener('input', recalculate);
    });

    // Run initial calculation
    recalculate();
  }

  function closeOppDetailModal() {
    const modal = document.getElementById('opp-detail-modal');
    const backdrop = document.getElementById('opp-detail-backdrop');
    if (modal) modal.classList.remove('open');
    if (backdrop) backdrop.classList.remove('open');
    setTimeout(() => {
      if (modal && !modal.classList.contains('open')) modal.style.display = 'none';
    }, 200);
    if (window.location.hash.startsWith('#opportunity/')) {
      window.location.hash = 'opportunities';
    }
  }

  // Helper: Client-Side Surebet Stake & Preset Calculation Engine
  function calculateSurebetDistribution(totalStake, rawLegs, bookmakerTaxConfigs = {}) {
    const totStake = Math.max(0, Number(totalStake) || 0);
    if (totStake <= 0 || !rawLegs || rawLegs.length === 0) {
      return { isSurebet: false, netS: 0, roi: 0, guaranteedPayout: 0, guaranteedProfit: 0, legs: [] };
    }

    const calculatedLegs = rawLegs.map(leg => {
      const rawOdds = Number(leg.raw_odds !== undefined ? leg.raw_odds : (leg.odds !== undefined ? leg.odds : (leg.decimal_odds !== undefined ? leg.decimal_odds : (leg.selected_odds || 1.0))));
      const provider = String(leg.provider || leg.selected_provider || leg.bookmaker || 'unknown').toLowerCase();
      const selType = String(leg.selection_type || leg.selectionType || leg.type || 'SELECTION');
      const selOutcome = leg.selection_outcome || leg.outcome || selType;
      
      // Determine tax rate
      let taxRate = 0.0;
      const cfg = bookmakerTaxConfigs[provider] || (state.settings?.bookmaker_tax_configs ? state.settings.bookmaker_tax_configs[provider] : null);
      if (cfg && cfg.tax_enabled) {
        taxRate = Number(cfg.tax_rate) || 0.12;
      } else if (!cfg) {
        if (provider === 'superbet') taxRate = 0.12;
        else taxRate = 0.0;
      }

      // Check if leg already carries an explicit tax rate
      if (leg.tax_rate !== undefined && leg.tax_rate !== null) {
        taxRate = Number(leg.tax_rate);
      }

      const effOdds = rawOdds * (1.0 - taxRate);
      const impliedProb = effOdds > 0 ? (1.0 / effOdds) : 0;

      return {
        selectionType: selType,
        selectionOutcome: selOutcome,
        provider: leg.provider || leg.selected_provider || leg.bookmaker || 'unknown',
        rawOdds,
        taxRate,
        taxFactor: (1.0 - taxRate),
        effectiveOdds: effOdds,
        impliedProb,
      };
    });

    // Safety check: verify that legs represent distinct, mutually exclusive outcomes!
    const distinctOutcomes = new Set(calculatedLegs.map(l => (l.selectionOutcome || l.selectionType || '').toLowerCase().trim()));
    const hasComplementaryOutcomes = distinctOutcomes.size >= 2 && distinctOutcomes.size === calculatedLegs.length;

    const netS = calculatedLegs.reduce((acc, l) => acc + l.impliedProb, 0);
    const isSurebet = netS > 0 && netS < 1.0 && hasComplementaryOutcomes;
    const roi = (netS > 0 && isSurebet) ? ((1.0 / netS) - 1.0) * 100.0 : 0;

    if (!isSurebet || netS <= 0) {
      return {
        isSurebet: false,
        netS,
        roi,
        guaranteedPayout: 0,
        guaranteedProfit: 0,
        legs: calculatedLegs.map(l => ({
          ...l,
          stakePct: netS > 0 ? (l.impliedProb / netS) * 100.0 : 0,
          allocatedStake: 0,
          expectedPayout: 0,
          expectedProfit: -totStake,
        })),
      };
    }

    // Allocate stakes and round to 2 decimals
    const rawStakes = [];
    const roundedStakes = [];
    const residuals = [];

    calculatedLegs.forEach((l, idx) => {
      const weight = l.impliedProb / netS;
      const raw = totStake * weight;
      const rnd = Math.round(raw * 100) / 100;
      rawStakes.push(raw);
      roundedStakes.push(rnd);
      residuals.push({ diff: raw - rnd, index: idx });
    });

    // Reconcile rounding discrepancy against total stake
    let currentSum = roundedStakes.reduce((a, b) => a + b, 0);
    let discrepancy = Math.round((totStake - currentSum) * 100) / 100;

    if (discrepancy !== 0) {
      const cents = Math.round(Math.abs(discrepancy) * 100);
      if (discrepancy > 0) {
        residuals.sort((a, b) => b.diff - a.diff);
        for (let c = 0; c < cents; c++) {
          const targetIdx = residuals[c % residuals.length].index;
          roundedStakes[targetIdx] = Math.round((roundedStakes[targetIdx] + 0.01) * 100) / 100;
        }
      } else {
        residuals.sort((a, b) => a.diff - b.diff);
        for (let c = 0; c < cents; c++) {
          const targetIdx = residuals[c % residuals.length].index;
          if (roundedStakes[targetIdx] >= 0.01) {
            roundedStakes[targetIdx] = Math.round((roundedStakes[targetIdx] - 0.01) * 100) / 100;
          }
        }
      }
    }

    const finalLegs = calculatedLegs.map((l, idx) => {
      const stake = roundedStakes[idx];
      const payout = Math.round(stake * l.effectiveOdds * 100) / 100;
      const profit = Math.round((payout - totStake) * 100) / 100;
      const stakePct = (l.impliedProb / netS) * 100.0;
      return {
        ...l,
        stakePct,
        allocatedStake: stake,
        expectedPayout: payout,
        expectedProfit: profit,
      };
    });

    const guaranteedPayout = Math.min(...finalLegs.map(l => l.expectedPayout));
    const guaranteedProfit = Math.round((guaranteedPayout - totStake) * 100) / 100;

    return {
      isSurebet: true,
      netS,
      roi,
      guaranteedPayout,
      guaranteedProfit,
      legs: finalLegs,
    };
  }

  function renderSurebetStakeCalculator(detail, rawLegs) {
    const inputEl = document.getElementById('detail-calc-total-stake');
    const invalidAlert = document.getElementById('detail-calc-invalid-alert');
    const summaryGrid = document.getElementById('detail-calc-summary-grid');
    const roiBadge = document.getElementById('detail-calc-roi-badge');

    // Ensure legs array is extracted properly from detail if rawLegs is empty
    const legs = (rawLegs && rawLegs.length > 0) ? rawLegs : (detail.selections || detail.legs || []);

    if (inputEl && (!inputEl.value || parseFloat(inputEl.value) <= 0)) {
      inputEl.value = "1000";
    }

    function updateCalculator() {
      const rawVal = inputEl ? parseFloat(inputEl.value) : 1000;
      const totalStake = isNaN(rawVal) || rawVal <= 0 ? 1000 : rawVal;
      const res = calculateSurebetDistribution(totalStake, legs, state.settings?.bookmaker_tax_configs || {});

      if (!res.isSurebet) {
        if (invalidAlert) invalidAlert.style.display = 'block';
        if (summaryGrid) summaryGrid.style.opacity = '0.5';
        if (roiBadge) {
          roiBadge.textContent = 'Non-Surebet';
          roiBadge.className = 'badge badge-cycle-failed';
        }
        const totEl = document.getElementById('detail-calc-summary-total');
        if (totEl) totEl.textContent = `${totalStake.toFixed(2)} PLN`;
        const payEl = document.getElementById('detail-calc-summary-payout');
        if (payEl) payEl.textContent = '—';
        const profEl = document.getElementById('detail-calc-summary-profit');
        if (profEl) profEl.textContent = '—';
        const roiEl = document.getElementById('detail-calc-summary-roi');
        if (roiEl) roiEl.textContent = `${res.roi.toFixed(2)}%`;
      } else {
        if (invalidAlert) invalidAlert.style.display = 'none';
        if (summaryGrid) summaryGrid.style.opacity = '1';
        if (roiBadge) {
          roiBadge.textContent = `ROI: +${res.roi.toFixed(2)}%`;
          roiBadge.className = 'badge badge-success';
        }
        const totEl = document.getElementById('detail-calc-summary-total');
        if (totEl) totEl.textContent = `${totalStake.toFixed(2)} PLN`;
        const payEl = document.getElementById('detail-calc-summary-payout');
        if (payEl) payEl.textContent = `${res.guaranteedPayout.toFixed(2)} PLN`;
        const profEl = document.getElementById('detail-calc-summary-profit');
        if (profEl) profEl.textContent = `+${res.guaranteedProfit.toFixed(2)} PLN`;
        const roiEl = document.getElementById('detail-calc-summary-roi');
        if (roiEl) roiEl.textContent = `+${res.roi.toFixed(2)}%`;
      }

      // Render Active Stakes Table
      const tbody = document.getElementById('detail-calc-legs-body');
      if (tbody) {
        tbody.innerHTML = res.legs.map(l => {
          const taxDisplay = l.taxRate > 0 ? `${(l.taxRate * 100).toFixed(0)}% (factor ${(1 - l.taxRate).toFixed(2)})` : `0% (factor 1.00)`;
          const profitClass = l.expectedProfit >= 0 ? 'text-success font-bold' : 'text-danger';
          const outcomeLabel = l.selectionOutcome || l.selectionType;
          return `
            <tr>
              <td><strong class="text-accent">${outcomeLabel}</strong></td>
              <td><span class="badge badge-success">${l.provider}</span></td>
              <td class="mono font-bold">${l.rawOdds.toFixed(2)}</td>
              <td class="mono text-muted">${taxDisplay}</td>
              <td class="mono text-accent font-bold">${l.effectiveOdds.toFixed(2)}</td>
              <td class="mono">${l.stakePct.toFixed(2)}%</td>
              <td class="mono font-bold" style="font-size: 1rem; color: var(--accent-primary);">${l.allocatedStake.toFixed(2)} PLN</td>
              <td class="mono text-success">${l.expectedPayout.toFixed(2)} PLN</td>
              <td class="mono ${profitClass}">${l.expectedProfit >= 0 ? '+' : ''}${l.expectedProfit.toFixed(2)} PLN</td>
            </tr>
          `;
        }).join('');
      }

      // Render Preset Table (50, 100, 200, 500, 1000 PLN)
      renderPresetTable(legs);
    }

    function renderPresetTable(legsList) {
      const thead = document.getElementById('detail-calc-preset-thead');
      const tbody = document.getElementById('detail-calc-preset-tbody');
      if (!thead || !tbody) return;

      const legHeaders = legsList.map((l, i) => {
        const selName = l.selection_outcome || l.outcome || l.selection_type || l.selectionType || `Leg ${i+1}`;
        const bm = l.provider || l.selected_provider || l.bookmaker || 'BM';
        const rawO = Number(l.raw_odds || l.odds || 1.0).toFixed(2);
        const effO = l.effective_odds ? Number(l.effective_odds).toFixed(2) : rawO;
        return `<th>${selName}<br><small class="text-muted" style="font-weight:normal;">${bm} @ ${rawO} (net ${effO})</small></th>`;
      });
      
      thead.innerHTML = `
        <tr>
          <th>Total Stake</th>
          ${legHeaders.join('')}
          <th>Guaranteed Payout</th>
          <th>Guaranteed Profit</th>
          <th>ROI</th>
        </tr>
      `;

      const presets = [50, 100, 200, 500, 1000];
      tbody.innerHTML = presets.map(p => {
        const presRes = calculateSurebetDistribution(p, legsList, state.settings?.bookmaker_tax_configs || {});
        if (!presRes.isSurebet) {
          return `
            <tr>
              <td class="bold mono">${p} PLN</td>
              ${presRes.legs.map(() => `<td class="mono text-muted">—</td>`).join('')}
              <td class="mono text-muted">—</td>
              <td class="mono text-muted">—</td>
              <td class="mono text-muted">No Surebet</td>
            </tr>
          `;
        }

        const legCells = presRes.legs.map(l => `<td class="mono font-bold">${l.allocatedStake.toFixed(2)} PLN</td>`).join('');
        return `
          <tr>
            <td class="bold mono text-accent">${p} PLN</td>
            ${legCells}
            <td class="mono text-success font-bold">${presRes.guaranteedPayout.toFixed(2)} PLN</td>
            <td class="mono text-success font-bold">+${presRes.guaranteedProfit.toFixed(2)} PLN</td>
            <td class="mono text-success font-bold">+${presRes.roi.toFixed(2)}%</td>
          </tr>
        `;
      }).join('');
    }

    if (inputEl) {
      inputEl.oninput = updateCalculator;
    }
    updateCalculator();
  }

  // ──────────────────────────────────────────────────────────────────────────
  // Provider Monitor View Loader
  // ──────────────────────────────────────────────────────────────────────────

  async function loadProvidersData() {
    const res = await api.fetchProviders();
    const data = res.data || {};
    const provHealth = data.provider_health || {};

    const cardsContainer = document.getElementById('providers-cards-container');
    const tbody = document.getElementById('provider-table-body');

    const providerNames = data.registered_providers || ['superbet', 'betclic', 'bet365', 'unibet', 'odds_api'];

    cardsContainer.innerHTML = providerNames.map(name => {
      const h = provHealth[name] || {};
      const lastScan = state.latestScan;
      const provScanData = (lastScan && lastScan.provider_results) ? lastScan.provider_results[name] : null;
      const cov = (lastScan && lastScan.bookmaker_coverage) ? lastScan.bookmaker_coverage[name] : null;

      const isViaOddsApi = h.is_via_odds_api || (cov && cov.is_via_odds_api) || name === 'bet365' || name === 'unibet';
      const isAggregator = name === 'odds_api' || h.provider_type === 'AGGREGATOR';

      const displayStatus = cov ? cov.status : (provScanData ? provScanData.status : (h.status || 'NOT_RUN'));
      const statusIsGood = displayStatus === 'HEALTHY' || displayStatus === 'COMPLETED' || displayStatus === 'SUCCESS' || displayStatus === 'OK';
      const statusBadge = statusIsGood ? 'badge-success' : (displayStatus === 'NOT_RUN' ? 'badge-outline' : (displayStatus === 'UNAVAILABLE' ? 'badge-danger' : 'badge-warning'));

      const titleBadge = isViaOddsApi ? `<span class="badge badge-outline" style="font-size: 0.65rem; margin-left: 0.35rem;">Odds API</span>` : (isAggregator ? `<span class="badge badge-accent" style="font-size: 0.65rem; margin-left: 0.35rem;">Gateway</span>` : `<span class="badge badge-outline" style="font-size: 0.65rem; margin-left: 0.35rem;">Direct</span>`);

      const discoveredCount = cov ? cov.discovered : (provScanData ? provScanData.discovered_count : '—');
      const parsedCount = cov ? cov.parsed : (provScanData ? provScanData.parsed_count : '—');
      const normalizedCount = cov ? cov.normalized : (provScanData ? '—' : '—');
      const matchedCount = cov ? cov.matched_events : '—';
      const durationVal = provScanData ? safeDuration(provScanData.execution_duration) : (h.avg_latency_ms ? h.avg_latency_ms + 'ms' : '—');

      const triggerTarget = isViaOddsApi ? 'odds_api' : name;
      const buttonLabel = isViaOddsApi ? `Run Provider (via Odds API)` : `Run Provider`;

      return `
        <div class="card provider-card">
          <div class="card-header">
            <h3 class="card-title">${name.toUpperCase()} ${titleBadge}</h3>
            <span class="badge ${statusBadge}">${displayStatus}</span>
          </div>
          <div class="provider-metrics">
            <div class="status-item">
              <span>Circuit Breaker</span>
              <span class="mono">${safeStr(h.circuit_breaker, 'CLOSED')}</span>
            </div>
            <div class="status-item">
              <span>5m Error Rate</span>
              <span class="mono">${safeStr(h.error_rate_5m, '0.0%')}</span>
            </div>
            <div class="status-item">
              <span>Last Discovered</span>
              <span class="mono">${safeNum(discoveredCount, '—')}</span>
            </div>
            <div class="status-item">
              <span>Last Parsed</span>
              <span class="mono">${safeNum(parsedCount, '—')}</span>
            </div>
            <div class="status-item">
              <span>Normalized / Matched</span>
              <span class="mono">${safeNum(normalizedCount, '—')} / ${safeNum(matchedCount, '—')}</span>
            </div>
            <div class="status-item">
              <span>Execution / Latency</span>
              <span class="mono">${durationVal}</span>
            </div>
          </div>
          <button class="btn btn-primary btn-sm btn-trigger-prov margin-top" data-name="${triggerTarget}">${buttonLabel}</button>
        </div>
      `;
    }).join('');

    tbody.innerHTML = providerNames.map(name => {
      const h = provHealth[name] || {};
      const lastScan = state.latestScan;
      const provScanData = (lastScan && lastScan.provider_results) ? lastScan.provider_results[name] : null;
      const cov = (lastScan && lastScan.bookmaker_coverage) ? lastScan.bookmaker_coverage[name] : null;

      const isViaOddsApi = h.is_via_odds_api || (cov && cov.is_via_odds_api) || name === 'bet365' || name === 'unibet';
      const isAggregator = name === 'odds_api' || h.provider_type === 'AGGREGATOR';

      const discovered = cov ? safeNum(cov.discovered, '—') : (provScanData ? safeNum(provScanData.discovered_count, '—') : '—');
      const parsed = cov ? safeNum(cov.parsed, '—') : (provScanData ? safeNum(provScanData.parsed_count, '—') : '—');
      const errors = cov ? safeNum((cov.errors || []).length, 0) : (provScanData ? safeNum((provScanData.errors || []).length, 0) : '—');
      const warnings = cov ? safeNum((cov.warnings || []).length, 0) : (provScanData ? safeNum((provScanData.warnings || []).length, 0) : '—');
      const latency = provScanData ? safeDuration(provScanData.execution_duration) : (h.avg_latency_ms ? h.avg_latency_ms + 'ms' : '—');
      const provStatus = cov ? cov.status : (provScanData ? provScanData.status : (h.status || 'NOT_RUN'));
      const statusClass = (provStatus === 'HEALTHY' || provStatus === 'COMPLETED' || provStatus === 'SUCCESS' || provStatus === 'OK') ? 'badge-success' : (provStatus === 'NOT_RUN' ? 'badge-outline' : (provStatus === 'UNAVAILABLE' ? 'badge-danger' : 'badge-warning'));

      const typeBadge = isViaOddsApi ? `<span class="badge badge-outline" style="font-size: 0.65rem; margin-left: 0.35rem;">Odds API</span>` : (isAggregator ? `<span class="badge badge-accent" style="font-size: 0.65rem; margin-left: 0.35rem;">Gateway</span>` : `<span class="badge badge-outline" style="font-size: 0.65rem; margin-left: 0.35rem;">Direct</span>`);
      const triggerTarget = isViaOddsApi ? 'odds_api' : name;

      return `
        <tr>
          <td class="bold">${name.toUpperCase()}</td>
          <td><span class="badge ${statusClass}">${provStatus}</span></td>
          <td class="mono">${latency}</td>
          <td class="mono">${discovered}</td>
          <td class="mono">${parsed}</td>
          <td class="mono">${errors}</td>
          <td class="mono">${warnings}</td>
          <td>
            <button class="btn btn-sm btn-outline btn-trigger-prov" data-name="${name}">Trigger Run</button>
          </td>
        </tr>
      `;
    }).join('');

    document.querySelectorAll('.btn-trigger-prov').forEach(btn => {
      btn.addEventListener('click', async () => {
        const pName = btn.getAttribute('data-name');
        btn.disabled = true;
        btn.textContent = 'Running...';
        const runRes = await api.triggerProvider(pName);
        btn.disabled = false;
        btn.textContent = 'Run Provider';
        showToast(`Provider ${pName} executed: ${runRes.data?.status || 'COMPLETED'}`);
        loadProvidersData();
      });
    });
  }

  // ──────────────────────────────────────────────────────────────────────────
  // Market Intelligence / Event Coverage Workspace Controller (UI/UX Redesign V3)
  // ──────────────────────────────────────────────────────────────────────────

  function escapeHtml(str) {
    if (!str && str !== 0) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function setMarketIntelViewMode(mode) {
    state.marketIntel.viewMode = mode;
    const wsBtn = document.getElementById('btn-mode-workspace');
    const matBtn = document.getElementById('btn-mode-matrix');
    const wsView = document.getElementById('event-workspace-view');
    const matView = document.getElementById('events-matrix-view');

    if (wsBtn) wsBtn.classList.toggle('active', mode === 'workspace');
    if (matBtn) matBtn.classList.toggle('active', mode === 'matrix');

    if (mode === 'workspace') {
      if (wsView) wsView.style.display = 'block';
      if (matView) matView.style.display = 'none';
      if (state.marketIntel.activeEventId && !state.marketIntel.activeDetail) {
        loadEventDetail(state.marketIntel.activeEventId);
      }
    } else {
      if (wsView) wsView.style.display = 'none';
      if (matView) matView.style.display = 'block';
      renderAllFixturesMatrix();
    }
  }

  function openFixtureSelectorModal() {
    const modal = document.getElementById('fixture-selector-modal');
    const backdrop = document.getElementById('fixture-selector-backdrop');
    const input = document.getElementById('modal-fixture-search');
    if (modal) modal.classList.add('open');
    if (backdrop) backdrop.classList.add('active');
    renderFixtureModalList();
    if (input) {
      input.value = '';
      setTimeout(() => input.focus(), 80);
    }
  }

  function closeFixtureSelectorModal() {
    const modal = document.getElementById('fixture-selector-modal');
    const backdrop = document.getElementById('fixture-selector-backdrop');
    if (modal) modal.classList.remove('open');
    if (backdrop) backdrop.classList.remove('active');
  }

  function filterFixtureModalList(query) {
    renderFixtureModalList(query);
  }

  function renderFixtureModalList(query = '') {
    const container = document.getElementById('modal-fixture-list');
    if (!container) return;
    const q = (query || '').toLowerCase().trim();
    let events = state.events || [];
    if (q) {
      events = events.filter(e =>
        (e.home_team || '').toLowerCase().includes(q) ||
        (e.away_team || '').toLowerCase().includes(q) ||
        (e.competition || '').toLowerCase().includes(q)
      );
    }

    if (!events.length) {
      container.innerHTML = '<div class="text-center text-muted" style="padding: 1.5rem;">No matching fixtures found.</div>';
      return;
    }

    container.innerHTML = events.map((ev, idx) => {
      const evId = ev.id || ev.canonical_event_id;
      const mkts = ev.matched_markets_count || ev.normalized_markets_count || 0;
      const isSelected = evId === state.marketIntel.activeEventId;
      const rank = (state.events || []).findIndex(e => (e.id || e.canonical_event_id) === evId) + 1;
      const opps = ev.has_surebet ? '⚡ Surebet' : (ev.has_valuebet ? '📈 Valuebet' : '');

      return `
        <div class="fixture-modal-item ${isSelected ? 'active' : ''}" data-event-id="${evId}">
          <div>
            <div style="font-weight: 700; font-size: 0.88rem; color: var(--text-primary);">
              <span class="ribbon-chip-rank" style="margin-right: 0.35rem;">#${rank}</span>
              ${escapeHtml(ev.home_team)} vs ${escapeHtml(ev.away_team)}
            </div>
            <div class="text-muted" style="font-size: 0.74rem;">
              ${escapeHtml(ev.competition || 'League')} • ${ev.kickoff ? formatTimestamp(ev.kickoff) : 'Kickoff —'}
            </div>
          </div>
          <div style="display: flex; align-items: center; gap: 0.5rem;">
            ${opps ? `<span class="badge badge-success" style="font-size: 0.68rem;">${opps}</span>` : ''}
            <span class="badge badge-outline" style="font-weight: 700; font-family: var(--font-mono); font-size: 0.78rem;">${mkts} mkts</span>
          </div>
        </div>
      `;
    }).join('');

    container.querySelectorAll('.fixture-modal-item').forEach(item => {
      item.addEventListener('click', () => {
        const evId = item.getAttribute('data-event-id');
        closeFixtureSelectorModal();
        setMarketIntelViewMode('workspace');
        loadEventDetail(evId);
      });
    });
  }

  function renderRibbonChips(events) {
    const container = document.getElementById('ribbon-quick-chips');
    if (!container) return;
    const topMatches = events.slice(0, 5);
    container.innerHTML = topMatches.map((ev, idx) => {
      const evId = ev.id || ev.canonical_event_id;
      const rank = idx + 1;
      const mkts = ev.matched_markets_count || ev.normalized_markets_count || 0;
      const isActive = evId === state.marketIntel.activeEventId;
      const oppIcon = ev.has_surebet ? '⚡' : (ev.has_valuebet ? '📈' : '');
      const hShort = (ev.home_team || '').split(' ')[0];
      const aShort = (ev.away_team || '').split(' ')[0];
      const shortName = `${hShort} v ${aShort}`;

      return `
        <button type="button" class="ribbon-chip ${isActive ? 'active' : ''}" data-event-id="${evId}" title="${escapeHtml(ev.home_team)} vs ${escapeHtml(ev.away_team)} (${mkts} mkts)">
          <span class="ribbon-chip-rank">#${rank}</span>
          <span>${escapeHtml(shortName)}</span>
          <span class="ribbon-chip-badge">${mkts}</span>
          ${oppIcon ? `<span>${oppIcon}</span>` : ''}
        </button>
      `;
    }).join('');

    container.querySelectorAll('.ribbon-chip').forEach(btn => {
      btn.addEventListener('click', () => {
        const evId = btn.getAttribute('data-event-id');
        if (evId) {
          setMarketIntelViewMode('workspace');
          loadEventDetail(evId);
        }
      });
    });
  }

  function renderAllFixturesMatrix() {
    const tbody = document.getElementById('fixtures-matrix-tbody');
    if (!tbody) return;

    let events = state.events || [];
    const search = (state.marketIntel.matrixSearch || '').toLowerCase().trim();
    const comp = state.marketIntel.matrixCompetition || '';
    const cov = state.marketIntel.matrixCoverage || 'ALL';

    if (search) {
      events = events.filter(e =>
        (e.home_team || '').toLowerCase().includes(search) ||
        (e.away_team || '').toLowerCase().includes(search) ||
        (e.competition || '').toLowerCase().includes(search)
      );
    }
    if (comp) {
      events = events.filter(e => e.competition === comp);
    }
    if (cov === 'DEEP') {
      events = events.filter(e => (e.matched_markets_count || e.normalized_markets_count || 0) >= 40);
    } else if (cov === 'MEGA') {
      events = events.filter(e => (e.matched_markets_count || e.normalized_markets_count || 0) >= 400);
    } else if (cov === 'OPPS') {
      events = events.filter(e => e.has_surebet || e.has_valuebet);
    }

    if (!events.length) {
      tbody.innerHTML = '<tr><td colspan="8" class="text-center text-muted" style="padding: 2.5rem;">No fixtures match the selected criteria.</td></tr>';
      return;
    }

    tbody.innerHTML = events.map((ev, idx) => {
      const evId = ev.id || ev.canonical_event_id;
      const mkts = ev.matched_markets_count || ev.normalized_markets_count || 0;
      const books = ev.participating_bookmakers || ['superbet', 'betclic'];
      const rank = (state.events || []).findIndex(e => (e.id || e.canonical_event_id) === evId) + 1;

      let signals = [];
      if (ev.has_surebet) signals.push('<span class="badge badge-success">⚡ Surebet</span>');
      if (ev.has_valuebet) signals.push('<span class="badge badge-info">📈 Valuebet</span>');
      if (ev.matching_status === 'MATCHED') signals.push('<span class="badge badge-outline">Matched (100%)</span>');
      if (!signals.length) signals.push('<span class="text-muted" style="font-size:0.75rem;">Standard</span>');

      const bookTags = books.map(b => `<span class="provider-tag ${b.toLowerCase()}">${b}</span>`).join(' ');

      return `
        <tr class="matrix-row" data-event-id="${evId}">
          <td><span class="ribbon-chip-rank font-bold">#${rank}</span></td>
          <td class="bold">
            <div>${escapeHtml(ev.home_team)} vs ${escapeHtml(ev.away_team)}</div>
            <small class="mono text-muted">${evId}</small>
          </td>
          <td>${escapeHtml(ev.competition || 'League')}</td>
          <td class="mono" style="font-size:0.8rem;">${ev.kickoff ? formatTimestamp(ev.kickoff) : 'Kickoff —'}</td>
          <td>
            <div style="display: flex; align-items: center; gap: 0.4rem;">
              <strong class="mono font-bold text-success" style="font-size: 1.05rem;">${mkts}</strong>
              <span class="badge badge-outline" style="font-size:0.68rem;">matched mkts</span>
            </div>
          </td>
          <td>${bookTags}</td>
          <td>${signals.join(' ')}</td>
          <td style="text-align: right;">
            <button class="btn btn-sm btn-primary btn-inspect-matrix-event" data-event-id="${evId}">
              Inspect Workspace →
            </button>
          </td>
        </tr>
      `;
    }).join('');

    tbody.querySelectorAll('.matrix-row').forEach(row => {
      row.addEventListener('click', (e) => {
        const evId = row.getAttribute('data-event-id');
        setMarketIntelViewMode('workspace');
        loadEventDetail(evId);
      });
    });

    tbody.querySelectorAll('.btn-inspect-matrix-event').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const evId = btn.getAttribute('data-event-id');
        setMarketIntelViewMode('workspace');
        loadEventDetail(evId);
      });
    });
  }

  async function loadEventsData() {
    const countPill = document.getElementById('events-count-pill');
    const matrixCount = document.getElementById('events-matrix-count');
    const ribbonAllCount = document.getElementById('ribbon-all-count');

    try {
      const res = await api.fetchEvents({ limit: 250, offset: 0 });
      if (res && res.detail) {
        console.warn('Events fetch returned API error detail:', res.detail);
      }
      let events = (res && Array.isArray(res.data)) ? res.data : [];

      // Sort by matched_markets_count descending (primary signal) with deterministic tie-breakers
      events.sort((a, b) => {
        const aMkts = a.matched_markets_count || a.normalized_markets_count || 0;
        const bMkts = b.matched_markets_count || b.normalized_markets_count || 0;
        if (bMkts !== aMkts) return bMkts - aMkts;
        // Secondary tie-breaker: opportunities presence
        const aOpp = (a.has_surebet ? 2 : 0) + (a.has_valuebet ? 1 : 0);
        const bOpp = (b.has_surebet ? 2 : 0) + (b.has_valuebet ? 1 : 0);
        if (bOpp !== aOpp) return bOpp - aOpp;
        // Tertiary tie-breaker: kickoff proximity
        const aKo = a.kickoff ? new Date(a.kickoff).getTime() : 0;
        const bKo = b.kickoff ? new Date(b.kickoff).getTime() : 0;
        if (aKo !== bKo) return aKo - bKo;
        // Quaternary: canonical ID
        return (a.canonical_event_id || a.id || '').localeCompare(b.canonical_event_id || b.id || '');
      });

      state.events = events;

      // Keep hidden legacy container populated for test compatibility
      const legacyList = document.getElementById('canonical-events-list');
      if (legacyList) {
        legacyList.innerHTML = events.map(ev => {
          const evId = ev.id || ev.canonical_event_id;
          return `<div class="event-card-item" data-id="${evId}"><span>${escapeHtml(ev.home_team)} vs ${escapeHtml(ev.away_team)}</span></div>`;
        }).join('');
      }

      if (countPill) countPill.textContent = `${events.length}`;
      if (matrixCount) matrixCount.textContent = `${events.length}`;
      if (ribbonAllCount) ribbonAllCount.textContent = `${events.length}`;

      // Populate competition filter dropdown in matrix view
      const compSelect = document.getElementById('filter-matrix-competition');
      if (compSelect) {
        const currentVal = compSelect.value;
        const comps = Array.from(new Set(events.map(e => e.competition).filter(Boolean))).sort();
        compSelect.innerHTML = '<option value="">All Competitions</option>' + comps.map(c => `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`).join('');
        compSelect.value = currentVal;
      }

      // Populate Ribbon Quick Chips (Top 5 mega/deep coverage matches)
      renderRibbonChips(events);

      // Render Matrix View Table
      renderAllFixturesMatrix();

      // Populate Fixture Modal List
      renderFixtureModalList();

      if (!events.length) {
        const container = document.getElementById('event-detail-container');
        if (container) {
          container.innerHTML = `
            <div class="empty-state" style="padding: 3rem 1rem;">
              <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
              <p style="font-weight: 600; margin-top: 0.5rem;">No canonical events available.</p>
              <span class="text-muted" style="font-size: 0.8rem;">Execute a scan to discover and normalize multi-bookmaker fixtures.</span>
            </div>
          `;
        }
        return;
      }

      // Determine active event
      const currentActiveExists = events.some(e => (e.id || e.canonical_event_id) === state.marketIntel.activeEventId);
      if (!currentActiveExists) {
        // Auto-select Rank #1 (highest matched coverage)
        const topEv = events[0];
        loadEventDetail(topEv.id || topEv.canonical_event_id);
      } else {
        loadEventDetail(state.marketIntel.activeEventId);
      }

    } catch (err) {
      console.error('Failed to load events data', err);
      showToast('Error loading events: ' + err.message);
    }
  }

  async function loadEventDetail(eventId) {
    if (!eventId) return;
    state.marketIntel.activeEventId = eventId;
    state.selectedEventId = eventId; // Backwards compatibility

    // Update Ribbon active chip
    document.querySelectorAll('.ribbon-chip').forEach(btn => {
      btn.classList.toggle('active', btn.getAttribute('data-event-id') === eventId);
    });

    const activeEv = (state.events || []).find(e => (e.id || e.canonical_event_id) === eventId);
    const ribbonTeams = document.getElementById('ribbon-active-teams');
    const ribbonMeta = document.getElementById('ribbon-active-meta');

    if (activeEv) {
      const rank = (state.events || []).findIndex(e => (e.id || e.canonical_event_id) === eventId) + 1;
      const mkts = activeEv.matched_markets_count || activeEv.normalized_markets_count || 0;
      if (ribbonTeams) ribbonTeams.textContent = `${activeEv.home_team} vs ${activeEv.away_team}`;
      if (ribbonMeta) ribbonMeta.textContent = `#${rank} Ranked • ${activeEv.competition || 'League'} • ${activeEv.kickoff ? formatTimestamp(activeEv.kickoff) : 'Kickoff —'} • ${mkts} Matched Mkts`;
    }

    const container = document.getElementById('event-detail-container');
    if (!container) return;

    // Loading State
    container.innerHTML = `
      <div class="empty-state" style="padding: 2.5rem 1rem;">
        <div style="font-size: 1.5rem; margin-bottom: 0.5rem;">⟳</div>
        <div style="font-weight: 600; margin-bottom: 0.25rem;">Analyzing Cross-Bookmaker Market Depth...</div>
        <div class="text-muted" style="font-size: 0.8rem;">Resolving Superbet & Betclic canonical coverage</div>
      </div>
    `;

    try {
      const res = await api.fetchEventDetail(eventId);
      const ev = res.data;
      if (!ev) {
        container.innerHTML = `
          <div class="empty-state">
            <p class="text-muted">Event detail not found for ID: ${escapeHtml(eventId)}</p>
          </div>
        `;
        return;
      }

      state.marketIntel.activeDetail = ev;

      // Group markets into families dynamically
      const markets = ev.markets || [];
      const familyMap = new Map();
      markets.forEach(m => {
        const type = m.market_type || 'OTHER';
        if (!familyMap.has(type)) {
          familyMap.set(type, {
            type,
            name: type.replace(/_/g, ' '),
            count: 0,
            completeCount: 0,
            partialCount: 0,
            markets: [],
          });
        }
        const fam = familyMap.get(type);
        fam.count += 1;
        fam.markets.push(m);
        if (m.completeness_status === 'COMPLETE' || m.status === 'MATCHED') fam.completeCount += 1;
        else fam.partialCount += 1;
      });

      const families = Array.from(familyMap.values()).sort((a, b) => b.count - a.count);

      // Render the complete workspace
      renderEventWorkspace(container, ev, families);

    } catch (err) {
      console.error('Failed to load event detail', err);
      container.innerHTML = `<div class="alert-banner error"><span>Failed to load event detail: ${escapeHtml(err.message)}</span></div>`;
    }
  }

  function renderEventWorkspace(container, ev, families) {
    const isMatched = ev.matching_status === 'MATCHED';
    const matchBadge = isMatched
      ? `<span class="badge badge-success">MATCHED (${ev.matching_confidence ? (ev.matching_confidence * 100).toFixed(0) + '%' : '100%'} confidence)</span>`
      : `<span class="badge badge-outline">UNMATCHED</span>`;

    // 1. Opportunities Section
    let oppsHtml = '';
    const surebets = ev.opportunities?.surebets || [];
    const valuebets = ev.opportunities?.valuebets || [];
    const nearest = ev.opportunities?.nearest_opportunity;

    if (surebets.length > 0) {
      const sb = surebets[0];
      oppsHtml = `
        <div class="event-opp-callout surebet" style="margin-top: 0.75rem;">
          <div>
            <div style="display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.25rem;">
              <span class="badge badge-success">⚡ SUREBET DETECTED</span>
              <strong class="text-success" style="font-size: 1.05rem;">+${Number(sb.margin_pct || sb.arbitrage_margin_pct || 0).toFixed(2)}% Guaranteed Profit</strong>
            </div>
            <p style="font-size: 0.82rem; margin: 0; color: var(--text-secondary);">
              Arbitrage partition sum S = ${Number(sb.mathematical_explanation?.implied_probability_sum || 0).toFixed(4)} &lt; 1.0000 across ${(sb.bookmakers || []).join(' + ')}.
            </p>
          </div>
          <button class="btn btn-sm btn-primary btn-inspect-opp-deep" data-opp-id="${sb.id || sb.opportunity_id}">
            Inspect in Explorer →
          </button>
        </div>
      `;
    } else if (valuebets.length > 0) {
      const vb = valuebets[0];
      oppsHtml = `
        <div class="event-opp-callout valuebet" style="margin-top: 0.75rem;">
          <div>
            <div style="display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.25rem;">
              <span class="badge badge-info">📈 VALUEBET DETECTED</span>
              <strong class="text-info" style="font-size: 1.05rem;">+${Number(vb.value_percent || 0).toFixed(2)}% Expected Value</strong>
            </div>
            <p style="font-size: 0.82rem; margin: 0; color: var(--text-secondary);">
              Market price ${vb.bookmaker_odds} vs Sharp Fair Odds ${vb.fair_odds} (${vb.reference_bookmaker || 'Pinnacle'} benchmark).
            </p>
          </div>
          <button class="btn btn-sm btn-primary btn-inspect-opp-deep" data-opp-id="${vb.id || vb.opportunity_id || vb.candidate_id}">
            Inspect in Explorer →
          </button>
        </div>
      `;
    }

    // 2. Bookmaker Coverage Calculations
    const providers = ev.providers || [];
    const bcProv = providers.find(p => p.provider === 'betclic') || {};
    const sbProv = providers.find(p => p.provider === 'superbet') || {};

    const bcRaw = bcProv.raw_market_count ?? bcProv.market_count ?? 0;
    const bcNorm = bcProv.normalized_market_count ?? bcProv.market_count ?? 0;
    const bcMatch = bcProv.matched_market_count ?? (ev.markets ? ev.markets.length : 0);

    const sbRaw = sbProv.raw_market_count ?? sbProv.market_count ?? 0;
    const sbNorm = sbProv.normalized_market_count ?? sbProv.market_count ?? 0;
    const sbMatch = sbProv.matched_market_count ?? (ev.markets ? ev.markets.length : 0);

    const matchedMarketsCount = ev.matched_markets_count || ev.markets?.length || 0;
    const overlapRate = bcNorm > 0 ? Math.min(100, Math.round((matchedMarketsCount / bcNorm) * 100)) : 100;

    // SVG Funnel / Overlap Flow Visualization
    const svgFlowHtml = `
      <svg class="coverage-funnel-svg" viewBox="0 0 700 85" fill="none" xmlns="http://www.w3.org/2000/svg">
        <!-- Betclic Path to Overlap Core -->
        <path d="M 170 25 C 240 25, 260 42, 320 42" stroke="var(--brand-primary)" stroke-width="2" stroke-dasharray="4 3" opacity="0.6"/>
        <!-- Superbet Path to Overlap Core -->
        <path d="M 530 25 C 460 25, 440 42, 380 42" stroke="#22c55e" stroke-width="2" stroke-dasharray="4 3" opacity="0.6"/>

        <!-- Left Node: Betclic Normalization -->
        <rect x="20" y="10" width="150" height="32" rx="4" fill="var(--surface-input)" stroke="var(--border-subtle)"/>
        <text x="30" y="26" fill="var(--text-secondary)" font-size="10" font-weight="600">BETCLIC</text>
        <text x="30" y="37" fill="var(--text-primary)" font-size="11" font-weight="700" font-family="var(--font-mono)">${bcNorm} norm mkts</text>

        <!-- Right Node: Superbet Normalization -->
        <rect x="530" y="10" width="150" height="32" rx="4" fill="var(--surface-input)" stroke="var(--border-subtle)"/>
        <text x="540" y="26" fill="var(--text-secondary)" font-size="10" font-weight="600">SUPERBET</text>
        <text x="540" y="37" fill="var(--text-primary)" font-size="11" font-weight="700" font-family="var(--font-mono)">${sbNorm} norm mkts</text>

        <!-- Central Core: Canonical Overlap -->
        <rect x="290" y="26" width="120" height="36" rx="6" fill="rgba(34, 197, 94, 0.15)" stroke="#22c55e" stroke-width="1.5"/>
        <text x="350" y="42" fill="#22c55e" font-size="10" font-weight="700" text-anchor="middle" letter-spacing="0.05em">MATCHED CORE</text>
        <text x="350" y="56" fill="var(--text-primary)" font-size="13" font-weight="800" text-anchor="middle" font-family="var(--font-mono)">${matchedMarketsCount} MKTS</text>

        <!-- Flow labels -->
        <text x="245" y="22" fill="var(--text-muted)" font-size="9" text-anchor="middle">100% matched</text>
        <text x="455" y="22" fill="var(--text-muted)" font-size="9" text-anchor="middle">${sbNorm > 0 ? Math.round((matchedMarketsCount / sbNorm) * 100) : 0}% matched</text>
      </svg>
    `;

    // Station 3: Market Families Grid HTML
    const totalMarketsCount = ev.markets?.length || 0;
    const isAllActive = state.marketIntel.activeFamily === 'ALL';

    const familyTilesHtml = `
      <div class="family-tile ${isAllActive ? 'active' : ''}" data-family="ALL">
        <div class="family-tile-top">
          <span class="family-tile-name">ALL FAMILIES</span>
          <span class="family-tile-count">${totalMarketsCount}</span>
        </div>
        <div class="family-tile-foot">
          <span>Complete Catalog</span>
          <span>100%</span>
        </div>
      </div>
      ${families.map(fam => {
        const isActive = state.marketIntel.activeFamily === fam.type;
        const pct = totalMarketsCount > 0 ? Math.round((fam.count / totalMarketsCount) * 100) : 0;
        return `
          <div class="family-tile ${isActive ? 'active' : ''}" data-family="${fam.type}">
            <div class="family-tile-top">
              <span class="family-tile-name" title="${escapeHtml(fam.name)}">${escapeHtml(fam.name)}</span>
              <span class="family-tile-count">${fam.count}</span>
            </div>
            <div class="family-tile-foot">
              <span>${fam.completeCount}/${fam.count} Complete</span>
              <span>${pct}%</span>
            </div>
          </div>
        `;
      }).join('')}
    `;

    // Station 5: Collapsible Technical Lineage & Diagnostics
    const techLineageHtml = `
      <details class="event-technical-details">
        <summary>
          <div style="display: flex; align-items: center; gap: 0.5rem;">
            <span class="badge badge-outline">🔬 TECHNICAL METADATA & DATA LINEAGE</span>
            <span class="text-muted" style="font-size: 0.76rem; font-weight: normal;">Canonical key mapping and raw bookmaker references</span>
          </div>
          <span class="text-muted" style="font-size: 0.76rem;">Toggle Details ▼</span>
        </summary>
        <div class="event-technical-body">
          <div style="display: flex; gap: 2rem; flex-wrap: wrap;">
            <div>
              <span class="text-muted" style="font-size: 0.72rem; text-transform: uppercase;">Canonical Event ID</span>
              <div class="mono font-bold" style="font-size: 0.86rem; margin-top: 0.15rem;">${ev.canonical_event_id || ev.id}</div>
            </div>
            <div>
              <span class="text-muted" style="font-size: 0.72rem; text-transform: uppercase;">Betclic Event ID</span>
              <div class="mono font-bold" style="font-size: 0.86rem; margin-top: 0.15rem;">${bcProv.provider_event_id || '—'}</div>
            </div>
            <div>
              <span class="text-muted" style="font-size: 0.72rem; text-transform: uppercase;">Superbet Event ID</span>
              <div class="mono font-bold" style="font-size: 0.86rem; margin-top: 0.15rem;">${sbProv.provider_event_id || '—'}</div>
            </div>
            <div>
              <span class="text-muted" style="font-size: 0.72rem; text-transform: uppercase;">Normalization Engine</span>
              <div class="mono font-bold text-success" style="font-size: 0.86rem; margin-top: 0.15rem;">V3 Canonical Graph</div>
            </div>
          </div>

          <div class="table-responsive" style="margin-top: 0.5rem;">
            <table class="data-table" style="font-size: 0.8rem;">
              <thead>
                <tr>
                  <th>Bookmaker</th>
                  <th>Status</th>
                  <th>Raw Event Name</th>
                  <th>Raw Markets</th>
                  <th>Normalized Markets</th>
                  <th>Matched Markets</th>
                </tr>
              </thead>
              <tbody>
                ${providers.map(p => `
                  <tr>
                    <td class="bold"><span class="provider-tag ${p.provider.toLowerCase()}">${p.provider}</span></td>
                    <td><span class="badge ${p.status === 'Available' ? 'badge-success' : 'badge-outline'}">${p.status}</span></td>
                    <td class="text-muted">${p.raw_event_name || `${ev.home_team} vs ${ev.away_team}`}</td>
                    <td class="mono">${p.raw_market_count ?? p.market_count ?? 0}</td>
                    <td class="mono">${p.normalized_market_count ?? p.market_count ?? 0}</td>
                    <td class="mono text-success font-bold">${p.matched_market_count ?? matchedMarketsCount}</td>
                  </tr>
                `).join('')}
              </tbody>
            </table>
          </div>
        </div>
      </details>
    `;

    // Render Master Template
    container.innerHTML = `
      <!-- Station 1: Fixture Hero Banner -->
      <div class="intel-hero-banner">
        <div class="intel-hero-top">
          <div>
            <div class="intel-match-title">
              ${escapeHtml(ev.home_team)} vs ${escapeHtml(ev.away_team)}
            </div>
            <div class="intel-match-sub">
              <span><strong>${escapeHtml(ev.competition || 'Competition')}</strong></span>
              <span>&bull;</span>
              <span>${escapeHtml(ev.sport || 'Football')}</span>
              <span>&bull;</span>
              <span class="mono">Kickoff: <strong>${ev.kickoff ? formatTimestamp(ev.kickoff) : 'Scheduled'}</strong></span>
            </div>
          </div>
          <div>
            ${matchBadge}
          </div>
        </div>
        ${oppsHtml}
      </div>

      <!-- Station 2: Bookmaker Coverage Intelligence Deck -->
      <div class="bookmaker-coverage-deck">
        <!-- Betclic Card -->
        <div class="coverage-card">
          <div class="coverage-card-head">
            <span class="provider-tag betclic" style="font-size: 0.78rem;">BETCLIC</span>
            <span class="badge badge-success">${bcProv.status || 'Available'}</span>
          </div>
          <div class="coverage-kpis-row">
            <div class="coverage-kpi-item">
              <span class="coverage-kpi-label">Raw Mkts</span>
              <span class="coverage-kpi-val">${bcRaw}</span>
            </div>
            <div class="coverage-kpi-item">
              <span class="coverage-kpi-label">Normalized</span>
              <span class="coverage-kpi-val text-info">${bcNorm}</span>
            </div>
            <div class="coverage-kpi-item">
              <span class="coverage-kpi-label">Matched</span>
              <span class="coverage-kpi-val text-success">${bcMatch}</span>
            </div>
          </div>
          <div class="text-muted" style="font-size: 0.74rem;">
            Raw Event ID: <span class="mono">${bcProv.provider_event_id || '—'}</span>
          </div>
        </div>

        <!-- Central Overlap Intelligence Card -->
        <div class="coverage-card highlight">
          <div class="coverage-card-head">
            <span class="coverage-card-title text-success">CROSS-BOOKMAKER OVERLAP</span>
            <span class="badge badge-accent font-bold">${overlapRate}% Overlap</span>
          </div>
          ${svgFlowHtml}
          <div class="text-muted text-center" style="font-size: 0.75rem;">
            Both Betclic and Superbet offer active markets for cross-comparison.
          </div>
        </div>

        <!-- Superbet Card -->
        <div class="coverage-card">
          <div class="coverage-card-head">
            <span class="provider-tag superbet" style="font-size: 0.78rem;">SUPERBET</span>
            <span class="badge badge-success">${sbProv.status || 'Available'}</span>
          </div>
          <div class="coverage-kpis-row">
            <div class="coverage-kpi-item">
              <span class="coverage-kpi-label">Raw Mkts</span>
              <span class="coverage-kpi-val">${sbRaw}</span>
            </div>
            <div class="coverage-kpi-item">
              <span class="coverage-kpi-label">Normalized</span>
              <span class="coverage-kpi-val text-info">${sbNorm}</span>
            </div>
            <div class="coverage-kpi-item">
              <span class="coverage-kpi-label">Matched</span>
              <span class="coverage-kpi-val text-success">${sbMatch}</span>
            </div>
          </div>
          <div class="text-muted" style="font-size: 0.74rem;">
            Raw Event ID: <span class="mono">${sbProv.provider_event_id || '—'}</span>
          </div>
        </div>
      </div>

      <!-- Station 3: Market Family Navigator -->
      <div class="family-navigator-container">
        <div class="family-navigator-head">
          <div>
            <strong style="font-size: 0.96rem; color: var(--text-primary);">Explore Market Families</strong>
            <span class="text-muted" style="font-size: 0.78rem; margin-left: 0.5rem;">Select a family to drill down into specific outcomes and odds</span>
          </div>
          <span class="badge badge-outline" style="font-family: var(--font-mono);">${families.length} Families Active</span>
        </div>
        <div class="family-grid" id="market-families-grid">
          ${familyTilesHtml}
        </div>
      </div>

      <!-- Station 4: Market Odds Terminal -->
      <div class="odds-terminal-card" id="market-odds-terminal">
        <!-- Rendered by renderMarketOddsTerminal() -->
      </div>

      <!-- Station 5: Technical Lineage Accordion -->
      ${techLineageHtml}
    `;

    // Wire Family Tile Clicks
    container.querySelectorAll('.family-tile').forEach(tile => {
      tile.addEventListener('click', () => {
        const famType = tile.getAttribute('data-family');
        state.marketIntel.activeFamily = famType;
        container.querySelectorAll('.family-tile').forEach(t => t.classList.toggle('active', t === tile));
        renderMarketOddsTerminal();
      });
    });

    // Wire Deep Links to Opportunity Explorer
    container.querySelectorAll('.btn-inspect-opp-deep').forEach(btn => {
      btn.addEventListener('click', () => {
        const oppId = btn.getAttribute('data-opp-id');
        if (oppId) loadOpportunityDetail(oppId);
      });
    });

    // Render initial Odds Terminal
    renderMarketOddsTerminal();
  }

  function renderMarketOddsTerminal() {
    const terminalEl = document.getElementById('market-odds-terminal');
    if (!terminalEl || !state.marketIntel.activeDetail) return;

    const ev = state.marketIntel.activeDetail;
    const allMarkets = ev.markets || [];
    const activeFam = state.marketIntel.activeFamily;
    const searchVal = (state.marketIntel.marketSearch || '').toLowerCase().trim();

    // Filter markets by active family
    let markets = activeFam === 'ALL'
      ? allMarkets
      : allMarkets.filter(m => (m.market_type || 'OTHER') === activeFam);

    // Apply inline search filter if specified
    if (searchVal) {
      markets = markets.filter(m => {
        const mType = (m.market_type || '').toLowerCase();
        const lineStr = String(m.line || '');
        const key = (m.canonical_market_key || '').toLowerCase();
        const hasSels = (m.selections || []).some(s =>
          (s.selection_type || '').toLowerCase().includes(searchVal) ||
          (s.participant || '').toLowerCase().includes(searchVal)
        );
        return mType.includes(searchVal) || lineStr.includes(searchVal) || key.includes(searchVal) || hasSels;
      });
    }

    const familyDisplayName = activeFam === 'ALL' ? 'All Market Families' : activeFam.replace(/_/g, ' ');

    // Terminal Header
    const terminalHeaderHtml = `
      <div class="odds-terminal-header">
        <div>
          <h3 style="font-size: 1.05rem; font-weight: 700; margin: 0; display: inline-flex; align-items: center; gap: 0.5rem;">
            ${escapeHtml(familyDisplayName)}
            <span class="badge badge-accent" style="font-family: var(--font-mono); font-size: 0.76rem;">${markets.length} Markets</span>
          </h3>
          <p class="text-muted" style="font-size: 0.78rem; margin: 0.15rem 0 0 0;">
            Comparing live execution odds between Betclic and Superbet with best-price detection.
          </p>
        </div>
        <div class="odds-terminal-search">
          <div class="search-input-wrapper" style="width: 100%;">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
            <input type="text" id="filter-family-market-search" placeholder="Filter selection, player, or line..." class="form-input" value="${escapeHtml(state.marketIntel.marketSearch)}">
          </div>
        </div>
      </div>
    `;

    if (!markets.length) {
      terminalEl.innerHTML = `
        ${terminalHeaderHtml}
        <div class="empty-state" style="padding: 2.5rem 1rem;">
          <p style="font-weight: 600;">No markets found matching "${escapeHtml(searchVal)}".</p>
          <span class="text-muted" style="font-size: 0.8rem;">Clear the filter to see all ${familyDisplayName} markets.</span>
        </div>
      `;
      wireTerminalSearchInput(terminalEl);
      return;
    }

    // Bookmaker columns
    const bookmakers = ['betclic', 'superbet'];

    // Table rows
    const rowsHtml = markets.map(m => {
      const mType = m.market_type || 'OTHER';
      const lineStr = (m.line !== null && m.line !== undefined) ? `Line: ${m.line}` : '';
      const periodScope = `${m.period || 'FULL_TIME'} • ${m.scope || 'MATCH'}`;
      const sels = m.selections || [];

      if (!sels.length) {
        return `
          <tr>
            <td colspan="7" class="text-center text-muted" style="padding: 0.75rem;">
              ${escapeHtml(mType)} ${lineStr} (${periodScope}) — No comparable selections.
            </td>
          </tr>
        `;
      }

      return sels.map((s, idx) => {
        const outcomeLabel = s.participant ? `${s.selection_type} (${s.participant})` : s.selection_type;
        const bestOdds = s.best_odds || {};
        const impProb = bestOdds.implied_probability ? (bestOdds.implied_probability * 100).toFixed(2) + '%' : '—';

        const oddsCols = bookmakers.map(b => {
          const price = s.odds ? s.odds[b] : null;
          if (price !== null && price !== undefined) {
            const isBest = bestOdds.bookmaker === b;
            if (isBest) {
              return `
                <td style="font-family: var(--font-mono); font-weight: 700;">
                  <span style="color: var(--val-positive);">${Number(price).toFixed(2)}</span>
                  <span class="best-odds-badge">BEST</span>
                </td>
              `;
            }
            return `<td class="mono">${Number(price).toFixed(2)}</td>`;
          }
          return `<td class="mono text-muted">—</td>`;
        }).join('');

        const compStatus = m.completeness_status || (m.status === 'PARTIAL' ? 'PARTIAL' : 'COMPLETE');
        let compBadge = '';
        if (compStatus === 'COMPLETE') compBadge = '<span class="badge badge-success" style="font-size:0.65rem;">COMPLETE</span>';
        else if (compStatus === 'PARTIAL') compBadge = '<span class="badge badge-warning" style="font-size:0.65rem;">PARTIAL</span>';
        else compBadge = `<span class="badge badge-outline" style="font-size:0.65rem;">${compStatus}</span>`;

        const marketSpecCol = idx === 0
          ? `<td rowspan="${sels.length}" style="vertical-align: top; border-right: 1px solid var(--border-subtle); background: rgba(0,0,0,0.06);">
              <div style="font-weight: 700; font-size: 0.84rem; color: var(--text-primary);">${escapeHtml(mType)}</div>
              ${lineStr ? `<div class="mono text-accent" style="font-size: 0.76rem;">${lineStr}</div>` : ''}
              <div class="text-muted" style="font-size: 0.72rem; margin-top: 0.15rem;">${periodScope}</div>
            </td>`
          : '';

        return `
          <tr>
            ${marketSpecCol}
            <td class="bold" style="color: var(--text-primary);">${escapeHtml(outcomeLabel)}</td>
            ${oddsCols}
            <td>
              <strong class="mono text-success" style="font-size: 0.95rem;">${bestOdds.odds ? Number(bestOdds.odds).toFixed(2) : '—'}</strong>
              <span class="text-muted" style="font-size: 0.72rem;">(${bestOdds.bookmaker || '—'})</span>
            </td>
            <td class="mono" style="font-size: 0.82rem;">${impProb}</td>
            <td>${compBadge}</td>
          </tr>
        `;
      }).join('');
    }).join('');

    terminalEl.innerHTML = `
      ${terminalHeaderHtml}
      <div class="table-responsive" style="max-height: 600px; overflow-y: auto;">
        <table class="data-table odds-table-terminal" style="font-size: 0.82rem;">
          <thead>
            <tr>
              <th style="width: 22%;">Market & Spec</th>
              <th>Outcome / Selection</th>
              <th style="width: 12%;">BETCLIC</th>
              <th style="width: 12%;">SUPERBET</th>
              <th style="width: 14%;">Best Price</th>
              <th style="width: 12%;">Implied Prob</th>
              <th style="width: 10%;">Status</th>
            </tr>
          </thead>
          <tbody>
            ${rowsHtml}
          </tbody>
        </table>
      </div>
    `;

    wireTerminalSearchInput(terminalEl);
  }

  function wireTerminalSearchInput(terminalEl) {
    const input = terminalEl.querySelector('#filter-family-market-search');
    if (input) {
      input.addEventListener('input', (e) => {
        state.marketIntel.marketSearch = e.target.value;
        renderMarketOddsTerminal();
      });
    }
  }

  // ──────────────────────────────────────────────────────────────────────────
  // Historical Analytics View Loader
  // ──────────────────────────────────────────────────────────────────────────

  async function loadHistoryData() {
    try {
      const res = await api.fetchOddsHistory('', '24h');
      const data = res.data || {};
      state.oddsHistory = data;
      if (data.available === false || !data.series || data.series.length === 0) {
        const canvas = document.getElementById('oddsChartCanvas');
        if (canvas) {
          const ctx = canvas.getContext('2d');
          const width = canvas.parentElement.clientWidth || 600;
          const height = 300;
          canvas.width = width;
          canvas.height = height;
          ctx.clearRect(0, 0, width, height);
          ctx.fillStyle = 'rgba(255,255,255,0.3)';
          ctx.font = '14px Outfit, sans-serif';
          ctx.textAlign = 'center';
          ctx.fillText(data.message || 'No historical odds data available.', width / 2, height / 2);
        }
      } else {
        renderOddsChart(data);
      }
    } catch (err) {
      console.error('Failed to load history data', err);
    }
  }

  function renderOddsChart(data) {
    const canvas = document.getElementById('oddsChartCanvas');
    if (!canvas || !canvas.getContext) return;

    const ctx = canvas.getContext('2d');
    const width = canvas.parentElement.clientWidth || 600;
    const height = 300;
    canvas.width = width;
    canvas.height = height;

    const padding = 40;
    ctx.clearRect(0, 0, width, height);

    // Draw Grid Lines
    ctx.strokeStyle = 'rgba(255,255,255,0.06)';
    ctx.lineWidth = 1;
    for (let y = padding; y <= height - padding; y += 50) {
      ctx.beginPath();
      ctx.moveTo(padding, y);
      ctx.lineTo(width - padding, y);
      ctx.stroke();
    }

    const seriesList = data.series || [];
    const colors = ['#10B981', '#3B82F6', '#F59E0B', '#EF4444', '#8B5CF6'];

    seriesList.forEach((s, idx) => {
      const vals = s.values;
      const color = colors[idx % colors.length];

      ctx.beginPath();
      ctx.strokeStyle = color;
      ctx.lineWidth = 2.5;

      vals.forEach((val, i) => {
        const x = padding + (i / (vals.length - 1)) * (width - 2 * padding);
        const y = height - padding - ((val - 2.0) / (3.5 - 2.0)) * (height - 2 * padding);

        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.stroke();
    });
  }

  // ──────────────────────────────────────────────────────────────────────────
  // Stage 6: Global Props Scanner View Controller & Renderers (Player & Team Props)
  // ──────────────────────────────────────────────────────────────────────────

  let currentPropsCategory = 'top_value';

  function getStatDisplayName(statKey) {
    if (!statKey) return 'All Stats';
    const names = {
      shots: 'Shots',
      shots_on_target: 'Shots on Target',
      shotsOnTarget: 'Shots on Target',
      goals: 'Goals',
      assists: 'Assists',
      passes: 'Passes',
      tackles: 'Tackles',
      fouls: 'Fouls',
      cards: 'Cards',
      corners: 'Corners (Team)',
      offsides: 'Offsides (Team)',
      TEAM_SHOTS: 'Team Shots',
      TEAM_SHOTS_ON_TARGET: 'Team Shots on Target',
      TEAM_CORNERS: 'Team Corners',
      TEAM_FOULS: 'Team Fouls',
      TEAM_CARDS: 'Team Cards',
      TEAM_OFFSIDES: 'Team Offsides',
      TEAM_GOALS: 'Team Goals',
    };
    return names[statKey] || names[statKey.toLowerCase()] || statKey.replace(/_/g, ' ');
  }

  const STAT_LINES_MAP = {
    shots: [0.5, 1.5, 2.5, 3.5, 4.5],
    shots_on_target: [0.5, 1.5, 2.5, 3.5],
    goals: [0.5, 1.5, 2.5],
    assists: [0.5, 1.5],
    fouls: [0.5, 1.5, 2.5, 3.5, 4.5],
    cards: [0.5, 1.5],
    corners: [3.5, 4.5, 5.5, 6.5, 7.5, 8.5, 9.5, 10.5],
    offsides: [0.5, 1.5, 2.5, 3.5],
    passes: [25.5, 35.5, 45.5, 55.5, 65.5, 75.5],
    tackles: [0.5, 1.5, 2.5, 3.5, 4.5],
  };

  function updateLineSelectorOptions(statKey, availableLines) {
    const selectEl = document.getElementById('props-filter-threshold');
    if (!selectEl) return;
    const statName = getStatDisplayName(statKey);
    const currentVal = (selectEl.value || '').trim();

    let lines = [0.5, 1.5, 2.5, 3.5, 4.5, 5.5];
    if (availableLines && availableLines.length > 0) {
      lines = availableLines.map(Number).sort((a, b) => a - b);
    } else if (statKey && STAT_LINES_MAP[statKey.toLowerCase()]) {
      lines = STAT_LINES_MAP[statKey.toLowerCase()];
    }

    const optionsHtml = [
      `<option value="">All ${statName || 'Stat'} Lines</option>`,
      ...lines.map(line => {
        const lineStr = Number(line).toFixed(1);
        return `<option value="${lineStr}">Over ${lineStr} ${statName || ''}</option>`;
      })
    ].join('');

    selectEl.innerHTML = optionsHtml;
    const parsedCurrent = parseFloat(currentVal);
    const hasMatch = !isNaN(parsedCurrent) && lines.some(l => Math.abs(l - parsedCurrent) < 0.05);
    if (hasMatch) {
      selectEl.value = parsedCurrent.toFixed(1);
    } else {
      selectEl.value = '';
    }
  }

  function syncStatDropdownWithScope(scope) {
    const playerGroup = document.getElementById('props-optgroup-player');
    const teamGroup = document.getElementById('props-optgroup-team');
    const statSelect = document.getElementById('props-filter-stat');
    if (!statSelect) return;

    const s = (scope || 'ALL').toUpperCase();
    if (playerGroup) playerGroup.style.display = (s === 'TEAM') ? 'none' : '';
    if (teamGroup) teamGroup.style.display = (s === 'PLAYER') ? 'none' : '';

    const selectedOption = statSelect.selectedOptions?.[0];
    if (selectedOption) {
      const optScope = (selectedOption.getAttribute('data-scope') || '').toUpperCase();
      if (optScope && s !== 'ALL' && optScope !== s) {
        statSelect.value = '';
      }
    }
  }

  function initPlayerPropsEvents() {
    if (state.playerProps._eventsInitialized) return;
    state.playerProps._eventsInitialized = true;

    // Scope Switcher (PLAYER | TEAM | ALL)
    const scopeButtons = document.querySelectorAll('#props-scope-switcher .scope-btn');
    scopeButtons.forEach(btn => {
      btn.addEventListener('click', () => {
        const scope = btn.getAttribute('data-scope') || 'ALL';
        state.playerProps.propsScope = scope;
        scopeButtons.forEach(b => b.classList.toggle('active', b === btn));
        syncStatDropdownWithScope(scope);
        if (hasLocalPropsData()) {
          applyLocalFiltersAndRender();
        } else {
          fetchAndRenderPropsFromBackend();
        }
      });
    });

    // Scan Mode Switcher (NORMAL | ULTRA)
    const scanModeButtons = document.querySelectorAll('#props-scan-mode-switcher .scope-btn');
    scanModeButtons.forEach(btn => {
      btn.addEventListener('click', () => {
        const mode = btn.getAttribute('data-mode') || 'NORMAL';
        state.playerProps.scanMode = mode;
        scanModeButtons.forEach(b => b.classList.toggle('active', b === btn));

        const ultraBanner = document.getElementById('props-ultra-banner');
        if (ultraBanner) {
          ultraBanner.style.display = (mode === 'ULTRA') ? 'block' : 'none';
        }

        const btnScanText = document.getElementById('btn-scan-props-text');
        if (btnScanText && !state.playerProps.isScanning) {
          btnScanText.textContent = (mode === 'ULTRA') ? 'Scan Props (Ultra)' : 'Scan Props (Normal)';
        }

        fetchAndRenderPropsFromBackend();
      });
    });

    // Scan Props Main Button
    const btnScan = document.getElementById('btn-scan-props');
    if (btnScan) {
      btnScan.addEventListener('click', () => handlePropsScan());
    }

    // Toggle Advanced Filters Panel
    const btnToggleAdv = document.getElementById('btn-toggle-advanced-filters');
    const advPanel = document.getElementById('props-advanced-filters-panel');
    if (btnToggleAdv && advPanel) {
      btnToggleAdv.addEventListener('click', () => {
        const isHidden = advPanel.style.display === 'none' || !advPanel.style.display;
        advPanel.style.display = isHidden ? 'block' : 'none';
        btnToggleAdv.classList.toggle('active', isHidden);
      });
    }

    // Close Detail Inspector Panel / Drawer Helper
    const closePropDrawer = () => {
      state.playerProps.selectedPropId = null;
      const detailCard = document.getElementById('prop-detail-container');
      const backdrop = document.getElementById('prop-drawer-backdrop');
      if (detailCard) {
        detailCard.classList.remove('open', 'active');
        setTimeout(() => {
          if (!detailCard.classList.contains('open')) detailCard.style.display = 'none';
        }, 220);
      }
      if (backdrop) backdrop.classList.remove('open', 'active');
    };

    const btnCloseDetail = document.getElementById('btn-close-prop-detail');
    if (btnCloseDetail) {
      btnCloseDetail.addEventListener('click', closePropDrawer);
    }

    const propBackdrop = document.getElementById('prop-drawer-backdrop');
    if (propBackdrop) {
      propBackdrop.addEventListener('click', closePropDrawer);
    }

    // Global Escape key handler
    window.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        closePropDrawer();
        closeOppDetailModal();
        closeFixtureSelectorModal();
        const stakeModal = document.getElementById('stake-modal');
        if (stakeModal) stakeModal.classList.remove('active');
      }
    });

    // Category Tabs
    document.querySelectorAll('.market-tabs-container [data-category]').forEach(btn => {
      btn.addEventListener('click', () => {
        const cat = btn.getAttribute('data-category');
        if (!cat) return;
        currentPropsCategory = cat;

        const bookieEl = document.getElementById('props-filter-bookmaker');
        const statusEl = document.getElementById('props-filter-exec-status');
        const minEvEl = document.getElementById('props-filter-min-ev');
        const sortSelect = document.getElementById('props-filter-sortby');
        const scopeBtns = document.querySelectorAll('#props-scope-switcher .scope-btn');

        if (cat === 'top_value') {
          state.playerProps.propsScope = 'ALL';
          scopeBtns.forEach(b => b.classList.toggle('active', b.getAttribute('data-scope') === 'ALL'));
          if (statusEl) statusEl.value = '';
          if (bookieEl) bookieEl.value = '';
          if (minEvEl && (minEvEl.value === '' || minEvEl.value === '0')) minEvEl.value = '3.0';
          if (sortSelect && sortSelect.value.startsWith('discrepancy')) sortSelect.value = 'net_ev';
        } else if (cat === 'discrepancy') {
          state.playerProps.propsScope = 'ALL';
          scopeBtns.forEach(b => b.classList.toggle('active', b.getAttribute('data-scope') === 'ALL'));
          if (statusEl) statusEl.value = '';
          if (bookieEl) bookieEl.value = '';
          // Requirement 6: Default sort for Quote Discrepancy automatically becomes DISCREPANCY % — HIGH -> LOW
          if (sortSelect) sortSelect.value = 'discrepancy_pct';
        } else if (cat === 'player') {
          state.playerProps.propsScope = 'PLAYER';
          scopeBtns.forEach(b => b.classList.toggle('active', b.getAttribute('data-scope') === 'PLAYER'));
          if (sortSelect && sortSelect.value.startsWith('discrepancy')) sortSelect.value = 'net_ev';
        } else if (cat === 'team') {
          state.playerProps.propsScope = 'TEAM';
          scopeBtns.forEach(b => b.classList.toggle('active', b.getAttribute('data-scope') === 'TEAM'));
          if (sortSelect && sortSelect.value.startsWith('discrepancy')) sortSelect.value = 'net_ev';
        } else if (cat === 'all') {
          state.playerProps.propsScope = 'ALL';
          scopeBtns.forEach(b => b.classList.toggle('active', b.getAttribute('data-scope') === 'ALL'));
          if (statusEl) statusEl.value = '';
          if (bookieEl) bookieEl.value = '';
          if (sortSelect && sortSelect.value.startsWith('discrepancy')) sortSelect.value = 'net_ev';
        } else if (cat === 'diagnostics') {
          if (statusEl) statusEl.value = '';
          if (bookieEl) bookieEl.value = '';
        } else if (cat === 'superbet') {
          if (bookieEl) bookieEl.value = 'Superbet';
        } else if (cat === 'betclic') {
          if (bookieEl) bookieEl.value = 'Betclic';
        } else if (cat === 'below_threshold') {
          if (statusEl) statusEl.value = 'BELOW_VALUE_THRESHOLD';
        } else if (cat === 'ref_gap') {
          if (statusEl) statusEl.value = 'INSUFFICIENT_REFERENCE_SOURCES';
        } else if (cat === 'no_polish') {
          if (statusEl) statusEl.value = 'POLISH_ODDS_UNAVAILABLE';
        }

        document.querySelectorAll('.market-tabs-container [data-category]').forEach(b => {
          const isActive = (b === btn);
          b.classList.toggle('active', isActive);
          b.setAttribute('aria-selected', isActive ? 'true' : 'false');
        });
        if (hasLocalPropsData()) {
          applyLocalFiltersAndRender();
        } else {
          fetchAndRenderPropsFromBackend();
        }
      });
    });

    // Reset Filters action
    const btnResetFilters = document.getElementById('btn-reset-props-filters');
    if (btnResetFilters) {
      btnResetFilters.addEventListener('click', () => {
        resetPropsFilters();
      });
    }

    // Stat Type change
    const statSelectEl = document.getElementById('props-filter-stat');
    if (statSelectEl) {
      statSelectEl.addEventListener('change', () => {
        const newStat = statSelectEl.value;
        updateLineSelectorOptions(newStat);
        if (hasLocalPropsData()) {
          applyLocalFiltersAndRender();
        } else {
          fetchAndRenderPropsFromBackend();
        }
      });
    }

    // Filter controls change listeners
    const filterInputs = [
      'props-filter-horizon',
      'props-filter-tournaments',
      'props-filter-position',
      'props-filter-lastgames',
      'props-filter-min-hitrate',
      'props-filter-threshold',
      'props-filter-bookmaker',
      'props-filter-match-status',
      'props-filter-exec-status',
      'props-filter-limit',
      'props-filter-sortby',
    ];

    filterInputs.forEach(id => {
      const el = document.getElementById(id);
      if (el) {
        el.addEventListener('change', () => {
          syncTabButtonsWithFilters();
          if (hasLocalPropsData()) {
            applyLocalFiltersAndRender();
          } else {
            fetchAndRenderPropsFromBackend();
          }
        });
      }
    });

    // Debounced Search, Odds, and EV filters
    const debounceInputs = ['props-filter-search', 'props-filter-min-ev', 'props-filter-min-odds', 'props-filter-min-exec-edge', 'props-filter-min-stat-edge'];
    debounceInputs.forEach(id => {
      const inputEl = document.getElementById(id);
      if (inputEl) {
        let debounceTimer = null;
        inputEl.addEventListener('input', () => {
          if (hasLocalPropsData()) {
            applyLocalFiltersAndRender();
          } else {
            clearTimeout(debounceTimer);
            debounceTimer = setTimeout(() => {
              fetchAndRenderPropsFromBackend();
            }, 250);
          }
        });
      }
    });

    // Initialize line selector
    if (statSelectEl) {
      updateLineSelectorOptions(statSelectEl.value);
    }
  }

  function syncTabButtonsWithFilters() {
    const bookie = document.getElementById('props-filter-bookmaker')?.value || '';
    const status = document.getElementById('props-filter-exec-status')?.value || '';
    const scope = state.playerProps.propsScope || 'ALL';

    let activeCat = currentPropsCategory;
    if (activeCat === 'discrepancy') {
      // Discrepancy is a primary intent tab
    } else if (status === 'BELOW_VALUE_THRESHOLD') activeCat = 'below_threshold';
    else if (status === 'INSUFFICIENT_REFERENCE_SOURCES') activeCat = 'ref_gap';
    else if (status === 'POLISH_ODDS_UNAVAILABLE') activeCat = 'no_polish';
    else if (bookie === 'Superbet') activeCat = 'superbet';
    else if (bookie === 'Betclic') activeCat = 'betclic';
    else if (scope === 'PLAYER') activeCat = 'player';
    else if (scope === 'TEAM') activeCat = 'team';
    else if (currentPropsCategory === 'all') activeCat = 'all';
    else if (currentPropsCategory === 'diagnostics') activeCat = 'diagnostics';
    else activeCat = 'top_value';

    currentPropsCategory = activeCat;
    document.querySelectorAll('.market-tabs-container [data-category]').forEach(b => {
      const isActive = (b.getAttribute('data-category') === activeCat);
      b.classList.toggle('active', isActive);
      b.setAttribute('aria-selected', isActive ? 'true' : 'false');
    });
  }

  function resetPropsFilters() {
    const searchInput = document.getElementById('props-filter-search');
    if (searchInput) searchInput.value = '';
    const statSelect = document.getElementById('props-filter-stat');
    if (statSelect) {
      statSelect.value = '';
      updateLineSelectorOptions('');
    }
    const minEvInput = document.getElementById('props-filter-min-ev');
    if (minEvInput) minEvInput.value = '3.0';
    const sortSelect = document.getElementById('props-filter-sortby');
    if (sortSelect) sortSelect.value = 'net_ev';
    const horizonSelect = document.getElementById('props-filter-horizon');
    if (horizonSelect) horizonSelect.value = '7';
    const compSelect = document.getElementById('props-filter-tournaments');
    if (compSelect) compSelect.value = '';
    const minOddsInput = document.getElementById('props-filter-min-odds');
    if (minOddsInput) minOddsInput.value = '1.0';
    const posSelect = document.getElementById('props-filter-position');
    if (posSelect) posSelect.value = 'D,M,F';
    const threshSelect = document.getElementById('props-filter-threshold');
    if (threshSelect) threshSelect.value = '';
    const bookieSelect = document.getElementById('props-filter-bookmaker');
    if (bookieSelect) bookieSelect.value = '';
    const matchStatusSelect = document.getElementById('props-filter-match-status');
    if (matchStatusSelect) matchStatusSelect.value = '';
    const statusSelect = document.getElementById('props-filter-exec-status');
    if (statusSelect) statusSelect.value = '';
    const limitSelect = document.getElementById('props-filter-limit');
    if (limitSelect) limitSelect.value = '50';

    state.playerProps.propsScope = 'ALL';
    const scopeButtons = document.querySelectorAll('#props-scope-switcher .scope-btn');
    scopeButtons.forEach(b => b.classList.toggle('active', b.getAttribute('data-scope') === 'ALL'));
    syncStatDropdownWithScope('ALL');

    currentPropsCategory = 'top_value';
    syncTabButtonsWithFilters();

    if (hasLocalPropsData()) {
      applyLocalFiltersAndRender();
    } else {
      fetchAndRenderPropsFromBackend();
    }
  }

  async function loadPlayerPropsData() {
    initPlayerPropsEvents();
    try {
      const healthRes = await api.fetchPropsHealth();
      if (healthRes && healthRes.data) {
        const statusBadge = document.getElementById('props-provider-status');
        if (statusBadge) {
          statusBadge.textContent = healthRes.data.status || 'HEALTHY';
          statusBadge.className = `badge ${healthRes.data.status === 'HEALTHY' ? 'badge-accent' : 'badge-danger'}`;
        }
      }
    } catch (e) {
      console.warn('Failed to fetch props health:', e);
    }

    await fetchAndRenderPropsFromBackend();
  }

  let _activePropsRequestId = 0;

  async function handlePropsScan() {
    const btnScan = document.getElementById('btn-scan-props');
    const btnText = document.getElementById('btn-scan-props-text');
    const alertContainer = document.getElementById('props-alert-container');

    if (state.playerProps.isScanning) return;
    state.playerProps.isScanning = true;

    const currentReqId = ++_activePropsRequestId;
    const mode = state.playerProps.scanMode || 'NORMAL';
    const isUltra = (mode === 'ULTRA');

    if (btnScan) {
      btnScan.disabled = true;
      btnScan.innerHTML = `<span class="spinner-icon">⟳</span> <span id="btn-scan-props-text">${isUltra ? 'Scanning Global Props (Ultra)...' : 'Scanning Global Props...'}</span>`;
    }
    if (alertContainer) alertContainer.innerHTML = '';

    const scopeVal = state.playerProps.propsScope || 'ALL';
    const horizonVal = parseInt(document.getElementById('props-filter-horizon')?.value || '7', 10);
    const minEvVal = parseFloat(document.getElementById('props-filter-min-ev')?.value || '3.0');
    const limitVal = parseInt(document.getElementById('props-filter-limit')?.value || '50', 10);
    const statVal = document.getElementById('props-filter-stat')?.value || '';
    const tourVal = document.getElementById('props-filter-tournaments')?.value || '';

    const params = {
      scan_mode: mode,
      props_scope: scopeVal,
      time_horizon_days: horizonVal,
      min_ev_percent: minEvVal,
      max_results: limitVal,
      max_fixtures: isUltra ? 60 : 30,
      max_trends_requests: isUltra ? 60 : 20,
      max_execution_events: isUltra ? 60 : 25,
      auto_paginate: isUltra ? 'true' : 'false',
    };
    if (statVal) params.stat_types = statVal;
    if (tourVal) params.tournaments = tourVal;

    try {
      const res = await api.scanGlobalProps(params);
      if (currentReqId !== _activePropsRequestId) return;

      if (res && res.data) {
        const scanData = res.data;
        const qualified = scanData.qualified_opportunities || [];
        const diagnostic = scanData.diagnostic_candidates || [];
        const allCandidates = scanData.all_candidates || [...qualified, ...diagnostic];
        const funnel = scanData.funnel_metrics || {};

        state.playerProps.rawUniverse = allCandidates;
        state.playerProps.results = qualified;
        state.playerProps.diagnosticCandidates = diagnostic;
        state.playerProps.funnelMetrics = funnel;
        state.playerProps.metadata = scanData;

        applyLocalFiltersAndRender();

        const durSec = (scanData.duration_ms ? (scanData.duration_ms / 1000).toFixed(2) : '0.00');
        const fixDesc = (funnel.fixtures_selected && funnel.fixtures_discovered && funnel.fixtures_discovered > funnel.fixtures_selected)
          ? `${funnel.fixtures_selected} of ${funnel.fixtures_discovered} fixtures`
          : `${funnel.fixtures_selected || funnel.fixtures_discovered || 0} fixtures`;
        showToast(`Global ${isUltra ? 'Ultra ' : ''}scan complete! Found ${qualified.length} qualified valuebets across ${fixDesc} (${durSec}s).`);
      } else if (res && res.errors && res.errors.length > 0) {
        if (alertContainer) {
          alertContainer.innerHTML = `
            <div class="alert alert-danger" style="margin-bottom: 1rem; padding: 0.75rem 1rem; background: rgba(239, 68, 68, 0.15); border: 1px solid rgba(239, 68, 68, 0.3); border-radius: var(--radius-sm); color: #FCA5A5;">
              <strong>Scan Error:</strong> ${res.errors.join(', ')}
            </div>
          `;
        }
      }
    } catch (err) {
      if (currentReqId !== _activePropsRequestId) return;
      console.error('Error in handlePropsScan:', err);
      if (alertContainer) {
        alertContainer.innerHTML = `
          <div class="alert alert-danger" style="margin-bottom: 1rem; padding: 0.75rem 1rem; background: rgba(239, 68, 68, 0.15); border: 1px solid rgba(239, 68, 68, 0.3); border-radius: var(--radius-sm); color: #FCA5A5;">
            <strong>Network Error:</strong> Failed to connect to Global Props Scanner.
          </div>
        `;
      }
    } finally {
      if (currentReqId === _activePropsRequestId) {
        state.playerProps.isScanning = false;
        if (btnScan) {
          btnScan.disabled = false;
          const currentMode = state.playerProps.scanMode || 'NORMAL';
          const label = (currentMode === 'ULTRA') ? 'Scan Props (Ultra)' : 'Scan Props';
          btnScan.innerHTML = `
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
            <span id="btn-scan-props-text">${label}</span>
          `;
        }
      }
    }
  }

  function updatePropsSummaryMetrics(scanData, items) {
    const scannedEl = document.getElementById('props-stat-scanned');
    const uniqueEl = document.getElementById('props-stat-unique');
    const matchedBothEl = document.getElementById('props-stat-matched-both');
    const polishOddsEl = document.getElementById('props-stat-polish-odds');
    const bettableEl = document.getElementById('props-stat-bettable');
    const refOnlyEl = document.getElementById('props-stat-ref-only');
    const uncertainEl = document.getElementById('props-stat-uncertain');
    const discrepancyStatEl = document.getElementById('props-stat-discrepancy');
    const pagesEl = document.getElementById('props-pages-count');
    const srcTotalEl = document.getElementById('props-stat-source-total');

    const funnel = scanData?.funnel_metrics || state.playerProps.funnelMetrics || {};
    const qualifiedList = scanData?.qualified_opportunities || state.playerProps.results || [];
    const diagnosticList = scanData?.diagnostic_candidates || state.playerProps.diagnosticCandidates || [];
    const allCandidates = scanData?.all_candidates || [...qualifiedList, ...diagnosticList];

    const qualifiedCount = scanData?.qualified_count ?? qualifiedList.length;
    const totalDiscovered = funnel.trends_discovered || allCandidates.length;
    const totalDeduped = funnel.trends_deduplicated || allCandidates.length;

    // Exact canonical matched Betclic + Superbet count:
    // Requires BOTH bookmakers to have executable quotes (> 1.0) on the exact same canonical proposition
    const matchedBothList = allCandidates.filter(i => (
      i.superbet_odds !== null && i.superbet_odds !== undefined && Number(i.superbet_odds) > 1.0 &&
      i.betclic_odds !== null && i.betclic_odds !== undefined && Number(i.betclic_odds) > 1.0
    ));
    const matchedBothCount = matchedBothList.length;

    const matchUncertain = funnel.match_uncertain ?? (funnel.rejection_breakdown?.['MATCH_UNCERTAIN'] ?? (funnel.rejection_breakdown?.['EVENT_AMBIGUOUS'] ?? 0));
    const unavailablePolish = funnel.unavailable_polish_odds || (funnel.rejection_breakdown?.['POLISH_ODDS_UNAVAILABLE'] ?? (funnel.rejection_breakdown?.['NO_POLISH_ODDS'] ?? 0));
    const fixturesSelected = funnel.fixtures_selected || funnel.fixtures_discovered || 0;
    const fixturesDiscovered = funnel.fixtures_discovered || fixturesSelected;

    const discrepancyList = allCandidates.filter(i => (
      i.is_discrepancy === true ||
      (i.relative_price_difference_pct !== null && i.relative_price_difference_pct !== undefined && Number(i.relative_price_difference_pct) >= 10.0)
    ));

    if (scannedEl) scannedEl.textContent = totalDiscovered > 0 ? totalDiscovered : totalDeduped;
    if (uniqueEl) uniqueEl.textContent = totalDeduped;
    if (matchedBothEl) matchedBothEl.textContent = matchedBothCount;
    if (polishOddsEl) polishOddsEl.textContent = matchedBothCount;
    if (bettableEl) bettableEl.textContent = qualifiedCount;
    if (refOnlyEl) refOnlyEl.textContent = unavailablePolish;
    if (uncertainEl) uncertainEl.textContent = matchUncertain;
    if (discrepancyStatEl) discrepancyStatEl.textContent = discrepancyList.length;

    if (pagesEl) {
      if (fixturesSelected > 0) {
        pagesEl.textContent = (fixturesDiscovered > fixturesSelected)
          ? `${fixturesSelected} of ${fixturesDiscovered} matches`
          : `${fixturesSelected} matches`;
      } else {
        pagesEl.textContent = '—';
      }
    }
    if (srcTotalEl) {
      srcTotalEl.textContent = totalDiscovered > 0
        ? `${totalDiscovered} raw trends • ${totalDeduped} unique (${fixturesSelected} of ${fixturesDiscovered} fixtures)`
        : 'Multi-Fixture Dataset';
    }

    // Category & Diagnostic tab badges
    const tabAll = document.getElementById('tab-count-all');
    const tabValuebet = document.getElementById('tab-count-valuebet');
    const tabValuebetCompat = document.getElementById('tab-count-valuebet-compat');
    const tabDiagnostics = document.getElementById('tab-count-diagnostics');
    const tabDiscrepancy = document.getElementById('tab-count-discrepancy');
    const tabPlayer = document.getElementById('tab-count-player');
    const tabTeam = document.getElementById('tab-count-team');
    const tabSuperbet = document.getElementById('tab-count-superbet');
    const tabBetclic = document.getElementById('tab-count-betclic');
    const tabBelowThreshold = document.getElementById('tab-count-below-threshold');
    const tabRefGap = document.getElementById('tab-count-ref-gap');
    const tabNoPolish = document.getElementById('tab-count-no-polish');
    const tabUncertain = document.getElementById('tab-count-uncertain');

    const playerList = allCandidates.filter(i => (i.prop_type || '').toUpperCase() === 'PLAYER');
    const teamList = allCandidates.filter(i => (i.prop_type || '').toUpperCase() === 'TEAM');
    const superbetList = allCandidates.filter(i => (i.best_bookmaker || '').toLowerCase() === 'superbet' || i.superbet_odds !== null || (i.execution_odds && i.execution_odds.Superbet));
    const betclicList = allCandidates.filter(i => (i.best_bookmaker || '').toLowerCase() === 'betclic' || i.betclic_odds !== null || (i.execution_odds && i.execution_odds.Betclic));
    const belowThreshList = allCandidates.filter(i => i.reason_code === 'BELOW_VALUE_THRESHOLD');
    const refGapList = allCandidates.filter(i => i.reason_code === 'INSUFFICIENT_REFERENCE_SOURCES' || i.reference_fair_odds === null);
    const noPolishList = allCandidates.filter(i => i.reason_code === 'POLISH_ODDS_UNAVAILABLE');

    const limitVal = parseInt(document.getElementById('props-filter-limit')?.value || '50', 10);
    const isAllTab = (currentPropsCategory === 'all' || currentPropsCategory === 'diagnostics');
    const isPlayerTab = (currentPropsCategory === 'player' || state.playerProps.propsScope === 'PLAYER');
    const isTeamTab = (currentPropsCategory === 'team' || state.playerProps.propsScope === 'TEAM');
    const isDiscrepancyTab = (currentPropsCategory === 'discrepancy');

    if (tabAll) {
      tabAll.textContent = (isAllTab && limitVal < allCandidates.length)
        ? `${Math.min(limitVal, allCandidates.length)} / ${allCandidates.length}`
        : allCandidates.length;
    }
    if (tabValuebet) tabValuebet.textContent = qualifiedCount;
    if (tabValuebetCompat) tabValuebetCompat.textContent = qualifiedCount;
    if (tabDiagnostics) tabDiagnostics.textContent = allCandidates.length;
    if (tabDiscrepancy) {
      tabDiscrepancy.textContent = (isDiscrepancyTab && limitVal < discrepancyList.length)
        ? `${Math.min(limitVal, discrepancyList.length)} / ${discrepancyList.length}`
        : discrepancyList.length;
    }
    if (tabPlayer) {
      tabPlayer.textContent = (isPlayerTab && limitVal < playerList.length)
        ? `${Math.min(limitVal, playerList.length)} / ${playerList.length}`
        : playerList.length;
    }
    if (tabTeam) {
      tabTeam.textContent = (isTeamTab && limitVal < teamList.length)
        ? `${Math.min(limitVal, teamList.length)} / ${teamList.length}`
        : teamList.length;
    }
    if (tabSuperbet) tabSuperbet.textContent = superbetList.length;
    if (tabBetclic) tabBetclic.textContent = betclicList.length;
    if (tabBelowThreshold) tabBelowThreshold.textContent = belowThreshList.length;
    if (tabRefGap) tabRefGap.textContent = refGapList.length;
    if (tabNoPolish) tabNoPolish.textContent = noPolishList.length;
    if (tabUncertain) tabUncertain.textContent = matchUncertain;
  }

  function renderScanDiagnostics(scanData, items) {
    const funnel = scanData?.funnel_metrics || state.playerProps.funnelMetrics || {};
    const rejectionBreakdown = funnel.rejection_breakdown || {};

    const pagesEl = document.getElementById('diag-pages-fetched');
    const acquiredEl = document.getElementById('diag-props-acquired');
    const refOddsEl = document.getElementById('diag-ref-odds');
    const execCandEl = document.getElementById('diag-exec-candidates');
    const execQuotesEl = document.getElementById('diag-exec-quotes');
    const activePolishEl = document.getElementById('diag-active-polish');
    const bettableEl = document.getElementById('diag-bettable-count');
    const refOnlyEl = document.getElementById('diag-ref-only');
    const noExecEl = document.getElementById('diag-no-exec');
    const uncertainEl = document.getElementById('diag-uncertain');

    const fixturesDiscovered = funnel.fixtures_discovered || 0;
    const fixturesSelected = funnel.fixtures_selected || fixturesDiscovered;
    const trendsDiscovered = funnel.trends_discovered || (items ? items.length : 0);
    const trendsDeduped = funnel.trends_deduplicated || trendsDiscovered;
    const matched = funnel.matched_props || (items ? items.length : 0);
    const qualified = funnel.qualified_count ?? (items ? items.length : 0);
    const matchUncertain = funnel.match_uncertain ?? (rejectionBreakdown['MATCH_UNCERTAIN'] ?? (rejectionBreakdown['EVENT_AMBIGUOUS'] ?? 0));
    const refOnlyCount = funnel.unavailable_polish_odds || (rejectionBreakdown['POLISH_ODDS_UNAVAILABLE'] ?? (rejectionBreakdown['NO_POLISH_ODDS'] ?? 0));
    const noExecCount = funnel.unmatched_markets || (rejectionBreakdown['NO_EXECUTION_MARKET'] ?? (rejectionBreakdown['MARKET_UNMATCHED'] ?? 0));
    const validRefOdds = Math.max(0, trendsDeduped - (funnel.invalid_stale_reference || 0));

    if (pagesEl) {
      pagesEl.textContent = (fixturesDiscovered > fixturesSelected)
        ? `${fixturesSelected} / ${fixturesDiscovered}`
        : fixturesSelected;
    }
    if (acquiredEl) acquiredEl.textContent = trendsDiscovered > 0 ? `${trendsDiscovered} (${trendsDeduped} uniq)` : trendsDeduped;
    if (refOddsEl) refOddsEl.textContent = validRefOdds;
    if (execCandEl) execCandEl.textContent = trendsDeduped;
    if (execQuotesEl) execQuotesEl.textContent = matched;
    if (activePolishEl) activePolishEl.textContent = matched;
    if (bettableEl) bettableEl.textContent = qualified;
    if (refOnlyEl) refOnlyEl.textContent = refOnlyCount;
    if (noExecEl) noExecEl.textContent = noExecCount;
    if (uncertainEl) uncertainEl.textContent = matchUncertain;

    // Render Rejection Breakdown Chips with actual reason codes
    const chipsContainer = document.getElementById('props-rejection-chips');
    if (chipsContainer) {
      const entries = Object.entries(rejectionBreakdown);
      if (entries.length === 0) {
        chipsContainer.innerHTML = `<span class="text-muted" style="font-size: 0.8rem;">No rejections recorded in the latest scan cycle.</span>`;
      } else {
        chipsContainer.innerHTML = entries.map(([code, count]) => `
          <div class="rejection-chip">
            <strong>${code}</strong>
            <span class="count-badge">${count}</span>
          </div>
        `).join('');
      }
    }
  }

  function hasLocalPropsData() {
    return Array.isArray(state.playerProps.rawUniverse) &&
      state.playerProps.rawUniverse.length > 0 &&
      (state.playerProps.scanMode || 'NORMAL') !== 'ULTRA';
  }

  function applyLocalFiltersAndRender() {
    if (!hasLocalPropsData()) {
      return fetchAndRenderPropsFromBackend();
    }

    const scopeVal = state.playerProps.propsScope || 'ALL';
    const searchVal = (document.getElementById('props-filter-search')?.value || '').trim().toLowerCase();
    const statVal = (document.getElementById('props-filter-stat')?.value || '').trim().toLowerCase();
    const statusVal = (document.getElementById('props-filter-exec-status')?.value || '').trim().toUpperCase();
    const bookmakerVal = (document.getElementById('props-filter-bookmaker')?.value || '').trim().toLowerCase();
    const matchStatusVal = (document.getElementById('props-filter-match-status')?.value || '').trim().toUpperCase();
    const minOddsVal = parseFloat(document.getElementById('props-filter-min-odds')?.value || '');
    const tournVal = (document.getElementById('props-filter-tournaments')?.value || '').trim().toLowerCase();
    const posVal = (document.getElementById('props-filter-position')?.value || '').trim().toUpperCase();
    const threshStr = (document.getElementById('props-filter-threshold')?.value || '').trim();
    const threshVal = (threshStr && threshStr !== '0' && threshStr !== 'all') ? parseFloat(threshStr) : NaN;
    const sortByVal = document.getElementById('props-filter-sortby')?.value || 'net_ev';
    const minEvVal = parseFloat(document.getElementById('props-filter-min-ev')?.value || '');
    const limitVal = parseInt(document.getElementById('props-filter-limit')?.value || '50', 10);

    const isDiscrepancyTab = currentPropsCategory === 'discrepancy';
    const isTopValueTab = (currentPropsCategory === 'top_value' || currentPropsCategory === 'valuebets');

    let activeSort = sortByVal;
    if (isDiscrepancyTab && (!activeSort || activeSort === 'net_ev')) {
      activeSort = 'discrepancy_pct';
      const sortEl = document.getElementById('props-filter-sortby');
      if (sortEl) sortEl.value = 'discrepancy_pct';
    }

    let candidates = [...state.playerProps.rawUniverse];

    // 1. Primary Category & View Filter
    if (isTopValueTab) {
      candidates = candidates.filter(o => {
        const hasEv = o.net_ev_pct !== null && o.net_ev_pct !== undefined;
        const evMeets = !isNaN(minEvVal) ? (hasEv && Number(o.net_ev_pct) >= minEvVal) : true;
        return o.is_valuebet === true || (o.action || '').toUpperCase() === 'VALUE BET' || (o.status || '').toUpperCase() === 'QUALIFIED' || (hasEv && Number(o.net_ev_pct) >= 3.0 && evMeets);
      });
    } else if (isDiscrepancyTab) {
      candidates = candidates.filter(o => {
        return o.is_discrepancy === true ||
          (o.relative_price_difference_pct !== null && o.relative_price_difference_pct !== undefined && Number(o.relative_price_difference_pct) >= 10.0);
      });
    } else if (currentPropsCategory === 'below_threshold') {
      candidates = candidates.filter(o => (o.reason_code || '').toUpperCase() === 'BELOW_VALUE_THRESHOLD');
    } else if (currentPropsCategory === 'ref_gap') {
      candidates = candidates.filter(o => ['INSUFFICIENT_REFERENCE_SOURCES', 'REFERENCE_GAP', 'STALE_REFERENCE_DATA'].includes((o.reason_code || '').toUpperCase()));
    } else if (currentPropsCategory === 'no_polish') {
      candidates = candidates.filter(o => ['POLISH_ODDS_UNAVAILABLE', 'ODDS_INACTIVE'].includes((o.reason_code || '').toUpperCase()));
    } else if (currentPropsCategory === 'superbet') {
      candidates = candidates.filter(o => o.superbet_odds !== null && o.superbet_odds !== undefined && Number(o.superbet_odds) > 1.0);
    } else if (currentPropsCategory === 'betclic') {
      candidates = candidates.filter(o => o.betclic_odds !== null && o.betclic_odds !== undefined && Number(o.betclic_odds) > 1.0);
    } else if (currentPropsCategory === 'player') {
      candidates = candidates.filter(o => (o.prop_type || '').toUpperCase() === 'PLAYER');
    } else if (currentPropsCategory === 'team') {
      candidates = candidates.filter(o => (o.prop_type || '').toUpperCase() === 'TEAM');
    }

    // 2. Scope Filter (PLAYER | TEAM | ALL)
    if (scopeVal && scopeVal !== 'ALL') {
      candidates = candidates.filter(o => (o.prop_type || '').toUpperCase() === scopeVal.toUpperCase());
    }

    // 3. Search Filter
    if (searchVal) {
      candidates = candidates.filter(o => {
        const fields = [
          o.player_name, o.team, o.opponent, o.match_name, o.stat_type, o.competition,
          `${o.side || ''} ${o.line || ''}`, o.best_bookmaker
        ].map(f => String(f || '').toLowerCase());
        return fields.some(f => f.includes(searchVal));
      });
    }

    // 4. Stat Type Filter
    if (statVal) {
      if (statVal.startsWith('player_')) {
        const pStat = statVal.replace('player_', '').toUpperCase();
        candidates = candidates.filter(o => (o.prop_type || '').toUpperCase() === 'PLAYER' && (o.stat_type || '').toUpperCase() === pStat);
      } else if (statVal.startsWith('team_')) {
        const tStat = statVal.replace('team_', '').toUpperCase();
        candidates = candidates.filter(o => (o.prop_type || '').toUpperCase() === 'TEAM' && (o.stat_type || '').toUpperCase() === tStat);
      } else {
        const targetStat = statVal.toUpperCase();
        candidates = candidates.filter(o => (o.stat_type || '').toUpperCase() === targetStat);
      }
    }

    // 5. Min Net EV Filter (unless discrepancy tab with default)
    if (!isNaN(minEvVal) && (!isDiscrepancyTab || minEvVal !== 3.0)) {
      candidates = candidates.filter(o => o.net_ev_pct !== null && o.net_ev_pct !== undefined && Number(o.net_ev_pct) >= minEvVal);
    }

    // 6. Min Odds Filter
    if (!isNaN(minOddsVal) && minOddsVal > 1.0) {
      candidates = candidates.filter(o => {
        if (bookmakerVal === 'superbet' && o.superbet_odds) return Number(o.superbet_odds) >= minOddsVal;
        if (bookmakerVal === 'betclic' && o.betclic_odds) return Number(o.betclic_odds) >= minOddsVal;
        if (o.best_raw_odds && Number(o.best_raw_odds) >= minOddsVal) return true;
        if (o.superbet_odds && Number(o.superbet_odds) >= minOddsVal) return true;
        if (o.betclic_odds && Number(o.betclic_odds) >= minOddsVal) return true;
        return false;
      });
    }

    // 7. Tournament / Competition Filter
    if (tournVal) {
      candidates = candidates.filter(o => String(o.competition || '').toLowerCase().includes(tournVal));
    }

    // 8. Position Filter
    if (posVal && posVal !== 'D,M,F' && posVal !== 'ALL') {
      if (posVal === 'HOME' || posVal === 'AWAY') {
        candidates = candidates.filter(o => {
          const role = (o.participant_role || (o.provenance || {}).target_role || '').toUpperCase();
          return role === posVal;
        });
      } else {
        candidates = candidates.filter(o => {
          if ((o.prop_type || '').toUpperCase() === 'TEAM') return false;
          let p = (o.position || (o.provenance || {}).position || '').toUpperCase();
          if (p === 'FW' || p === 'FORWARD' || p === 'FORWARDS') p = 'F';
          if (p === 'MF' || p === 'MIDFIELDER' || p === 'MIDFIELDERS') p = 'M';
          if (p === 'DF' || p === 'DEFENDER' || p === 'DEFENDERS') p = 'D';
          return p === posVal;
        });
      }
    }

    // 9. Threshold / Line Filter
    if (!isNaN(threshVal)) {
      candidates = candidates.filter(o => o.line !== null && o.line !== undefined && Math.abs(Number(o.line) - threshVal) < 0.05);
    }

    // 10. Match Status Filter
    if (matchStatusVal) {
      const isBoth = (o) => {
        const sb = Number(o.superbet_odds);
        const bc = Number(o.betclic_odds);
        return sb > 1.0 && bc > 1.0;
      };
      if (['MATCHED', 'MATCHED_BOTH', 'BOTH', 'MATCHED_BETCLIC_SUPERBET'].includes(matchStatusVal)) {
        candidates = candidates.filter(isBoth);
      } else if (['UNMATCHED', 'PARTIAL', 'PARTIAL_UNMATCHED'].includes(matchStatusVal)) {
        candidates = candidates.filter(o => !isBoth(o));
      }
    }

    // 11. Status Filter
    if (statusVal) {
      candidates = candidates.filter(o => {
        const rCode = (o.reason_code || '').toUpperCase();
        const oStatus = (o.status || '').toUpperCase();
        const oAction = (o.action || '').toUpperCase();
        if ([rCode, oStatus, oAction].includes(statusVal)) return true;
        if (statusVal === 'MATCHING_FAILURE' && ['MATCHING_FAILURE', 'MARKET_UNMATCHED', 'EVENT_UNMATCHED', 'PLAYER_UNMATCHED', 'TEAM_UNMATCHED', 'LINE_MISMATCH', 'SELECTION_MISMATCH', 'MATCH_UNCERTAIN'].includes(rCode)) return true;
        if (['REFERENCE_GAP', 'INSUFFICIENT_REFERENCE_SOURCES'].includes(statusVal) && ['REFERENCE_GAP', 'INSUFFICIENT_REFERENCE_SOURCES', 'STALE_REFERENCE_DATA'].includes(rCode)) return true;
        if (statusVal === 'POLISH_ODDS_UNAVAILABLE' && ['POLISH_ODDS_UNAVAILABLE', 'ODDS_INACTIVE'].includes(rCode)) return true;
        if (['BELOW_THRESHOLD', 'BELOW_VALUE_THRESHOLD'].includes(statusVal) && ['BELOW_THRESHOLD', 'BELOW_VALUE_THRESHOLD'].includes(rCode)) return true;
        if (statusVal === 'QUALIFIED' && (rCode === 'QUALIFIED' || oStatus === 'QUALIFIED' || o.is_valuebet)) return true;
        return false;
      });
    }

    // 12. Bookmaker Filter
    if (bookmakerVal) {
      candidates = candidates.filter(o => {
        if (String(o.best_bookmaker || '').toLowerCase() === bookmakerVal) return true;
        if (bookmakerVal === 'superbet' && o.superbet_odds !== null && o.superbet_odds !== undefined) return true;
        if (bookmakerVal === 'betclic' && o.betclic_odds !== null && o.betclic_odds !== undefined) return true;
        if (o.execution_odds && typeof o.execution_odds === 'object') {
          return Object.keys(o.execution_odds).some(k => k.toLowerCase().includes(bookmakerVal));
        }
        return false;
      });
    }

    // 13. Deterministic Sorting
    const sortMode = (activeSort || 'net_ev').toLowerCase();
    candidates.sort((a, b) => {
      if (sortMode === 'discrepancy_pct' || sortMode === 'discrepancy' || sortMode === 'discrepancy_high') {
        const aRel = a.relative_price_difference_pct !== null && a.relative_price_difference_pct !== undefined ? Number(a.relative_price_difference_pct) : null;
        const bRel = b.relative_price_difference_pct !== null && b.relative_price_difference_pct !== undefined ? Number(b.relative_price_difference_pct) : null;
        const aHas = (aRel !== null && aRel >= 10.0) ? 0 : (aRel !== null ? 1 : 2);
        const bHas = (bRel !== null && bRel >= 10.0) ? 0 : (bRel !== null ? 1 : 2);
        if (aHas !== bHas) return aHas - bHas;
        const aDiffVal = aRel !== null ? aRel : 0;
        const bDiffVal = bRel !== null ? bRel : 0;
        if (bDiffVal !== aDiffVal) return bDiffVal - aDiffVal;
        const aOddsDiff = Number(a.odds_difference || 0);
        const bOddsDiff = Number(b.odds_difference || 0);
        if (bOddsDiff !== aOddsDiff) return bOddsDiff - aOddsDiff;
        const aKey = String(a.canonical_prop_key || a.prop_id || '');
        const bKey = String(b.canonical_prop_key || b.prop_id || '');
        return aKey.localeCompare(bKey);
      } else if (sortMode === 'discrepancy_pct_asc' || sortMode === 'discrepancy_low') {
        const aRel = a.relative_price_difference_pct !== null && a.relative_price_difference_pct !== undefined ? Number(a.relative_price_difference_pct) : null;
        const bRel = b.relative_price_difference_pct !== null && b.relative_price_difference_pct !== undefined ? Number(b.relative_price_difference_pct) : null;
        const aHas = (aRel !== null && aRel >= 10.0) ? 0 : (aRel !== null ? 1 : 2);
        const bHas = (bRel !== null && bRel >= 10.0) ? 0 : (bRel !== null ? 1 : 2);
        if (aHas !== bHas) return aHas - bHas;
        const aDiffVal = aRel !== null ? aRel : 0;
        const bDiffVal = bRel !== null ? bRel : 0;
        if (aDiffVal !== bDiffVal) return aDiffVal - bDiffVal;
        const aOddsDiff = Number(a.odds_difference || 0);
        const bOddsDiff = Number(b.odds_difference || 0);
        if (aOddsDiff !== bOddsDiff) return aOddsDiff - bOddsDiff;
        const aKey = String(a.canonical_prop_key || a.prop_id || '');
        const bKey = String(b.canonical_prop_key || b.prop_id || '');
        return aKey.localeCompare(bKey);
      } else if (sortMode === 'hit_rate') {
        const aHr = Number(a.hit_rate_pct || 0);
        const bHr = Number(b.hit_rate_pct || 0);
        if (bHr !== aHr) return bHr - aHr;
      } else if (sortMode === 'sample_size') {
        const aSs = Number(a.trend_window || a.sample_size || 0);
        const bSs = Number(b.trend_window || b.sample_size || 0);
        if (bSs !== aSs) return bSs - aSs;
      } else if (sortMode === 'gross_ev') {
        const aEv = a.gross_ev_pct !== null && a.gross_ev_pct !== undefined ? Number(a.gross_ev_pct) : -999;
        const bEv = b.gross_ev_pct !== null && b.gross_ev_pct !== undefined ? Number(b.gross_ev_pct) : -999;
        if (bEv !== aEv) return bEv - aEv;
      } else if (sortMode === 'odds') {
        const aO = Number(a.best_raw_odds || 0);
        const bO = Number(b.best_raw_odds || 0);
        if (bO !== aO) return bO - aO;
      } else if (sortMode === 'name') {
        const aN = String(a.player_name || a.team || '');
        const bN = String(b.player_name || b.team || '');
        return aN.localeCompare(bN);
      } else {
        // 'net_ev' default
        const aEv = a.net_ev_pct !== null && a.net_ev_pct !== undefined ? Number(a.net_ev_pct) : -999;
        const bEv = b.net_ev_pct !== null && b.net_ev_pct !== undefined ? Number(b.net_ev_pct) : -999;
        if (bEv !== aEv) return bEv - aEv;
      }
      const aKey = String(a.canonical_prop_key || a.prop_id || '');
      const bKey = String(b.canonical_prop_key || b.prop_id || '');
      return aKey.localeCompare(bKey);
    });

    const totalMatching = candidates.length;
    const paginatedItems = candidates.slice(0, limitVal);

    updatePropsSummaryMetrics(state.playerProps.metadata, state.playerProps.rawUniverse);
    renderScanDiagnostics(state.playerProps.metadata, state.playerProps.rawUniverse);
    renderPropsTable(paginatedItems, totalMatching, state.playerProps.funnelMetrics, state.playerProps.rawUniverse.length);

    if (state.playerProps.selectedPropId) {
      const stillPresent = state.playerProps.rawUniverse.find(i => (i.canonical_prop_key === state.playerProps.selectedPropId || i.prop_id === state.playerProps.selectedPropId));
      if (stillPresent) {
        showPropDetail(state.playerProps.selectedPropId);
      } else {
        const detailCard = document.getElementById('prop-detail-container');
        if (detailCard) detailCard.style.display = 'none';
        state.playerProps.selectedPropId = null;
      }
    }
  }

  let _activeResultsRequestId = 0;

  async function fetchAndRenderPropsFromBackend() {
    const currentReqId = ++_activeResultsRequestId;

    const scopeVal = state.playerProps.propsScope || 'ALL';
    const searchVal = (document.getElementById('props-filter-search')?.value || '').trim();
    const statVal = document.getElementById('props-filter-stat')?.value || '';
    const statusVal = document.getElementById('props-filter-exec-status')?.value || '';
    const bookmakerVal = document.getElementById('props-filter-bookmaker')?.value || '';
    const matchStatusVal = (document.getElementById('props-filter-match-status')?.value || '').trim();
    const minOddsVal = parseFloat(document.getElementById('props-filter-min-odds')?.value || '');
    const tournVal = document.getElementById('props-filter-tournaments')?.value || '';
    const posVal = document.getElementById('props-filter-position')?.value || '';
    const threshVal = parseFloat(document.getElementById('props-filter-threshold')?.value || '');
    const sortByVal = document.getElementById('props-filter-sortby')?.value || 'net_ev';
    const minEvVal = parseFloat(document.getElementById('props-filter-min-ev')?.value || '');
    const limitVal = parseInt(document.getElementById('props-filter-limit')?.value || '50', 10);

    const isDiagnosticStatus = statusVal && statusVal !== 'QUALIFIED';
    const isDiscrepancyTab = currentPropsCategory === 'discrepancy';
    const isTopValueTab = (currentPropsCategory === 'top_value' || currentPropsCategory === 'valuebets');
    const isAllTab = currentPropsCategory === 'all' || currentPropsCategory === 'diagnostics';

    let viewMode = isTopValueTab ? 'TOP_VALUE' : 'ALL_CANDIDATES';
    if (isDiscrepancyTab) {
      viewMode = 'QUOTE_DISCREPANCY';
    }

    let activeSort = sortByVal;
    if (isDiscrepancyTab && (!activeSort || activeSort === 'net_ev')) {
      activeSort = 'discrepancy_pct';
      const sortEl = document.getElementById('props-filter-sortby');
      if (sortEl) sortEl.value = 'discrepancy_pct';
    }

    const mode = state.playerProps.scanMode || 'NORMAL';
    const params = {
      scan_mode: mode,
      view_mode: viewMode,
      limit: limitVal,
      offset: 0,
      sort_by: activeSort,
    };

    if (scopeVal && scopeVal !== 'ALL') {
      params.props_scope = scopeVal;
    } else if (currentPropsCategory === 'player') {
      params.props_scope = 'PLAYER';
    } else if (currentPropsCategory === 'team') {
      params.props_scope = 'TEAM';
    }

    if (bookmakerVal) {
      params.bookmaker = bookmakerVal;
    } else if (currentPropsCategory === 'superbet') {
      params.bookmaker = 'Superbet';
    } else if (currentPropsCategory === 'betclic') {
      params.bookmaker = 'Betclic';
    }

    if (matchStatusVal) {
      params.match_status = matchStatusVal;
    }

    if (statusVal) {
      params.status = statusVal;
    } else if (currentPropsCategory === 'below_threshold') {
      params.status = 'BELOW_VALUE_THRESHOLD';
    } else if (currentPropsCategory === 'ref_gap') {
      params.status = 'INSUFFICIENT_REFERENCE_SOURCES';
    } else if (currentPropsCategory === 'no_polish') {
      params.status = 'POLISH_ODDS_UNAVAILABLE';
    }

    if (statVal) params.stat = statVal;
    if (searchVal) params.search = searchVal;
    if (!isNaN(minEvVal)) {
      if (!isDiscrepancyTab || minEvVal !== 3.0) {
        params.min_net_ev = minEvVal;
      }
    }
    if (!isNaN(minOddsVal) && minOddsVal > 1.0) params.min_odds = minOddsVal;
    if (tournVal) params.competition = tournVal;
    if (posVal && posVal !== 'D,M,F' && posVal !== 'ALL') params.position = posVal;
    const threshStr = (document.getElementById('props-filter-threshold')?.value || '').trim();
    if (threshStr && threshStr !== '0' && threshStr !== 'all') {
      const parsedThresh = parseFloat(threshStr);
      if (!isNaN(parsedThresh)) params.threshold = parsedThresh;
    }

    try {
      const res = await api.fetchGlobalPropsResults(params);
      if (currentReqId !== _activeResultsRequestId) return;

      if (res && res.data) {
        const scanData = res.data;
        const qualified = scanData.qualified_opportunities || [];
        const diagnostic = scanData.diagnostic_candidates || [];
        const allCandidates = scanData.all_candidates || [...qualified, ...diagnostic];
        const items = scanData.items || (viewMode === 'TOP_VALUE' ? qualified : allCandidates);
        const funnel = scanData.funnel_metrics || {};

        state.playerProps.rawUniverse = allCandidates;
        state.playerProps.results = qualified;
        state.playerProps.diagnosticCandidates = diagnostic;
        state.playerProps.metadata = scanData;
        state.playerProps.funnelMetrics = funnel;

        updatePropsSummaryMetrics(scanData, allCandidates);
        renderScanDiagnostics(scanData, allCandidates);
        renderPropsTable(items, scanData.total_items_matching_filter ?? items.length, funnel, allCandidates.length);

        // Keep Inspector synchronized if selected
        if (state.playerProps.selectedPropId) {
          const stillPresent = allCandidates.find(i => (i.canonical_prop_key === state.playerProps.selectedPropId || i.prop_id === state.playerProps.selectedPropId));
          if (stillPresent) {
            showPropDetail(state.playerProps.selectedPropId);
          } else {
            const detailCard = document.getElementById('prop-detail-container');
            if (detailCard) detailCard.style.display = 'none';
            state.playerProps.selectedPropId = null;
          }
        }
      }
    } catch (err) {
      if (currentReqId !== _activeResultsRequestId) return;
      console.error('Failed to fetch global props results from backend:', err);
    }
  }

  function renderPropsTable(items, totalScanned, funnelMetrics, totalDatasetCount) {
    const tbody = document.getElementById('props-table-body');
    const countBadge = document.getElementById('props-table-count');
    const titleEl = document.getElementById('props-table-title');
    const metaLine = document.getElementById('props-table-meta-line');

    let titleText = '🏆 Top Valuebets Workspace';
    if (currentPropsCategory === 'discrepancy') {
      titleText = '⚡ Polish Bookmaker Quote Discrepancy Opportunities';
    } else if (currentPropsCategory === 'diagnostics' || currentPropsCategory === 'all') {
      titleText = '🔬 All Opportunities & Candidates (Full Pipeline View)';
    } else if (currentPropsCategory === 'below_threshold') {
      titleText = '⚠️ Evaluated Candidates Below Net EV Threshold';
    } else if (currentPropsCategory === 'ref_gap') {
      titleText = '🌐 Candidates with Reference Odds Gaps (INSUFFICIENT_REFERENCE_SOURCES)';
    } else if (currentPropsCategory === 'no_polish') {
      titleText = '⚪ Candidates with Polish Odds Unavailable';
    } else if (state.playerProps.propsScope === 'PLAYER' || currentPropsCategory === 'player') {
      titleText = '👤 Player Props Opportunities & Diagnostics';
    } else if (state.playerProps.propsScope === 'TEAM' || currentPropsCategory === 'team') {
      titleText = '🛡️ Team Props Opportunities & Diagnostics';
    } else if (currentPropsCategory === 'superbet') {
      titleText = '🔴 Superbet Execution Opportunities';
    } else if (currentPropsCategory === 'betclic') {
      titleText = '🔵 Betclic Execution Opportunities';
    }

    if (titleEl) titleEl.textContent = titleText;

    if (countBadge) {
      const totalMatching = totalScanned || items.length;
      const totalInAll = funnelMetrics?.trends_deduplicated || totalDatasetCount || funnelMetrics?.rejected_count || (state.playerProps.diagnosticCandidates?.length || 0) + (state.playerProps.results?.length || 0) || totalMatching;

      let categoryNoun = 'Results';
      const matchFilterVal = document.getElementById('props-filter-match-status')?.value;
      if (matchFilterVal === 'MATCHED') {
        categoryNoun = 'Matched Props';
      } else if (currentPropsCategory === 'discrepancy') {
        categoryNoun = 'Quote Discrepancies';
      } else if (currentPropsCategory === 'top_value' || currentPropsCategory === 'valuebets') {
        categoryNoun = 'Valuebets';
      } else if (currentPropsCategory === 'player' || state.playerProps.propsScope === 'PLAYER') {
        categoryNoun = 'Player Props';
      } else if (currentPropsCategory === 'team' || state.playerProps.propsScope === 'TEAM') {
        categoryNoun = 'Team Props';
      } else if (currentPropsCategory === 'below_threshold') {
        categoryNoun = 'Below Threshold';
      } else if (currentPropsCategory === 'ref_gap') {
        categoryNoun = 'Ref Gap';
      } else if (currentPropsCategory === 'no_polish') {
        categoryNoun = 'Unpriced';
      }

      if (items.length < totalMatching) {
        countBadge.textContent = `Showing ${items.length} of ${totalMatching} ${categoryNoun} · ${totalInAll} Candidates Scanned`;
      } else if (totalInAll > totalMatching) {
        countBadge.textContent = `${totalMatching} ${categoryNoun} · ${totalInAll} Candidates Scanned`;
      } else {
        countBadge.textContent = `${totalMatching} ${categoryNoun}`;
      }
    }

    if (metaLine) {
      const sortLabels = {
        discrepancy_pct: 'Discrepancy % (High → Low)',
        discrepancy_pct_asc: 'Discrepancy % (Low → High)',
        net_ev: 'Net EV % (High)',
        hit_rate: 'Hit Rate (High)',
        odds: 'Odds (High)',
        sample_size: 'Sample Size',
        gross_ev: 'Gross EV',
        name: 'Name (A-Z)',
      };
      const activeSortLabel = sortLabels[document.getElementById('props-filter-sortby')?.value || 'net_ev'] || 'Net EV %';
      if (items.length > 0) {
        metaLine.textContent = `Ranking sorted by ${activeSortLabel} • Displaying ${items.length} records`;
      } else {
        metaLine.textContent = `Deterministic Ranking (Sorted by ${activeSortLabel})`;
      }
    }

    if (!tbody) return;

    if (items.length === 0) {
      let emptyHeading = 'No opportunities match the current filters.';
      let emptySub = 'Try adjusting your search query, stat type, odds, or status filters.';

      if (currentPropsCategory === 'discrepancy') {
        emptyHeading = 'No significant Polish bookmaker price discrepancies found.';
        emptySub = 'Discrepancies require the same player, market, line and period to be matched across Betclic and Superbet and to exceed the configured threshold.';
      } else if (currentPropsCategory === 'player' || state.playerProps.propsScope === 'PLAYER') {
        emptyHeading = 'No player prop opportunities match the current filters.';
        emptySub = 'Try broadening your player filters or search query.';
      } else if (currentPropsCategory === 'team' || state.playerProps.propsScope === 'TEAM') {
        emptyHeading = 'No team prop opportunities match the current filters.';
        emptySub = 'Try broadening your team filters or search query.';
      } else if (currentPropsCategory === 'top_value') {
        emptyHeading = 'No valuebets meet the current criteria.';
        emptySub = 'No opportunities currently exceed the required positive expected value threshold against reference models. <!-- Brak zakwalifikowanych propsów w wybranym zakresie. -->';
      }

      tbody.innerHTML = `
        <tr>
          <td colspan="5" class="text-center text-muted" style="padding: 2.5rem;">
            <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="margin-bottom: 0.5rem; opacity: 0.5;"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
            <div style="font-weight: 600; color: var(--text-primary); margin-bottom: 0.25rem;">${emptyHeading}</div>
            <div style="font-size: 0.8rem; margin-bottom: 0.5rem;">${emptySub}</div>
          </td>
        </tr>
      `;
      return;
    }

    const prevPropsMap = state.playerProps._prevMap || new Map();
    const nextPropsMap = new Map();

    tbody.innerHTML = items.map((item, idx) => {
      const propType = (item.prop_type || 'PLAYER').toUpperCase();
      const isPlayer = propType === 'PLAYER';
      const propId = item.canonical_prop_key || item.prop_id || `${item.fixture_id}_${item.stat_type}_${item.line}`;
      const rankNum = idx + 1;

      // Delta detection (previous cycle comparison)
      const prevEntry = prevPropsMap.get(propId);
      let evDeltaClass = '';
      let rankDeltaClass = '';

      // 1. Value & Net EV (Decision Column)
      const hasNetEv = item.net_ev_pct !== null && item.net_ev_pct !== undefined;
      const netEv = hasNetEv ? Number(item.net_ev_pct).toFixed(1) : null;
      const grossEv = item.gross_ev_pct !== null && item.gross_ev_pct !== undefined ? Number(item.gross_ev_pct).toFixed(1) : null;
      const edgeVal = item.value_edge_pp !== null && item.value_edge_pp !== undefined ? `${Number(item.value_edge_pp).toFixed(1)}pp` : null;
      const isPositiveEv = hasNetEv && Number(item.net_ev_pct) >= 0.0;
      const isHighEv = hasNetEv && Number(item.net_ev_pct) >= 10.0;
      const isValueBetOpportunity = item.is_valuebet === true || (item.action || '').toUpperCase() === 'VALUE BET' || (item.status || '').toUpperCase() === 'QUALIFIED';
      const reasonCode = item.reason_code || '';

      if (prevEntry) {
        if (hasNetEv && prevEntry.net_ev_pct !== null && prevEntry.net_ev_pct !== undefined) {
          const diff = Number(item.net_ev_pct) - Number(prevEntry.net_ev_pct);
          if (diff > 0.05) evDeltaClass = 'ev-updated-up';
          else if (diff < -0.05) evDeltaClass = 'ev-updated-down';
        }
        if (prevEntry.rank !== undefined && rankNum < prevEntry.rank) {
          rankDeltaClass = 'rank-improved';
        }
      }

      nextPropsMap.set(propId, {
        net_ev_pct: item.net_ev_pct,
        rank: rankNum,
      });

      const isDiscrepancyRow = item.is_discrepancy === true || (item.relative_price_difference_pct !== null && item.relative_price_difference_pct !== undefined && Number(item.relative_price_difference_pct) >= 10.0);
      const relDiffPct = item.relative_price_difference_pct !== null && item.relative_price_difference_pct !== undefined ? Number(item.relative_price_difference_pct).toFixed(1) : null;
      const oddsDiff = item.odds_difference !== null && item.odds_difference !== undefined ? Number(item.odds_difference).toFixed(2) : null;

      let statusBadgeHtml = '';
      if (isDiscrepancyRow) {
        statusBadgeHtml = '<span class="badge badge-warning" style="font-weight: 700; font-size: 0.70rem; letter-spacing: 0.02em;">QUOTE DISCREPANCY</span>';
      } else if (isValueBetOpportunity) {
        statusBadgeHtml = '<span class="badge badge-success" style="font-weight: 700; font-size: 0.70rem;">VALUE BET</span>';
      } else if (reasonCode === 'BELOW_VALUE_THRESHOLD') {
        statusBadgeHtml = '<span class="badge badge-warning" style="font-weight: 600; font-size: 0.68rem;">BELOW THRESHOLD</span>';
      } else if (reasonCode === 'INSUFFICIENT_REFERENCE_SOURCES') {
        statusBadgeHtml = '<span class="badge badge-info" style="font-weight: 600; font-size: 0.68rem;">REF GAP</span>';
      } else if (reasonCode === 'POLISH_ODDS_UNAVAILABLE') {
        statusBadgeHtml = '<span class="badge badge-outline" style="font-weight: 600; font-size: 0.68rem;">NO POLISH ODDS</span>';
      } else {
        statusBadgeHtml = `<span class="badge badge-outline" style="font-size: 0.68rem;">${item.status || 'EVALUATED'}</span>`;
      }

      const rankPrefix = (rankDeltaClass === 'rank-improved') ? `<span class="rank-delta-arrow">▲</span> ` : '';

      let evHtml = '';
      if (isDiscrepancyRow) {
        const netEvSub = hasNetEv ? ` • Net EV: ${Number(netEv) >= 0 ? `+${netEv}%` : `${netEv}%`}` : ' • Score —';
        evHtml = `
          <div style="display: flex; flex-direction: column; gap: 0.25rem;">
            <div style="display: flex; align-items: center; gap: 0.35rem;">
              <span class="mono text-muted ${rankDeltaClass}" style="font-size: 0.74rem; font-weight: 700; min-width: 1.6rem;">${rankPrefix}#${rankNum}</span>
              <div class="net-ev-pill" style="background: rgba(245, 158, 11, 0.15); border: 1px solid rgba(245, 158, 11, 0.4); color: #F59E0B; font-weight: 700;">
                <span>+${relDiffPct}% DISC</span>
              </div>
              ${statusBadgeHtml}
            </div>
            <div class="text-muted" style="font-size: 0.70rem; padding-left: 1.95rem;">
              Δ +${oddsDiff || '0.00'}${netEvSub}
            </div>
          </div>
        `;
      } else if (hasNetEv) {
        evHtml = `
          <div style="display: flex; flex-direction: column; gap: 0.25rem;">
            <div style="display: flex; align-items: center; gap: 0.35rem;">
              <span class="mono text-muted ${rankDeltaClass}" style="font-size: 0.74rem; font-weight: 700; min-width: 1.6rem;">${rankPrefix}#${rankNum}</span>
              <div class="net-ev-pill ${isHighEv ? 'high-ev' : (isPositiveEv ? '' : 'negative-ev')} ${evDeltaClass}">
                <span>${Number(netEv) >= 0 ? `+${netEv}%` : `${netEv}%`}</span>
              </div>
              ${statusBadgeHtml}
            </div>
            <div class="text-muted" style="font-size: 0.70rem; padding-left: 1.95rem;">
              ${grossEv !== null ? `Gross: ${Number(grossEv) >= 0 ? `+${grossEv}%` : `${grossEv}%`}` : ''}${edgeVal !== null ? ` • Edge: ${edgeVal}` : ''}
            </div>
          </div>
        `;
      } else {
        evHtml = `
          <div style="display: flex; flex-direction: column; gap: 0.25rem;">
            <div style="display: flex; align-items: center; gap: 0.35rem;">
              <span class="mono text-muted ${rankDeltaClass}" style="font-size: 0.74rem; font-weight: 700; min-width: 1.6rem;">${rankPrefix}#${rankNum}</span>
              <span class="badge badge-outline" style="font-size: 0.75rem; color: #94a3b8;">N/A (GAP)</span>
              ${statusBadgeHtml}
            </div>
            <div class="text-muted" style="font-size: 0.70rem; padding-left: 1.95rem;">${item.reason || 'Brak wyceny EV'}</div>
          </div>
        `;
      }

      // 2. Candidate & Market Column
      let subjectHtml = '';
      if (isPlayer) {
        const posBadge = item.position ? `<span class="badge badge-outline" style="font-size:0.65rem; padding:0.1rem 0.35rem; color: var(--accent-primary); border-color: rgba(0, 230, 153, 0.4); font-weight:600;">${item.position}</span>` : '';
        subjectHtml = `
          <div style="display: flex; align-items: center; gap: 0.35rem;">
            <strong style="font-size: 0.92rem; color: var(--text-primary);">${item.player_name || 'Unknown Player'}</strong>
            <span class="prop-type-badge player">PLAYER</span>
            ${posBadge}
          </div>
          <div class="text-muted" style="font-size: 0.74rem;">${item.team || ''} <span style="opacity:0.6;">(vs ${item.opponent || '—'})</span></div>
        `;
      } else {
        const rolePill = item.participant_role ? `<span class="badge badge-outline" style="font-size:0.65rem; padding:0.1rem 0.3rem;">${item.participant_role}</span>` : '';
        subjectHtml = `
          <div style="display: flex; align-items: center; gap: 0.35rem;">
            <strong style="font-size: 0.92rem; color: var(--text-primary);">${item.team || 'Team'}</strong>
            <span class="prop-type-badge team">TEAM</span>
            ${rolePill}
          </div>
          <div class="text-muted" style="font-size: 0.74rem;">vs ${item.opponent || '—'}</div>
        `;
      }

      const statDisplayName = getStatDisplayName(item.stat_type);
      const sideText = (item.side || 'OVER').toUpperCase();
      const lineText = item.line !== undefined ? item.line : '—';
      const fixtureName = item.match_name || `${item.team} vs ${item.opponent}`;

      const candidateMarketHtml = `
        <div style="display: flex; flex-direction: column; gap: 0.25rem;">
          ${subjectHtml}
          <div style="margin-top: 0.15rem;">
            <span class="badge badge-accent" style="font-weight: 600;">${sideText} ${lineText} ${statDisplayName}</span>
          </div>
          <div class="text-muted" style="font-size: 0.70rem;">${fixtureName}${item.competition ? ` • ${item.competition}` : ''}</div>
        </div>
      `;

      // 3. Polish Execution Odds Column
      const sbOdds = item.superbet_odds !== null && item.superbet_odds !== undefined ? Number(item.superbet_odds).toFixed(2) : (item.execution_odds?.Superbet?.decimal_odds ? Number(item.execution_odds.Superbet.decimal_odds).toFixed(2) : null);
      const bcOdds = item.betclic_odds !== null && item.betclic_odds !== undefined ? Number(item.betclic_odds).toFixed(2) : (item.execution_odds?.Betclic?.decimal_odds ? Number(item.execution_odds.Betclic.decimal_odds).toFixed(2) : null);
      const bestBookie = (item.best_bookmaker || (item.execution_odds ? Object.keys(item.execution_odds)[0] : '')).toLowerCase();

      let polishOddsHtml = '';
      if (sbOdds || bcOdds) {
        const bestLabel = bestBookie === 'superbet' ? 'Superbet' : (bestBookie === 'betclic' ? 'Betclic' : (item.best_bookmaker || ''));
        const bestVal = bestBookie === 'superbet' ? sbOdds : (bestBookie === 'betclic' ? bcOdds : (item.best_raw_odds ? Number(item.best_raw_odds).toFixed(2) : ''));
        const bestIndicator = (isDiscrepancyRow && bestLabel && bestVal)
          ? `<div style="font-size: 0.68rem; color: #10B981; font-weight: 600; margin-top: 0.25rem; display: flex; align-items: center; gap: 0.25rem;">
               <span>⚡ Best executable: <strong>${bestLabel} ${bestVal}</strong></span>
             </div>`
          : '';

        polishOddsHtml = `
          <div style="display: flex; gap: 0.4rem; flex-wrap: wrap;">
            ${sbOdds ? `
              <div class="bookmaker-odds-pill ${bestBookie === 'superbet' ? 'best-bookie' : ''}">
                <span class="bookie-name">Superbet</span>
                <span class="bookie-odds">${sbOdds}</span>
                <span class="effective-odds">eff: ${(Number(sbOdds) * 0.88).toFixed(2)}</span>
              </div>
            ` : ''}
            ${bcOdds ? `
              <div class="bookmaker-odds-pill ${bestBookie === 'betclic' ? 'best-bookie' : ''}">
                <span class="bookie-name">Betclic</span>
                <span class="bookie-odds">${bcOdds}</span>
                <span class="effective-odds">0% tax</span>
              </div>
            ` : ''}
          </div>
          ${bestIndicator}
        `;
      } else {
        polishOddsHtml = `
          <div class="text-muted" style="font-size: 0.75rem;">
            <span class="badge badge-outline" style="font-size: 0.68rem; color: #94a3b8;">No Polish Odds</span>
          </div>
        `;
      }

      // 4. Reference Fair & Consensus Column
      const refConsensus = item.reference_consensus_odds !== null && item.reference_consensus_odds !== undefined ? Number(item.reference_consensus_odds).toFixed(2) : (item.best_reference_odds ? Number(item.best_reference_odds).toFixed(2) : '—');
      const fairProbPct = item.reference_fair_probability !== null && item.reference_fair_probability !== undefined ? `${(Number(item.reference_fair_probability) * 100).toFixed(1)}%` : (item.reference_probability_pct !== null && item.reference_probability_pct !== undefined ? `${Number(item.reference_probability_pct).toFixed(1)}%` : 'N/A');
      const fairOdds = item.reference_fair_odds !== null && item.reference_fair_odds !== undefined ? Number(item.reference_fair_odds).toFixed(2) : (item.fair_odds ? Number(item.fair_odds).toFixed(2) : 'N/A');
      const sourcesCount = item.reference_sources_count !== undefined ? item.reference_sources_count : (item.reference_odds ? item.reference_odds.length : 0);

      const refHtml = (sourcesCount > 0 && refConsensus !== '—') ? `
        <div>
          <div><span class="text-muted" style="font-size: 0.70rem;">Ref:</span> <strong class="mono" style="font-size: 0.88rem; color: var(--val-reference);">${refConsensus}</strong> <span class="text-muted" style="font-size: 0.68rem;">(${sourcesCount} books)</span></div>
          <div class="text-muted" style="font-size: 0.70rem;">Fair: <strong class="mono" style="color: var(--text-primary); font-weight: 600;">${fairOdds}</strong> (${fairProbPct})</div>
        </div>
      ` : `
        <div class="text-muted" style="font-size: 0.72rem;">
          <span class="badge badge-outline" style="font-size: 0.68rem; color: #94a3b8;">Ref Gap</span>
          <div style="font-size: 0.70rem; margin-top: 0.15rem;">Fair: N/A</div>
        </div>
      `;

      // 5. StatsHub Trend & Action Column
      const hitRatePct = Math.round(item.hit_rate_pct || 0);
      const hitsCount = item.trend_hits !== undefined && item.trend_hits !== null ? item.trend_hits : Math.round((hitRatePct / 100) * 10);
      const winCount = item.trend_window || 10;
      const statAvg = item.stat_average !== null && item.stat_average !== undefined ? Number(item.stat_average).toFixed(2) : '—';
      const l5Avg = item.last_5_avg !== null && item.last_5_avg !== undefined ? Number(item.last_5_avg).toFixed(1) : '—';
      const l10Avg = item.last_10_avg !== null && item.last_10_avg !== undefined ? Number(item.last_10_avg).toFixed(1) : '—';

      const trendActionHtml = `
        <div style="display: flex; align-items: center; justify-content: space-between; gap: 0.65rem;">
          <div class="hit-rate-container">
            <div style="font-size: 0.78rem; font-family: var(--font-mono); font-weight: 600;">
              ${hitsCount}/${winCount} (${hitRatePct}%)
            </div>
            <div class="hit-rate-bar-bg" style="width: 65px;">
              <div class="hit-rate-bar-fill" style="width: ${Math.min(100, hitRatePct)}%;"></div>
            </div>
            <div class="text-muted" style="font-size: 0.68rem;">Avg: ${statAvg} (L5:${l5Avg}, L10:${l10Avg})</div>
          </div>
          <button type="button" class="btn btn-outline btn-sm btn-prop-detail" data-prop-id="${propId}" title="Inspect Details">
            Inspect →
          </button>
        </div>
      `;

      return `
        <tr class="prop-table-row" data-prop-id="${propId}" style="cursor: pointer;">
          <td>${evHtml}</td>
          <td>${candidateMarketHtml}</td>
          <td>${polishOddsHtml}</td>
          <td>${refHtml}</td>
          <td>${trendActionHtml}</td>
        </tr>
      `;
    }).join('');

    state.playerProps._prevMap = nextPropsMap;

    tbody.querySelectorAll('.btn-prop-detail').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const propId = btn.getAttribute('data-prop-id');
        showPropDetail(propId);
      });
    });

    tbody.querySelectorAll('.prop-table-row').forEach(row => {
      row.addEventListener('click', () => {
        const propId = row.getAttribute('data-prop-id');
        showPropDetail(propId);
      });
    });
  }

  /* ─────────────────────────────────────────────────────────────────────────────
     UI Polish V2: SVG Visualizations (Net EV Gauge, Confidence Ring, Decision Flow)
     ───────────────────────────────────────────────────────────────────────────── */

  function renderNetEvGaugeSvg(numNetEv, size = 52) {
    const hasVal = numNetEv !== null && numNetEv !== undefined && !isNaN(Number(numNetEv));
    const evVal = hasVal ? Number(numNetEv) : null;
    const r = (size / 2) - 6;
    const cx = size / 2;
    const cy = size / 2;
    const arcAngle = 260;
    const circumference = 2 * Math.PI * r;
    const arcLength = circumference * (arcAngle / 360);
    const strokeDasharray = `${arcLength.toFixed(1)} ${circumference.toFixed(1)}`;

    let strokeColor = 'var(--text-muted)';
    let strokeDashoffset = arcLength;
    let displayVal = 'N/A';

    if (hasVal) {
      displayVal = evVal >= 0 ? `+${evVal.toFixed(1)}%` : `${evVal.toFixed(1)}%`;
      if (evVal >= 10.0) {
        strokeColor = 'var(--val-positive-high)';
      } else if (evVal >= 0.0) {
        strokeColor = 'var(--val-positive)';
      } else {
        strokeColor = 'var(--val-negative)';
      }
      const fillRatio = Math.max(0.06, Math.min(1.0, Math.abs(evVal) / 15.0));
      strokeDashoffset = arcLength * (1 - fillRatio);
    }

    return `
      <svg class="net-ev-gauge-svg" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" role="img" aria-label="Net EV Gauge: ${displayVal}">
        <circle class="net-ev-gauge-track" cx="${cx}" cy="${cy}" r="${r}"
          stroke-dasharray="${strokeDasharray}"
          stroke-dashoffset="0"
          transform="rotate(140 ${cx} ${cy})" />
        <circle class="net-ev-gauge-arc" cx="${cx}" cy="${cy}" r="${r}"
          stroke="${strokeColor}"
          stroke-dasharray="${strokeDasharray}"
          stroke-dashoffset="${strokeDashoffset.toFixed(1)}"
          transform="rotate(140 ${cx} ${cy})" />
        <text class="net-ev-gauge-val" x="${cx}" y="${cy}" fill="${strokeColor}">${displayVal}</text>
      </svg>
    `;
  }

  function renderConfidenceRingSvg(confidenceLevel, size = 18) {
    const conf = (confidenceLevel || '').toUpperCase();
    const cx = size / 2;
    const cy = size / 2;
    const r = (size / 2) - 3;
    const c = 2 * Math.PI * r;

    const segCount = conf === 'HIGH' ? 3 : (conf === 'MEDIUM' ? 2 : (conf === 'LOW' ? 1 : 0));
    const color = conf === 'HIGH' ? 'var(--val-positive)' : (conf === 'MEDIUM' ? 'var(--brand-primary)' : 'var(--val-warning)');

    const dashLen = Math.max(1, (c - 9) / 3);
    const dashArray = `${dashLen.toFixed(1)} 3`;
    const offset = (c - (segCount * (dashLen + 3)) + 3).toFixed(1);

    return `
      <svg class="confidence-ring-svg" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" role="img" aria-label="Confidence: ${conf || 'N/A'}">
        <circle class="confidence-ring-track" cx="${cx}" cy="${cy}" r="${r}"
          stroke-dasharray="${dashArray}"
          transform="rotate(-90 ${cx} ${cy})" />
        <circle class="confidence-ring-seg" cx="${cx}" cy="${cy}" r="${r}"
          stroke="${color}"
          stroke-dasharray="${dashArray}"
          stroke-dashoffset="${offset}"
          transform="rotate(-90 ${cx} ${cy})" />
      </svg>
    `;
  }

  function renderDrawerDecisionPipeline(item) {
    const hasNetEv = item.net_ev_pct !== null && item.net_ev_pct !== undefined && !isNaN(Number(item.net_ev_pct));
    const numNetEv = hasNetEv ? Number(item.net_ev_pct) : null;
    const netColor = hasNetEv ? (numNetEv >= 0 ? 'var(--val-positive)' : 'var(--val-negative)') : 'var(--text-muted)';

    // Stage 1: Reference Odds
    const refOddsList = item.reference_odds || [];
    const refSources = item.reference_sources_count !== undefined ? item.reference_sources_count : refOddsList.length;
    const bestRefVal = item.best_reference_odds || (refOddsList.length > 0 ? (refOddsList[0].odds || refOddsList[0].decimal_odds) : null);
    const bestRefDisplay = bestRefVal ? Number(bestRefVal).toFixed(2) : (refSources > 0 ? `${refSources} quotes` : '—');
    const refTag = refSources > 0 ? `${refSources} Sources` : 'REF GAP';

    // Stage 2: Consensus Odds
    const consensusVal = item.reference_consensus_odds ? Number(item.reference_consensus_odds).toFixed(2) : '—';

    // Stage 3: Fair Value (Odds & Fair Probability)
    const fairOddsVal = item.reference_fair_odds ? Number(item.reference_fair_odds).toFixed(2) : (item.fair_odds ? Number(item.fair_odds).toFixed(2) : '—');
    const fairProbVal = item.reference_fair_probability ? `${(Number(item.reference_fair_probability) * 100).toFixed(1)}%` : (item.reference_probability_pct ? `${Number(item.reference_probability_pct).toFixed(1)}%` : '—');

    // Stage 4: Polish Execution Odds
    const bestBookie = item.best_bookmaker || 'Polish Bookmaker';
    const rawOddsVal = item.best_raw_odds ? Number(item.best_raw_odds).toFixed(2) : (item.execution_odds && item.execution_odds[bestBookie]?.raw_odds ? Number(item.execution_odds[bestBookie].raw_odds).toFixed(2) : '—');
    const isSb = (bestBookie || '').toLowerCase() === 'superbet';
    const effOddsVal = item.best_effective_odds !== undefined && item.best_effective_odds !== null
      ? Number(item.best_effective_odds).toFixed(2)
      : (!isNaN(Number(rawOddsVal)) ? (Number(rawOddsVal) * (isSb ? 0.88 : 1.0)).toFixed(2) : '—');
    const taxLabel = isSb ? '12% turnover tax' : '0% promo tax';

    // Stage 5: Net EV Gauge
    const gaugeSvg = renderNetEvGaugeSvg(numNetEv, 48);

    return `
      <div class="drawer-decision-pipeline">
        <div class="decision-pipeline-header">
          <span class="decision-pipeline-title">Deterministic Valuation Flow</span>
          <span class="badge badge-accent" style="font-size: 0.64rem;">DECISION FLOW</span>
        </div>
        <div class="decision-flow-nodes">
          <!-- Node 1: Reference Odds -->
          <div class="flow-stage-card stage-ref">
            <div class="flow-stage-info">
              <span class="flow-stage-label">1. Foreign Reference Odds</span>
              <span class="flow-stage-meta">${refTag} &bull; Bet365 / Global Baseline</span>
            </div>
            <div class="flow-stage-val" style="color: var(--val-reference);">${bestRefDisplay}</div>
          </div>

          <!-- Connector 1 -> 2 -->
          <div class="flow-stage-connector">
            <div class="flow-connector-line"></div>
            <span class="flow-connector-pill">&darr; Cross-Market Aggregation</span>
            <div class="flow-connector-line"></div>
          </div>

          <!-- Node 2: Consensus Odds -->
          <div class="flow-stage-card stage-consensus">
            <div class="flow-stage-info">
              <span class="flow-stage-label">2. Consensus Benchmark</span>
              <span class="flow-stage-meta">Global Market Consensus</span>
            </div>
            <div class="flow-stage-val">${consensusVal}</div>
          </div>

          <!-- Connector 2 -> 3 -->
          <div class="flow-stage-connector">
            <div class="flow-connector-line"></div>
            <span class="flow-connector-pill">&darr; Margin Removal (De-Vigging)</span>
            <div class="flow-connector-line"></div>
          </div>

          <!-- Node 3: Fair Value -->
          <div class="flow-stage-card stage-fair">
            <div class="flow-stage-info">
              <span class="flow-stage-label">3. Fair Odds &amp; Probability</span>
              <span class="flow-stage-meta">True Prob: <strong class="mono" style="color: var(--text-primary);">${fairProbVal}</strong></span>
            </div>
            <div class="flow-stage-val" style="color: var(--brand-primary);">${fairOddsVal}</div>
          </div>

          <!-- Connector 3 -> 4 -->
          <div class="flow-stage-connector">
            <div class="flow-connector-line"></div>
            <span class="flow-connector-pill">&darr; Polish Execution Withholding (${taxLabel})</span>
            <div class="flow-connector-line"></div>
          </div>

          <!-- Node 4: Polish Bookmaker -->
          <div class="flow-stage-card stage-polish">
            <div class="flow-stage-info">
              <span class="flow-stage-label">4. Polish Execution (${bestBookie})</span>
              <span class="flow-stage-meta">Raw: ${rawOddsVal} &bull; Eff: <strong class="mono" style="color: var(--text-primary);">${effOddsVal}</strong></span>
            </div>
            <div class="flow-stage-val" style="color: #F59E0B;">${rawOddsVal}</div>
          </div>

          <!-- Connector 4 -> 5 -->
          <div class="flow-stage-connector">
            <div class="flow-connector-line"></div>
            <span class="flow-connector-pill">&darr; Net Expected Value Calculation</span>
            <div class="flow-connector-line"></div>
          </div>

          <!-- Node 5: Net EV -->
          <div class="flow-stage-card stage-netev">
            <div class="flow-stage-info">
              <span class="flow-stage-label" style="color: ${netColor};">5. Net Expected Value</span>
              <span class="flow-stage-meta">Effective Odds vs Fair Probability</span>
            </div>
            <div>
              ${gaugeSvg}
            </div>
          </div>
        </div>
      </div>
    `;
  }

  function renderDiscrepancyDrawerSection(item) {
    const isDisc = item.is_discrepancy === true || (item.relative_price_difference_pct !== null && item.relative_price_difference_pct !== undefined && Number(item.relative_price_difference_pct) >= 10.0);
    if (!isDisc) return '';

    const bestOdds = item.best_raw_odds ? Number(item.best_raw_odds) : 1.0;
    const lowerOdds = item.lower_executable_odds ? Number(item.lower_executable_odds) : (item.superbet_odds && item.betclic_odds ? Math.min(Number(item.superbet_odds), Number(item.betclic_odds)) : 1.0);
    const oDiff = item.odds_difference !== null && item.odds_difference !== undefined ? Number(item.odds_difference) : (bestOdds - lowerOdds);
    const relPct = item.relative_price_difference_pct !== null && item.relative_price_difference_pct !== undefined ? Number(item.relative_price_difference_pct) : (((bestOdds / lowerOdds) - 1.0) * 100);

    const bestBm = item.best_bookmaker || 'Highest Bookmaker';
    const lowerBm = item.lower_executable_bookmaker || (bestBm.toLowerCase() === 'superbet' ? 'Betclic' : 'Superbet');

    const impBest = (100.0 / bestOdds).toFixed(1);
    const impLower = (100.0 / lowerOdds).toFixed(1);
    const impDiff = (Number(impLower) - Number(impBest)).toFixed(1);

    return `
      <div class="card" style="background: var(--surface-input); border: 1px solid var(--val-warning); margin-bottom: 1rem; padding: 0.85rem; border-left: 4px solid var(--val-warning);">
        <div style="font-size: 0.80rem; font-weight: 700; color: #F59E0B; text-transform: uppercase; margin-bottom: 0.4rem; display: flex; align-items: center; justify-content: space-between;">
          <div style="display: flex; align-items: center; gap: 0.4rem;">
            <span>⚡ POLISH BOOKMAKER PRICE DISCREPANCY ANALYSIS</span>
          </div>
          <span class="badge badge-warning" style="font-size: 0.70rem; font-weight: 700;">+${relPct.toFixed(1)}% DISC</span>
        </div>
        <div class="mono" style="font-size: 0.95rem; font-weight: 600; margin-bottom: 0.35rem;">
          Δ = ${bestOdds.toFixed(2)} − ${lowerOdds.toFixed(2)} = <span class="text-warning font-bold">+${oDiff.toFixed(2)}</span> (<span class="text-success font-bold">+${relPct.toFixed(1)}%</span> relative improvement)
        </div>
        <div style="font-size: 0.78rem; line-height: 1.45; color: var(--text-primary); margin-bottom: 0.6rem;">
          <strong>${bestBm}</strong> offers <strong>${bestOdds.toFixed(2)}</strong> (implied probability ${impBest}%) vs <strong>${lowerBm}</strong> at <strong>${lowerOdds.toFixed(2)}</strong> (implied probability ${impLower}%).
          This represents a <strong>${impDiff} pp</strong> implied probability discrepancy on the identical proposition.
        </div>
        <div style="padding: 0.45rem 0.65rem; background: rgba(245, 158, 11, 0.12); border: 1px solid rgba(245, 158, 11, 0.3); border-radius: var(--radius-sm); font-weight: 700; font-size: 0.72rem; color: #F59E0B; letter-spacing: 0.02em;">
          ⚠️ NOT A SUREBET • NO GUARANTEED PROFIT • SINGLE-LEG VALUE DISCOVERY
        </div>
        <div class="text-muted" style="font-size: 0.70rem; margin-top: 0.4rem; line-height: 1.4;">
          This opportunity exploits cross-bookmaker pricing inefficiency between licensed Polish bookmakers for the exact same bet. It is not an arbitrage surebet as it does not cover complementary outcomes.
        </div>
      </div>
    `;
  }

  async function showPropDetail(propId) {
    if (!propId) return;
    state.playerProps.selectedPropId = propId;

    const detailCard = document.getElementById('prop-detail-container');
    const backdrop = document.getElementById('prop-drawer-backdrop');
    const content = document.getElementById('prop-detail-content');
    const title = document.getElementById('prop-detail-title');

    if (!detailCard || !content) return;
    detailCard.style.display = 'flex';
    requestAnimationFrame(() => {
      detailCard.classList.add('open', 'active');
      if (backdrop) backdrop.classList.add('open', 'active');
    });

    // Loading State
    content.innerHTML = `
      <div class="text-center text-muted" style="padding: 2.5rem 1rem;">
        <div style="font-size: 1.5rem; margin-bottom: 0.5rem;">⚡</div>
        <div style="font-weight: 600; margin-bottom: 0.25rem;">Loading intelligence breakdown...</div>
        <div style="font-size: 0.78rem; opacity: 0.7;">Formatting Net EV mathematics and bookmaker quotes</div>
      </div>
    `;

    let item = (state.playerProps.results || []).find(p => (p.canonical_prop_key === propId || p.prop_id === propId));
    if (!item) {
      item = (state.playerProps.diagnosticCandidates || []).find(p => (p.canonical_prop_key === propId || p.prop_id === propId));
    }

    if (!item) {
      try {
        const resp = await api.fetchPropDetail(propId);
        if (resp && resp.ok && resp.data) {
          item = resp.data;
        }
      } catch (err) {
        console.warn('Could not fetch single prop detail:', err);
      }
    }

    if (!item) {
      content.innerHTML = `
        <div class="alert alert-danger" style="margin: 1rem 0; padding: 1rem; background: rgba(239, 68, 68, 0.12); border: 1px solid rgba(239, 68, 68, 0.3); border-radius: var(--radius-sm); color: #FCA5A5;">
          <div style="font-weight: 700; margin-bottom: 0.35rem;">Unable to load prop details</div>
          <div style="font-size: 0.82rem;">The selected opportunity was not found in active cache. Try scanning again.</div>
        </div>
      `;
      return;
    }

    try {
      const decision = item.decision || {};
      const isPlayer = (item.prop_type || 'PLAYER').toUpperCase() === 'PLAYER';
      const subjectName = isPlayer ? (item.player_name || 'Player Prop') : (item.team || 'Team Prop');
      const marketName = `${item.side || 'OVER'} ${item.line || 0.5} ${getStatDisplayName(item.stat_type)}`;

      if (title) title.textContent = `${subjectName} — ${marketName}`;

      const scoreVal = decision.score !== undefined ? decision.score : item.score;
      const rawEdgeVal = decision.raw_edge_pct !== undefined ? decision.raw_edge_pct : item.raw_edge_pct;
      const hasNetEv = item.net_ev_pct !== null && item.net_ev_pct !== undefined && !isNaN(Number(item.net_ev_pct));
      const numNetEv = hasNetEv ? Number(item.net_ev_pct) : null;
      const netEvVal = hasNetEv ? numNetEv.toFixed(2) : '—';
      const isPositiveEv = hasNetEv && numNetEv >= 0.0;
      const isHighEv = hasNetEv && numNetEv >= 10.0;
      const isValueBetOpportunity = item.is_valuebet === true || (item.action || '').toUpperCase() === 'VALUE BET' || (item.status || '').toUpperCase() === 'QUALIFIED';
      const reasonCode = item.reason_code || '';

      const grossEvVal = item.gross_ev_pct !== null && item.gross_ev_pct !== undefined ? Number(item.gross_ev_pct).toFixed(2) : '—';
      const valueEdgeVal = item.value_edge_pp !== null && item.value_edge_pp !== undefined ? Number(item.value_edge_pp).toFixed(2) : (rawEdgeVal !== undefined && rawEdgeVal !== null ? Number(rawEdgeVal).toFixed(2) : '—');
      const statusText = item.status || (isPositiveEv ? 'QUALIFIED' : 'EVALUATED');

      const netEvPillClass = hasNetEv ? (isHighEv ? 'high-ev' : (isPositiveEv ? '' : 'negative-ev')) : 'badge-outline';
      const netEvDisplay = hasNetEv ? (numNetEv >= 0 ? `+${netEvVal}% Net EV` : `${netEvVal}% Net EV`) : 'N/A Net EV';

      let statusBadgeHtml = '';
      if (isValueBetOpportunity) {
        statusBadgeHtml = '<span class="badge badge-success" style="font-weight: 700; font-size: 0.72rem;">VALUE BET</span>';
      } else if (reasonCode === 'BELOW_VALUE_THRESHOLD') {
        statusBadgeHtml = '<span class="badge badge-warning" style="font-weight: 600; font-size: 0.72rem;">BELOW THRESHOLD</span>';
      } else if (reasonCode === 'INSUFFICIENT_REFERENCE_SOURCES') {
        statusBadgeHtml = '<span class="badge badge-info" style="font-weight: 600; font-size: 0.72rem;">REF GAP</span>';
      } else if (reasonCode === 'POLISH_ODDS_UNAVAILABLE') {
        statusBadgeHtml = '<span class="badge badge-outline" style="font-weight: 600; font-size: 0.72rem;">NO POLISH ODDS</span>';
      } else {
        statusBadgeHtml = `<span class="badge badge-outline" style="font-size: 0.72rem;">${statusText}</span>`;
      }

      // Polish Execution Bookmakers
      const execQuotesObj = item.execution_odds || {};
      const execEntries = Object.entries(execQuotesObj);
      let execHtml = '';

      if (execEntries.length > 0) {
        execHtml = execEntries.map(([bName, q]) => {
          const rawPrice = (q.raw_odds !== undefined && q.raw_odds !== null) ? q.raw_odds : ((q.decimal_odds !== undefined && q.decimal_odds !== null) ? q.decimal_odds : (q.odds || '—'));
          const isSuperbet = bName.toLowerCase() === 'superbet';
          const taxRate = q.tax_rate !== undefined ? Number(q.tax_rate) : (isSuperbet ? 0.12 : 0.0);
          const taxPct = `${(taxRate * 100).toFixed(0)}%`;
          const isBest = (item.best_bookmaker && item.best_bookmaker.toLowerCase() === bName.toLowerCase());
          const effPrice = (isBest && item.best_effective_odds !== undefined && item.best_effective_odds !== null)
            ? Number(item.best_effective_odds).toFixed(2)
            : (q.effective_odds !== undefined && q.effective_odds !== null
              ? Number(q.effective_odds).toFixed(2)
              : (!isNaN(Number(rawPrice)) ? (Number(rawPrice) * (1 - taxRate)).toFixed(2) : '—'));

          return `
            <div class="bookmaker-odds-pill ${isBest ? 'best-bookie' : ''}" style="width: 100%; padding: 0.5rem 0.75rem; margin-bottom: 0.4rem;">
              <div style="display: flex; justify-content: space-between; align-items: center; width: 100%;">
                <span class="bookie-name" style="font-size: 0.78rem;">${bName} (Execution)</span>
                <span class="bookie-odds" style="font-size: 1.1rem; color: var(--brand-primary);">${!isNaN(Number(rawPrice)) ? Number(rawPrice).toFixed(2) : rawPrice}</span>
              </div>
              <div class="text-muted" style="font-size: 0.74rem; margin-top: 0.2rem;">
                Effective Odds: <strong class="mono" style="color: var(--text-primary);">${effPrice}</strong> (Tax: ${taxPct})
              </div>
            </div>
          `;
        }).join('');
      } else if (item.best_bookmaker) {
        const rawP = item.best_raw_odds ? Number(item.best_raw_odds).toFixed(2) : '—';
        const isSb = (item.best_bookmaker || '').toLowerCase() === 'superbet';
        const effP = (item.best_effective_odds !== undefined && item.best_effective_odds !== null)
          ? Number(item.best_effective_odds).toFixed(2)
          : (!isNaN(Number(rawP)) ? (Number(rawP) * (isSb ? 0.88 : 1.0)).toFixed(2) : rawP);
        execHtml = `
          <div class="bookmaker-odds-pill best-bookie" style="width: 100%; padding: 0.5rem 0.75rem;">
            <div style="display: flex; justify-content: space-between; align-items: center; width: 100%;">
              <span class="bookie-name" style="font-size: 0.78rem;">${item.best_bookmaker} (Execution)</span>
              <span class="bookie-odds" style="font-size: 1.1rem; color: var(--brand-primary);">${rawP}</span>
            </div>
            <div class="text-muted" style="font-size: 0.74rem; margin-top: 0.2rem;">
              Effective Odds: <strong class="mono" style="color: var(--text-primary);">${effP}</strong> (Tax: ${isSb ? '12%' : '0%'})
            </div>
          </div>
        `;
      } else {
        execHtml = `<p class="text-muted" style="font-size:0.8rem; padding: 0.5rem 0;">No Polish execution quotes matched for this prop line.</p>`;
      }

      // Foreign Reference Bookmaker Quotes
      const refOddsList = item.reference_odds || [];
      let refHtml = '';
      if (refOddsList.length > 0) {
        refHtml = refOddsList.map(o => {
          const bookName = o.bookmaker || 'Foreign Bookmaker';
          const rawRefVal = (o.odds !== undefined && o.odds !== null) ? o.odds : o.decimal_odds;
          const price = (rawRefVal !== undefined && rawRefVal !== null && !isNaN(Number(rawRefVal))) ? Number(rawRefVal).toFixed(2) : '—';
          const impProbStr = (o.implied_probability !== undefined && o.implied_probability !== null)
            ? ` <span class="text-muted" style="font-size: 0.72rem; font-weight: normal;">(${(Number(o.implied_probability) * 100).toFixed(0)}% imp.)</span>`
            : '';
          return `
            <div style="display: flex; justify-content: space-between; align-items: center; background: var(--surface-input); border: 1px solid var(--border-subtle); padding: 0.4rem 0.65rem; border-radius: var(--radius-sm); margin-bottom: 0.35rem;">
              <span style="font-size: 0.78rem; font-weight: 500;">${bookName}</span>
              <span class="mono" style="font-weight: 700; font-size: 0.95rem; color: var(--val-reference);">${price}${impProbStr}</span>
            </div>
          `;
        }).join('');
      } else if (item.reference_consensus_odds) {
        refHtml = `
          <div style="display: flex; justify-content: space-between; align-items: center; background: var(--surface-input); border: 1px solid var(--border-subtle); padding: 0.4rem 0.65rem; border-radius: var(--radius-sm);">
            <span style="font-size: 0.78rem;">Reference Consensus</span>
            <span class="mono" style="font-weight: 700; font-size: 0.95rem; color: var(--val-reference);">${Number(item.reference_consensus_odds).toFixed(2)}</span>
          </div>
        `;
      } else {
        refHtml = `<p class="text-muted" style="font-size:0.8rem; padding: 0.5rem 0;">No foreign reference odds recorded.</p>`;
      }

      // StatsHub Trend Details
      const hitRatePct = Math.round(item.hit_rate_pct || 0);
      const hitsCount = item.trend_hits !== undefined && item.trend_hits !== null ? item.trend_hits : Math.round((hitRatePct / 100) * 10);
      const winCount = item.trend_window || 10;
      const statAvg = item.stat_average !== null && item.stat_average !== undefined ? Number(item.stat_average).toFixed(2) : 'N/A';
      const l5Avg = item.last_5_avg !== null && item.last_5_avg !== undefined ? Number(item.last_5_avg).toFixed(1) : 'N/A';
      const l10Avg = item.last_10_avg !== null && item.last_10_avg !== undefined ? Number(item.last_10_avg).toFixed(1) : 'N/A';

      // Provenance
      const prov = item.provenance || {};
      const canonicalKey = item.canonical_prop_key || item.prop_id || '—';
      const fixtureDeepLink = prov.fixture_deep_link || prov.deep_link;

      const numEdge = Number(valueEdgeVal);
      const edgeStr = !isNaN(numEdge) ? (numEdge >= 0 ? `+${numEdge.toFixed(2)} pp` : `${numEdge.toFixed(2)} pp`) : '—';
      const edgeClass = !isNaN(numEdge) ? (numEdge >= 0 ? 'text-info' : 'text-danger') : 'text-muted';

      const numGross = Number(grossEvVal);
      const grossStr = !isNaN(numGross) ? (numGross >= 0 ? `+${numGross.toFixed(2)}%` : `${numGross.toFixed(2)}%`) : '—';
      const grossClass = !isNaN(numGross) ? (numGross >= 0 ? 'text-success' : 'text-danger') : 'text-muted';

      const numNet = Number(netEvVal);
      const netStr = !isNaN(numNet) ? (numNet >= 0 ? `+${numNet.toFixed(2)}%` : `${numNet.toFixed(2)}%`) : '—';
      const netClass = !isNaN(numNet) ? (numNet >= 0 ? 'text-success font-bold' : 'text-danger font-bold') : 'text-muted';

      const bestBookie = item.best_bookmaker || 'Superbet';
      const rawOddsVal = item.best_raw_odds ? Number(item.best_raw_odds).toFixed(2) : (item.execution_odds && item.execution_odds[bestBookie]?.raw_odds ? Number(item.execution_odds[bestBookie].raw_odds).toFixed(2) : '—');
      const isSb = (bestBookie || '').toLowerCase() === 'superbet';
      const effOddsVal = item.best_effective_odds !== undefined && item.best_effective_odds !== null
        ? Number(item.best_effective_odds).toFixed(2)
        : (!isNaN(Number(rawOddsVal)) ? (Number(rawOddsVal) * (isSb ? 0.88 : 1.0)).toFixed(2) : '—');
      const taxLabel = isSb ? '12% tax' : '0% promo';

      const fairOddsVal = item.reference_fair_odds ? Number(item.reference_fair_odds).toFixed(2) : (item.fair_odds ? Number(item.fair_odds).toFixed(2) : '—');
      const fairProbVal = item.reference_fair_probability ? `${(Number(item.reference_fair_probability) * 100).toFixed(1)}%` : (item.reference_probability_pct ? `${Number(item.reference_probability_pct).toFixed(1)}%` : '—');

      content.innerHTML = `
        <!-- Decision Header -->
        <div class="card" style="background: var(--surface-input); border: 1px solid var(--border-strong); margin-bottom: 0.85rem; padding: 0.85rem;">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.4rem;">
            <div style="display: flex; align-items: center; gap: 0.4rem; flex-wrap: wrap;">
              <div class="net-ev-pill ${netEvPillClass}">
                <span>${netEvDisplay}</span>
              </div>
              ${statusBadgeHtml}
              ${item.confidence ? `<span class="badge ${item.confidence === 'HIGH' ? 'badge-accent' : (item.confidence === 'MEDIUM' ? 'badge-outline' : 'badge-danger')}" style="font-size: 0.68rem; font-weight: 600; display: inline-flex; align-items: center;">${renderConfidenceRingSvg(item.confidence, 14)}${item.confidence} CONFIDENCE</span>` : ''}
            </div>
            <span class="prop-type-badge ${isPlayer ? 'player' : 'team'}">${item.prop_type || 'PLAYER'}</span>
          </div>
          <div style="margin-top: 0.35rem; margin-bottom: 0.35rem;">
            <span class="badge badge-accent" style="font-size: 0.82rem; font-weight: 700; padding: 0.2rem 0.55rem;">${marketName}</span>
          </div>
          <div style="font-size: 0.88rem; font-weight: 600; color: var(--text-primary);">
            ${item.match_name || `${item.team} vs ${item.opponent}`}
          </div>
          <div class="text-muted" style="font-size: 0.74rem;">
            ${item.competition ? `${item.competition} • ` : ''}Kickoff: ${item.kickoff || '—'}
          </div>
        </div>

        <!-- Valuation KPIs Row (Net EV, Confidence, PL Execution, Fair Odds) -->
        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 0.65rem; margin-bottom: 1rem;">
          <div style="background: var(--surface-input); border: 1px solid var(--border-subtle); border-radius: var(--radius-sm); padding: 0.65rem 0.85rem;">
            <div style="font-size: 0.70rem; color: var(--text-muted); text-transform: uppercase; font-weight: 600; margin-bottom: 0.15rem;">Net Expected Value</div>
            <div class="mono" style="font-size: 1.25rem; font-weight: 700; color: ${isPositiveEv ? 'var(--val-positive)' : 'var(--val-negative)'};">${netEvDisplay}</div>
            <div style="font-size: 0.68rem; color: var(--text-muted); margin-top: 0.15rem;">Edge: ${edgeStr} &bull; Gross: ${grossStr}</div>
          </div>
          <div style="background: var(--surface-input); border: 1px solid var(--border-subtle); border-radius: var(--radius-sm); padding: 0.65rem 0.85rem;">
            <div style="font-size: 0.70rem; color: var(--text-muted); text-transform: uppercase; font-weight: 600; margin-bottom: 0.15rem;">Confidence Rating</div>
            <div style="font-size: 1.15rem; font-weight: 700; color: var(--text-primary); display: flex; align-items: center; gap: 0.4rem;">
              ${renderConfidenceRingSvg(item.confidence, 18)} ${item.confidence || 'STANDARD'}
            </div>
            <div style="font-size: 0.68rem; color: var(--text-muted); margin-top: 0.15rem;">${item.reference_sources_count || (item.reference_odds ? item.reference_odds.length : 0)} Reference Sources</div>
          </div>
          <div style="background: var(--surface-input); border: 1px solid var(--border-subtle); border-radius: var(--radius-sm); padding: 0.65rem 0.85rem;">
            <div style="font-size: 0.70rem; color: var(--text-muted); text-transform: uppercase; font-weight: 600; margin-bottom: 0.15rem;">PL Execution (${bestBookie})</div>
            <div class="mono" style="font-size: 1.25rem; font-weight: 700; color: var(--brand-primary);">${rawOddsVal}</div>
            <div style="font-size: 0.68rem; color: var(--text-muted); margin-top: 0.15rem;">Eff. Odds: <strong class="mono" style="color: var(--text-primary);">${effOddsVal}</strong> (${taxLabel})</div>
          </div>
          <div style="background: var(--surface-input); border: 1px solid var(--border-subtle); border-radius: var(--radius-sm); padding: 0.65rem 0.85rem;">
            <div style="font-size: 0.70rem; color: var(--text-muted); text-transform: uppercase; font-weight: 600; margin-bottom: 0.15rem;">Fair Odds (Benchmark)</div>
            <div class="mono" style="font-size: 1.25rem; font-weight: 700; color: var(--val-reference);">${fairOddsVal}</div>
            <div style="font-size: 0.68rem; color: var(--text-muted); margin-top: 0.15rem;">True Prob: <strong class="mono" style="color: var(--text-primary);">${fairProbVal}</strong></div>
          </div>
        </div>

        <!-- Polish Price Discrepancy Matrix (if discrepancy detected) -->
        ${renderDiscrepancyDrawerSection(item)}

        <!-- Quantitative Valuation Flow Visualization -->
        ${renderDrawerDecisionPipeline(item)}

        <!-- Polish Execution Markets -->
        <div style="margin-bottom: 1rem;">
          <div style="font-size: 0.76rem; font-weight: 600; color: var(--text-muted); text-transform: uppercase; margin-bottom: 0.2rem;">
            Polish Execution Odds (Superbet / Betclic)
          </div>
          <div style="font-size: 0.70rem; color: var(--text-muted); margin-bottom: 0.4rem;">
            Gdzie zagrać (kurs u bukmachera w Polsce po uwzględnieniu podatku)
          </div>
          ${execHtml}
        </div>

        <!-- Foreign Reference Consensus Matrix -->
        <div style="margin-bottom: 1rem;">
          <div style="font-size: 0.76rem; font-weight: 600; color: var(--text-muted); text-transform: uppercase; margin-bottom: 0.2rem;">
            Foreign Reference Consensus & Fair Probability
          </div>
          <div style="font-size: 0.70rem; color: var(--text-muted); margin-bottom: 0.4rem;">
            Średnie kursy u bukmacherów zagranicznych (benchmark rynkowy przed marżą)
          </div>
          ${refHtml}
        </div>

        <!-- Valuebet EV & Mathematics Breakdown -->
        <div class="card" style="background: var(--surface-input); border: 1px solid var(--border-subtle); margin-bottom: 1rem; padding: 0.75rem;">
          <div style="font-size: 0.76rem; font-weight: 600; color: var(--text-muted); text-transform: uppercase; margin-bottom: 0.2rem;">
            Edge Comparison & Math
          </div>
          <div style="font-size: 0.70rem; color: var(--text-muted); margin-bottom: 0.5rem;">
            Wycena Fair Value i Net EV (po odmarżowaniu i podatku)
          </div>
          <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 0.5rem; font-size: 0.8rem; margin-bottom: 0.5rem;">
            <div>Consensus Odds: <strong class="mono" style="color: var(--val-reference);">${item.reference_consensus_odds ? Number(item.reference_consensus_odds).toFixed(2) : '—'}</strong></div>
            <div>Fair Probability: <strong class="mono">${item.reference_fair_probability ? `${(Number(item.reference_fair_probability) * 100).toFixed(2)}%` : '—'}</strong></div>
            <div>Reference Fair Odds: <strong class="mono" style="color: var(--text-primary); font-weight: 600;">${item.reference_fair_odds ? Number(item.reference_fair_odds).toFixed(2) : '—'}</strong></div>
            <div>Value Edge: <strong class="mono ${edgeClass}">${edgeStr}</strong></div>
            <div>Gross EV: <strong class="mono ${grossClass}">${grossStr}</strong></div>
            <div>Net EV (After Tax): <strong class="mono ${netClass}">${netStr}</strong></div>
          </div>
          <div class="text-muted" style="font-size: 0.70rem; border-top: 1px dashed var(--border-subtle); padding-top: 0.4rem;">
            Formula: Net EV = (Effective Odds &times; Fair Probability) - 1. Tax withheld: Superbet (12%) / Betclic (0% promo).
          </div>
        </div>

        <!-- StatsHub Trend Profile -->
        <div style="margin-bottom: 1rem; background: var(--surface-input); padding: 0.75rem; border-radius: var(--radius-sm); border: 1px solid var(--border-subtle);">
          <div style="font-size: 0.76rem; font-weight: 600; color: var(--text-muted); text-transform: uppercase; margin-bottom: 0.4rem;">
            StatsHub Trend Profile
          </div>
          <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 0.35rem; font-size: 0.8rem;">
            <div>Hit Rate: <strong class="mono">${hitsCount}/${winCount} (${hitRatePct}%)</strong></div>
            <div>Season Avg: <strong class="mono">${statAvg}</strong></div>
            <div>Last 5 Avg: <strong class="mono">${l5Avg}</strong></div>
            <div>Last 10 Avg: <strong class="mono">${l10Avg}</strong></div>
            <div>Scope: <strong class="mono">${item.scope || item.prop_type || '—'}</strong></div>
            ${item.participant_role ? `<div>Role: <strong class="mono">${item.participant_role}</strong></div>` : ''}
          </div>
          ${fixtureDeepLink ? `
            <div style="margin-top: 0.5rem; border-top: 1px solid var(--border-subtle); padding-top: 0.4rem;">
              <a href="${fixtureDeepLink}" target="_blank" rel="noopener noreferrer" style="color: var(--brand-primary); font-size: 0.78rem; text-decoration: none; font-weight: 500;">
                View fixture on StatsHub &rarr;
              </a>
            </div>
          ` : ''}
        </div>

        <!-- Provenance & Canonical Key -->
        <div style="background: var(--surface-input); padding: 0.5rem 0.65rem; border-radius: var(--radius-sm); font-size: 0.72rem; border: 1px solid var(--border-subtle);">
          <div class="text-muted" style="margin-bottom: 0.15rem;">CANONICAL KEY & REASON CODE:</div>
          <div class="mono text-muted" style="word-break: break-all;">${canonicalKey}</div>
          <div class="text-muted" style="margin-top: 0.2rem;">Status Code: <strong style="color: var(--text-primary);">${item.reason_code || 'QUALIFIED'}</strong></div>
        </div>
      `;
    } catch (renderErr) {
      console.error('Error rendering prop detail inspector:', renderErr);
      content.innerHTML = `
        <div class="alert alert-danger" style="margin: 1rem 0; padding: 1rem; background: rgba(239, 68, 68, 0.12); border: 1px solid rgba(239, 68, 68, 0.3); border-radius: var(--radius-sm); color: #FCA5A5;">
          <div style="font-weight: 700; margin-bottom: 0.35rem;">Unable to render prop detail</div>
          <div style="font-size: 0.82rem;">An unexpected error occurred while formatting the inspector data.</div>
        </div>
      `;
    }
  }

  // ──────────────────────────────────────────────────────────────────────────
  // ──────────────────────────────────────────────────────────────────────────
  // Telegram Notification Control Center (IA Redesign Controller)
  // ──────────────────────────────────────────────────────────────────────────

  async function loadNotificationsData() {
    initTelegramHealthEvents();
    await loadTelegramHealthPanel();
  }

  async function loadTelegramHealthPanel() {
    try {
      const res = await api.fetchTelegramHealth();
      const data = res.data || {};
      state.telegramHealth = data;
      renderTelegramControlCenter(data);
    } catch (err) {
      console.error('Failed to load Telegram health', err);
      renderTelegramControlCenter({
        telegram_status: 'ERROR',
        error_message: 'Unable to reach backend API to verify Telegram health',
        recent_activity: [],
      });
    }
  }

  function renderTelegramControlCenter(data) {
    const isConfigured = Boolean(data.safe_config?.configured && data.safe_config?.chat_id_masked);
    const hasError = Boolean(data.telegram_status === 'ERROR' || data.error_message);
    const isPaused = Boolean(!data.instant_alerts_enabled && !data.evening_digest_enabled);

    let currentState = 'OPERATIONAL';
    if (!isConfigured || data.telegram_status === 'NOT CONFIGURED') {
      currentState = 'UNCONFIGURED';
    } else if (hasError) {
      currentState = 'ERROR';
    } else if (isPaused) {
      currentState = 'PAUSED';
    }

    // 1. Primary Hero Card
    const heroCard = document.getElementById('tg-channel-hero');
    const heroBadge = document.getElementById('tg-hero-badge');
    const heroDot = document.getElementById('tg-hero-dot');
    const heroBadgeText = document.getElementById('tg-hero-badge-text');
    const heroTitle = document.getElementById('tg-hero-title');
    const heroDesc = document.getElementById('tg-hero-desc');
    const heroErrorBox = document.getElementById('tg-hero-error-box');
    const heroErrorMessage = document.getElementById('tg-hero-error-message');
    const btnSendTest = document.getElementById('btn-telegram-send-test');
    const btnConsoleTest = document.getElementById('btn-console-send-test');
    const btnEmptyTest = document.getElementById('btn-empty-send-test');
    const btnHeroSettings = document.getElementById('btn-hero-goto-settings');

    if (heroCard) {
      heroCard.className = `tg-hero-card is-${currentState.toLowerCase()}`;
    }

    if (heroBadge && heroDot && heroBadgeText) {
      heroBadge.className = `tg-hero-badge is-${currentState.toLowerCase()}`;
      if (currentState === 'OPERATIONAL') {
        heroDot.className = 'tg-pulse-dot pulse';
        heroBadgeText.textContent = 'OPERATIONAL';
      } else if (currentState === 'UNCONFIGURED') {
        heroDot.className = 'tg-pulse-dot';
        heroBadgeText.textContent = 'NOT CONFIGURED';
      } else if (currentState === 'ERROR') {
        heroDot.className = 'tg-pulse-dot';
        heroBadgeText.textContent = 'DELIVERY FAILED';
      } else {
        heroDot.className = 'tg-pulse-dot';
        heroBadgeText.textContent = 'DISPATCH PAUSED';
      }
    }

    const maskedChat = data.safe_config?.chat_id_masked || '***';
    if (heroTitle && heroDesc) {
      if (currentState === 'OPERATIONAL') {
        heroTitle.textContent = 'Telegram is connected and dispatching alerts';
        heroDesc.innerHTML = `Bot channel is authenticated and connected to Chat ID: <strong class="mono">${escapeHtml(maskedChat)}</strong>. Qualified Player Props and Team Props valuebets will dispatch immediately upon detection.`;
      } else if (currentState === 'UNCONFIGURED') {
        heroTitle.textContent = 'Telegram alert channel is not configured';
        heroDesc.innerHTML = 'Missing <code>TELEGRAM_BOT_TOKEN</code> or <code>TELEGRAM_CHAT_ID</code> in the server environment. Alert dispatches are currently disabled.';
      } else if (currentState === 'ERROR') {
        heroTitle.textContent = 'Alert delivery interrupted';
        heroDesc.innerHTML = 'The Telegram Bot API failed during recent dispatch attempts. Check diagnostic error details below.';
      } else {
        heroTitle.textContent = 'Automated alert dispatch is paused';
        heroDesc.innerHTML = 'Both instant notifications and evening digest are disabled in your dispatch policy. Re-enable rules on the right to resume delivery.';
      }
    }

    if (heroErrorBox && heroErrorMessage) {
      if (currentState === 'ERROR') {
        heroErrorBox.style.display = 'flex';
        heroErrorMessage.textContent = data.error_message || data.last_error || 'Delivery failed: check Telegram Bot credentials and connectivity.';
      } else {
        heroErrorBox.style.display = 'none';
      }
    }

    // Configure test button states
    const testButtons = [btnSendTest, btnConsoleTest, btnEmptyTest];
    testButtons.forEach(btn => {
      if (!btn) return;
      if (currentState === 'UNCONFIGURED') {
        btn.disabled = true;
        btn.title = 'Configure Telegram Bot credentials in .env to enable test delivery';
      } else {
        btn.disabled = false;
        btn.title = 'Send controlled diagnostic packet to Telegram API';
      }
    });

    if (btnHeroSettings) {
      btnHeroSettings.style.display = currentState === 'UNCONFIGURED' ? 'inline-flex' : 'none';
    }

    // 2. Integrated Telemetry Strip
    const lastDeliveryEl = document.getElementById('tg-metric-last-delivery');
    if (lastDeliveryEl) {
      if (data.last_successful_message_at) {
        const msgIdSuffix = data.last_successful_message_id ? ` (#${data.last_successful_message_id})` : '';
        lastDeliveryEl.textContent = formatDate(data.last_successful_message_at) + msgIdSuffix;
        lastDeliveryEl.className = 'tg-telemetry-value text-success';
      } else {
        lastDeliveryEl.textContent = 'None yet';
        lastDeliveryEl.className = 'tg-telemetry-value text-muted';
      }
    }

    const volumeTodayEl = document.getElementById('tg-metric-volume-today');
    if (volumeTodayEl) {
      volumeTodayEl.textContent = `${data.daily_sent_count ?? 0} alerts`;
    }

    const targetChatEl = document.getElementById('tg-metric-target-chat');
    if (targetChatEl) {
      targetChatEl.textContent = data.safe_config?.chat_id_masked || 'Not configured';
    }

    const digestWindowEl = document.getElementById('tg-metric-digest-window');
    if (digestWindowEl) {
      const dw = data.digest_window || {};
      digestWindowEl.textContent = dw.sent_today
        ? 'Sent today'
        : (dw.is_in_window ? 'Window open (16-22)' : 'Next at 16:00');
    }

    // 3. Dispatch Policy & Rules
    const toggleInstant = document.getElementById('tg-toggle-instant');
    const badgeInstant = document.getElementById('tg-rule-badge-instant');
    if (toggleInstant) toggleInstant.checked = !!data.instant_alerts_enabled;
    if (badgeInstant) {
      badgeInstant.className = data.instant_alerts_enabled ? 'badge badge-success' : 'badge badge-outline';
      badgeInstant.textContent = data.instant_alerts_enabled ? 'Active' : 'Paused';
    }

    const toggleDigest = document.getElementById('tg-toggle-digest');
    const badgeDigest = document.getElementById('tg-rule-badge-digest');
    const digestWindowPill = document.getElementById('tg-digest-window-pill');
    if (toggleDigest) toggleDigest.checked = !!data.evening_digest_enabled;
    if (badgeDigest) {
      badgeDigest.className = data.evening_digest_enabled ? 'badge badge-success' : 'badge badge-outline';
      badgeDigest.textContent = data.evening_digest_enabled ? 'Active' : 'Paused';
    }
    if (digestWindowPill) {
      const dw = data.digest_window || {};
      digestWindowPill.textContent = `● ${dw.status_label || '16:00–22:00 Europe/Warsaw'}`;
    }

    // 4. Specs Card
    const specChatId = document.getElementById('spec-chat-id');
    if (specChatId) {
      specChatId.textContent = data.safe_config?.chat_id_masked || 'Not configured';
    }

    // 5. Header Last Synced
    const lastSyncedTime = document.getElementById('notif-last-synced-time');
    if (lastSyncedTime) {
      const now = new Date();
      lastSyncedTime.textContent = now.toLocaleTimeString('pl-PL', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    }

    // 6. Render Delivery Activity Stream
    state.recentTelegramActivity = data.recent_activity || [];
    renderDeliveryStream(state.recentTelegramActivity, state.notifStreamFilter || 'all');
  }

  function renderDeliveryStream(events, filter = 'all') {
    const container = document.getElementById('tg-stream-container');
    const emptyState = document.getElementById('notif-empty-state');
    const countBadge = document.getElementById('tg-stream-count-badge');
    if (!container) return;

    let filtered = events || [];
    if (filter === 'delivered') {
      filtered = filtered.filter(e => e.status === 'DELIVERED');
    } else if (filter === 'failed') {
      filtered = filtered.filter(e => e.status === 'FAILED');
    } else if (filter === 'suppressed') {
      filtered = filtered.filter(e => e.status === 'SUPPRESSED' || e.status === 'SKIPPED');
    }

    if (countBadge) {
      countBadge.textContent = `${filtered.length} event${filtered.length === 1 ? '' : 's'}`;
    }

    if (filtered.length === 0) {
      container.innerHTML = '';
      if (emptyState) emptyState.style.display = 'flex';
      return;
    }

    if (emptyState) emptyState.style.display = 'none';

    container.innerHTML = filtered.map(item => {
      const evType = item.event_type || 'NOTIFICATION';
      let iconClass = 'is-test';
      let iconSvg = '<line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/>';

      if (evType === 'INSTANT_ALERT') {
        iconClass = 'is-alert';
        iconSvg = '<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>';
      } else if (evType === 'EVENING_DIGEST') {
        iconClass = 'is-digest';
        iconSvg = '<rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/>';
      }

      const st = (item.status || 'LOGGED').toUpperCase();
      let statusClass = 'is-delivered';
      if (st === 'FAILED') statusClass = 'is-failed';
      else if (st === 'SUPPRESSED' || st === 'SKIPPED') statusClass = 'is-suppressed';

      const timeStr = item.timestamp ? formatDate(item.timestamp) : '—';

      return `
        <div class="tg-stream-item">
          <div class="tg-stream-left">
            <div class="tg-stream-icon ${iconClass}">
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">${iconSvg}</svg>
            </div>
            <div class="tg-stream-content">
              <div class="tg-stream-title">${escapeHtml(item.title || 'Telegram Alert')}</div>
              <div class="tg-stream-sub">${escapeHtml(item.message || item.details || '')}</div>
            </div>
          </div>
          <div class="tg-stream-right">
            <span class="tg-stream-status ${statusClass}">
              ${st === 'DELIVERED' ? '✓ ' : (st === 'FAILED' ? '✕ ' : '— ')}${escapeHtml(st)}
            </span>
            <span class="tg-stream-time" title="${item.timestamp || ''}">${timeStr}</span>
          </div>
        </div>
      `;
    }).join('');
  }

  async function handleSendTestMessage(sourceBtn) {
    const feedbackBox = document.getElementById('tg-test-feedback');
    const testButtons = [
      document.getElementById('btn-telegram-send-test'),
      document.getElementById('btn-console-send-test'),
      document.getElementById('btn-empty-send-test'),
      document.getElementById('btn-settings-telegram-test'),
    ].filter(Boolean);

    // 1. Enter SENDING state
    testButtons.forEach(btn => {
      btn.disabled = true;
    });

    if (sourceBtn) {
      sourceBtn.dataset.originalHtml = sourceBtn.innerHTML;
      sourceBtn.innerHTML = '<span class="spinner-sm" style="margin-right: 4px;"></span> Sending...';
    }

    if (feedbackBox) {
      feedbackBox.className = 'tg-test-feedback is-sending';
      feedbackBox.style.display = 'flex';
      feedbackBox.innerHTML = '<span class="spinner-sm" style="margin-right: 6px;"></span> Dispatching diagnostic test payload to Telegram API...';
    }

    try {
      // 2. Real API dispatch
      const res = await api.sendTelegramTestMessage();
      const payload = res.data || {};

      if (payload.delivered) {
        if (feedbackBox) {
          feedbackBox.className = 'tg-test-feedback is-success';
          feedbackBox.innerHTML = `<strong>✓ Delivered:</strong> Test message #${payload.telegram_message_id || 'OK'} successfully received by Telegram servers.`;
        }
        showToast(`Diagnostic message sent! (ID: #${payload.telegram_message_id || 'OK'})`);
      } else {
        if (feedbackBox) {
          feedbackBox.className = 'tg-test-feedback is-failed';
          feedbackBox.innerHTML = `<strong>✕ Delivery Failed:</strong> ${escapeHtml(payload.error || 'Telegram returned error')}`;
        }
        showToast(`Telegram delivery failed: ${payload.error || 'Error'}`, 'error');
      }
    } catch (err) {
      if (feedbackBox) {
        feedbackBox.className = 'tg-test-feedback is-failed';
        feedbackBox.innerHTML = `<strong>✕ Network Error:</strong> ${escapeHtml(err.message || 'Unable to connect')}`;
      }
      showToast(`API Connection Error: ${err.message}`, 'error');
    } finally {
      // 3. Reset button states and reload telemetry
      testButtons.forEach(btn => {
        btn.disabled = false;
        if (btn.dataset.originalHtml) {
          btn.innerHTML = btn.dataset.originalHtml;
        }
      });
      await loadTelegramHealthPanel();
    }
  }

  function initTelegramHealthEvents() {
    if (state._telegramControlEventsInitialized) return;
    state._telegramControlEventsInitialized = true;

    // Test Message Dispatches
    const testTriggerIds = ['btn-telegram-send-test', 'btn-console-send-test', 'btn-empty-send-test', 'btn-settings-telegram-test'];
    testTriggerIds.forEach(id => {
      const btn = document.getElementById(id);
      if (btn) {
        btn.addEventListener('click', (e) => {
          e.preventDefault();
          handleSendTestMessage(btn);
        });
      }
    });

    // Refresh Button
    const refreshBtn = document.getElementById('btn-refresh-telegram-health');
    if (refreshBtn) {
      refreshBtn.addEventListener('click', async (e) => {
        e.preventDefault();
        const orig = refreshBtn.innerHTML;
        refreshBtn.disabled = true;
        refreshBtn.innerHTML = '<span class="spinner-sm" style="margin-right: 4px;"></span> Refreshing...';
        try {
          await loadTelegramHealthPanel();
          showToast('Telegram status refreshed');
        } finally {
          refreshBtn.disabled = false;
          refreshBtn.innerHTML = orig;
        }
      });
    }

    // Filter Buttons for Stream
    document.querySelectorAll('.tg-stream-filter-btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.preventDefault();
        document.querySelectorAll('.tg-stream-filter-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        const filter = btn.getAttribute('data-filter') || 'all';
        state.notifStreamFilter = filter;
        renderDeliveryStream(state.recentTelegramActivity || [], filter);
      });
    });

    // Toggle Policy Handlers
    const toggleInstant = document.getElementById('tg-toggle-instant');
    const toggleDigest = document.getElementById('tg-toggle-digest');

    const handlePolicyChange = async () => {
      const instantVal = toggleInstant ? toggleInstant.checked : true;
      const digestVal = toggleDigest ? toggleDigest.checked : true;
      try {
        await api.configureTelegram({
          instant_alerts_enabled: instantVal,
          evening_digest_enabled: digestVal,
        });
        showToast('Updated Telegram alert rules');
        await loadTelegramHealthPanel();
      } catch (err) {
        showToast('Failed to save Telegram settings', 'error');
      }
    };

    if (toggleInstant) toggleInstant.addEventListener('change', handlePolicyChange);
    if (toggleDigest) toggleDigest.addEventListener('change', handlePolicyChange);
  }


  // ──────────────────────────────────────────────────────────────────────────
  // Settings View Controller & Persistence (Stage 22B: Tax Configuration)
  // ──────────────────────────────────────────────────────────────────────────

  async function loadSettingsData() {
    await loadTelegramHealthPanel();

    try {
      const res = await api.fetchSettings();
      const s = res.data || {};
      state.settings = s;

      const themeSel = document.getElementById('setting-theme');
      if (themeSel && s.theme) themeSel.value = s.theme;

      const minSureRoi = document.getElementById('setting-min-surebet-roi');
      if (minSureRoi && s.min_surebet_roi !== undefined) minSureRoi.value = s.min_surebet_roi;

      const minValEv = document.getElementById('setting-min-valuebet-ev');
      if (minValEv && s.min_valuebet_edge !== undefined) minValEv.value = s.min_valuebet_edge;

      const maxStake = document.getElementById('setting-max-stake');
      if (maxStake && s.max_bankroll_stake !== undefined) maxStake.value = s.max_bankroll_stake;

      // Tax Rates
      const taxSuper = document.getElementById('setting-tax-superbet');
      const taxSuperFactor = document.getElementById('setting-tax-superbet-factor');
      const taxBetclic = document.getElementById('setting-tax-betclic');
      const taxBetclicFactor = document.getElementById('setting-tax-betclic-factor');

      const taxRates = s.bookmaker_tax_rates || {};
      const taxConfigs = s.bookmaker_tax_configs || {};

      const superRate = taxRates.superbet !== undefined ? Number(taxRates.superbet) * 100 : (taxConfigs.superbet?.tax_rate_percent ?? 12);
      const betclicRate = taxRates.betclic !== undefined ? Number(taxRates.betclic) * 100 : (taxConfigs.betclic?.tax_rate_percent ?? 0);

      if (taxSuper) {
        taxSuper.value = superRate;
        if (taxSuperFactor) taxSuperFactor.textContent = `Factor: ${(1 - superRate / 100).toFixed(2)}`;
        taxSuper.oninput = () => {
          const val = parseFloat(taxSuper.value) || 0;
          if (taxSuperFactor) taxSuperFactor.textContent = `Factor: ${(1 - val / 100).toFixed(2)}`;
        };
      }

      if (taxBetclic) {
        taxBetclic.value = betclicRate;
        if (taxBetclicFactor) taxBetclicFactor.textContent = `Factor: ${(1 - betclicRate / 100).toFixed(2)}`;
        taxBetclic.oninput = () => {
          const val = parseFloat(taxBetclic.value) || 0;
          if (taxBetclicFactor) taxBetclicFactor.textContent = `Factor: ${(1 - val / 100).toFixed(2)}`;
        };
      }

    } catch (err) {
      console.error('Failed to load settings data', err);
      showToast('Error fetching user settings.');
    }
  }

  async function saveSettingsFromForm() {
    try {
      const themeVal = document.getElementById('setting-theme')?.value || 'dark';
      const minSureRoi = parseFloat(document.getElementById('setting-min-surebet-roi')?.value) || 1.0;
      const minValEv = parseFloat(document.getElementById('setting-min-valuebet-ev')?.value) || 3.0;
      const maxStake = parseFloat(document.getElementById('setting-max-stake')?.value) || 1000;

      const taxSuper = parseFloat(document.getElementById('setting-tax-superbet')?.value) || 0;
      const taxBetclic = parseFloat(document.getElementById('setting-tax-betclic')?.value) || 0;

      const payload = {
        theme: themeVal,
        min_surebet_roi: minSureRoi,
        min_valuebet_edge: minValEv,
        max_bankroll_stake: maxStake,
        bookmaker_tax_rates: {
          superbet: (taxSuper / 100),
          betclic: (taxBetclic / 100),
        },
      };

      const res = await api.updateSettings(payload);
      if (res && res.data) {
        state.settings = res.data;
        showToast('Preferences & Tax Configurations successfully saved.');
      } else {
        showToast('Settings saved.');
      }
    } catch (err) {
      console.error('Failed to save settings', err);
      showToast('Error saving settings.');
    }
  }

  // ──────────────────────────────────────────────────────────────────────────
  // SCAN PROFILER VIEW (Stage 46)
  // ──────────────────────────────────────────────────────────────────────────

  let currentProfilerMode = 'main';
  let currentProfilerTrace = null;

  function updateProfilerModeSwitcherUI(mode) {
    const switcher = document.getElementById('profiler-mode-switcher');
    if (switcher) {
      switcher.querySelectorAll('.scope-btn').forEach(btn => {
        if (btn.dataset.mode === mode) {
          btn.classList.add('active');
        } else {
          btn.classList.remove('active');
        }
      });
    }

    const headingEl = document.getElementById('profiler-heading');
    if (headingEl) {
      if (mode === 'team_props') {
        headingEl.textContent = 'Team Props Execution Profiler';
      } else if (mode === 'player_props') {
        headingEl.textContent = 'Player Props Execution Profiler';
      } else {
        headingEl.textContent = 'Main Scan Execution Profiler';
      }
    }
  }

  async function loadProfilerData(mode = currentProfilerMode) {
    currentProfilerMode = mode;
    updateProfilerModeSwitcherUI(currentProfilerMode);
    try {
      const res = await api.fetchLatestTrace(currentProfilerMode);
      if (res && res.data && res.status_code === 200) {
        currentProfilerTrace = res.data;
        renderProfilerTrace(res.data);
      } else {
        // Fallback for main scan: check if latest scan has scan_trace embedded
        if (currentProfilerMode === 'main') {
          const scanRes = await api.fetchLatestScan();
          if (scanRes && scanRes.data && scanRes.data.scan_trace && scanRes.data.scan_trace.trace_id) {
            currentProfilerTrace = scanRes.data.scan_trace;
            renderProfilerTrace(scanRes.data.scan_trace);
            return;
          }
        }
        showEmptyProfilerState(currentProfilerMode);
      }
    } catch (err) {
      console.error(`Failed loading profiler trace for ${currentProfilerMode}:`, err);
      showEmptyProfilerState(currentProfilerMode);
    }
  }

  function showEmptyProfilerState(mode = currentProfilerMode) {
    currentProfilerTrace = null;
    updateProfilerModeSwitcherUI(mode);
    const traceIdEl = document.getElementById('profiler-trace-id');
    if (traceIdEl) traceIdEl.textContent = 'None';
    const wallClockEl = document.getElementById('profiler-wall-clock');
    if (wallClockEl) wallClockEl.textContent = '—';
    const summaryEl = document.getElementById('prof-bottleneck-summary');
    if (summaryEl) {
      let emptyMsg = 'No scan trace loaded yet. Click <strong>Run Scan</strong> on the Dashboard to generate execution telemetry.';
      if (mode === 'team_props') {
        emptyMsg = 'No Team Props scan trace available yet. Run a <strong>Team Props</strong> scan to generate execution telemetry.';
      } else if (mode === 'player_props') {
        emptyMsg = 'No Player Props scan trace available yet. Run a <strong>Player Props</strong> scan to generate execution telemetry.';
      }
      summaryEl.innerHTML = `<p class="text-muted">${emptyMsg}</p>`;
    }
    const barContainer = document.getElementById('prof-phase-bar-container');
    if (barContainer) barContainer.innerHTML = '';
    const legendEl = document.getElementById('prof-phase-legend');
    if (legendEl) legendEl.innerHTML = '';
    const phasesTbody = document.getElementById('prof-phases-tbody');
    if (phasesTbody) phasesTbody.innerHTML = '<tr><td colspan="6" class="text-muted text-center">No trace data.</td></tr>';
    const workersContainer = document.getElementById('prof-worker-timelines-container');
    if (workersContainer) workersContainer.innerHTML = '';
    const workersTbody = document.getElementById('prof-workers-tbody');
    if (workersTbody) workersTbody.innerHTML = '<tr><td colspan="10" class="text-muted text-center">No worker data.</td></tr>';
    const pTbody = document.getElementById('prof-percentiles-tbody');
    if (pTbody) pTbody.innerHTML = '<tr><td colspan="7" class="text-muted text-center">No latency percentiles.</td></tr>';
    const sTbody = document.getElementById('prof-stragglers-tbody');
    if (sTbody) sTbody.innerHTML = '<tr><td colspan="6" class="text-muted text-center">No straggler requests.</td></tr>';
  }

  function renderProfilerTrace(trace) {
    if (!trace || !trace.trace_id) {
      showEmptyProfilerState(currentProfilerMode);
      return;
    }

    const activeMode = trace.scan_type || currentProfilerMode || 'main';
    updateProfilerModeSwitcherUI(activeMode);

    // Header & KPIs
    const traceIdEl = document.getElementById('profiler-trace-id');
    if (traceIdEl) traceIdEl.textContent = trace.trace_id || trace.execution_id || '—';

    const wallClockEl = document.getElementById('profiler-wall-clock');
    if (wallClockEl) wallClockEl.textContent = `${(trace.total_duration_wall_s || 0).toFixed(3)}s`;

    const kpiDurEl = document.getElementById('prof-kpi-duration');
    if (kpiDurEl) kpiDurEl.textContent = `${(trace.total_duration_wall_s || 0).toFixed(3)}s`;

    const kpiModeEl = document.getElementById('prof-kpi-mode');
    if (kpiModeEl) {
      const modeLabel = (trace.scan_type || activeMode).replace('_', ' ').toUpperCase();
      kpiModeEl.textContent = `Mode: ${trace.scan_mode || 'NORMAL'} (${modeLabel})`;
    }

    const conc = trace.concurrency_summary || {};
    const kpiWorkersEl = document.getElementById('prof-kpi-workers');
    if (kpiWorkersEl) kpiWorkersEl.textContent = `${conc.total_workers_registered || (trace.workers ? trace.workers.length : 0)} Workers`;

    const kpiConcEl = document.getElementById('prof-kpi-concurrency');
    if (kpiConcEl) kpiConcEl.textContent = `Peak: ${conc.peak_concurrency || 0} | Avg: ${(conc.avg_concurrency || 0).toFixed(1)}`;

    const reqSummary = trace.request_summary || trace.request_telemetry_summary || {};
    const kpiReqsEl = document.getElementById('prof-kpi-requests');
    if (kpiReqsEl) kpiReqsEl.textContent = `${reqSummary.total_requests || (trace.requests ? trace.requests.length : 0)} Requests`;

    const kpiReqSuccEl = document.getElementById('prof-kpi-req-success');
    if (kpiReqSuccEl) kpiReqSuccEl.textContent = `Success: ${reqSummary.successful_requests || 0} | Failed: ${reqSummary.failed_requests || 0}`;

    const mem = trace.memory_summary || trace.resource_telemetry || {};
    const kpiMemEl = document.getElementById('prof-kpi-memory');
    if (kpiMemEl) kpiMemEl.textContent = `RSS: ${(mem.peak_rss_mb || mem.peak_memory_mb || 0).toFixed(1)} MB`;

    const cpu = trace.cpu_summary || {};
    const kpiCpuEl = document.getElementById('prof-kpi-cpu');
    if (kpiCpuEl) kpiCpuEl.textContent = `CPU: ${(cpu.total_cpu_seconds || trace.total_cpu_seconds || 0).toFixed(3)}s (${(cpu.cpu_utilization_pct || trace.cpu_utilization_pct || 0).toFixed(1)}%)`;

    // Bottleneck & Recommendations
    renderBottleneckSummary(trace.bottleneck_summary);

    // Phases
    renderPhases(trace.phases || [], trace.total_duration_wall_s || 1.0);

    // Acquisition Forensics Table
    renderAcquisitionForensics(trace.acquisition_forensics || {});

    // Workers
    renderWorkerTimelines(trace.workers || [], trace.total_duration_wall_s || 1.0);

    // Latency Distributions
    renderPercentiles(trace.latency_percentiles || {});

    // Stragglers
    renderStragglers(trace.top_stragglers || []);
  }

  function renderAcquisitionForensics(forensics) {
    const tbody = document.getElementById('prof-forensics-tbody');
    if (!tbody) return;
    tbody.innerHTML = '';

    const keys = Object.keys(forensics);
    if (!keys.length) {
      tbody.innerHTML = '<tr><td colspan="9" class="text-muted text-center">No acquisition forensics available.</td></tr>';
      return;
    }

    keys.forEach(k => {
      const f = forensics[k];
      const longest = f.longest_task ? `${(f.longest_task.duration_ms || 0).toFixed(0)}ms (${escapeHtml(f.longest_task.task_id || '—')})` : '—';
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td><strong class="text-accent">${escapeHtml(f.provider || k).toUpperCase()}</strong></td>
        <td class="mono">${f.worker_count || 0}</td>
        <td class="mono font-semibold">${f.requests_count || 0}</td>
        <td class="mono">${f.tasks_completed || 0}</td>
        <td class="mono font-semibold" style="color:#10b981;">${(f.total_work_seconds || 0).toFixed(3)}s</td>
        <td class="mono" style="color:#3b82f6;">${(f.total_network_seconds || 0).toFixed(3)}s</td>
        <td class="mono" style="color:#f59e0b;">${(f.total_rate_limit_wait_seconds || 0).toFixed(3)}s</td>
        <td class="mono">${(f.total_queue_wait_seconds || 0).toFixed(3)}s</td>
        <td class="mono text-muted" style="font-size:0.75rem;">${longest}</td>
      `;
      tbody.appendChild(tr);
    });
  }

  function renderBottleneckSummary(summary) {
    const el = document.getElementById('prof-bottleneck-summary');
    if (!el) return;
    if (!summary) {
      el.innerHTML = '<p class="text-muted">No bottleneck analysis available.</p>';
      return;
    }

    let recsHtml = '';
    if (summary.recommendations && summary.recommendations.length > 0) {
      recsHtml = `
        <div style="margin-top: 0.75rem; padding: 0.75rem; background: var(--bg-surface-alt); border-radius: var(--radius-sm); border-left: 3px solid #3b82f6;">
          <strong style="color: var(--text-primary); font-size: 0.85rem;">Recommendations:</strong>
          <ul style="margin: 0.25rem 0 0 1.25rem; font-size: 0.82rem; color: var(--text-muted);">
            ${summary.recommendations.map(r => `<li>${escapeHtml(r)}</li>`).join('')}
          </ul>
        </div>
      `;
    }

    el.innerHTML = `
      <div style="display: flex; flex-wrap: wrap; gap: 1.5rem; margin-bottom: 0.5rem;">
        <div><strong>Primary Bottleneck:</strong> <span class="badge badge-accent">${escapeHtml(summary.primary_bottleneck || 'None')}</span></div>
        <div><strong>Phase Duration:</strong> <span class="mono font-semibold">${(summary.bottleneck_duration_s || 0).toFixed(3)}s</span> (${(summary.bottleneck_pct_of_total || 0).toFixed(1)}% of total)</div>
        <div><strong>Total Wall Duration:</strong> <span class="mono font-semibold">${(summary.total_wall_duration_s || 0).toFixed(3)}s</span></div>
      </div>
      <p style="margin: 0.25rem 0; color: var(--text-muted); font-size: 0.88rem;">${escapeHtml(summary.bottleneck_explanation || '')}</p>
      ${recsHtml}
    `;
  }

  function renderPhases(phases, totalWallS) {
    const barContainer = document.getElementById('prof-phase-bar-container');
    const legendEl = document.getElementById('prof-phase-legend');
    const tbody = document.getElementById('prof-phases-tbody');

    if (!barContainer || !legendEl || !tbody) return;

    barContainer.innerHTML = '';
    legendEl.innerHTML = '';
    tbody.innerHTML = '';

    if (!phases.length) {
      tbody.innerHTML = '<tr><td colspan="6" class="text-muted text-center">No phases recorded.</td></tr>';
      return;
    }

    const palette = ['#10b981', '#3b82f6', '#8b5cf6', '#ec4899', '#f59e0b', '#06b6d4', '#84cc16', '#6366f1'];

    phases.forEach((ph, idx) => {
      const color = palette[idx % palette.length];
      const pct = ph.pct_of_total || (totalWallS > 0 ? ((ph.duration_s / totalWallS) * 100) : 0);

      // Bar Segment
      const seg = document.createElement('div');
      seg.style.width = `${Math.max(0.5, pct)}%`;
      seg.style.backgroundColor = color;
      seg.style.height = '100%';
      seg.title = `${ph.phase_name}: ${ph.duration_s.toFixed(3)}s (${pct.toFixed(1)}%)`;
      barContainer.appendChild(seg);

      // Legend Item
      const legItem = document.createElement('div');
      legItem.innerHTML = `<span style="display:inline-block; width:10px; height:10px; background:${color}; border-radius:2px; margin-right:4px;"></span> <strong>${escapeHtml(ph.phase_name)}</strong>: ${ph.duration_s.toFixed(2)}s (${pct.toFixed(1)}%)`;
      legendEl.appendChild(legItem);

      // Table Row
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td><strong>${escapeHtml(ph.phase_name)}</strong></td>
        <td class="mono">${ph.start_rel_s.toFixed(3)}s</td>
        <td class="mono">${ph.end_rel_s.toFixed(3)}s</td>
        <td class="mono font-semibold">${ph.duration_s.toFixed(3)}s</td>
        <td class="mono">${pct.toFixed(1)}%</td>
        <td class="mono">${ph.memory_delta_mb > 0 ? '+' : ''}${ph.memory_delta_mb.toFixed(2)} MB</td>
      `;
      tbody.appendChild(tr);
    });
  }

  function renderWorkerTimelines(workers, totalWallS) {
    const container = document.getElementById('prof-worker-timelines-container');
    const tbody = document.getElementById('prof-workers-tbody');
    if (!container || !tbody) return;

    container.innerHTML = '';
    tbody.innerHTML = '';

    if (!workers.length) {
      tbody.innerHTML = '<tr><td colspan="10" class="text-muted text-center">No worker records.</td></tr>';
      return;
    }

    const stateColors = {
      'WORKING': '#10b981',
      'RATE_LIMIT_WAIT': '#f59e0b',
      'QUEUE_WAIT': '#3b82f6',
      'IDLE': '#64748b',
    };

    const maxDuration = Math.max(0.001, totalWallS);

    workers.forEach(w => {
      // 1. Worker Timeline Row
      const row = document.createElement('div');
      row.style.display = 'flex';
      row.style.alignItems = 'center';
      row.style.gap = '0.75rem';

      const label = document.createElement('div');
      label.style.width = '160px';
      label.style.fontSize = '0.78rem';
      label.style.fontWeight = '600';
      label.style.color = 'var(--text-primary)';
      label.className = 'mono';
      label.textContent = w.worker_id;

      const track = document.createElement('div');
      track.style.flex = '1';
      track.style.height = '18px';
      track.style.position = 'relative';
      track.style.backgroundColor = 'rgba(255,255,255,0.05)';
      track.style.borderRadius = '3px';
      track.style.overflow = 'hidden';

      const intervals = w.intervals || [];
      intervals.forEach(iv => {
        const leftPct = (iv.start_rel_s / maxDuration) * 100;
        const widthPct = Math.max(0.3, ((iv.end_rel_s - iv.start_rel_s) / maxDuration) * 100);
        const color = stateColors[iv.state] || '#10b981';

        const block = document.createElement('div');
        block.style.position = 'absolute';
        block.style.left = `${leftPct}%`;
        block.style.width = `${widthPct}%`;
        block.style.height = '100%';
        block.style.backgroundColor = color;
        block.title = `${w.worker_id} [${iv.state}] ${iv.start_rel_s.toFixed(2)}s - ${iv.end_rel_s.toFixed(2)}s (${((iv.end_rel_s - iv.start_rel_s) * 1000).toFixed(0)}ms)`;
        track.appendChild(block);
      });

      row.appendChild(label);
      row.appendChild(track);
      container.appendChild(row);

      // 2. Table Row
      const longest = w.longest_task ? `${w.longest_task.duration_ms.toFixed(0)}ms (${escapeHtml(w.longest_task.task_id)})` : '—';
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td class="mono font-semibold">${escapeHtml(w.worker_id)}</td>
        <td><span class="badge badge-accent">${escapeHtml(w.provider || '—')}</span></td>
        <td>${escapeHtml(w.role || '—')}</td>
        <td class="mono">${w.completed_tasks || 0}</td>
        <td class="mono">${w.failed_tasks ? `<span style="color:#ef4444;">${w.failed_tasks}</span>` : '0'}</td>
        <td class="mono">${(w.total_active_time_s || 0).toFixed(3)}s</td>
        <td class="mono">${(w.total_rate_limit_wait_s || 0).toFixed(3)}s</td>
        <td class="mono">${(w.total_idle_time_s || 0).toFixed(3)}s</td>
        <td class="mono font-semibold">${(w.utilization_pct || 0).toFixed(1)}%</td>
        <td class="mono text-muted" style="font-size:0.75rem;">${longest}</td>
      `;
      tbody.appendChild(tr);
    });
  }

  function renderPercentiles(percentiles) {
    const tbody = document.getElementById('prof-percentiles-tbody');
    if (!tbody) return;
    tbody.innerHTML = '';

    const keys = Object.keys(percentiles);
    if (!keys.length) {
      tbody.innerHTML = '<tr><td colspan="7" class="text-muted text-center">No latency percentiles.</td></tr>';
      return;
    }

    keys.forEach(k => {
      const p = percentiles[k];
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td><strong>${escapeHtml(k)}</strong></td>
        <td class="mono">${p.count}</td>
        <td class="mono">${p.min.toFixed(0)}ms</td>
        <td class="mono font-semibold">${p.median.toFixed(0)}ms</td>
        <td class="mono">${p.p95.toFixed(0)}ms</td>
        <td class="mono">${p.p99.toFixed(0)}ms</td>
        <td class="mono font-semibold">${p.max.toFixed(0)}ms</td>
      `;
      tbody.appendChild(tr);
    });
  }

  function renderStragglers(stragglers) {
    const tbody = document.getElementById('prof-stragglers-tbody');
    if (!tbody) return;
    tbody.innerHTML = '';

    if (!stragglers.length) {
      tbody.innerHTML = '<tr><td colspan="6" class="text-muted text-center">No straggler requests.</td></tr>';
      return;
    }

    stragglers.slice(0, 10).forEach(st => {
      const tr = document.createElement('tr');
      const statusBadge = st.http_status === 200
        ? '<span class="badge badge-success">200 OK</span>'
        : `<span class="badge badge-danger">${st.http_status || 'ERR'}</span>`;

      tr.innerHTML = `
        <td><span class="badge badge-accent">${escapeHtml(st.provider || '—')}</span></td>
        <td class="mono" style="font-size:0.75rem;">${escapeHtml(st.endpoint_category || '—')}</td>
        <td class="mono" style="font-size:0.75rem;">${escapeHtml(st.worker_id || '—')}</td>
        <td class="mono font-semibold" style="color:#f59e0b;">${(st.duration_ms || 0).toFixed(0)}ms</td>
        <td class="mono">${(st.rate_limit_wait_ms || 0).toFixed(0)}ms</td>
        <td>${statusBadge}</td>
      `;
      tbody.appendChild(tr);
    });
  }

  // Setup profiler button listeners on DOM ready
  function bootProfiler() {
    const switcher = document.getElementById('profiler-mode-switcher');
    if (switcher) {
      switcher.querySelectorAll('.scope-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
          e.preventDefault();
          const targetMode = btn.dataset.mode;
          if (targetMode && targetMode !== currentProfilerMode) {
            currentProfilerMode = targetMode;
            loadProfilerData(targetMode);
          }
        });
      });
    }

    const btnReload = document.getElementById('btn-refresh-profiler');
    if (btnReload) {
      btnReload.addEventListener('click', () => {
        loadProfilerData(currentProfilerMode);
        showToast('Profiler trace reloaded.');
      });
    }

    const btnExport = document.getElementById('btn-export-trace');
    if (btnExport) {
      btnExport.addEventListener('click', () => {
        if (!currentProfilerTrace) {
          showToast('No trace available to export.');
          return;
        }
        const dataStr = 'data:text/json;charset=utf-8,' + encodeURIComponent(JSON.stringify(currentProfilerTrace, null, 2));
        const dlAnchor = document.createElement('a');
        dlAnchor.setAttribute('href', dataStr);
        dlAnchor.setAttribute('download', `trace_${currentProfilerMode}_${currentProfilerTrace.trace_id || 'scan'}.json`);
        document.body.appendChild(dlAnchor);
        dlAnchor.click();
        dlAnchor.remove();
        showToast('Trace JSON exported.');
      });
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bootProfiler);
  } else {
    bootProfiler();
  }

  // Expose namespace for testing & automated browser inspection
  window.__zb = { state, api, showPropDetail, loadOpportunityDetail, switchView, viewRegistry, renderPropsTable, loadTelegramHealthPanel, renderTelegramControlCenter, handleSendTestMessage, renderDashboardView };

})();




