(function () {
  "use strict";

  if (window.QuizMasterTikTokMonitor) return;

  const listeners = new Set();
  let timer = null;
  let running = false;
  let inFlight = false;

  const state = {
    connected: false,
    username: "",
    error: "",
    message: "TikTok LIVE disconnected",
    lastUpdated: 0,
  };

  function cloneState() {
    return { ...state };
  }

  function emit() {
    const detail = cloneState();
    listeners.forEach((callback) => {
      try { callback(detail); } catch (_) {}
    });
    try { window.dispatchEvent(new CustomEvent("quizmaster:tiktok-status", { detail })); } catch (_) {}
    try { window.postMessage({ type: "tiktok_status_changed", status: detail }, "*"); } catch (_) {}
  }

  async function jsonFetch(endpoint) {
    const response = await fetch(endpoint, { cache: "no-store" });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.error || body.detail || `HTTP ${response.status}`);
    return body;
  }

  function apply(payload) {
    state.connected = !!(payload && payload.success && payload.connected);
    state.username = String((payload && payload.username) || "").replace(/^@/, "");
    state.error = state.connected ? "" : String((payload && payload.last_error) || "");
    if (state.connected) {
      state.message = state.username ? `TikTok LIVE @${state.username}` : "TikTok LIVE connected";
    } else if (state.error) {
      state.message = state.error;
    } else {
      state.message = "TikTok LIVE disconnected";
    }
    state.lastUpdated = Date.now();
  }

  function schedule(delay) {
    if (!running) return;
    clearTimeout(timer);
    timer = setTimeout(refresh, delay ?? (state.connected ? 8000 : 15000));
  }

  async function refresh() {
    if (inFlight) return;
    inFlight = true;
    try {
      apply(await jsonFetch("/api/tiktok/status"));
    } catch (error) {
      state.connected = false;
      state.error = error.message;
      state.message = "TikTok status unavailable";
      state.lastUpdated = Date.now();
    } finally {
      inFlight = false;
      emit();
      schedule();
    }
  }

  window.QuizMasterTikTokMonitor = {
    start() {
      if (running) return;
      running = true;
      refresh();
    },
    stop() {
      running = false;
      clearTimeout(timer);
    },
    refreshNow() {
      return refresh();
    },
    getState() {
      return cloneState();
    },
    subscribe(callback) {
      if (typeof callback !== "function") return () => {};
      listeners.add(callback);
      try { callback(cloneState()); } catch (_) {}
      return () => listeners.delete(callback);
    },
  };
})();
