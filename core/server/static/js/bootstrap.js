(function () {
  'use strict';

  const GLOBAL_MARKER = "__QUIZ_BOOTSTRAP_LOADED__";
  if (window[GLOBAL_MARKER]) return;
  window[GLOBAL_MARKER] = true;

  const DEBUG = window.QM_DEBUG_BOOTSTRAP === true || new URLSearchParams(window.location.search).has("debug");
  const BootstrapState = {
    phase: "initializing",
    ready: false,
    readyPromise: null,
    readyResolve: null,
    readyReject: null,
    services: {},
    errors: [],
    startTime: Date.now(),
    runCount: 0,
  };

  BootstrapState.readyPromise = new Promise((resolve, reject) => {
    BootstrapState.readyResolve = resolve;
    BootstrapState.readyReject = reject;
  });

  const log = {
    info: (...a) => { if (DEBUG) console.log("[Bootstrap]", ...a); },
    success: (...a) => { if (DEBUG) console.log("[Bootstrap]", ...a); },
    warn: (...a) => { if (DEBUG) console.warn("[Bootstrap]", ...a); },
    error: (...a) => console.error("[Bootstrap]", ...a),
    debug: (...a) => { if (DEBUG) console.debug("[Bootstrap]", ...a); },
    phase: (name) => { BootstrapState.phase = name; if (DEBUG) console.log("[Bootstrap] Phase:", name); },
  };

  async function waitFor(fn, { timeout = 5000, interval = 50, description = "condition" } = {}) {
    const start = Date.now();
    while (Date.now() - start < timeout) {
      try {
        const value = fn();
        if (value) return value;
      } catch (_) {}
      await new Promise((resolve) => setTimeout(resolve, interval));
    }
    throw new Error(`Timeout waiting for ${description}`);
  }

  function loadScript(src, options = {}) {
    return new Promise((resolve, reject) => {
      if (document.querySelector(`script[src="${src}"]`)) return resolve();
      const script = document.createElement("script");
      script.src = src;
      script.async = options.async !== false;
      script.defer = options.defer || false;
      script.onload = () => resolve();
      script.onerror = () => reject(new Error(`Failed to load ${src}`));
      document.head.appendChild(script);
    });
  }

  const isSocketLoaded = () => typeof window.io === "function";

  async function phase1() {
    log.phase("Socket.IO");
    if (!isSocketLoaded()) {
      await loadScript("https://cdn.socket.io/4.7.5/socket.io.min.js");
      await waitFor(isSocketLoaded, { description: "Socket.IO" });
    }
    BootstrapState.services.socketIO = window.io;
  }

  async function phase2() {
    log.phase("ServiceLocator");
    if (!window.ServiceLocator) {
      await loadScript("/service_locator.js");
      await waitFor(() => window.ServiceLocator, { description: "ServiceLocator" });
    }
    BootstrapState.services.serviceLocator = window.ServiceLocator.get_instance();
  }

  async function phase3() {
    const path = window.location.pathname;
    if (path.includes("/chess/")) return;
    log.phase("QuizSignals");
    const locator = BootstrapState.services.serviceLocator;
    if (locator.get_service("QuizSignals")) {
      BootstrapState.services.quizSignals = locator.get_service("QuizSignals");
      return;
    }
    await loadScript("/core/quiz/js/quiz_signals.js");
    await waitFor(() => window.QuizSignals, { description: "QuizSignals" });
    const signals = window.QuizSignals.getInstance?.() || new window.QuizSignals();
    locator.register_service("QuizSignals", signals);
    BootstrapState.services.quizSignals = signals;
    window.quizSignals = signals;
  }

  async function phase4() {
    log.phase("BridgeClient");
    if (window.httpBridgeClient?.isFullyReady?.()) {
      BootstrapState.services.bridgeClient = window.httpBridgeClient;
      return;
    }
    await loadScript("/bridge_client.js");
    await waitFor(() => window.HTTPBridgeClient, { description: "HTTPBridgeClient" });
    const client = new window.HTTPBridgeClient(window.BRIDGE_ORIGIN || window.location.origin, { roomId: "default" });
    client.connectWebSocket();
    await waitFor(
      () => client.isWebSocketConnected && client.isWebSocketConnected(),
      { timeout: 1000, description: "WebSocket connection" }
    ).catch(() => {});
    window.httpBridgeClient = client;
    BootstrapState.services.bridgeClient = client;
  }

  async function phase5() {
    log.phase("ThemeManager");
    try {
      const overlayThemeSources = new Set(["quiz_display", "leaderboard", "timer_display"]);
      if (overlayThemeSources.has(window.CLIENT_TYPE)) return;
      if (window.themeManager) {
        BootstrapState.services.themeManager = window.themeManager;
        return;
      }
      await loadScript("/theme_manager.js");
      await waitFor(() => window.ThemeManager, { description: "ThemeManager" });
      window.themeManager = new window.ThemeManager();
      window.themeManager.init();
      BootstrapState.services.themeManager = window.themeManager;
    } catch (_) {}
  }

  async function phase6() {
    log.phase("PageLoad");
    const path = window.location.pathname;

    if (path.includes("/chess/") || path.includes("actions_events")) return;

    if (path.includes("quiz_display")) {
      if (window.QM_RESILIENT_QUIZ_DISPLAY) return;
      await loadScript("/core/quiz/js/quiz_display.js");
      await waitFor(() => window.QuizDisplay, { description: "QuizDisplay" });
      window.QuizDisplay.getInstance({ parent: document.getElementById("quiz-root") || document.body });
      return;
    }

    if (path.includes("quiz_controls")) {
      await loadScript("/core/quiz/js/quiz_controls.js");
      await waitFor(() => window.QuizControls, { description: "QuizControls" });
      await window.QuizMasterURLs?.exchangeControlToken?.();
      window.QuizControls.create_singleton();
      return;
    }

    if (path.includes("leaderboard")) {
      await loadScript("/core/quiz/js/leaderboard.js");
      return;
    }

    if (path.includes("quiz_tab") || path.includes("settings_tab")) {
      await loadScript("/core/quiz/js/quiz_tab.js").catch((error) => log.warn("Quiz tab script failed", error));
      await waitFor(() => window.QuizTab, { timeout: 1500, description: "QuizTab class" }).catch(() => {});
      if (window.QuizTab && !window.quizTabInstance) window.quizTabInstance = new window.QuizTab();
      return;
    }

    if (path.includes("tiktok_tab")) {
      await loadScript("/tiktok_tab.js");
      return;
    }

    if (path.includes("main_tab")) {
      await loadScript("/main_tab.js").catch((error) => log.warn("Main tab script failed", error));
      return;
    }

    if (path.includes("overlays")) {
      window.CLIENT_TYPE = "openmic_overlay";
      await loadScript("/overlays/openmicquiz/overlay.js").catch(() => {});
    }
  }

  async function run() {
    if (window.widgetBridge || BootstrapState.runCount++) return;
    try {
      await phase1();
      await phase2();
      await phase3();
      await phase4();
      await phase5();
      await phase6();
      BootstrapState.ready = true;
      BootstrapState.phase = "complete";
      BootstrapState.readyResolve({ services: BootstrapState.services, elapsed: Date.now() - BootstrapState.startTime });
      log.success(`complete (${Date.now() - BootstrapState.startTime}ms)`);
    } catch (error) {
      BootstrapState.errors.push(error);
      BootstrapState.readyReject(error);
      log.error("Bootstrap failed", error);
    }
  }

  window.QuizBootstrap = {
    ready: () => BootstrapState.readyPromise,
    isReady: () => BootstrapState.ready,
    getPhase: () => BootstrapState.phase,
    getService: (name) => BootstrapState.services[name],
    getServices: () => ({ ...BootstrapState.services }),
    getErrors: () => [...BootstrapState.errors],
    getState: () => ({
      phase: BootstrapState.phase,
      ready: BootstrapState.ready,
      services: Object.keys(BootstrapState.services),
      errors: BootstrapState.errors.map((error) => error.message),
      elapsed: Date.now() - BootstrapState.startTime,
      runCount: BootstrapState.runCount,
    }),
  };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", run, { once: true });
  else run();
})();
