(function () {
  "use strict";

  if (window.QuizMasterTikTokMonitor) return;

  const listeners = new Set();
  let timer = null;
  let running = false;
  let inFlight = false;
  let connectInFlight = false;
  let lastConnectAttemptAt = 0;
  let lastConnectAttemptUsername = "";

  const state = {
    linked: false,
    liveConnected: false,
    username: "",
    displayName: "",
    avatar: "",
    followers: null,
    message: "TikTok LIVE disconnected",
    lastUpdated: 0,
    waitingForLive: false,
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
    try { window.postMessage({ type: "official_tiktok_status_changed", status: detail }, "*"); } catch (_) {}
  }

  async function jsonFetch(endpoint, options) {
    const response = await fetch(endpoint, { cache: "no-store", ...(options || {}) });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.error || body.reason || body.detail || `HTTP ${response.status}`);
    return body;
  }

  function jsonPost(endpoint, payload = {}) {
    return jsonFetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload || {}),
    });
  }

  function getStats(payload) {
    return payload?.account_snapshot || payload?.account_stats || payload?.status?.account_stats || null;
  }

  function applyOfficial(payload) {
    const stats = getStats(payload);
    const available = !!(stats && (stats.available || stats.username));
    // "linked" means an actual TikTok account is logged in -- a real account
    // snapshot (available/username) or a live broker token (connected). Do NOT
    // treat `configured` as linked: it is true whenever the broker URL is set
    // (always, in production), which would hide the "Log in" button forever.
    state.linked = !!(available || payload?.connected);
    if (available) {
      state.username = String(stats.username || "").replace(/^@/, "");
      state.displayName = stats.display_name || stats.nickname || state.username;
      state.avatar = stats.avatar_url_100 || stats.avatar_url || stats.avatar_large_url || "";
      state.followers = stats.exact_current_followers ?? stats.followers ?? stats.follower_count ?? null;
    }
  }

  function applyLive(payload) {
    state.liveConnected = !!(payload && payload.success && payload.connected);
    if (state.liveConnected && payload.username) {
      state.username = String(payload.username || "").replace(/^@/, "");
      if (!state.displayName) state.displayName = state.username;
    }
  }

  function updateMessage() {
    if (state.liveConnected) {
      state.waitingForLive = false;
      state.message = state.username ? `TikTok LIVE @${state.username}` : "TikTok LIVE connected";
    } else if (state.linked) {
      state.waitingForLive = true;
      state.message = "Waiting for TikTok LIVE";
    } else {
      state.waitingForLive = false;
      state.message = "TikTok LIVE disconnected";
    }
    state.lastUpdated = Date.now();
  }

  function nextDelay() {
    if (!state.linked) return 30000;
    if (state.liveConnected) return 8000;
    return 12000;
  }

  function schedule(delay) {
    if (!running) return;
    clearTimeout(timer);
    timer = setTimeout(refresh, delay ?? nextDelay());
  }

  async function maybeConnectLive() {
    const username = state.username || "linked";
    if (!state.linked || state.liveConnected || connectInFlight) return;

    const now = Date.now();
    const cooldown = username === lastConnectAttemptUsername ? 45000 : 15000;
    if (now - lastConnectAttemptAt < cooldown) return;

    connectInFlight = true;
    lastConnectAttemptAt = now;
    lastConnectAttemptUsername = username;
    try {
      // The linked route resolves the username from the auth broker; the plain
      // connect route requires one in the body and would just 400 here.
      await jsonPost("/api/tiktok/connect-linked", {});
    } catch (_) {
      // Normal while the creator is not live yet. Keep waiting quietly.
    } finally {
      connectInFlight = false;
    }
  }

  async function refresh() {
    if (inFlight) return;
    inFlight = true;
    try {
      const [official, liveBefore] = await Promise.allSettled([
        jsonFetch("/api/tiktok/official-login/status"),
        jsonFetch("/api/tiktok/status"),
      ]);
      if (official.status === "fulfilled") applyOfficial(official.value);
      if (liveBefore.status === "fulfilled") applyLive(liveBefore.value);

      if (state.linked && !state.liveConnected) {
        await maybeConnectLive();
        const liveAfter = await jsonFetch("/api/tiktok/status").catch(() => null);
        if (liveAfter) applyLive(liveAfter);
      }

      updateMessage();
      emit();
    } finally {
      inFlight = false;
      schedule();
    }
  }

  async function openLogin() {
    await jsonPost("/api/tiktok/official-login/open", {});
    schedule(3000);
    return refresh();
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
    openLogin,
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
