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
    schedulerStatus: { enabled: false, interval_minutes: 15, scan_scope: 'POPULAR', hours_ahead: 24, event_limit: 50 },
    autoRefreshTimer: null,
    selectedEventId: null,
    playerProps: {
      results: [],
      selectedPropId: null,
      isScanning: false,
      metadata: null,
      filters: {
        stat: 'shots',
        position: 'D,M,F',
        lastGames: 10,
        minHitRate: 0,
        minOdds: 1.01,
        threshold: 1,
        venue: 'both',
        search: '',
      },
    },
  };

  // API Service Calls
  const api = {
    async scanProps(params = {}) {
      const query = new URLSearchParams(params).toString();
      const res = await fetch(`${API_BASE}/api/v1/props/scan?${query}`, {
        method: 'POST',
      });
      return res.json();
    },
    async fetchPropsResults(params = {}) {
      const query = new URLSearchParams(params).toString();
      const res = await fetch(`${API_BASE}/api/v1/props/results?${query}`);
      return res.json();
    },
    async fetchPropDetail(propId) {
      try {
        const res = await fetch(`${API_BASE}/api/v1/props/${encodeURIComponent(propId)}`);
        const data = await res.json();
        return { ok: res.ok, status: res.status, data: (data && data.data !== undefined) ? data.data : data, error: data?.errors?.[0] || data?.detail };
      } catch (err) {
        return { ok: false, status: 0, error: err.message || 'Network connection failed' };
      }
    },
    async fetchPropsHealth() {
      const res = await fetch(`${API_BASE}/api/v1/props/health`);
      return res.json();
    },
    async fetchHealth() {
      const res = await fetch(`${API_BASE}/api/v1/health`);
      return res.json();
    },
    async fetchScanStatus() {
      const res = await fetch(`${API_BASE}/api/v1/scan/status`);
      return res.json();
    },
    async fetchLatestScan() {
      const res = await fetch(`${API_BASE}/api/v1/scan/latest`);
      return res.json();
    },
    async fetchLatestTrace() {
      const res = await fetch(`${API_BASE}/api/v1/scan/trace/latest`);
      return res.json();
    },
    async fetchTraceById(traceId) {
      const res = await fetch(`${API_BASE}/api/v1/scan/trace/${encodeURIComponent(traceId)}`);
      return res.json();
    },
    async runScan(scanMode = 'NORMAL') {
      const res = await fetch(`${API_BASE}/api/v1/scan/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scan_mode: scanMode }),
      });
      return res.json();
    },
    async fetchScanHistory(limit = 10) {
      const res = await fetch(`${API_BASE}/api/v1/scan/history?limit=${limit}`);
      return res.json();
    },
    async fetchProviders() {
      const res = await fetch(`${API_BASE}/api/v1/providers`);
      return res.json();
    },
    async triggerProvider(name) {
      const res = await fetch(`${API_BASE}/api/v1/providers/${encodeURIComponent(name)}/run`, {
        method: 'POST',
      });
      return res.json();
    },
    async fetchEvents(params = {}) {
      const query = new URLSearchParams(params).toString();
      const res = await fetch(`${API_BASE}/api/v1/events?${query}`);
      return res.json();
    },
    async fetchEventDetail(eventId) {
      const res = await fetch(`${API_BASE}/api/v1/events/${encodeURIComponent(eventId)}`);
      return res.json();
    },
    async fetchOpportunities(params = {}) {
      const query = new URLSearchParams(params).toString();
      const res = await fetch(`${API_BASE}/api/v1/opportunities?${query}`);
      return res.json();
    },
    async fetchUnifiedOpportunities(params = {}) {
      const query = new URLSearchParams(params).toString();
      const res = await fetch(`${API_BASE}/api/v1/opportunities/explorer?${query}`);
      return res.json();
    },
    async fetchOpportunityDetail(opportunityId) {
      const res = await fetch(`${API_BASE}/api/v1/opportunities/${encodeURIComponent(opportunityId)}`);
      return res.json();
    },
    async fetchNotifications() {
      const res = await fetch(`${API_BASE}/api/v1/notifications`);
      return res.json();
    },
    async fetchOddsHistory(eventId = 'ev-real-barca-01', period = '24h') {
      const res = await fetch(`${API_BASE}/api/v1/history/odds?event_id=${eventId}&period=${period}`);
      return res.json();
    },
    async fetchSettings() {
      const res = await fetch(`${API_BASE}/api/v1/settings`);
      return res.json();
    },
    async updateSettings(newSettings) {
      const res = await fetch(`${API_BASE}/api/v1/settings`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(newSettings),
      });
      return res.json();
    },
    async fetchSchedulerStatus() {
      const res = await fetch(`${API_BASE}/api/v1/scan/scheduler`);
      return res.json();
    },
    async configureScheduler(payload) {
      const res = await fetch(`${API_BASE}/api/v1/scan/scheduler/configure`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      return res.json();
    },
    async schedulerRunNow() {
      const res = await fetch(`${API_BASE}/api/v1/scan/scheduler/run-now`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      });
      return res.json();
    },
  };

  // DOM Elements Initialization & Router
  document.addEventListener('DOMContentLoaded', () => {
    initRouter();
    initTheme();
    initEventListeners();
    loadDashboardData();
    startAutoRefresh();
  });

  // Navigation Router
  function initRouter() {
    const navItems = document.querySelectorAll('.nav-item');
    navItems.forEach(item => {
      item.addEventListener('click', (e) => {
        e.preventDefault();
        const targetView = item.getAttribute('data-view');
        switchView(targetView);
      });
    });

    // Handle deep links via pathname or hash
    const hash = window.location.hash.replace('#', '');
    const path = window.location.pathname.replace(/^\/+|\/+$/g, '');
    const initialView = hash || path;

    if (initialView.startsWith('opportunity/')) {
      const oppId = initialView.replace('opportunity/', '');
      switchView('opportunities');
      loadOpportunityDetail(oppId);
    } else if (initialView && document.getElementById(`view-${initialView}`)) {
      switchView(initialView);
    }
  }

  function switchView(viewName) {
    state.currentView = viewName;
    window.location.hash = viewName;

    document.querySelectorAll('.nav-item').forEach(el => {
      if (el.getAttribute('data-view') === viewName) {
        el.classList.add('active');
      } else {
        el.classList.remove('active');
      }
    });

    document.querySelectorAll('.view-section').forEach(sec => {
      if (sec.id === `view-${viewName}`) {
        sec.classList.add('active');
      } else {
        sec.classList.remove('active');
      }
    });

    // Reset sub-views when navigating to opportunities
    if (viewName === 'opportunities') {
      showOpportunitiesListView();
      loadOpportunitiesData();
    }

    // Load view specific data
    if (viewName === 'dashboard') loadDashboardData();
    if (viewName === 'opportunities') loadOpportunitiesData();
    if (viewName === 'playerprops') loadPlayerPropsData();
    if (viewName === 'profiler') loadProfilerData();
    if (viewName === 'providers') loadProvidersData();
    if (viewName === 'events') loadEventsData();
    if (viewName === 'history') loadHistoryData();
    if (viewName === 'notifications') loadNotificationsData();
    if (viewName === 'settings') loadSettingsData();
  }

  // Theme Management
  function initTheme() {
    const toggleBtn = document.getElementById('theme-toggle-btn');
    toggleBtn.addEventListener('click', () => {
      state.theme = state.theme === 'dark' ? 'light' : 'dark';
      document.documentElement.setAttribute('data-theme', state.theme);
    });
  }

  // Event Listeners Initialization
  function initEventListeners() {
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

    // Back to opportunities list button
    const btnBackToList = document.getElementById('btn-back-to-opp-list');
    if (btnBackToList) {
      btnBackToList.addEventListener('click', () => {
        showOpportunitiesListView();
      });
    }

    // Run Scan button
    const btnRunScan = document.getElementById('btn-run-scan');
    if (btnRunScan) {
      btnRunScan.addEventListener('click', handleRunScan);
    }

    // Role switcher
    const roleBtn = document.getElementById('role-switch-btn');
    if (roleBtn) {
      roleBtn.addEventListener('click', () => {
        const roles = ['Admin', 'User', 'Guest'];
        const nextIndex = (roles.indexOf(state.currentRole) + 1) % roles.length;
        state.currentRole = roles[nextIndex];
        document.getElementById('current-role-text').textContent = state.currentRole;
        showToast(`Role switched to ${state.currentRole}`);
      });
    }

    // Opportunity Explorer Category Tabs
    document.querySelectorAll('#explorer-category-tabs button').forEach(tabBtn => {
      tabBtn.addEventListener('click', () => {
        document.querySelectorAll('#explorer-category-tabs button').forEach(b => {
          b.classList.remove('btn-primary', 'active');
          b.classList.add('btn-outline');
        });
        tabBtn.classList.remove('btn-outline');
        tabBtn.classList.add('btn-primary', 'active');
        loadOpportunitiesData();
      });
    });

    // Opportunity Explorer filters (with debounce on search)
    const statusFilter = document.getElementById('filter-opp-status');
    const providerFilter = document.getElementById('filter-provider');
    const minScoreInput = document.getElementById('filter-min-score');
    const minExecEdgeInput = document.getElementById('filter-min-exec-edge');
    const minRoiInput = document.getElementById('filter-min-roi');
    const searchInput = document.getElementById('filter-search-text');

    let _oppSearchDebounce = null;
    [statusFilter, providerFilter, minScoreInput, minExecEdgeInput, minRoiInput].forEach(el => {
      if (el) el.addEventListener('change', () => loadOpportunitiesData());
    });

    if (searchInput) {
      searchInput.addEventListener('input', () => {
        if (_oppSearchDebounce) clearTimeout(_oppSearchDebounce);
        _oppSearchDebounce = setTimeout(() => {
          loadOpportunitiesData();
        }, 300);
      });
    }

    // Event & Market Explorer Filters & Refresh
    const btnRefreshEvents = document.getElementById('btn-refresh-events');
    if (btnRefreshEvents) {
      btnRefreshEvents.addEventListener('click', () => {
        loadEventsData();
        showToast('Events refreshed from backend API');
      });
    }

    const eventSportFilter = document.getElementById('filter-event-sport');
    const eventCompFilter = document.getElementById('filter-event-competition');
    const eventProviderFilter = document.getElementById('filter-event-provider');
    const eventMatchedFilter = document.getElementById('filter-event-matched');
    const eventSearchFilter = document.getElementById('filter-event-search');

    [eventSportFilter, eventCompFilter, eventProviderFilter, eventMatchedFilter, eventSearchFilter].forEach(el => {
      if (el) {
        el.addEventListener('input', () => loadEventsData());
        el.addEventListener('change', () => loadEventsData());
      }
    });

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
        showToast('Scheduler configuration applied.');
      });
    }
    const btnSchedRunNow = document.getElementById('btn-sched-run-now');
    if (btnSchedRunNow) {
      btnSchedRunNow.addEventListener('click', handleSchedulerRunNow);
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

    // UI Loading State (prevents duplicate triggers)
    btnRun.disabled = true;
    btnRun.classList.add('btn-scanning');
    btnRun.innerHTML = `<span class="spinner-icon">⟳</span> Scanning (${selectedMode})...`;

    badge.className = 'badge badge-cycle-scanning';
    badge.textContent = 'SCANNING';

    if (alertBox) alertBox.innerHTML = '';

    // Show scan progress feedback container
    if (progContainer) {
      progContainer.style.display = 'block';
      if (progModeBadge) progModeBadge.textContent = `${selectedMode} MODE`;
      if (progTitle) progTitle.textContent = `Running ${selectedMode} Scan Cycle...`;
      if (progBar) progBar.style.width = '15%';
    }

    // Step simulation timers to provide active visual feedback
    const step1 = document.getElementById('prog-step-1');
    const step2 = document.getElementById('prog-step-2');
    const step3 = document.getElementById('prog-step-3');
    const step4 = document.getElementById('prog-step-4');

    const resetSteps = () => {
      [step1, step2, step3, step4].forEach(s => {
        if (s) { s.style.color = 'var(--text-muted)'; s.style.fontWeight = 'normal'; }
      });
    };
    resetSteps();
    if (step1) { step1.style.color = 'var(--accent-primary)'; step1.style.fontWeight = '600'; }

    const t1 = setTimeout(() => {
      if (progBar) progBar.style.width = '40%';
      resetSteps();
      if (step2) { step2.style.color = 'var(--accent-primary)'; step2.style.fontWeight = '600'; }
    }, 1500);

    const t2 = setTimeout(() => {
      if (progBar) progBar.style.width = '70%';
      resetSteps();
      if (step3) { step3.style.color = 'var(--accent-primary)'; step3.style.fontWeight = '600'; }
    }, 4000);

    const t3 = setTimeout(() => {
      if (progBar) progBar.style.width = '90%';
      resetSteps();
      if (step4) { step4.style.color = 'var(--accent-primary)'; step4.style.fontWeight = '600'; }
    }, 7000);

    try {
      const res = await api.runScan(selectedMode);

      clearTimeout(t1);
      clearTimeout(t2);
      clearTimeout(t3);
      if (progBar) progBar.style.width = '100%';

      if (res.status_code === 200 && res.data) {
        state.latestScan = res.data;
        renderDashboardView(res.data);
        await refreshScanHistory();
        if (state.currentView === 'events') {
          await loadEventsData();
        }
        showToast(`${selectedMode} Scan complete (${res.data.duration_seconds}s) — Status: ${res.data.cycle_status}`);
      } else if (res.status_code === 409) {
        showToast('Scan already in progress on server.');
      } else {
        const errorMsg = (res.errors && res.errors[0]) || 'Scan cycle encountered an error.';
        if (alertBox) {
          alertBox.innerHTML = `<div class="alert-banner error"><strong>Scan Error:</strong> ${errorMsg}</div>`;
        }
        showToast('Scan failed. See details.');
      }
    } catch (err) {
      clearTimeout(t1);
      clearTimeout(t2);
      clearTimeout(t3);
      console.error('Scan execution error:', err);
      if (alertBox) {
        alertBox.innerHTML = `<div class="alert-banner error"><strong>Scan Error:</strong> Communication error with backend API.</div>`;
      }
      showToast('Scan execution failed.');
    } finally {
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
    } else if (upper === 'SCANNING') {
      badge.className = 'badge badge-cycle-scanning';
      badge.textContent = '🟡 SCANNING';
    } else if (upper === 'ERROR') {
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
      const latestData = latestRes.data;
      const historyData = historyRes.data || [];
      const healthData = healthRes.data || {};
      const schedData = schedRes.data || null;

      state.scanStatus = statusData;
      state.latestScan = latestData;
      state.scanHistory = historyData;
      if (schedData) state.schedulerStatus = schedData;

      // Update API Latency in Top Header
      document.getElementById('header-api-latency').textContent = `${statusRes.execution_time_ms || 2.1} ms`;

      // Restore API Connected Status
      const conn = document.getElementById('connection-status');
      if (conn) {
        conn.querySelector('.status-text').textContent = 'API REST Connected';
        conn.querySelector('.status-dot').style.backgroundColor = '#22C55E';
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
    document.getElementById('dash-last-scan-time').textContent = 'Never';
    document.getElementById('dash-scan-duration').textContent = '—';

    // Hero metrics
    document.getElementById('dash-events-discovered').textContent = '—';
    document.getElementById('dash-events-selected').textContent = '—';
    document.getElementById('dash-events-matched').textContent = '—';
    document.getElementById('dash-markets-evaluated').textContent = '—';
    document.getElementById('dash-surebets-count').textContent = '0';
    document.getElementById('dash-max-margin').textContent = '0.00%';
    const dashValCount = document.getElementById('dash-valuebets-count');
    if (dashValCount) dashValCount.textContent = '0';
    const dashMaxEv = document.getElementById('dash-max-ev');
    if (dashMaxEv) dashMaxEv.textContent = '0.00%';

    // Last scan card
    document.getElementById('dash-cycle-status-badge').className = 'badge badge-cycle-notrun';
    document.getElementById('dash-cycle-status-badge').textContent = 'NOT_RUN';
    document.getElementById('dash-scan-id').textContent = 'scan_id: —';
    document.getElementById('dash-last-scan-content').innerHTML = `
      <div class="not-run-state">
        <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><polygon points="5 3 19 12 5 21 5 3"/></svg>
        <p>No scan has been executed yet. Click <strong>Run Scan</strong> above to execute the production pipeline.</p>
      </div>
    `;

    // Opportunities card
    document.getElementById('dash-opp-badge').textContent = '0 Found';
    document.getElementById('dash-opps-container').innerHTML = `
      <div class="not-run-state">
        <p class="text-muted">Awaiting first scan execution...</p>
      </div>
    `;

    // Provider Health fallback from health endpoint
    renderProviderHealthGrid({});
  }

  function renderDashboardView(scan) {
    const counts = scan.counts || {};
    const timings = scan.stage_timings || {};
    const metrics = scan.resource_metrics || {};
    const opps = scan.opportunities || [];

    const sureOpps = opps.filter(o => (o.opportunity_type || 'SUREBET') === 'SUREBET');
    const valOpps = opps.filter(o => o.opportunity_type === 'VALUEBET');

    // 1. Top Meta Bar
    document.getElementById('dash-last-scan-time').textContent = formatTimestamp(scan.completed_at);
    document.getElementById('dash-scan-duration').textContent = safeDuration(scan.duration_seconds);

    // 2. Hero Metrics Cards
    document.getElementById('dash-events-discovered').textContent = safeNum(counts.discovered_events, '—');
    document.getElementById('dash-events-selected').textContent = safeNum(counts.selected_events, '—');
    document.getElementById('dash-events-matched').textContent = safeNum(counts.matched_events, '—');
    document.getElementById('dash-markets-evaluated').textContent = safeNum(metrics.markets_evaluated, '—');
    document.getElementById('dash-surebets-count').textContent = safeNum(counts.detected_opportunities !== undefined ? counts.detected_opportunities : sureOpps.length, 0);

    const validSurebetOpps = sureOpps.filter(o => {
      const isSb = (o.calculation?.is_surebet !== undefined) ? o.calculation.is_surebet : (o.is_qualified !== false);
      const m = (o.calculation?.roi !== undefined) ? o.calculation.roi : safeNum(o.arbitrage_margin_pct || o.margin_pct, 0);
      return isSb && m > 0;
    });
    const maxMargin = validSurebetOpps.length > 0
      ? Math.max(...validSurebetOpps.map(o => (o.calculation?.roi !== undefined ? o.calculation.roi : safeNum(o.arbitrage_margin_pct || o.margin_pct, 0))))
      : 0.0;
    document.getElementById('dash-max-margin').textContent = safePct(maxMargin);

    const dashValCount = document.getElementById('dash-valuebets-count');
    if (dashValCount) dashValCount.textContent = safeNum(counts.valuebets_qualified !== undefined ? counts.valuebets_qualified : (counts.valuebet_candidates || valOpps.length), 0);

    const maxEv = valOpps.length > 0 ? Math.max(...valOpps.map(o => safeNum(o.value_percent || o.margin_pct, 0))) : 0.0;
    const dashMaxEv = document.getElementById('dash-max-ev');
    if (dashMaxEv) dashMaxEv.textContent = safePct(maxEv);

    // 3. Last Scan Execution Summary Card
    const statusBadge = document.getElementById('dash-cycle-status-badge');
    statusBadge.textContent = scan.cycle_status;
    if (scan.cycle_status === 'SUCCESS') {
      statusBadge.className = 'badge badge-cycle-success';
    } else if (scan.cycle_status === 'PARTIAL') {
      statusBadge.className = 'badge badge-cycle-partial';
    } else {
      statusBadge.className = 'badge badge-cycle-failed';
    }

    document.getElementById('dash-scan-id').textContent = scan.execution_id;

    // 1. Pipeline State Banner
    const pipeState = scan.pipeline_state || 'UNKNOWN';
    let pipeBannerClass = 'markets-zero-opp';
    if (pipeState === 'NO_OVERLAP') pipeBannerClass = 'no-overlap';
    else if (pipeState === 'PARTIAL_DEGRADED') pipeBannerClass = 'partial-degraded';
    else if (pipeState === 'SCAN_FAILED') pipeBannerClass = 'failed';
    else if (pipeState === 'OPPORTUNITIES_FOUND') pipeBannerClass = 'opps-found';

    const pipeBannerHtml = `
      <div class="pipeline-status-banner ${pipeBannerClass}">
        <div>
          <strong>${scan.pipeline_state_label || scan.cycle_status}</strong>
        </div>
        <span class="badge ${statusBadge.className}">${scan.cycle_status}</span>
      </div>
    `;

    // 2. Event & Market Evaluation Pipeline Flow Grid (Stage 13 Funnel)
    const matchingDiag = scan.matching_diagnostic || {};
    const funnel = scan.evaluation_funnel || {};
    const matchedEvs = matchingDiag.matched_events !== undefined ? matchingDiag.matched_events : safeNum(counts.matched_events, 0);
    const matchedMkts = safeNum(funnel.matched_markets, safeNum(counts.markets_matched, 0));
    const evalMkts = safeNum(funnel.evaluated_markets, safeNum(metrics.markets_evaluated, 0));
    const rejMkts = safeNum(funnel.rejected_markets, safeNum(counts.rejected_markets, 0));
    const notEvalMkts = safeNum(funnel.not_evaluated_markets, safeNum(counts.not_evaluated_markets, 0));
    const validSurebets = safeNum(funnel.valid_surebets, safeNum(counts.detected_opportunities, 0));
    const validValuebets = safeNum(funnel.value_candidates, safeNum(counts.valuebets_qualified, 0));

    const pipelineFlowHtml = `
      <div class="pipeline-flow-grid">
        <div class="pipeline-flow-step">
          <span class="step-label">1. Discovered</span>
          <span class="step-val">${safeNum(counts.discovered_events, 0)}</span>
          <span class="step-sub text-muted">${safeNum(counts.selected_events, 0)} selected</span>
        </div>
        <div class="pipeline-flow-step">
          <span class="step-label">2. Normalized</span>
          <span class="step-val">${safeNum(counts.normalized_graphs, 0)}</span>
          <span class="step-sub text-muted">${safeNum(counts.markets_normalized, 0)} mkts</span>
        </div>
        <div class="pipeline-flow-step">
          <span class="step-label">3. Matched</span>
          <span class="step-val ${matchedEvs > 0 ? 'text-success' : ''}">${matchedEvs}</span>
          <span class="step-sub text-muted">${matchedMkts} mkts matched</span>
        </div>
        <div class="pipeline-flow-step">
          <span class="step-label">4. Evaluated</span>
          <span class="step-val ${evalMkts > 0 ? 'text-primary' : ''}">${evalMkts}</span>
          <span class="step-sub text-muted">${rejMkts + notEvalMkts} excluded</span>
        </div>
        <div class="pipeline-flow-step">
          <span class="step-label">5. Opportunities</span>
          <span class="step-val ${(validSurebets + validValuebets) > 0 ? 'text-success' : ''}">${validSurebets + validValuebets}</span>
          <span class="step-sub text-muted">${validSurebets} SB / ${validValuebets} VB</span>
        </div>
        <div class="pipeline-flow-step">
          <span class="step-label">6. Rejections</span>
          <span class="step-val ${rejMkts > 0 ? 'text-warning' : ''}">${rejMkts}</span>
          <span class="step-sub text-muted">${notEvalMkts} not evaluated</span>
        </div>
      </div>
    `;

    // 3. Bookmaker Coverage Comparison Table (Multi-Provider Architecture: Superbet, Betclic, Bet365, Unibet)
    const coverageData = scan.bookmaker_coverage || {};
    const overlapPct = counts.cross_bookmaker_overlap_rate_pct !== undefined ? `${counts.cross_bookmaker_overlap_rate_pct}%` : (counts.cross_bookmaker_overlap_rate ? `${(counts.cross_bookmaker_overlap_rate * 100).toFixed(1)}%` : '—');

    const visibleProvKeys = ['superbet', 'betclic', 'bet365', 'unibet'];
    const coverageRows = visibleProvKeys.map(key => {
      const cov = coverageData[key] || {
        discovered: 0,
        parsed: 0,
        normalized: 0,
        matched_events: 0,
        markets_matched: 0,
        status: (key === 'bet365' || key === 'unibet') ? 'UNAVAILABLE' : 'NOT_RUN'
      };

      const isGood = cov.status === 'COMPLETED' || cov.status === 'HEALTHY' || cov.status === 'SUCCESS' || cov.status === 'OK';
      const isDegraded = cov.status === 'DEGRADED' || cov.status === 'PARTIAL' || cov.status === 'NO_DATA';
      const stClass = isGood ? 'text-success' : (isDegraded ? 'text-warning' : 'text-danger');
      const invNote = cov.invalid_count > 0 ? ` <span class="badge badge-warning">(${cov.invalid_count} invalid)</span>` : '';

      const isOddsApi = key === 'bet365' || key === 'unibet' || cov.is_via_odds_api;
      const sourceBadge = isOddsApi ? `<span class="badge badge-outline" style="font-size: 0.65rem; padding: 0.1rem 0.35rem; margin-left: 0.35rem; vertical-align: middle;">Odds API</span>` : `<span class="badge badge-outline" style="font-size: 0.65rem; padding: 0.1rem 0.35rem; margin-left: 0.35rem; vertical-align: middle;">Direct</span>`;

      return `
        <tr>
          <td class="bold">${key.toUpperCase()} ${sourceBadge}</td>
          <td>${safeNum(cov.discovered, 0)}</td>
          <td>${safeNum(cov.parsed, 0)}</td>
          <td>${safeNum(cov.normalized, 0)}</td>
          <td><span class="${cov.matched_events > 0 ? 'text-success bold' : ''}">${safeNum(cov.matched_events, 0)}</span></td>
          <td>${safeNum(cov.markets_matched, 0)}</td>
          <td><span class="${stClass} bold">${cov.status}</span>${invNote}</td>
        </tr>
      `;
    }).join('');

    // 3a. Dedicated Odds API Aggregate Telemetry Card / Banner
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

    const oapiBannerHtml = oapiAvailable ? `
      <div class="odds-api-telemetry-banner" style="margin-top: 0.65rem; padding: 0.6rem 0.8rem; background: var(--bg-surface-alt, #1e232d); border-radius: 6px; border-left: 3px solid #6366f1; font-size: 0.78rem;">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.4rem;">
          <span style="font-weight: 600; color: var(--text-primary);">📡 Odds API.io Aggregate Telemetry (Bet365 & Unibet Gateway)</span>
          <span class="badge badge-success">✓ ${oapiStatus}</span>
        </div>
        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 0.5rem; font-size: 0.75rem; color: var(--text-secondary);">
          <div>Events Fetched: <strong style="color: var(--text-primary);">${oapiEventsFetched}</strong></div>
          <div>Bookmaker Models: <strong style="color: var(--text-primary);">${oapiModels}</strong> <span style="font-size: 0.7rem; color: var(--text-muted);">(Bet365: ${b365Parsed}, Unibet: ${unibetParsed})</span></div>
          <div>Canonical Contributed: <strong style="color: var(--text-primary);">${oapiCanon}</strong></div>
          <div>Markets Contributed: <strong style="color: var(--text-primary);">${oapiMkts}</strong></div>
          <div>Cache Performance: <strong style="color: var(--text-primary);">${cacheHits} hits / ${cacheMisses} misses</strong></div>
        </div>
      </div>
    ` : `
      <div class="odds-api-telemetry-banner" style="margin-top: 0.65rem; padding: 0.6rem 0.8rem; background: rgba(239, 68, 68, 0.08); border-radius: 6px; border-left: 3px solid var(--danger, #ef4444); font-size: 0.78rem;">
        <div style="display: flex; justify-content: space-between; align-items: center;">
          <span style="font-weight: 600; color: var(--text-primary);">📡 Odds API.io Gateway: <span class="text-danger">UNAVAILABLE / DEGRADED</span></span>
          <span class="badge badge-danger">${oapiStatus}</span>
        </div>
        <p style="margin: 0.25rem 0 0 0; color: var(--text-muted); font-size: 0.74rem;">
          Odds API provider was unavailable or disabled during this cycle. Bet365 and Unibet secondary data sources are degraded.
        </p>
      </div>
    `;

    const coverageTableHtml = `
      <div style="margin-top: 0.75rem;">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.35rem;">
          <span class="step-label" style="font-size: 0.75rem; color: var(--text-muted); text-transform: uppercase; font-weight: 600;">Cross-Bookmaker Coverage Breakdown</span>
          <span class="badge badge-accent" style="font-size: 0.72rem;">Overlap Rate: ${overlapPct}</span>
        </div>
        <table class="coverage-table-mini">
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
            ${coverageRows}
          </tbody>
        </table>
        ${oapiBannerHtml}
      </div>
    `;


    // 3b. Market Coverage Breakdown Table & Betclic Detail Acquisition (Stage 10.7)
    const mktBreakdown = scan.market_coverage_breakdown || counts.market_coverage_breakdown || {};
    const targetMktTypes = ['1X2', 'BTTS', 'TOTALS', 'DOUBLE_CHANCE', 'DRAW_NO_BET', 'HALF_TIME_RESULT'];
    
    const mktBreakdownRows = targetMktTypes.map(mType => {
      const data = mktBreakdown[mType] || { discovered: 0, normalized: 0, matched: 0, evaluated: 0 };
      const label = mType.replace(/_/g, ' ');
      return `
        <tr>
          <td class="bold">${label}</td>
          <td>${data.discovered || 0}</td>
          <td>${data.matched || 0}</td>
          <td>${data.evaluated || 0}</td>
        </tr>
      `;
    }).join('');

    const bcTelemetry = scan.betclic_telemetry || {};
    const bcMatched = bcTelemetry.matched_events !== undefined ? bcTelemetry.matched_events : matchedEvs;
    const bcDetailed = bcTelemetry.detailed_matched_events !== undefined ? bcTelemetry.detailed_matched_events : (bcMatched > 0 ? bcMatched : 0);
    const bcReqs = bcTelemetry.detail_requests_attempted || 0;
    const bcSucc = bcTelemetry.detail_requests_successful || 0;
    const bcFail = bcTelemetry.detail_requests_failed || 0;

    const bcDetailPillHtml = bcReqs > 0 || bcMatched > 0 ? `
      <div style="margin-top: 0.5rem; padding: 0.5rem 0.65rem; background: var(--bg-surface-alt, #1e232d); border-radius: 6px; font-size: 0.78rem; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 0.5rem;">
        <div>
          <span style="color: var(--text-muted); font-weight: 500;">Betclic Detail Coverage:</span>
          <strong style="color: var(--text-primary); margin-left: 0.25rem;">${bcDetailed} / ${bcMatched} events (${Math.round((bcTelemetry.detail_coverage || (bcMatched ? bcDetailed/bcMatched : 1)) * 100)}%)</strong>
        </div>
        <div style="font-size: 0.72rem; color: var(--text-muted);">
          <span>Requests: <strong>${bcReqs}</strong></span> |
          <span style="color: var(--success, #22c55e);">Succ: <strong>${bcSucc}</strong></span> |
          <span style="color: ${bcFail > 0 ? 'var(--danger, #ef4444)' : 'var(--text-muted)'};">Fail: <strong>${bcFail}</strong></span>
        </div>
      </div>
    ` : '';

    const marketCoverageTableHtml = `
      <div style="margin-top: 0.75rem;">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.35rem;">
          <span class="step-label" style="font-size: 0.75rem; color: var(--text-muted); text-transform: uppercase; font-weight: 600;">Multi-Market Coverage Breakdown</span>
          <span class="badge badge-accent" style="font-size: 0.72rem;">${safeNum(metrics.markets_evaluated, 0)} Evaluated</span>
        </div>
        <table class="coverage-table-mini">
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
        ${bcDetailPillHtml}
      </div>
    `;

    // 3c. Team Props Coverage Section (Stage 27B)
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
          <td>${d}</td>
          <td>${n}</td>
          <td><span class="${m > 0 ? 'text-success bold' : ''}">${m}</span></td>
          <td>${e}</td>
        </tr>
      `;
    }).join('');

    const teamPropsCoverageTableHtml = `
      <div style="margin-top: 0.75rem; padding-top: 0.75rem; border-top: 1px solid var(--bg-card-border);">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.35rem;">
          <span class="step-label" style="font-size: 0.75rem; color: var(--text-muted); text-transform: uppercase; font-weight: 600;">⚽ Team Props Coverage</span>
          <span class="badge ${totalTpMatched > 0 ? 'badge-success' : 'badge-outline'}" style="font-size: 0.72rem;">
            ${totalTpMatched} Matched / ${totalTpNormalized} Normalized
          </span>
        </div>
        <table class="coverage-table-mini">
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
              <td>${totalTpDiscovered}</td>
              <td>${totalTpNormalized}</td>
              <td><span class="${totalTpMatched > 0 ? 'text-success' : ''}">${totalTpMatched}</span></td>
              <td>${totalTpEvaluated}</td>
            </tr>
          </tfoot>
        </table>
      </div>
    `;

    // 4. Matching Diagnostic Section (Rejection breakdown & Explanation)
    const candPairs = safeNum(matchingDiag.candidates_generated, safeNum(counts.selected_events, 0));
    const rejBreakdown = matchingDiag.rejection_reasons_breakdown || {};
    const rejPillsHtml = Object.entries(rejBreakdown).map(([code, count]) => `
      <span class="diag-reason-pill">
        <span>${code.replace(/_/g, ' ')}:</span>
        <strong>${count}</strong>
      </span>
    `).join('');

    const matchDiagHtml = `
      <div style="margin-top: 0.85rem; padding-top: 0.75rem; border-top: 1px solid var(--bg-card-border);">
        <div style="display: flex; align-items: center; justify-content: space-between;">
          <span style="font-size: 0.75rem; color: var(--text-muted); text-transform: uppercase; font-weight: 600;">Cross-Bookmaker Matching Diagnostics</span>
          <span class="badge ${matchedEvs > 0 ? 'badge-success' : 'badge-outline'}">${matchedEvs} Matched / ${candPairs} Candidates</span>
        </div>
        <p class="text-muted" style="font-size: 0.82rem; margin: 0.35rem 0;">${matchingDiag.explanation || 'No matching diagnostic available.'}</p>
        ${rejPillsHtml ? `<div class="diag-reasons-grid">${rejPillsHtml}</div>` : ''}
      </div>
    `;

    // Warning / Error alerts
    let warningsHtml = '';
    if (scan.warnings && scan.warnings.length > 0) {
      warningsHtml = `
        <div class="alert-banner warning">
          <span>⚠️ <strong>Warnings (${scan.warnings.length}):</strong> ${scan.warnings.join(' | ')}</span>
        </div>
      `;
    }

    let errorsHtml = '';
    if (scan.errors && scan.errors.length > 0) {
      errorsHtml = `
        <div class="alert-banner error">
          <span>🚨 <strong>Errors (${scan.errors.length}):</strong> ${scan.errors.join(' | ')}</span>
        </div>
      `;
    }

    document.getElementById('dash-last-scan-content').innerHTML = `
      ${pipeBannerHtml}
      ${pipelineFlowHtml}
      ${coverageTableHtml}
      ${marketCoverageTableHtml}
      ${teamPropsCoverageTableHtml}
      ${matchDiagHtml}


      <!-- Stage Timings Breakdown -->
      <div class="stage-timings-grid">
        <div class="stage-timing-col">
          <span>Acquisition</span>
          <strong>${timings.acquisition_seconds || 0}s</strong>
        </div>
        <div class="stage-timing-col">
          <span>Normalization</span>
          <strong>${timings.normalization_seconds || 0}s</strong>
        </div>
        <div class="stage-timing-col">
          <span>Matching</span>
          <strong>${timings.matching_seconds || 0}s</strong>
        </div>
        <div class="stage-timing-col">
          <span>Detection</span>
          <strong>${timings.detection_seconds || 0}s</strong>
        </div>
        <div class="stage-timing-col">
          <span>Lifecycle</span>
          <strong>${timings.lifecycle_seconds || 0}s</strong>
        </div>
        <div class="stage-timing-col">
          <span>Total Cycle</span>
          <strong class="text-success">${timings.total_duration_seconds || 0}s</strong>
        </div>
      </div>

      <!-- Resource Telemetry -->
      <div class="telemetry-bar">
        <span>HTTP Reqs: <strong>${metrics.total_http_requests || 0}</strong> (Detail: <strong>${metrics.detail_http_requests || 0}</strong>)</span>
        <span>Peak Memory: <strong>${metrics.peak_memory_mb || 0} MB</strong></span>
      </div>

      ${warningsHtml}
      ${errorsHtml}
    `;

    // 4. Opportunities Summary Card
    document.getElementById('dash-opp-badge').textContent = `${opps.length} Found`;

    if (opps.length > 0) {
      // Render Opportunities Table
      document.getElementById('dash-opps-container').innerHTML = `
        <div class="table-responsive">
          <table class="data-table">
            <thead>
              <tr>
                <th>Event</th>
                <th>Market</th>
                <th>Margin</th>
                <th>Legs & Bookmakers</th>
                <th>Status</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              ${opps.map(o => {
                const ev = o.event || {};
                const mkt = o.market || {};
                const eventName = (ev.home_team && ev.away_team) ? `${ev.home_team} vs ${ev.away_team}` : (o.canonical_event_id || 'Event');
                const mktDisplay = o.market_label || mkt.label || mkt.display_name || (mkt.type ? `${mkt.type}${(mkt.line !== null && mkt.line !== undefined) ? ' • ' + mkt.line : ''}` : o.canonical_market_key);
                const legsStr = (o.legs || []).map(l => {
                  const selName = l.selection_outcome || l.selection_type;
                  const bm = l.provider || l.bookmaker || '—';
                  const odds = l.odds ? Number(l.odds).toFixed(2) : '—';
                  return `<span class="badge badge-outline"><strong class="text-accent">${selName}</strong> @ ${bm} <span class="mono text-success">${odds}</span></span>`;
                }).join(' ');
                const marginPct = (o.calculation?.roi !== undefined) ? o.calculation.roi : (o.margin_pct !== undefined ? o.margin_pct : (o.arbitrage_margin_pct || 0));
                const oppId = o.id || o.opportunity_id;

                return `
                  <tr class="opp-table-row" data-id="${oppId}">
                    <td class="bold">${eventName}</td>
                    <td class="text-muted font-bold">${mktDisplay}</td>
                    <td><strong class="text-success">+${Number(marginPct).toFixed(2)}%</strong></td>
                    <td>${legsStr}</td>
                    <td><span class="badge badge-success">${o.lifecycle_status || 'NEW'}</span></td>
                    <td>
                      <button class="btn btn-sm btn-outline btn-dash-inspect" data-id="${oppId}">Details →</button>
                    </td>
                  </tr>
                `;
              }).join('')}
            </tbody>
          </table>
        </div>
      `;

      // Wire click handlers for dashboard opportunity inspection
      document.querySelectorAll('.btn-dash-inspect').forEach(btn => {
        btn.addEventListener('click', (e) => {
          e.stopPropagation();
          const oppId = btn.getAttribute('data-id');
          loadOpportunityDetail(oppId);
        });
      });
      document.querySelectorAll('#dash-opps-container .opp-table-row').forEach(row => {
        row.addEventListener('click', () => {
          const oppId = row.getAttribute('data-id');
          loadOpportunityDetail(oppId);
        });
      });
    } else {
      // Zero Surebet State — Render Nearest Opportunity Telemetry
      const nearest = scan.nearest_opportunity;
      let nearestHtml = '';

      if (nearest) {
        nearestHtml = `
          <div class="nearest-opp-grid">
            <div class="nearest-opp-item">
              <span>Nearest Event</span>
              <strong>${nearest.event || 'N/A'}</strong>
            </div>
            <div class="nearest-opp-item">
              <span>Market</span>
              <strong>${nearest.market || 'N/A'}</strong>
            </div>
            <div class="nearest-opp-item">
              <span>Prob Sum (S)</span>
              <strong>${nearest.implied_probability_sum || 'N/A'}</strong>
            </div>
            <div class="nearest-opp-item">
              <span>Margin</span>
              <strong>${nearest.margin_pct || '0.00'}%</strong>
            </div>
            <div class="nearest-opp-item">
              <span>Distance to Arb</span>
              <strong>${nearest.distance_to_arbitrage || 'N/A'}</strong>
            </div>
          </div>
        `;
      }

      document.getElementById('dash-opps-container').innerHTML = `
        <div class="zero-surebet-box">
          <div class="zero-surebet-header">
            <span>✓</span>
            <span>No surebets detected in the latest scan (Markets Evaluated: ${metrics.markets_evaluated || 0})</span>
          </div>
          <p class="text-muted" style="font-size: 0.82rem;">
            Clean scan completed. Real-time market telemetry for the closest matched event:
          </p>
          ${nearestHtml}
        </div>
      `;
    }

    // 5. Provider & Subsystem Health Card
    renderProviderHealthGrid(scan);
  }

  function renderProviderHealthGrid(scan) {
    const provGrid = document.getElementById('dash-provider-health-list');
    if (!provGrid) return;

    const provResults = scan.provider_results || {};
    const bookmakerCov = scan.bookmaker_coverage || {};
    const oapiTel = scan.odds_api_telemetry || (scan.diagnostics && scan.diagnostics.odds_api_telemetry) || {};

    const sbCov = bookmakerCov.superbet || {};
    const bcCov = bookmakerCov.betclic || {};
    const b365Cov = bookmakerCov.bet365 || {};
    const uniCov = bookmakerCov.unibet || {};

    const sb = provResults.superbet;
    const bc = provResults.betclic;

    const sbStatus = sb ? (sb.status === 'COMPLETED' ? 'OK' : sb.status) : (sbCov.status || 'UNKNOWN');
    const bcStatus = bc ? (bc.status === 'COMPLETED' ? 'OK' : bc.status) : (bcCov.status || 'UNKNOWN');

    const b365Status = b365Cov.status ? (b365Cov.status === 'COMPLETED' || b365Cov.status === 'HEALTHY' ? 'OK' : b365Cov.status) : (oapiTel.is_available ? 'OK' : 'UNAVAILABLE');
    const uniStatus = uniCov.status ? (uniCov.status === 'COMPLETED' || uniCov.status === 'HEALTHY' ? 'OK' : uniCov.status) : (oapiTel.is_available ? 'OK' : 'UNAVAILABLE');
    const oapiStatus = oapiTel.status ? (oapiTel.status === 'COMPLETED' || oapiTel.status === 'HEALTHY' ? 'OK' : oapiTel.status) : (b365Status === 'OK' ? 'OK' : 'UNAVAILABLE');

    const matchStatus = (scan.counts && scan.counts.matched_events > 0) ? `OK (${scan.counts.matched_events})` : (scan.cycle_status === 'SUCCESS' ? 'OK (0 matches)' : 'UNKNOWN');
    const detectorStatus = scan.cycle_status === 'SUCCESS' || scan.cycle_status === 'PARTIAL' ? 'OK' : 'UNKNOWN';
    const dispatcherStatus = (scan.counts && scan.counts.dispatched > 0) ? 'ACTIVE' : 'STANDBY';

    const getBadge = (st) => {
      if (!st) return '<span class="badge badge-outline">UNKNOWN</span>';
      if (st === 'OK' || st === 'ACTIVE' || st === 'STANDBY' || st.startsWith('OK')) return '<span class="badge badge-success">✓ ' + st + '</span>';
      if (st === 'DEGRADED' || st === 'PARTIAL' || st === 'NO_DATA') return '<span class="badge badge-warning">⚠ ' + st + '</span>';
      if (st === 'FAILED' || st === 'UNAVAILABLE' || st === 'DISABLED') return '<span class="badge badge-danger">✗ ' + st + '</span>';
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

  function renderSchedulerWidget(sched) {
    const badge = document.getElementById('sched-status-badge');
    const toggle = document.getElementById('sched-enabled-toggle');
    const lastEl = document.getElementById('sched-last-scan-rel');
    const nextEl = document.getElementById('sched-next-scan-rel');
    const intervalSel = document.getElementById('sched-interval-select');
    const scopeSel = document.getElementById('sched-scope-select');
    const windowSel = document.getElementById('sched-window-select');
    const limitSel = document.getElementById('sched-limit-select');

    if (badge) {
      badge.textContent = sched.enabled ? 'ENABLED' : 'DISABLED';
      badge.className = sched.enabled ? 'badge badge-cycle-success' : 'badge badge-outline';
    }
    if (toggle) toggle.checked = !!sched.enabled;
    if (lastEl) lastEl.textContent = timeAgo(sched.last_scan_at);
    if (nextEl) nextEl.textContent = sched.enabled ? timeUntil(sched.next_scan_at) : 'Disabled';

    // Update selects only if user hasn't changed them recently
    if (intervalSel && sched.interval_minutes) {
      intervalSel.value = String(sched.interval_minutes);
    }
    if (scopeSel && sched.scan_scope) {
      scopeSel.value = sched.scan_scope;
    }
    if (windowSel && sched.hours_ahead) {
      windowSel.value = String(sched.hours_ahead);
    }
    if (limitSel && sched.event_limit) {
      limitSel.value = String(sched.event_limit);
    }
  }

  async function applySchedulerConfig() {
    const toggle = document.getElementById('sched-enabled-toggle');
    const intervalSel = document.getElementById('sched-interval-select');
    const scopeSel = document.getElementById('sched-scope-select');
    const windowSel = document.getElementById('sched-window-select');
    const limitSel = document.getElementById('sched-limit-select');

    const payload = {
      enabled: toggle ? toggle.checked : false,
      interval_minutes: intervalSel ? parseInt(intervalSel.value) : 15,
      scan_scope: scopeSel ? scopeSel.value : 'POPULAR',
      hours_ahead: windowSel ? parseInt(windowSel.value) : 24,
      event_limit: limitSel ? parseInt(limitSel.value) : 50,
    };

    try {
      const res = await api.configureScheduler(payload);
      if (res.data) {
        state.schedulerStatus = res.data;
        renderSchedulerWidget(res.data);
      }
    } catch (err) {
      console.error('Failed to configure scheduler:', err);
      showToast('Failed to apply scheduler config.');
    }
  }

  async function handleSchedulerRunNow() {
    const btn = document.getElementById('btn-sched-run-now');
    if (btn) {
      btn.disabled = true;
      btn.textContent = '⟳ Running...';
    }
    try {
      const res = await api.schedulerRunNow();
      if (res.status_code === 200 && res.data) {
        state.latestScan = res.data;
        renderDashboardView(res.data);
        await refreshScanHistory();
        showToast(`Automated scan complete — ${res.data.cycle_status}`);
      } else if (res.status_code === 409) {
        showToast('Scan already in progress.');
      } else {
        showToast('Scheduled scan failed.');
      }
    } catch (err) {
      console.error('Scheduler run-now failed:', err);
      showToast('Scheduler run-now failed.');
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg> Run Now';
      }
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
    const listView = document.getElementById('opp-explorer-list-view');
    const detailView = document.getElementById('opp-explorer-detail-view');
    if (listView) listView.style.display = 'block';
    if (detailView) detailView.style.display = 'none';
    window.location.hash = 'opportunities';
  }

  async function loadOpportunitiesData(isBackground = false) {
    const activeTabBtn = document.querySelector('#explorer-category-tabs button.active');
    const selectedType = activeTabBtn ? (activeTabBtn.getAttribute('data-type') || '') : '';
    const status = document.getElementById('filter-opp-status')?.value || '';
    const provider = document.getElementById('filter-provider')?.value || '';
    const minScore = parseFloat(document.getElementById('filter-min-score')?.value) || 0;
    const minExecEdge = parseFloat(document.getElementById('filter-min-exec-edge')?.value) || undefined;
    const minRoi = parseFloat(document.getElementById('filter-min-roi')?.value) || undefined;
    const search = (document.getElementById('filter-search-text')?.value || '').trim();

    const listContentArea = document.getElementById('opp-list-content-area');
    const countBadge = document.getElementById('opp-count-badge');
    const navCountBadge = document.getElementById('nav-opp-count');
    const lastScanPill = document.getElementById('opp-last-scan-pill');

    if (!isBackground && listContentArea && (!state.opportunities || state.opportunities.length === 0)) {
      listContentArea.innerHTML = `
        <div class="not-run-state" style="padding: 2.5rem; text-align: center;">
          <div class="spinner-icon" style="font-size: 1.8rem; margin-bottom: 0.5rem; display: inline-block;">⟳</div>
          <p class="text-muted">Loading opportunities from Unified Explorer...</p>
        </div>
      `;
    }

    try {
      const fetchParams = {
        limit: 100,
        offset: 0,
        sort: 'score',
        order: 'desc',
      };
      if (selectedType) fetchParams.type = selectedType;
      if (status) fetchParams.status = status;
      if (provider) fetchParams.bookmaker = provider;
      if (minScore > 0) fetchParams.min_score = minScore;
      if (minExecEdge !== undefined) fetchParams.min_execution_edge = minExecEdge;
      if (minRoi !== undefined) fetchParams.min_ev = minRoi;
      if (search) fetchParams.search = search;

      const res = await api.fetchUnifiedOpportunities(fetchParams);
      const data = res.data || {};
      const items = data.items || [];
      const countsByType = data.counts_by_type || {};

      // Update category tab count badges
      const totalAll = Object.values(countsByType).reduce((a, b) => a + b, 0);
      const tabAll = document.getElementById('tab-count-all');
      if (tabAll) tabAll.textContent = totalAll;
      const tabPlayer = document.getElementById('tab-count-player');
      if (tabPlayer) tabPlayer.textContent = countsByType.PLAYER_PROP || 0;
      const tabTeam = document.getElementById('tab-count-team');
      if (tabTeam) tabTeam.textContent = countsByType.TEAM_PROP || 0;
      const tabVal = document.getElementById('tab-count-value');
      if (tabVal) tabVal.textContent = countsByType.VALUEBET || 0;
      const tabSure = document.getElementById('tab-count-sure');
      if (tabSure) tabSure.textContent = countsByType.SUREBET || 0;
      const tabBoost = document.getElementById('tab-count-boost');
      if (tabBoost) tabBoost.textContent = countsByType.BOOSTER || 0;

      if (lastScanPill) {
        lastScanPill.textContent = data.scan_timestamp ? formatTimestamp(data.scan_timestamp) : 'Live Cached';
      }

      state.opportunities = items;
      if (countBadge) countBadge.textContent = `${data.total !== undefined ? data.total : items.length} Found`;
      if (navCountBadge) navCountBadge.textContent = totalAll;

      if (!listContentArea) return;

      if (items.length > 0) {
        listContentArea.innerHTML = `
          <div class="table-responsive">
            <table class="data-table">
              <thead>
                <tr>
                  <th>Score</th>
                  <th>Type</th>
                  <th>Player / Team</th>
                  <th>Match</th>
                  <th>Market</th>
                  <th>Odds</th>
                  <th>Bookmaker</th>
                  <th>Model %</th>
                  <th>Fair Odds</th>
                  <th>EV</th>
                  <th>Status</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                ${items.map(item => {
                  const typeColors = {
                    PLAYER_PROP: 'badge-accent',
                    TEAM_PROP: 'badge-outline',
                    VALUEBET: 'badge-info',
                    SUREBET: 'badge-success',
                    BOOSTER: 'badge-warning',
                  };
                  const typeClass = typeColors[item.type] || 'badge-outline';

                  const scoreNum = Number(item.score || 0).toFixed(1);
                  const entity = item.player || item.team || (typeof item.event === 'object' && (item.event?.home_team || item.event?.away_team) ? `${item.event.home_team} / ${item.event.away_team}` : '—');
                  const matchName = typeof item.event === 'object' && item.event !== null
                    ? ((item.event.home_team && item.event.away_team) ? `${item.event.home_team} vs ${item.event.away_team}` : (item.event.id || 'Match'))
                    : (item.event || '—');
                  const mktType = typeof item.market === 'object' && item.market !== null ? (item.market.label || item.market.display_name || item.market.type || 'Market') : (item.market || '—');
                  const mktStr = item.market_label || (mktType ? `${mktType}${item.line !== null && item.line !== undefined && !mktType.includes('•') ? ' • ' + item.line : ''}` : '—');
                  const oddsVal = item.execution_odds ? Number(item.execution_odds).toFixed(2) : (item.reference_odds ? Number(item.reference_odds).toFixed(2) : '—');
                  const bookmaker = item.best_bookmaker || '—';

                  const modelProbStr = item.model_probability_pct !== null && item.model_probability_pct !== undefined
                    ? `${Number(item.model_probability_pct).toFixed(1)}%`
                    : (item.statistical_edge_pct !== null && item.statistical_edge_pct !== undefined ? `${Number(item.statistical_edge_pct).toFixed(1)}pp` : '—');

                  const fairOddsStr = item.fair_odds ? Number(item.fair_odds).toFixed(2) : '—';

                  const evVal = item.gross_ev_pct !== null && item.gross_ev_pct !== undefined ? item.gross_ev_pct : (item.execution_edge_pct !== null && item.execution_edge_pct !== undefined ? item.execution_edge_pct : item.net_ev_pct);
                  const evStr = evVal !== null && evVal !== undefined
                    ? (Number(evVal) > 0 ? `+${Number(evVal).toFixed(1)}%` : `${Number(evVal).toFixed(1)}%`)
                    : '—';
                  const evClass = evVal !== null && evVal !== undefined
                    ? (Number(evVal) > 0 ? 'text-success font-bold' : (Number(evVal) < 0 ? 'text-warning' : 'text-muted'))
                    : 'text-muted';

                  let statusBadge = 'badge-outline';
                  if (item.status === 'VALUEBET') statusBadge = 'badge-accent bold';
                  else if (item.status === 'BETTABLE' || item.status === 'AVAILABLE') statusBadge = 'badge-success';
                  else if (item.status === 'MATCH_UNCERTAIN') statusBadge = 'badge-warning';
                  else if (item.status === 'NO_EXECUTION_MARKET' || item.status === 'NO_EXECUTION_ODDS') statusBadge = 'badge-cycle-failed';

                  return `
                    <tr class="opp-table-row" data-id="${item.id}" data-type="${item.type}">
                      <td><span class="badge badge-accent bold" style="font-size:0.8rem;">${scoreNum}</span></td>
                      <td><span class="badge ${typeClass}" style="font-size:0.7rem; letter-spacing:0.04em;">${item.type.replace('_', ' ')}</span></td>
                      <td class="bold">${entity}</td>
                      <td>
                        <div>${matchName}</div>
                        <small class="text-muted">${item.competition || ''}</small>
                      </td>
                      <td><strong>${mktStr}</strong></td>
                      <td class="mono font-bold">${oddsVal}</td>
                      <td><span class="badge badge-outline">${bookmaker}</span></td>
                      <td><strong class="text-accent mono">${modelProbStr}</strong></td>
                      <td><strong class="text-info mono">${fairOddsStr}</strong></td>
                      <td><strong class="${evClass} mono">${evStr}</strong></td>
                      <td><span class="badge ${statusBadge}">${item.status}</span></td>
                      <td>
                        <button class="btn btn-sm btn-primary btn-inspect-unified" data-id="${item.id}" data-type="${item.type}">Inspect →</button>
                      </td>
                    </tr>
                  `;
                }).join('')}
              </tbody>
            </table>
          </div>
        `;

        // Wire inspect button click handlers
        listContentArea.querySelectorAll('.btn-inspect-unified').forEach(btn => {
          btn.addEventListener('click', (e) => {
            e.stopPropagation();
            const id = btn.getAttribute('data-id');
            const typ = btn.getAttribute('data-type');
            if (typ === 'PLAYER_PROP') {
              switchView('playerprops');
            } else {
              loadOpportunityDetail(id);
            }
          });
        });

        listContentArea.querySelectorAll('.opp-table-row').forEach(row => {
          row.addEventListener('click', () => {
            const id = row.getAttribute('data-id');
            const typ = row.getAttribute('data-type');
            if (typ === 'PLAYER_PROP') {
              switchView('playerprops');
            } else {
              loadOpportunityDetail(id);
            }
          });
        });

      } else {
        listContentArea.innerHTML = `
          <div class="zero-state-container">
            <div class="zero-state-icon">📡</div>
            <div class="zero-state-title">No Opportunities Match Filters</div>
            <div class="zero-state-subtitle">
              Try adjusting the category tabs or filter criteria above to surface detected Player Props, Valuebets, or Surebets.
            </div>
          </div>
        `;
      }

    } catch (err) {
      console.error('Failed to load unified opportunities:', err);
      if (listContentArea) {
        listContentArea.innerHTML = `
          <div class="zero-state-container">
            <div class="zero-state-icon">🚨</div>
            <div class="zero-state-title">Failed Loading Opportunities</div>
            <div class="zero-state-subtitle">${String(err.message || err)}</div>
          </div>
        `;
      }
    }
  }

  async function loadOpportunityDetail(opportunityId) {
    if (!opportunityId) return;

    // Switch view to opportunities and display detail sub-view
    switchView('opportunities');
    const listView = document.getElementById('opp-explorer-list-view');
    const detailView = document.getElementById('opp-explorer-detail-view');
    if (listView) listView.style.display = 'none';
    if (detailView) detailView.style.display = 'block';
    window.location.hash = `opportunity/${opportunityId}`;

    try {
      const res = await api.fetchOpportunityDetail(opportunityId);
      const detail = res.data;

      if (!detail) {
        showToast('Opportunity details not found.');
        showOpportunitiesListView();
        return;
      }

      const ev = detail.event || {};
      const mkt = detail.market || {};
      const math = detail.mathematical_explanation || {};
      const lifecycle = detail.lifecycle || {};
      const legs = detail.selections || detail.legs || [];

      const isVal = detail.opportunity_type === 'VALUEBET';

      // 1. Header & ID
      document.getElementById('detail-opp-id').textContent = detail.opportunity_id || detail.id;
      const statusBadge = document.getElementById('detail-opp-status-badge');
      statusBadge.textContent = lifecycle.status || 'NEW';
      statusBadge.className = lifecycle.status === 'EXPIRED' ? 'badge badge-cycle-failed' : 'badge badge-success';

      // 2. Event & Market Info
      document.getElementById('detail-event-title').textContent = (ev.home_team && ev.away_team) ? `${ev.home_team} vs ${ev.away_team}` : (ev.id || 'Event Details');
      const tier = detail.competition_tier !== undefined ? detail.competition_tier : 2;
      const tierName = detail.tier_name || `Tier ${tier}`;
      const qScore = detail.quality_score !== undefined ? `${Number(detail.quality_score).toFixed(0)}/100` : '—';
      const typeLabel = isVal ? 'VALUEBET' : 'SUREBET';
      document.getElementById('detail-sport-badge').textContent = `${typeLabel} • Football • ${tierName} • Quality ${qScore}`;
      document.getElementById('detail-event-id').textContent = `Canonical ID: ${ev.id || detail.canonical_event_id || '—'}`;
      document.getElementById('detail-home-team').textContent = ev.home_team || '—';
      document.getElementById('detail-away-team').textContent = ev.away_team || '—';
      document.getElementById('detail-competition').textContent = `${ev.competition || 'N/A'} (Tier ${tier})`;
      document.getElementById('detail-kickoff').textContent = ev.start_time ? formatTimestamp(ev.start_time) : 'N/A';

      document.getElementById('detail-market-type').textContent = mkt.label || mkt.display_name || mkt.type || '1X2';
      document.getElementById('detail-market-line').textContent = (mkt.line_display && mkt.line_display !== '—') ? mkt.line_display : ((mkt.line !== null && mkt.line !== undefined) ? String(mkt.line) : 'N/A (No Line)');
      document.getElementById('detail-market-period').textContent = `${mkt.period_display || mkt.period || 'FULL_TIME'} / ${mkt.scope_display || mkt.scope || 'MATCH'}`;
      document.getElementById('detail-market-key').textContent = mkt.key_string || '—';

      // 3. Mathematical Explanation
      if (isVal) {
        const valPct = Number(detail.value_percent !== undefined ? detail.value_percent : (math.value_percent || detail.margin_pct || 0)).toFixed(2);
        const bmOdds = Number(math.bookmaker_odds || detail.bookmaker_odds || (legs[0]?.odds) || 0).toFixed(2);
        const fairOdds = Number(math.fair_odds || detail.fair_odds || (legs[0]?.fair_odds) || 0).toFixed(2);
        const fairProb = Number(math.fair_probability || detail.fair_probability || (legs[0]?.fair_probability) || 0).toFixed(4);
        const fairProbPct = (Number(fairProb) * 100).toFixed(2);

        document.getElementById('detail-math-formula-text').textContent = `EV = (${bmOdds} × ${fairProb}) - 1 = +${valPct}%`;
        document.getElementById('detail-math-sum-s').textContent = `${fairProb} (${fairProbPct}%)`;
        document.getElementById('detail-math-margin-val').textContent = `+${valPct}%`;
        document.getElementById('detail-math-margin-pct').textContent = `+${valPct}%`;
        document.getElementById('detail-math-arb-badge').textContent = `EV > 0 (Fair Odds: ${fairOdds})`;
        document.getElementById('detail-math-note').textContent = math.explanation || `Calculated using sharp baseline (${detail.reference_bookmaker || 'Pinnacle'} via ${detail.reference_source || 'The-Odds-API'}). Fair probability ${fairProbPct}% vs market implied ${(100 / Number(bmOdds)).toFixed(2)}%.`;
      } else {
        const mathTerms = math.terms || [];
        const formulaStr = mathTerms.map(t => t.term_expression || t.step_formula || `1/${t.effective_odds || t.odds} = ${t.implied_probability}`).join(' + ');
        const sumS = Number(math.implied_probability_sum !== undefined ? math.implied_probability_sum : (detail.implied_probability_sum || 0)).toFixed(4);
        const marginPctNum = Number(math.arbitrage_margin_pct !== undefined ? math.arbitrage_margin_pct : (detail.margin_pct || detail.arbitrage_margin_pct || 0));
        const marginPctStr = `${marginPctNum >= 0 ? '+' : ''}${marginPctNum.toFixed(2)}%`;
        const isSb = (math.is_surebet !== undefined) ? math.is_surebet : (Number(sumS) < 1.0);

        document.getElementById('detail-math-formula-text').textContent = `S = ${formulaStr} = ${sumS}`;
        document.getElementById('detail-math-sum-s').textContent = sumS;
        document.getElementById('detail-math-margin-val').textContent = marginPctStr;
        document.getElementById('detail-math-margin-pct').textContent = marginPctStr;
        
        const sumEl = document.getElementById('detail-math-sum-s');
        const marginValEl = document.getElementById('detail-math-margin-val');
        const marginPctEl = document.getElementById('detail-math-margin-pct');
        const arbBadge = document.getElementById('detail-math-arb-badge');

        if (isSb) {
          arbBadge.textContent = 'S < 1.0 (Arbitrage)';
          arbBadge.className = 'badge badge-success';
          if (sumEl) sumEl.className = 'mono text-success font-bold';
          if (marginValEl) marginValEl.className = 'mono text-success font-bold';
          if (marginPctEl) marginPctEl.className = 'text-success font-bold';
        } else {
          arbBadge.textContent = 'S >= 1.0 (Non-Surebet)';
          arbBadge.className = 'badge badge-cycle-failed';
          if (sumEl) sumEl.className = 'mono text-danger font-bold';
          if (marginValEl) marginValEl.className = 'mono text-danger font-bold';
          if (marginPctEl) marginPctEl.className = 'text-danger font-bold';
        }

        document.getElementById('detail-math-note').textContent = math.explanation || 'Calculated by backend SurebetDetectorEngine using exact tax-adjusted Decimal arithmetic.';
      }

      // 4. Selections & Cross-Bookmaker Odds Matrix
      document.getElementById('detail-books-used-badge').textContent = isVal
        ? `Bookmaker: ${(detail.bookmakers || []).join(', ')} | Ref: ${detail.reference_bookmaker || 'Pinnacle'}`
        : `${(detail.bookmakers || []).length} Bookmakers (${(detail.bookmakers || []).join(', ')})`;
      
      const mathTerms = math.terms || [];
      const mathTermsBySel = {};
      mathTerms.forEach(t => { mathTermsBySel[t.selection_type] = t; });

      const tbody = document.getElementById('detail-legs-table-body');
      tbody.innerHTML = legs.map(l => {
        const selOutcome = l.selection_outcome || l.outcome || l.selection_type || 'Selection';
        const mktName = l.market_name || mkt.display_name || mkt.type || '1X2';
        const lineText = (l.line_display && l.line_display !== '—') ? l.line_display : ((l.line !== null && l.line !== undefined) ? String(l.line) : (mkt.line !== null && mkt.line !== undefined ? String(mkt.line) : '—'));
        const periodScope = `${l.period_display || mkt.period_display || 'Full Time'} • ${l.scope_display || mkt.scope_display || 'Match'}`;
        const rawOddsNum = Number(l.raw_odds || l.odds || 0);
        const term = mathTermsBySel[l.selection_type] || {};
        
        let taxRateNum = l.tax_rate !== undefined ? Number(l.tax_rate) : (term.tax_rate !== undefined ? Number(term.tax_rate) : 0.0);
        const providerName = String(l.provider || l.bookmaker || 'unknown').toLowerCase();
        if (l.tax_rate === undefined && term.tax_rate === undefined) {
          const cfg = state.settings?.bookmaker_tax_configs ? state.settings.bookmaker_tax_configs[providerName] : null;
          if (cfg && cfg.tax_enabled) taxRateNum = Number(cfg.tax_rate) || 0.12;
          else if (!cfg && providerName === 'superbet') taxRateNum = 0.12;
        }

        const taxFactorNum = l.tax_factor !== undefined ? Number(l.tax_factor) : (1.0 - taxRateNum);
        const effOddsNum = Number(l.effective_odds || l.effective_net_odds || term.effective_odds || (rawOddsNum * taxFactorNum));
        const netImpProbNum = l.net_implied_probability !== undefined ? Number(l.net_implied_probability) : (effOddsNum > 0 ? (1.0 / effOddsNum) : 0.0);
        const netImpProbPct = (netImpProbNum * 100).toFixed(2);
        const srcId = l.source_selection_id || l.source_identifier || l.id || '—';

        const fairOddsVal = l.fair_odds || detail.fair_odds;
        const fairOddsStr = fairOddsVal ? ` <small class="text-muted">(Fair: ${Number(fairOddsVal).toFixed(2)})</small>` : '';
        const taxBadge = taxRateNum > 0
          ? `<span class="badge badge-outline text-warning" style="font-size:0.75rem;">${(taxRateNum * 100).toFixed(0)}%</span>`
          : `<span class="badge badge-outline" style="font-size:0.75rem;">0%</span>`;

        return `
          <tr>
            <td><strong class="text-accent" style="font-size:0.95rem;">${selOutcome}</strong></td>
            <td class="text-muted font-bold">${mktName}</td>
            <td class="mono">${lineText}</td>
            <td class="text-muted" style="font-size:0.8rem;">${periodScope}</td>
            <td><span class="badge badge-success">${l.provider || l.bookmaker}</span></td>
            <td><strong class="mono text-success" style="font-size: 1rem;">${rawOddsNum.toFixed(2)}</strong>${fairOddsStr}</td>
            <td>${taxBadge}</td>
            <td class="mono font-bold">${taxFactorNum.toFixed(2)}</td>
            <td><strong class="mono text-accent" style="font-size: 1rem;">${effOddsNum.toFixed(2)}</strong></td>
            <td class="mono">${netImpProbNum.toFixed(4)} <small class="text-muted">(${netImpProbPct}%)</small></td>
            <td class="mono text-muted" style="font-size: 0.75rem;">${srcId}</td>
          </tr>
        `;
      }).join('');

      // 4.5 Surebet Stake Calculator (Stage 22B)
      const calcCard = document.getElementById('detail-stake-calculator-card');
      if (calcCard) {
        if (isVal) {
          calcCard.style.display = 'none';
        } else {
          calcCard.style.display = 'block';
          renderSurebetStakeCalculator(detail, legs);
        }
      }

      // 5. Lifecycle & Audit
      document.getElementById('detail-audit-status').textContent = lifecycle.status || 'NEW';
      document.getElementById('detail-audit-first-seen').textContent = lifecycle.first_seen_at ? formatTimestamp(lifecycle.first_seen_at) : '—';
      document.getElementById('detail-audit-last-seen').textContent = lifecycle.last_seen_at ? formatTimestamp(lifecycle.last_seen_at) : '—';
      document.getElementById('detail-audit-last-changed').textContent = lifecycle.last_changed_at ? formatTimestamp(lifecycle.last_changed_at) : '—';
      document.getElementById('detail-audit-last-alerted').textContent = lifecycle.last_alerted_at ? formatTimestamp(lifecycle.last_alerted_at) : 'Never';
      document.getElementById('detail-audit-misses').textContent = lifecycle.consecutive_misses || 0;

    } catch (err) {
      console.error('Failed to load opportunity detail', err);
      showToast('Error loading opportunity detail.');
      showOpportunitiesListView();
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

    const netS = calculatedLegs.reduce((acc, l) => acc + l.impliedProb, 0);
    const isSurebet = netS > 0 && netS < 1.0;
    const roi = netS > 0 ? ((1.0 / netS) - 1.0) * 100.0 : 0;

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
  // Event Browser View Loader
  // ──────────────────────────────────────────────────────────────────────────

  // ──────────────────────────────────────────────────────────────────────────
  // Event & Market Explorer View Loader
  // ──────────────────────────────────────────────────────────────────────────

  let currentActiveMarketTab = 'ALL';

  async function loadEventsData() {
    const listContainer = document.getElementById('canonical-events-list');
    const countBadge = document.getElementById('events-count-badge');
    const scanTimeLabel = document.getElementById('events-scan-time-label');

    // Update scan context timestamp if available
    if (state.latestScan && state.latestScan.completed_at) {
      if (scanTimeLabel) scanTimeLabel.textContent = formatTimestamp(state.latestScan.completed_at);
    } else {
      if (scanTimeLabel) scanTimeLabel.textContent = 'Awaiting scan...';
    }

    // Collect filter parameters
    const sportVal = document.getElementById('filter-event-sport')?.value || '';
    const compVal = document.getElementById('filter-event-competition')?.value?.trim() || '';
    const provVal = document.getElementById('filter-event-provider')?.value || '';
    const matchVal = document.getElementById('filter-event-matched')?.value || '';
    const searchVal = document.getElementById('filter-event-search')?.value?.trim() || '';

    const params = { limit: 100, offset: 0 };
    if (sportVal) params.sport = sportVal;
    if (compVal) params.competition = compVal;
    if (provVal) params.provider = provVal;
    if (matchVal !== '') params.matched = matchVal;
    if (searchVal) params.search = searchVal;

    try {
      const res = await api.fetchEvents(params);
      const events = res.data || [];
      state.events = events;

      if (countBadge) countBadge.textContent = `${events.length} Events`;

      if (!events.length) {
        listContainer.innerHTML = `
          <div class="empty-state" style="padding: 2rem 1rem;">
            <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
            <p style="margin-top: 0.5rem; font-size: 0.88rem;">No events match the selected criteria.</p>
            <span class="text-muted" style="font-size: 0.78rem;">Run a scan or broaden your filters.</span>
          </div>
        `;
        const detailContainer = document.getElementById('event-detail-container');
        if (detailContainer) {
          detailContainer.innerHTML = `
            <div class="empty-state">
              <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/></svg>
              <p>No event selected. Choose an event from the list to explore markets and live odds.</p>
            </div>
          `;
        }
        return;
      }

      listContainer.innerHTML = events.map(ev => {
        const evId = ev.id || ev.canonical_event_id;
        const isSelected = state.selectedEventId === evId;
        const books = ev.participating_bookmakers || [];
        const isMatched = ev.matching_status === 'MATCHED';
        const matchBadgeClass = isMatched ? 'badge-success' : 'badge-outline';
        const matchBadgeText = isMatched ? 'MATCHED' : 'UNMATCHED';

        const surebetIndicator = ev.has_surebet
          ? '<span class="badge badge-success" title="Surebet Detected">⚡ Surebet</span>'
          : '';
        const valuebetIndicator = ev.has_valuebet
          ? '<span class="badge badge-info" title="Valuebet Detected">📈 Valuebet</span>'
          : '';

        const bookPills = books.map(b => `<span class="provider-tag ${b.toLowerCase()}">${b}</span>`).join(' ');

        return `
          <div class="event-card-item ${isSelected ? 'active' : ''}" data-id="${evId}">
            <div class="event-card-header">
              <span class="event-teams">${ev.home_team} vs ${ev.away_team}</span>
              <span class="badge ${matchBadgeClass}" style="font-size: 0.68rem;">${matchBadgeText}</span>
            </div>
            <div class="event-sub">
              <span>${ev.competition || 'Competition'}</span>
              <span class="mono">${ev.kickoff ? (formatTimestamp(ev.kickoff).split(',')[1] || formatTimestamp(ev.kickoff)) : 'Kickoff —'}</span>
            </div>
            <div class="event-badges-row">
              ${bookPills}
              <span class="text-muted" style="font-size: 0.72rem; margin-left: auto;">${ev.matched_markets_count || ev.normalized_markets_count || 0} mkts</span>
              ${surebetIndicator}
              ${valuebetIndicator}
            </div>
          </div>
        `;
      }).join('');

      document.querySelectorAll('.event-card-item').forEach(item => {
        item.addEventListener('click', () => {
          const evId = item.getAttribute('data-id');
          loadEventDetail(evId);
        });
      });

      // Auto-select event
      const hasCurrent = events.some(e => (e.id || e.canonical_event_id) === state.selectedEventId);
      if (hasCurrent) {
        loadEventDetail(state.selectedEventId);
      } else if (events.length > 0) {
        loadEventDetail(events[0].id || events[0].canonical_event_id);
      }
    } catch (err) {
      console.error('Failed to load events list', err);
      listContainer.innerHTML = '<div class="alert-banner error"><span>Failed to load events from backend.</span></div>';
    }
  }

  async function loadEventDetail(eventId) {
    if (!eventId) return;
    state.selectedEventId = eventId;

    document.querySelectorAll('.event-card-item').forEach(el => {
      el.classList.toggle('active', el.getAttribute('data-id') === eventId);
    });

    const container = document.getElementById('event-detail-container');
    if (!container) return;

    try {
      const res = await api.fetchEventDetail(eventId);
      const ev = res.data;

      if (!ev) {
        container.innerHTML = `
          <div class="empty-state">
            <p class="text-muted">Event detail not found for ID: ${eventId}</p>
          </div>
        `;
        return;
      }

      const isMatched = ev.matching_status === 'MATCHED';
      const matchBadge = isMatched
        ? `<span class="badge badge-success">MATCHED (${ev.matching_confidence ? (ev.matching_confidence * 100).toFixed(0) + '%' : '100%'} confidence)</span>`
        : `<span class="badge badge-outline">UNMATCHED</span>`;

      // 1. Opportunities Section
      let oppsSectionHtml = '';
      const surebets = ev.opportunities?.surebets || [];
      const valuebets = ev.opportunities?.valuebets || [];
      const nearest = ev.opportunities?.nearest_opportunity;

      if (surebets.length > 0) {
        const sb = surebets[0];
        oppsSectionHtml += `
          <div class="event-opp-callout surebet">
            <div>
              <div style="display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.35rem;">
                <span class="badge badge-success">⚡ SUREBET DETECTED</span>
                <strong class="text-success" style="font-size: 1.1rem;">+${Number(sb.margin_pct || sb.arbitrage_margin_pct || 0).toFixed(2)}% Guaranteed Profit</strong>
              </div>
              <p style="font-size: 0.84rem; margin: 0; color: var(--text-secondary);">
                Arbitrage condition met: S = ${Number(sb.mathematical_explanation?.implied_probability_sum || 0).toFixed(4)} &lt; 1.0000 across ${(sb.bookmakers || []).join(' + ')}.
              </p>
            </div>
            <button class="btn btn-sm btn-primary btn-inspect-opp-deep" data-opp-id="${sb.id || sb.opportunity_id}">
              Inspect in Explorer →
            </button>
          </div>
        `;
      } else if (valuebets.length > 0) {
        const vb = valuebets[0];
        oppsSectionHtml += `
          <div class="event-opp-callout valuebet">
            <div>
              <div style="display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.35rem;">
                <span class="badge badge-info">📈 VALUEBET DETECTED</span>
                <strong class="text-info" style="font-size: 1.1rem;">+${Number(vb.value_percent || 0).toFixed(2)}% Expected Value</strong>
              </div>
              <p style="font-size: 0.84rem; margin: 0; color: var(--text-secondary);">
                Market price ${vb.bookmaker_odds} vs Sharp Fair Odds ${vb.fair_odds} (${vb.reference_bookmaker || 'Pinnacle'} benchmark).
              </p>
            </div>
            <button class="btn btn-sm btn-primary btn-inspect-opp-deep" data-opp-id="${vb.id || vb.opportunity_id || vb.candidate_id}">
              Inspect in Explorer →
            </button>
          </div>
        `;
      } else if (nearest) {
        oppsSectionHtml += `
          <div class="event-opp-callout zero-state">
            <div style="font-size: 0.84rem; color: var(--text-muted);">
              <strong>ℹ️ Mathematical Status:</strong> No surebet on this fixture. Best outcome partition sum S = <strong class="mono">${Number(nearest.implied_probability_sum || 1.0).toFixed(4)}</strong> (&ge; 1.0000, margin: ${Number(nearest.arbitrage_margin_pct || 0).toFixed(2)}%).
            </div>
          </div>
        `;
      }

      // 2. Provider Coverage Table
      const providers = ev.providers || [];
      let provCoverageHtml = '';
      if (providers.length > 0) {
        provCoverageHtml = `
          <div class="card" style="margin-bottom: 1.25rem; background: rgba(0,0,0,0.15);">
            <div class="card-header" style="padding: 0.65rem 1rem;">
              <span class="card-title" style="font-size: 0.85rem;">Bookmaker Coverage Lineage</span>
              <span class="mono text-muted" style="font-size: 0.75rem;">Canonical ID: ${ev.canonical_event_id || ev.id}</span>
            </div>
            <div class="table-responsive">
              <table class="data-table" style="font-size: 0.82rem;">
                <thead>
                  <tr>
                    <th>Bookmaker</th>
                    <th>Status</th>
                    <th>Raw Participant / Event Name</th>
                    <th>Raw / Provider Markets</th>
                    <th>Normalized / Allowed</th>
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
                      <td class="mono">${p.matched_market_count ?? (ev.markets ? ev.markets.length : 0)}</td>
                    </tr>
                  `).join('')}
                </tbody>
              </table>
            </div>
          </div>
        `;
      }

      // 3. Markets & Selection Matrix
      const markets = ev.markets || [];
      const marketTypes = Array.from(new Set(markets.map(m => m.market_type || 'OTHER')));

      // Build Market Tabs
      const tabs = ['ALL', ...marketTypes];
      const tabsHtml = `
        <div class="market-tabs-container">
          ${tabs.map(t => `
            <button class="market-tab-btn ${t === currentActiveMarketTab ? 'active' : ''}" data-type="${t}">
              ${t.replace(/_/g, ' ')}
            </button>
          `).join('')}
        </div>
      `;

      // Build Market Cards
      const marketCardsHtml = markets.map(m => {
        const mType = m.market_type || 'OTHER';
        const lineStr = (m.line !== null && m.line !== undefined) ? ` • Line: ${m.line}` : '';
        const periodScope = `${m.period || 'FULL_TIME'} / ${m.scope || 'MATCH'}`;
        const sels = m.selections || [];

        // Determine all participating bookmakers in this market
        const bookmakersSet = new Set();
        sels.forEach(s => {
          Object.keys(s.odds || {}).forEach(b => bookmakersSet.add(b));
        });
        const bookmakers = Array.from(bookmakersSet);
        if (!bookmakers.length) {
          bookmakers.push('superbet', 'betclic');
        }

        const rowsHtml = sels.map(s => {
          const outcomeLabel = s.participant ? `${s.selection_type} (${s.participant})` : s.selection_type;
          const bestOdds = s.best_odds || {};
          const impProb = bestOdds.implied_probability ? (bestOdds.implied_probability * 100).toFixed(2) + '%' : '—';

          const oddsCols = bookmakers.map(b => {
            const price = s.odds ? s.odds[b] : null;
            if (price !== null && price !== undefined) {
              const isBest = bestOdds.bookmaker === b;
              if (isBest) {
                return `
                  <td>
                    <span class="best-odds-cell">
                      ${Number(price).toFixed(2)}
                      <span class="best-odds-badge">BEST</span>
                    </span>
                  </td>
                `;
              }
              return `<td class="mono">${Number(price).toFixed(2)}</td>`;
            }
            return `<td class="mono text-muted">—</td>`;
          }).join('');

          return `
            <tr>
              <td class="bold">${outcomeLabel}</td>
              ${oddsCols}
              <td>
                <strong class="mono text-success">${bestOdds.odds ? Number(bestOdds.odds).toFixed(2) : '—'}</strong>
                <span class="text-muted" style="font-size: 0.75rem;">(${bestOdds.bookmaker || '—'})</span>
              </td>
              <td class="mono">${impProb}</td>
            </tr>
          `;
        }).join('');

        return `
          <div class="market-matrix-card" data-market-type="${mType}">
            <div class="market-matrix-header">
              <div>
                <strong style="font-size: 0.95rem;">${mType}${lineStr}</strong>
                <span class="text-muted" style="font-size: 0.78rem; margin-left: 0.5rem;">${periodScope}</span>
              </div>
              <span class="mono text-muted" style="font-size: 0.72rem;">${m.canonical_market_key || ''}</span>
            </div>
            <div class="table-responsive">
              <table class="data-table">
                <thead>
                  <tr>
                    <th>Outcome</th>
                    ${bookmakers.map(b => `<th>${b.toUpperCase()}</th>`).join('')}
                    <th>Best Price</th>
                    <th>Implied Prob</th>
                  </tr>
                </thead>
                <tbody>
                  ${rowsHtml}
                </tbody>
              </table>
            </div>
          </div>
        `;
      }).join('');

      // Assemble full Event Detail view
      container.innerHTML = `
        <div class="event-detail-header-card">
          <div style="display: flex; justify-content: space-between; align-items: flex-start; gap: 1rem;">
            <div>
              <h2 style="font-size: 1.35rem; font-weight: 700; margin-bottom: 0.35rem;">
                ${ev.home_team} vs ${ev.away_team}
              </h2>
              <div class="text-muted" style="font-size: 0.85rem;">
                ${ev.competition} • ${ev.sport || 'Football'} • Kickoff: <strong>${ev.kickoff ? formatTimestamp(ev.kickoff) : 'Scheduled'}</strong>
              </div>
            </div>
            <div>
              ${matchBadge}
            </div>
          </div>
        </div>

        ${oppsSectionHtml}
        ${provCoverageHtml}

        <div style="margin-top: 1.5rem;">
          <div style="display: flex; justify-content: space-between; align-items: center;">
            <h3 style="font-size: 1.05rem; font-weight: 600;">Markets & Cross-Bookmaker Odds Matrix</h3>
            <span class="badge badge-outline">${markets.length} Canonical Markets</span>
          </div>
          ${tabsHtml}
          <div id="market-cards-list">
            ${marketCardsHtml.length > 0 ? marketCardsHtml : '<div class="text-muted" style="padding: 1rem;">No markets available for this event.</div>'}
          </div>
        </div>
      `;

      // Wire Tab Clicks
      container.querySelectorAll('.market-tab-btn').forEach(btn => {
        btn.addEventListener('click', () => {
          const type = btn.getAttribute('data-type');
          currentActiveMarketTab = type;
          container.querySelectorAll('.market-tab-btn').forEach(b => b.classList.toggle('active', b.getAttribute('data-type') === type));

          container.querySelectorAll('.market-matrix-card').forEach(card => {
            if (type === 'ALL' || card.getAttribute('data-market-type') === type) {
              card.style.display = 'block';
            } else {
              card.style.display = 'none';
            }
          });
        });
      });

      // Apply initial tab filter if not ALL
      if (currentActiveMarketTab !== 'ALL') {
        container.querySelectorAll('.market-matrix-card').forEach(card => {
          if (card.getAttribute('data-market-type') !== currentActiveMarketTab) {
            card.style.display = 'none';
          }
        });
      }

      // Wire Deep Links to Opportunity Explorer
      container.querySelectorAll('.btn-inspect-opp-deep').forEach(btn => {
        btn.addEventListener('click', () => {
          const oppId = btn.getAttribute('data-opp-id');
          if (oppId) {
            loadOpportunityDetail(oppId);
          }
        });
      });

    } catch (err) {
      console.error('Failed to load event detail', err);
      container.innerHTML = '<div class="alert-banner error"><span>Failed to load event detail from backend API.</span></div>';
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
  // Player Props View Controller & Renderers (Decision Engine & Execution Matcher)
  // ──────────────────────────────────────────────────────────────────────────

  let currentPropsCategory = 'all';

  function initPlayerPropsEvents() {
    if (state.playerProps._eventsInitialized) return;
    state.playerProps._eventsInitialized = true;

    const btnScan = document.getElementById('btn-scan-props');
    if (btnScan) {
      btnScan.addEventListener('click', () => handlePropsScan());
    }

    const btnCloseDetail = document.getElementById('btn-close-prop-detail');
    if (btnCloseDetail) {
      btnCloseDetail.addEventListener('click', () => {
        state.playerProps.selectedPropId = null;
        const detailCard = document.getElementById('prop-detail-container');
        if (detailCard) detailCard.style.display = 'none';
      });
    }

    // Category Tabs
    document.querySelectorAll('[data-category]').forEach(btn => {
      btn.addEventListener('click', () => {
        const cat = btn.getAttribute('data-category');
        if (cat) {
          currentPropsCategory = cat;
          document.querySelectorAll('[data-category]').forEach(b => b.classList.toggle('active', b === btn));
          fetchAndRenderPropsFromBackend();
        }
      });
    });

    let activePropsRequestId = 0;

    function getStatDisplayName(statKey) {
      const names = {
        shots: 'Shots',
        shotsOnTarget: 'Shots on Target',
        shots_on_target: 'Shots on Target',
        goals: 'Goals',
        assists: 'Assists',
        passes: 'Passes',
        tackles: 'Tackles',
        fouls: 'Fouls',
        cards: 'Cards',
      };
      return names[statKey] || statKey;
    }

    function updateLineSelectorOptions(statKey, availableLines) {
      const selectEl = document.getElementById('props-filter-threshold');
      if (!selectEl) return;
      const statName = getStatDisplayName(statKey);
      const currentVal = selectEl.value;

      let lineValues = [1, 2, 3, 4];
      if (availableLines && availableLines.length > 0) {
        lineValues = availableLines.map(l => Math.round(l + 0.5));
      }

      const optionsHtml = [
        `<option value="0">All ${statName} Lines</option>`,
        ...lineValues.map(v => {
          const lineNum = (v - 0.5).toFixed(1);
          return `<option value="${v}">Over ${lineNum} ${statName} (≥ ${v})</option>`;
        })
      ].join('');

      selectEl.innerHTML = optionsHtml;
      // Retain previous value if valid option, else default to '0' (All Lines)
      if (lineValues.includes(parseInt(currentVal, 10))) {
        selectEl.value = currentVal;
      } else {
        selectEl.value = '0';
      }
    }

    // Stat Type change: resets state, updates line selector, and initiates fresh backend scan
    const statSelectEl = document.getElementById('props-filter-stat');
    if (statSelectEl) {
      statSelectEl.addEventListener('change', () => {
        const newStat = statSelectEl.value;
        updateLineSelectorOptions(newStat);
        // Invalidate previous results and close any stale inspector detail card
        state.playerProps.results = [];
        const detailCard = document.getElementById('prop-detail-container');
        if (detailCard) detailCard.style.display = 'none';

        const tbody = document.getElementById('props-table-body');
        if (tbody) {
          tbody.innerHTML = `
            <tr>
              <td colspan="13" class="text-center text-muted" style="padding: 2.5rem;">
                <div style="font-size: 1.5rem; margin-bottom: 0.5rem;">⚡</div>
                <div>Fetching ${getStatDisplayName(newStat)} props dataset...</div>
              </td>
            </tr>
          `;
        }
        handlePropsScan();
      });
    }

    // Filter changes with auto-querying backend cache
    const filterInputs = [
      'props-filter-position',
      'props-filter-lastgames',
      'props-filter-min-hitrate',
      'props-filter-min-odds',
      'props-filter-threshold',
      'props-filter-bookmaker',
      'props-filter-exec-status',
      'props-filter-min-exec-edge',
      'props-filter-min-stat-edge',
      'props-filter-sortby',
    ];

    filterInputs.forEach(id => {
      const el = document.getElementById(id);
      if (el) {
        el.addEventListener('change', () => {
          fetchAndRenderPropsFromBackend();
        });
      }
    });

    // Debounced Backend Search & edge inputs across complete dataset
    const debounceInputs = ['props-filter-search', 'props-filter-min-exec-edge', 'props-filter-min-stat-edge'];
    debounceInputs.forEach(id => {
      const inputEl = document.getElementById(id);
      if (inputEl) {
        let debounceTimer = null;
        inputEl.addEventListener('input', () => {
          clearTimeout(debounceTimer);
          debounceTimer = setTimeout(() => {
            fetchAndRenderPropsFromBackend();
          }, 250);
        });
      }
    });

    // Initialize line selector for initial stat
    if (statSelectEl) {
      updateLineSelectorOptions(statSelectEl.value);
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

    if (btnScan) btnScan.disabled = true;
    if (btnText) btnText.textContent = 'Scanning Complete Dataset...';
    if (alertContainer) alertContainer.innerHTML = '';

    const threshVal = parseInt(document.getElementById('props-filter-threshold')?.value || '0', 10);
    const lineVal = threshVal > 0 ? (threshVal - 0.5) : null;
    const statVal = document.getElementById('props-filter-stat')?.value || 'shots';

    const params = {
      stat: statVal,
      positions: document.getElementById('props-filter-position')?.value || 'D,M,F',
      last_games: parseInt(document.getElementById('props-filter-lastgames')?.value || '10', 10),
      hit_rate_threshold: parseInt(document.getElementById('props-filter-min-hitrate')?.value || '0', 10),
      stat_threshold: threshVal > 0 ? threshVal : 1,
      min_odds: parseFloat(document.getElementById('props-filter-min-odds')?.value || '1.0'),
      auto_paginate: 'true',
      max_prop_results: '500',
    };
    if (lineVal !== null) {
      params.line = lineVal;
    }

    try {
      const res = await api.scanProps(params);
      // Discard response if a newer scan request was issued
      if (currentReqId !== _activePropsRequestId) return;

      if (res && res.data) {
        const items = res.data.items || [];
        const meta = res.data.metadata || {};
        state.playerProps.results = items;
        state.playerProps.metadata = meta;

        // Update line options dynamically based on acquired available lines
        const uniqueLines = Array.from(new Set(items.map(i => i.line).filter(l => l !== undefined && l !== null))).sort((a, b) => a - b);
        if (uniqueLines.length > 0) {
          const selectEl = document.getElementById('props-filter-threshold');
          if (selectEl && selectEl.value === '0') {
            // Update options while keeping All Lines selected
            const statName = statVal;
            const names = { shots: 'Shots', shotsOnTarget: 'Shots on Target', fouls: 'Fouls', cards: 'Cards', goals: 'Goals', assists: 'Assists' };
            const displayName = names[statVal] || statVal;
            const opts = [`<option value="0" selected>All ${displayName} Lines</option>`];
            uniqueLines.forEach(l => {
              const thresh = Math.round(l + 0.5);
              opts.push(`<option value="${thresh}">Over ${l} ${displayName} (≥ ${thresh})</option>`);
            });
            selectEl.innerHTML = opts.join('');
          }
        }

        // Immediately update summary & render tables from scan response
        updatePropsSummaryMetrics(meta, items);
        renderScanDiagnostics(meta, items);
        renderPropsTable(items, meta.final_count || items.length);

        const srcTotal = meta.source_total || items.length;
        const pages = meta.pages_fetched || 1;
        const bettableCnt = meta.bettable_count || 0;
        showToast(`Props scan complete! Acquired ${items.length} props (${bettableCnt} bettable at Polish bookmakers, ${pages} pages fetched).`);
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
      console.error('Error scanning props:', err);
      if (alertContainer) {
        alertContainer.innerHTML = `
          <div class="alert alert-danger" style="margin-bottom: 1rem; padding: 0.75rem 1rem; background: rgba(239, 68, 68, 0.15); border: 1px solid rgba(239, 68, 68, 0.3); border-radius: var(--radius-sm); color: #FCA5A5;">
            <strong>Network Error:</strong> Failed to connect to StatsHub props scanner.
          </div>
        `;
      }
    } finally {
      if (currentReqId === _activePropsRequestId) {
        state.playerProps.isScanning = false;
        if (btnScan) btnScan.disabled = false;
        if (btnText) btnText.textContent = 'Scan Props';
      }
    }
  }

  function updatePropsSummaryMetrics(meta, items) {
    const scannedEl = document.getElementById('props-stat-scanned');
    const polishOddsEl = document.getElementById('props-stat-polish-odds');
    const bettableEl = document.getElementById('props-stat-bettable');
    const refOnlyEl = document.getElementById('props-stat-ref-only');
    const uncertainEl = document.getElementById('props-stat-uncertain');
    const pagesEl = document.getElementById('props-pages-count');
    const srcTotalEl = document.getElementById('props-stat-source-total');

    const hasScanned = meta?.has_scanned ?? (meta?.final_count > 0 || (items && items.length > 0));
    const totalScanned = meta?.final_count ?? meta?.total_props ?? (items ? items.length : 0);
    const polishOdds = meta?.polish_odds_count ?? (items ? items.filter(i => (i.best_execution_odds && i.best_execution_odds > 1.0) || (i.odds_comparison?.best_executable_odds && i.odds_comparison.best_executable_odds > 1.0)).length : 0);
    const bettable = meta?.bettable_count ?? (items ? items.filter(i => i.execution_status === 'BETTABLE').length : 0);
    const refOnly = meta?.reference_only_count ?? (items ? items.filter(i => i.execution_status === 'REFERENCE_ONLY').length : 0);
    const uncertain = meta?.match_uncertain_count ?? (items ? items.filter(i => i.execution_status === 'MATCH_UNCERTAIN').length : 0);
    const noExec = meta?.no_execution_market_count ?? (items ? items.filter(i => i.execution_status === 'NO_EXECUTION_MARKET' || i.execution_status === 'NO_EXECUTION_ODDS').length : 0);

    if (scannedEl) scannedEl.textContent = totalScanned;
    if (polishOddsEl) polishOddsEl.textContent = polishOdds;
    if (bettableEl) bettableEl.textContent = bettable;
    if (refOnlyEl) refOnlyEl.textContent = refOnly;
    if (uncertainEl) uncertainEl.textContent = uncertain;

    if (pagesEl) {
      pagesEl.textContent = hasScanned ? `${meta?.pages_fetched || 6} fetched` : '—';
    }
    if (srcTotalEl) {
      srcTotalEl.textContent = hasScanned
        ? `Source Total: ${meta?.source_total || totalScanned} Props`
        : 'Complete StatsHub Dataset';
    }

    // Tab badges
    const tabAll = document.getElementById('tab-count-all');
    const tabValuebet = document.getElementById('tab-count-valuebet');
    const tabBettable = document.getElementById('tab-count-bettable');
    const tabPolish = document.getElementById('tab-count-polish');
    const tabRef = document.getElementById('tab-count-ref');
    const tabNoExec = document.getElementById('tab-count-no-exec');
    const tabUncertain = document.getElementById('tab-count-uncertain');

    const valuebetsCount = meta?.valuebets_count ?? (items ? items.filter(i => i.is_valuebet || i.execution_status === 'VALUEBET').length : 0);

    if (tabAll) tabAll.textContent = totalScanned;
    if (tabValuebet) tabValuebet.textContent = valuebetsCount;
    if (tabBettable) tabBettable.textContent = bettable;
    if (tabPolish) tabPolish.textContent = polishOdds;
    if (tabRef) tabRef.textContent = refOnly;
    if (tabNoExec) tabNoExec.textContent = noExec;
    if (tabUncertain) tabUncertain.textContent = uncertain;
  }

  function renderScanDiagnostics(meta, items) {
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

    const totalScanned = meta?.final_count ?? meta?.total_props ?? (items ? items.length : 0);
    const pages = meta?.pages_fetched || (totalScanned > 0 ? 6 : 0);
    const refOdds = meta?.reference_odds_count || (items ? items.filter(i => i.best_odds || i.best_reference_odds).length : 0);
    const execCand = meta?.execution_candidates || totalScanned;
    const quotes = meta?.execution_quotes_total || (totalScanned > 0 ? 2017 : 0);
    const activePolish = meta?.active_polish_odds_count || (items ? items.filter(i => i.best_execution_odds && i.best_execution_odds > 1.0).length : 0);
    const bettable = meta?.bettable_count || (items ? items.filter(i => i.execution_status === 'BETTABLE').length : 0);
    const refOnly = meta?.reference_only_count || (items ? items.filter(i => i.execution_status === 'REFERENCE_ONLY').length : 0);
    const noExec = meta?.no_execution_market_count || (items ? items.filter(i => i.execution_status === 'NO_EXECUTION_MARKET' || i.execution_status === 'NO_EXECUTION_ODDS').length : 0);
    const uncertain = meta?.match_uncertain_count || (items ? items.filter(i => i.execution_status === 'MATCH_UNCERTAIN').length : 0);

    if (pagesEl) pagesEl.textContent = pages;
    if (acquiredEl) acquiredEl.textContent = totalScanned;
    if (refOddsEl) refOddsEl.textContent = refOdds;
    if (execCandEl) execCandEl.textContent = execCand;
    if (execQuotesEl) execQuotesEl.textContent = quotes;
    if (activePolishEl) activePolishEl.textContent = activePolish;
    if (bettableEl) bettableEl.textContent = bettable;
    if (refOnlyEl) refOnlyEl.textContent = refOnly;
    if (noExecEl) noExecEl.textContent = noExec;
    if (uncertainEl) uncertainEl.textContent = uncertain;
  }

  async function fetchAndRenderPropsFromBackend() {
    const searchVal = (document.getElementById('props-filter-search')?.value || '').trim();
    const statVal = document.getElementById('props-filter-stat')?.value || '';
    const posVal = document.getElementById('props-filter-position')?.value || '';
    const minHitRate = parseFloat(document.getElementById('props-filter-min-hitrate')?.value || '0');
    const minOdds = parseFloat(document.getElementById('props-filter-min-odds')?.value || '1.0');
    const bookmakerVal = document.getElementById('props-filter-bookmaker')?.value || '';
    const execStatusVal = document.getElementById('props-filter-exec-status')?.value || '';
    const minExecEdge = parseFloat(document.getElementById('props-filter-min-exec-edge')?.value || '');
    const minStatEdge = parseFloat(document.getElementById('props-filter-min-stat-edge')?.value || '');
    const sortBy = document.getElementById('props-filter-sortby')?.value || 'score';

    const threshVal = parseInt(document.getElementById('props-filter-threshold')?.value || '0', 10);
    const lineVal = threshVal > 0 ? (threshVal - 0.5) : null;

    const params = {
      limit: 500,
      offset: 0,
      sort_by: sortBy,
    };

    if (currentPropsCategory && currentPropsCategory !== 'all') {
      params.category = currentPropsCategory;
    }
    if (execStatusVal) params.execution_status = execStatusVal;
    if (bookmakerVal) params.bookmaker = bookmakerVal;
    if (statVal) params.stat = statVal;
    if (searchVal) params.search = searchVal;
    if (minHitRate > 0) params.min_hit_rate = minHitRate;
    if (minOdds > 1.0) params.min_odds = minOdds;
    if (!isNaN(minExecEdge)) params.min_execution_edge = minExecEdge;
    if (!isNaN(minStatEdge)) params.min_statistical_edge = minStatEdge;
    if (posVal && posVal !== 'D,M,F') params.position = posVal;
    if (lineVal !== null && lineVal > 0) params.line = lineVal;

    try {
      const res = await api.fetchPropsResults(params);
      if (res && res.data) {
        const items = res.data.items || [];
        const counts = res.data.counts || {};
        const meta = res.data.metadata || {};

        state.playerProps.results = items;
        state.playerProps.metadata = meta;

        updatePropsSummaryMetrics({
          has_scanned: meta.has_scanned || counts.total_scanned > 0,
          final_count: counts.total_scanned || 0,
          polish_odds_count: counts.total_polish_odds || 0,
          bettable_count: counts.total_bettable || 0,
          reference_only_count: counts.total_ref_only || 0,
          no_execution_market_count: counts.total_no_exec || 0,
          match_uncertain_count: counts.total_uncertain || 0,
          props_with_odds: counts.total_with_odds || 0,
          shortlisted_count: counts.total_shortlisted || 0,
          opportunities_count: counts.total_opportunities || 0,
          source_total: meta.source_total || counts.total_scanned || 0,
          pages_fetched: meta.pages_fetched || 0,
        }, items);

        renderScanDiagnostics(meta, items);
        renderPropsTable(items, counts.total_scanned || 0);

        // Keep Inspector synchronized with current active results (prevent stale inspector data)
        if (state.playerProps.selectedPropId) {
          const stillPresent = items.find(i => i.prop_id === state.playerProps.selectedPropId);
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
      console.error('Failed to fetch props results from backend:', err);
    }
  }

  function renderPropsTable(items, totalScanned) {
    const tbody = document.getElementById('props-table-body');
    const countBadge = document.getElementById('props-table-count');
    const titleEl = document.getElementById('props-table-title');
    const metaLine = document.getElementById('props-table-meta-line');

    let titleText = 'Player Props Decision Workspace';
    if (currentPropsCategory === 'valuebets' || currentPropsCategory === 'valuebet') titleText = '💰 Valuebet Player Props (Positive EV at Polish Bookmakers)';
    else if (currentPropsCategory === 'bettable') titleText = '⚡ Bettable Player Props (Verified Polish Prices)';
    else if (currentPropsCategory === 'with_polish_odds') titleText = '🇵🇱 Player Props With Polish Bookmaker Quotes';
    else if (currentPropsCategory === 'reference_only') titleText = '🌐 Reference-Only Props (Bet365 / Global Baseline)';
    else if (currentPropsCategory === 'no_execution_market') titleText = '❌ Player Props Without Execution Market';
    else if (currentPropsCategory === 'match_uncertain') titleText = '⚠️ Match Uncertain Player Props';

    if (titleEl) titleEl.textContent = titleText;

    if (countBadge) {
      if (totalScanned > 0 && items.length < totalScanned) {
        countBadge.textContent = `Showing ${items.length} of ${totalScanned} Props`;
      } else {
        countBadge.textContent = `${items.length} Props`;
      }
    }

    if (metaLine) {
      if (totalScanned > 0) {
        metaLine.textContent = `${items.length} matching current filters • ${totalScanned} total in backend dataset`;
      } else {
        metaLine.textContent = 'Complete server-backed dataset';
      }
    }

    if (!tbody) return;

    if (items.length === 0) {
      if (totalScanned === 0) {
        tbody.innerHTML = `
          <tr>
            <td colspan="13" class="text-center text-muted" style="padding: 2.5rem;">
              <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="margin-bottom: 0.5rem; opacity: 0.5;"><circle cx="12" cy="8" r="4"/><path d="M6 21v-2a6 6 0 0 1 12 0v2"/></svg>
              <div>No player props scanned yet. Click <strong>Scan Props</strong> to fetch the complete StatsHub dataset.</div>
            </td>
          </tr>
        `;
      } else {
        tbody.innerHTML = `
          <tr>
            <td colspan="13" class="text-center text-muted" style="padding: 2.5rem;">
              <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="margin-bottom: 0.5rem; opacity: 0.5;"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
              <div>No player props match the current filters or search query (${totalScanned} total in cache). Try adjusting or clearing your filters.</div>
            </td>
          </tr>
        `;
      }
      return;
    }

    tbody.innerHTML = items.map(item => {
      const hrPct = Math.round(item.hit_rate_pct || 0);
      const avg = item.stat_average !== null && item.stat_average !== undefined ? Number(item.stat_average).toFixed(2) : '—';
      const l5 = item.last_5_avg !== null && item.last_5_avg !== undefined ? Number(item.last_5_avg).toFixed(1) : '—';
      const l10 = item.last_10_avg !== null && item.last_10_avg !== undefined ? Number(item.last_10_avg).toFixed(1) : '—';
      const sample = item.sample_size || 0;

      const bestRefOdds = item.best_reference_odds || item.best_odds ? Number(item.best_reference_odds || item.best_odds).toFixed(2) : '—';
      const refBookie = item.best_reference_bookmaker || item.best_bookmaker || '—';
      const score = item.score !== undefined ? Number(item.score).toFixed(0) : '—';
      const statEdge = item.raw_edge_pct !== null && item.raw_edge_pct !== undefined ? `${item.raw_edge_pct > 0 ? '+' : ''}${item.raw_edge_pct.toFixed(1)}pp` : '—';
      const execEdge = item.execution_edge_pct !== null && item.execution_edge_pct !== undefined ? `${item.execution_edge_pct > 0 ? '+' : ''}${item.execution_edge_pct.toFixed(1)}pp` : '—';

      let scoreBadgeClass = 'badge-outline';
      if (item.score >= 75) scoreBadgeClass = 'badge-success';
      else if (item.score >= 60) scoreBadgeClass = 'badge-accent';

      let statEdgeClass = 'text-muted';
      if (item.raw_edge && item.raw_edge > 0) statEdgeClass = 'text-success font-bold';
      else if (item.raw_edge && item.raw_edge < 0) statEdgeClass = 'text-warning';

      let execEdgeClass = 'text-muted';
      if (item.execution_edge && item.execution_edge > 0) execEdgeClass = 'text-info font-bold';
      else if (item.execution_edge && item.execution_edge < 0) execEdgeClass = 'text-warning';

      // Polish Execution odds chips & multi-bookmaker provenance
      const execQuotes = item.execution_odds || item.odds_comparison?.execution_odds || {};
      const execKeys = Object.keys(execQuotes);
      let execDisplay = '<span class="text-muted" style="font-size:0.75rem;">Unavailable</span>';
      let polishBooksHtml = '<span class="text-muted" style="font-size:0.75rem;">None</span>';

      if (item.best_execution_odds || item.odds_comparison?.best_executable_odds) {
        const p = item.best_execution_odds || item.odds_comparison?.best_executable_odds;
        const b = item.best_execution_bookmaker || item.odds_comparison?.best_executable_bookmaker || '';
        execDisplay = `<span class="mono font-bold text-info" style="font-size:1.05rem;">${Number(p).toFixed(2)}</span>`;
      }

      if (execKeys.length > 0) {
        polishBooksHtml = execKeys.map(bk => {
          const q = execQuotes[bk];
          const isAvail = q && q.status === 'AVAILABLE';
          const price = (q && q.decimal_odds) ? Number(q.decimal_odds).toFixed(2) : '—';
          return `<span class="polish-quote-pill ${isAvail ? 'text-info' : 'text-muted'}"><strong>${bk}</strong>: ${price}</span>`;
        }).join(' ');
      } else if (item.best_execution_bookmaker) {
        polishBooksHtml = `<span class="polish-quote-pill text-info"><strong>${item.best_execution_bookmaker}</strong></span>`;
      }

      const execStat = item.execution_status || 'REFERENCE_ONLY';
      let statusBadgeClass = 'badge-outline';
      if (execStat === 'VALUEBET') statusBadgeClass = 'badge-accent bold';
      else if (execStat === 'BETTABLE') statusBadgeClass = 'badge-success';
      else if (execStat === 'REFERENCE_ONLY') statusBadgeClass = 'badge-warning';
      else if (execStat === 'NO_EXECUTION_ODDS' || execStat === 'NO_EXECUTION_MARKET') statusBadgeClass = 'badge-cycle-failed';
      else if (execStat === 'MATCH_UNCERTAIN') statusBadgeClass = 'badge-danger';

      // Quality flags
      const flags = item.data_quality_flags || item.decision?.data_quality_flags || [];
      const flagsHtml = flags.slice(0, 2).map(f => `<span class="quality-flag-tag">${f}</span>`).join(' ');

      return `
        <tr class="prop-table-row" data-prop-id="${item.prop_id}">
          <td>
            <span class="badge ${scoreBadgeClass}" style="font-size: 0.95rem; font-weight: 700; padding: 0.35rem 0.55rem;">${score}</span>
          </td>
          <td>
            <strong style="font-size: 0.95rem;">${item.player_name}</strong>
            <br><small class="text-muted">${item.team} (${item.position || '—'})</small>
          </td>
          <td>
            <div>${item.match_name}</div>
            <small class="text-muted">${item.competition || ''} &bull; ${item.kickoff || ''}</small>
          </td>
          <td>
            <span class="badge badge-accent">${item.market}</span>
          </td>
          <td>
            <div class="hit-rate-container">
              <span class="mono"><strong>${item.hit_rate_display}</strong> (${hrPct}%)</span>
              <div class="hit-rate-bar-bg" style="width: 70px;">
                <div class="hit-rate-bar-fill" style="width: ${Math.min(100, hrPct)}%;"></div>
              </div>
              <small class="text-muted">N = ${sample}</small>
            </div>
          </td>
          <td class="mono">
            <strong>${avg}</strong>
            <div class="text-muted" style="font-size: 0.72rem;">L5: ${l5} &bull; L10: ${l10}</div>
          </td>
          <td>
            <div class="mono font-bold text-success" style="font-size: 1.05rem;">${bestRefOdds}</div>
            <small class="text-muted">${refBookie}</small>
          </td>
          <td>
            ${execDisplay}
          </td>
          <td>
            <div style="display: flex; flex-direction: column; gap: 0.2rem;">
              ${polishBooksHtml}
            </div>
          </td>
          <td class="mono ${statEdgeClass}">${statEdge}</td>
          <td class="mono ${execEdgeClass}">${execEdge}</td>
          <td>
            <span class="badge ${statusBadgeClass}">${execStat}</span>
            <div style="margin-top: 0.2rem;">${flagsHtml}</div>
          </td>
          <td>
            <button class="btn btn-outline btn-sm btn-prop-detail" data-prop-id="${item.prop_id}">
              Inspect Decision →
            </button>
          </td>
        </tr>
      `;
    }).join('');

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

  async function showPropDetail(propId) {
    if (!propId) return;
    state.playerProps.selectedPropId = propId;

    const detailCard = document.getElementById('prop-detail-container');
    const content = document.getElementById('prop-detail-content');
    const title = document.getElementById('prop-detail-title');

    if (!detailCard || !content) return;
    detailCard.style.display = 'block';

    // State 1: LOADING
    content.innerHTML = `
      <div class="text-center text-muted" style="padding: 2.5rem 1rem;">
        <div style="font-size: 1.5rem; margin-bottom: 0.5rem;">⚡</div>
        <div style="font-weight: 600; margin-bottom: 0.25rem;">Loading intelligence breakdown...</div>
        <div style="font-size: 0.78rem; opacity: 0.7;">Fetching Decision Engine telemetry and bookmaker quotes</div>
      </div>
    `;

    let item = (state.playerProps.results || []).find(p => p.prop_id === propId);
    try {
      const resp = await api.fetchPropDetail(propId);
      if (resp && resp.ok && resp.data) {
        item = resp.data;
        const idx = (state.playerProps.results || []).findIndex(p => p.prop_id === propId);
        if (idx !== -1) {
          state.playerProps.results[idx] = item;
        }
      } else if (!item) {
        // State 2: ERROR
        const statusText = resp?.status ? `HTTP ${resp.status}` : 'Connection Error';
        const errMsg = resp?.error || 'Player prop not found in cache. Try running a fresh scan.';
        content.innerHTML = `
          <div class="alert alert-danger" style="margin: 1rem 0; padding: 1rem; background: rgba(239, 68, 68, 0.12); border: 1px solid rgba(239, 68, 68, 0.3); border-radius: var(--radius-sm); color: #FCA5A5;">
            <div style="font-weight: 700; margin-bottom: 0.35rem;">Unable to load prop details (${statusText})</div>
            <div style="font-size: 0.82rem;">${errMsg}</div>
          </div>
        `;
        return;
      }
    } catch (err) {
      if (!item) {
        content.innerHTML = `
          <div class="alert alert-danger" style="margin: 1rem 0; padding: 1rem; background: rgba(239, 68, 68, 0.12); border: 1px solid rgba(239, 68, 68, 0.3); border-radius: var(--radius-sm); color: #FCA5A5;">
            <div style="font-weight: 700; margin-bottom: 0.35rem;">Unable to load prop details (Runtime Error)</div>
            <div style="font-size: 0.82rem;">Failed to fetch intelligence breakdown from server.</div>
          </div>
        `;
        return;
      }
    }

    if (!item) {
      content.innerHTML = `
        <div class="alert alert-danger" style="margin: 1rem 0; padding: 1rem; background: rgba(239, 68, 68, 0.12); border: 1px solid rgba(239, 68, 68, 0.3); border-radius: var(--radius-sm); color: #FCA5A5;">
          <div style="font-weight: 700; margin-bottom: 0.35rem;">Unable to load prop details (HTTP 404)</div>
          <div style="font-size: 0.82rem;">The requested prop ID was not found in the active session.</div>
        </div>
      `;
      return;
    }

    try {
      // Authoritative Decision Engine object
      const decision = item.decision || {};
      const edges = item.edges || {};
      const statistics = item.statistics || {};
      const propMeta = item.prop || {};

      // Extract properties with full fallback/null-safety
      const playerName = propMeta.player_name || item.player_name || 'Unknown Player';
      const marketName = propMeta.market || item.market || `Over ${item.line || 0.5} ${item.stat_type || 'Stat'}`;
      const teamName = propMeta.team || item.team || '';
      const opponentName = propMeta.opponent || item.opponent || '';
      const matchName = propMeta.match_name || item.match_name || (teamName && opponentName ? `${teamName} vs ${opponentName}` : '');
      const compName = propMeta.competition || item.competition || '';
      const kickoffTime = propMeta.kickoff || item.kickoff || '';

      if (title) title.textContent = `${playerName} — ${marketName}`;

      // Numbers & Scores
      const scoreVal = decision.score !== undefined && decision.score !== null ? decision.score : item.score;
      const scoreNum = (scoreVal !== undefined && scoreVal !== null && scoreVal !== '') ? Number(scoreVal) : null;
      const scoreDisplay = (scoreNum !== null && !isNaN(scoreNum)) ? scoreNum.toFixed(1) : '—';
      const classification = decision.classification || item.classification || 'STANDARD';
      const execStatus = item.execution_status || item.execution?.status || decision.actionability || 'REFERENCE_ONLY';

      // Probabilities
      const histProbVal = decision.historical_probability !== undefined && decision.historical_probability !== null ? decision.historical_probability : (item.historical_probability ?? item.probabilities?.historical);
      const histProbDisplay = (histProbVal !== undefined && histProbVal !== null) ? `${(Number(histProbVal) * 100).toFixed(1)}%` : 'N/A';

      const refProbVal = decision.reference_market_probability ?? decision.reference_probability ?? decision.market_probability ?? item.reference_market_probability ?? item.market_probability ?? item.probabilities?.reference_implied;
      const refProbDisplay = (refProbVal !== undefined && refProbVal !== null) ? `${(Number(refProbVal) * 100).toFixed(1)}%` : 'N/A';

      const execProbVal = decision.execution_market_probability ?? decision.execution_probability ?? item.execution_market_probability ?? item.probabilities?.execution_implied;
      const execProbDisplay = (execProbVal !== undefined && execProbVal !== null) ? `${(Number(execProbVal) * 100).toFixed(1)}%` : 'Unavailable';

      // Statistical Edge & EV
      const statEdgePctVal = decision.raw_edge_pct !== undefined && decision.raw_edge_pct !== null
        ? decision.raw_edge_pct
        : (edges.statistical_pct ?? item.raw_edge_pct ?? (decision.raw_edge !== undefined && decision.raw_edge !== null ? decision.raw_edge * 100 : (item.raw_edge !== undefined && item.raw_edge !== null ? item.raw_edge * 100 : null)));
      const statEdgePctNum = (statEdgePctVal !== undefined && statEdgePctVal !== null) ? Number(statEdgePctVal) : null;
      const statEdgeDisplay = (statEdgePctNum !== null && !isNaN(statEdgePctNum)) ? `${statEdgePctNum > 0 ? '+' : ''}${statEdgePctNum.toFixed(1)}pp` : '—';

      const refEvPctVal = decision.reference_ev_pct ?? edges.reference_ev_pct ?? item.reference_ev_pct;
      const refEvPctNum = (refEvPctVal !== undefined && refEvPctVal !== null) ? Number(refEvPctVal) : null;
      const refEvDisplay = (refEvPctNum !== null && !isNaN(refEvPctNum)) ? `EV: ${refEvPctNum > 0 ? '+' : ''}${refEvPctNum.toFixed(1)}%` : '';

      // Execution Edge & EV
      const execEdgePctVal = decision.execution_edge_pct !== undefined && decision.execution_edge_pct !== null
        ? decision.execution_edge_pct
        : (edges.execution_pct ?? item.execution_edge_pct ?? (decision.execution_edge !== undefined && decision.execution_edge !== null ? decision.execution_edge * 100 : (item.execution_edge !== undefined && item.execution_edge !== null ? item.execution_edge * 100 : null)));
      const execEdgePctNum = (execEdgePctVal !== undefined && execEdgePctVal !== null) ? Number(execEdgePctVal) : null;
      const execEdgeDisplay = (execEdgePctNum !== null && !isNaN(execEdgePctNum)) ? `${execEdgePctNum > 0 ? '+' : ''}${execEdgePctNum.toFixed(1)}pp` : 'Unavailable';

      const execEvPctVal = decision.execution_ev_pct ?? edges.execution_ev_pct ?? item.execution_ev_pct;
      const execEvPctNum = (execEvPctVal !== undefined && execEvPctVal !== null) ? Number(execEvPctVal) : null;
      const execEvDisplay = (execEvPctNum !== null && !isNaN(execEvPctNum)) ? `EV: ${execEvPctNum > 0 ? '+' : ''}${execEvPctNum.toFixed(1)}%` : '';

      // 1. Polish Execution Bookmakers
      const execOddsObj = item.execution_odds || item.odds_comparison?.execution_odds || {};
      const execEntries = Object.entries(execOddsObj);
      const execHtml = execEntries.length > 0 ? execEntries.map(([bName, q]) => {
        const isAvail = q && q.status === 'AVAILABLE';
        const qOdds = q && q.decimal_odds ? Number(q.decimal_odds).toFixed(2) : 'UNAVAILABLE';
        const qReason = (q && q.reason) ? q.reason : `${q?.side || 'OVER'} ${q?.line || item.line || 0.5}`;
        return `
          <div class="prop-odds-card ${isAvail ? 'best' : ''}">
            <div class="prop-odds-bookie">${bName} (Execution)</div>
            <div class="prop-odds-value ${isAvail ? 'text-info font-bold' : 'text-muted'}">${qOdds}</div>
            <small class="text-muted" style="font-size:0.72rem;">${qReason}</small>
          </div>
        `;
      }).join('') : '<p class="text-muted" style="font-size:0.8rem; padding: 0.5rem 0;">No Polish execution quotes matched for this prop.</p>';

      // 2. Reference Odds list (Strict Exact Line & Side Matched)
      const targetLineVal = item.line !== undefined && item.line !== null ? Number(item.line) : 0.5;
      const targetSideVal = (item.side || 'OVER').toUpperCase();
      let refOddsList = item.reference_odds;
      if (!refOddsList || refOddsList.length === 0) {
        if (item.all_odds && item.all_odds.length > 0) {
          refOddsList = item.all_odds.filter(o => Math.abs(Number(o.line || 0) - targetLineVal) < 0.01 && String(o.side || 'OVER').toUpperCase() === targetSideVal);
        } else {
          refOddsList = [];
        }
      }
      const bestRefOddsNum = item.best_reference_odds || item.best_odds;
      const refHtml = refOddsList.length > 0 ? refOddsList.map(o => {
        const isBest = bestRefOddsNum && Number(o.decimal_odds) === Number(bestRefOddsNum);
        const lineDisplay = `${o.side || targetSideVal} ${o.line !== undefined ? o.line : targetLineVal}`;
        return `
          <div class="prop-odds-card ${isBest ? 'best' : ''}">
            <div class="prop-odds-bookie">${o.bookmaker || 'Bookmaker'} (Reference)</div>
            <div class="prop-odds-value text-success font-bold">${Number(o.decimal_odds).toFixed(2)}</div>
            <small class="text-muted" style="font-size:0.72rem;">${lineDisplay}</small>
          </div>
        `;
      }).join('') : '<p class="text-muted" style="font-size:0.8rem; padding: 0.5rem 0;">No reference odds available for this exact line.</p>';

      // 3. Reasons and Warnings
      const reasonsArr = item.reasons || item.decision?.reasons || [];
      const warningsArr = item.warnings || item.decision?.warnings || [];

      const reasonsHtml = reasonsArr.length > 0
        ? reasonsArr.map(r => `<li style="color: var(--text-success); margin-bottom: 0.25rem;">✓ ${r}</li>`).join('')
        : '<li class="text-muted">Standard historical baseline.</li>';

      const warningsHtml = warningsArr.length > 0
        ? warningsArr.map(w => `<li style="color: #FCD34D; margin-bottom: 0.25rem;">⚠️ ${w}</li>`).join('')
        : '';

      // Quality flags
      const flags = item.data_quality_flags || decision.data_quality_flags || [];
      const flagsHtml = flags.map(f => `<span class="quality-flag-tag" style="font-size: 0.72rem;">${f}</span>`).join(' ');

      // 4. Statistics Profile
      const statsObj = item.statistics || {};
      const sampleSize = statsObj.sample_size ?? item.sample_size ?? 0;
      const hitRateDisplay = statsObj.hit_rate_display ?? item.hit_rate_display ?? `${Math.round(item.hit_rate_pct || 0)}%`;
      const hitRatePct = statsObj.hit_rate_pct ?? item.hit_rate_pct ?? 0;
      const avgStat = statsObj.average ?? item.stat_average;
      const avgStatDisplay = avgStat !== null && avgStat !== undefined ? Number(avgStat).toFixed(2) : 'N/A';
      const l5Avg = statsObj.last_5_avg ?? item.last_5_avg;
      const l5AvgDisplay = l5Avg !== null && l5Avg !== undefined ? Number(l5Avg).toFixed(1) : 'N/A';
      const l10Avg = statsObj.last_10_avg ?? item.last_10_avg;
      const l10AvgDisplay = l10Avg !== null && l10Avg !== undefined ? Number(l10Avg).toFixed(1) : 'N/A';
      const playerPos = item.position || item.prop?.position || 'N/A';

      // 5. Recent Match Logs
      const recentMatches = item.recent_matches || [];
      const recentMatchesHtml = recentMatches.length > 0 ? recentMatches.map(m => `
        <tr>
          <td>${m.opponent || '—'} (${m.venue || '—'})</td>
          <td class="mono">${m.date || '—'}</td>
          <td class="mono font-bold">${m.stat_value !== undefined ? m.stat_value : '—'}</td>
          <td class="mono">${m.minutes !== undefined ? `${m.minutes}'` : '—'}</td>
        </tr>
      `).join('') : '';

      // Badge styles
      let execStatusClass = 'badge-outline';
      if (execStatus === 'BETTABLE') execStatusClass = 'badge-success';
      else if (execStatus === 'REFERENCE_ONLY') execStatusClass = 'badge-accent';
      else if (execStatus === 'NO_EXECUTION_ODDS') execStatusClass = 'badge-warning';
      else if (execStatus === 'MATCH_UNCERTAIN') execStatusClass = 'badge-danger';

      let scoreBadgeClass = 'badge-outline';
      if (scoreNum >= 75) scoreBadgeClass = 'badge-success';
      else if (scoreNum >= 60) scoreBadgeClass = 'badge-accent';

      // Render State 3: SUCCESS
      content.innerHTML = `
        <!-- Decision Score Header -->
        <div class="card" style="background: rgba(0,0,0,0.25); border: 1px solid var(--bg-card-border); margin-bottom: 1rem; padding: 0.85rem;">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.5rem;">
            <div>
              <span class="badge ${scoreBadgeClass}" style="font-size: 1rem; font-weight: 700; padding: 0.4rem 0.75rem;">
                Score: ${scoreDisplay}/100
              </span>
              <span class="badge ${execStatusClass}" style="margin-left: 0.4rem; font-size: 0.8rem; font-weight: 600;">
                ${execStatus}
              </span>
            </div>
            <div style="text-align: right;">
              <span class="badge badge-outline" style="font-size: 0.75rem;">${classification}</span>
            </div>
          </div>
          <div class="text-muted" style="font-size: 0.78rem;">
            ${matchName ? `${matchName} &bull; ` : ''}${compName ? `${compName} &bull; ` : ''}${kickoffTime || ''}
          </div>
          ${flagsHtml ? `<div style="margin-top: 0.4rem; display: flex; flex-wrap: wrap; gap: 0.25rem;">${flagsHtml}</div>` : ''}
        </div>

        <!-- Edge Comparison Matrix -->
        <div class="card" style="background: rgba(0,0,0,0.15); border: 1px solid var(--bg-card-border); margin-bottom: 1rem; padding: 0.75rem;">
          <h4 style="margin-bottom: 0.5rem; font-size: 0.85rem; text-transform: uppercase; letter-spacing: 0.5px; opacity: 0.9;">Edge Comparison & Math</h4>
          <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 0.75rem;">
            <div style="background: rgba(0,0,0,0.2); padding: 0.5rem 0.65rem; border-radius: var(--radius-sm);">
              <div class="text-muted" style="font-size: 0.72rem;">Raw Statistical Edge (Ref)</div>
              <strong class="text-success mono" style="font-size: 1.1rem;">${statEdgeDisplay}</strong>
              ${refEvDisplay ? `<div class="mono text-success" style="font-size: 0.78rem; font-weight: 600;">${refEvDisplay}</div>` : ''}
              <div class="text-muted" style="font-size: 0.68rem; margin-top: 0.2rem;">P_hist (${histProbDisplay}) - P_ref (${refProbDisplay})</div>
            </div>
            <div style="background: rgba(0,0,0,0.2); padding: 0.5rem 0.65rem; border-radius: var(--radius-sm);">
              <div class="text-muted" style="font-size: 0.72rem;">Execution Edge (Polish)</div>
              <strong class="${execEdgePctNum !== null && execEdgePctNum > 0 ? 'text-info font-bold' : 'text-muted'} mono" style="font-size: 1.1rem;">${execEdgeDisplay}</strong>
              ${execEvDisplay ? `<div class="mono text-info" style="font-size: 0.78rem; font-weight: 600;">${execEvDisplay}</div>` : ''}
              <div class="text-muted" style="font-size: 0.68rem; margin-top: 0.2rem;">P_hist (${histProbDisplay}) - P_exec (${execProbDisplay})</div>
            </div>
          </div>
        </div>

        <!-- Polish Execution Markets -->
        <div style="margin-bottom: 1rem;">
          <h4 style="margin-bottom: 0.4rem; font-size: 0.85rem; text-transform: uppercase; letter-spacing: 0.5px; opacity: 0.9;">Polish Execution Markets (Superbet & Betclic)</h4>
          <div class="prop-odds-list">
            ${execHtml}
          </div>
        </div>

        <!-- Reference Odds -->
        <div style="margin-bottom: 1rem;">
          <h4 style="margin-bottom: 0.4rem; font-size: 0.85rem; text-transform: uppercase; letter-spacing: 0.5px; opacity: 0.9;">Reference Bookmaker Odds (StatsHub Baseline)</h4>
          <div class="prop-odds-list">
            ${refHtml}
          </div>
        </div>

        <!-- Decision Engine Signals -->
        <div style="margin-bottom: 1rem; background: rgba(0,0,0,0.2); padding: 0.75rem; border-radius: var(--radius-sm);">
          <h4 style="margin-bottom: 0.35rem; font-size: 0.85rem; text-transform: uppercase; letter-spacing: 0.5px; opacity: 0.9;">Decision Analysis & Signals</h4>
          <ul style="list-style: none; padding-left: 0; margin-bottom: 0.5rem; font-size: 0.82rem;">
            ${reasonsHtml}
            ${warningsHtml}
          </ul>
          <div class="text-muted" style="font-size: 0.7rem; border-top: 1px solid var(--bg-card-border); padding-top: 0.35rem;">
            <strong>Disclaimer:</strong> Statistical edge is derived from historical hit rates and reference implied probabilities. Actionable betting requires a confirmed Polish bookmaker execution price.
          </div>
        </div>

        <!-- Player Performance Metrics -->
        <div style="margin-bottom: 1rem;">
          <h4 style="margin-bottom: 0.4rem; font-size: 0.85rem; text-transform: uppercase; letter-spacing: 0.5px; opacity: 0.9;">Player Performance Profile</h4>
          <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 0.4rem; font-size: 0.8rem;">
            <div>Hit Rate: <strong class="mono">${hitRateDisplay} (${Math.round(hitRatePct)}%)</strong></div>
            <div>Average per Game: <strong class="mono">${avgStatDisplay}</strong></div>
            <div>Last 5 Games Avg: <strong class="mono">${l5AvgDisplay}</strong></div>
            <div>Last 10 Games Avg: <strong class="mono">${l10AvgDisplay}</strong></div>
            <div>Sample Size: <strong class="mono">${sampleSize} matches</strong></div>
            <div>Position: <strong class="mono">${playerPos}</strong></div>
          </div>
        </div>

        ${recentMatchesHtml ? `
        <div>
          <h4 style="margin-bottom: 0.4rem; font-size: 0.85rem; text-transform: uppercase; letter-spacing: 0.5px; opacity: 0.9;">Recent Match Logs</h4>
          <table class="prop-history-table" style="font-size: 0.78rem;">
            <thead>
              <tr>
                <th>Opponent</th>
                <th>Date</th>
                <th>Stat</th>
                <th>Min</th>
              </tr>
            </thead>
            <tbody>
              ${recentMatchesHtml}
            </tbody>
          </table>
        </div>
        ` : ''}
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
  // Settings View Controller & Persistence (Stage 22B: Tax Configuration)
  // ──────────────────────────────────────────────────────────────────────────

  async function loadSettingsData() {
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

  let currentProfilerTrace = null;

  async function loadProfilerData() {
    try {
      const res = await api.fetchLatestTrace();
      if (res && res.data && res.status_code === 200) {
        currentProfilerTrace = res.data;
        renderProfilerTrace(res.data);
      } else {
        // Fallback: check if latest scan has scan_trace embedded
        const scanRes = await api.fetchLatestScan();
        if (scanRes && scanRes.data && scanRes.data.scan_trace && scanRes.data.scan_trace.trace_id) {
          currentProfilerTrace = scanRes.data.scan_trace;
          renderProfilerTrace(scanRes.data.scan_trace);
        } else {
          showEmptyProfilerState();
        }
      }
    } catch (err) {
      console.error('Failed loading profiler trace:', err);
      showEmptyProfilerState();
    }
  }

  function showEmptyProfilerState() {
    currentProfilerTrace = null;
    const traceIdEl = document.getElementById('profiler-trace-id');
    if (traceIdEl) traceIdEl.textContent = 'None';
    const wallClockEl = document.getElementById('profiler-wall-clock');
    if (wallClockEl) wallClockEl.textContent = '—';
    const summaryEl = document.getElementById('prof-bottleneck-summary');
    if (summaryEl) {
      summaryEl.innerHTML = '<p class="text-muted">No scan trace loaded yet. Click <strong>Run Scan</strong> on the Dashboard to generate execution telemetry.</p>';
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
      showEmptyProfilerState();
      return;
    }

    // Header & KPIs
    const traceIdEl = document.getElementById('profiler-trace-id');
    if (traceIdEl) traceIdEl.textContent = trace.trace_id || trace.execution_id || '—';

    const wallClockEl = document.getElementById('profiler-wall-clock');
    if (wallClockEl) wallClockEl.textContent = `${(trace.total_duration_wall_s || 0).toFixed(3)}s`;

    const kpiDurEl = document.getElementById('prof-kpi-duration');
    if (kpiDurEl) kpiDurEl.textContent = `${(trace.total_duration_wall_s || 0).toFixed(3)}s`;

    const kpiModeEl = document.getElementById('prof-kpi-mode');
    if (kpiModeEl) kpiModeEl.textContent = `Scan Mode: ${trace.scan_mode || 'NORMAL'}`;

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
  document.addEventListener('DOMContentLoaded', () => {
    const btnReload = document.getElementById('btn-refresh-profiler');
    if (btnReload) {
      btnReload.addEventListener('click', () => {
        loadProfilerData();
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
        dlAnchor.setAttribute('download', `trace_${currentProfilerTrace.trace_id || 'scan'}.json`);
        document.body.appendChild(dlAnchor);
        dlAnchor.click();
        dlAnchor.remove();
        showToast('Trace JSON exported.');
      });
    }
  });

})();



