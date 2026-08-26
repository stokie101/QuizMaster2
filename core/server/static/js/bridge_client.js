if (!window.HTTPBridgeClient) {
  const LOCKED_ROOM = "default";

  class HTTPBridgeClient {
    constructor(baseUrl = window.location.origin, options = {}) {
      if (HTTPBridgeClient._instance) {
        return HTTPBridgeClient._instance;
      }
      this.baseUrl = this._resolveBaseUrl(baseUrl);
      this.publicWidgetId = window.QuizMasterURLs?.publicWidgetId?.() || null;
      this.apiPrefix = this._resolveApiPrefix();
      this.apiBaseUrl = this._resolveApiBaseUrl();
      this.widgetDebug = Boolean(window.QuizMasterURLs?.config?.().WIDGET_DEBUG);
      this.timeout = options.timeout || 5000;
      this.retries = options.retries || 2;
      this.retryDelay = options.retryDelay || 500;
      this.logLevel = options.logLevel || "info";

      this._isConnected = false;
      this._connectionAttempts = 0;
      this._maxConnectionAttempts = 5;

      this._io = null;
      this._ioConnected = false;
      this._reconnectCount = 0;

      this._signalForwardingSetup = false;
      this._lastSeq = 0;
      this._serverProtocol = null;
      this._serverLastSeq = 0;
      this._snapshot = null;

      this._clientType = window.CLIENT_TYPE || "app_display";
      this._clientId = null;
      this._sessionId = null;
      this.widgetType = options.widgetType || (window.location.pathname.includes('/chess') ? 'chess' : (/quiz|leaderboard/.test(window.location.pathname) ? 'quiz' : ''));
      this.widgetSessionId = window.QuizMasterURLs?.sessionId?.() || null;
      this._roomId = options.roomId || LOCKED_ROOM;

      this._listeners = new Map();
      this._signalBuffer = [];
      this._maxSignalBufferSize = 500;
      this._quizSignalsReady = false;
      this._heartbeatTimer = null;
      // Fix: De-duplicate rapid duplicate TikTok gift aliases/payloads before local forwarding.
      this._recentSignalFingerprints = new Map();
      this._signalDedupeTtlMs = 1500;

      this._isFullyReady = false;
      this._readyPromiseResolve = null;
      this._readyPromise = new Promise((resolve) => {
        this._readyPromiseResolve = resolve;
      });

      this._clientInfo = null;

      this._checkConnection();
      HTTPBridgeClient._instance = this;
    }

    _resolveBaseUrl(baseUrl) {
      // The page origin already is the bridge: localhost for the desktop app
      // windows, and the public widget host (tunnelled to the same bridge) for
      // browser sources. Using it keeps the app working without the tunnel.
      if (/^https?:$/.test(window.location.protocol)) {
        return String(window.location.origin).replace(/\/$/, "");
      }
      const urls = window.QuizMasterURLs;
      const configuredLocalBase = urls?.activeBaseUrl?.() || urls?.config?.().LOCAL_BASE_URL;
      const candidate = configuredLocalBase || baseUrl || window.location.origin;
      return String(candidate || window.location.origin).replace(/\/$/, "");
    }

    _resolveApiPrefix() {
      const urls = window.QuizMasterURLs;
      return urls?.apiPrefix?.() || "";
    }

    _resolveApiBaseUrl() {
      const urls = window.QuizMasterURLs;
      const configuredApiBase = urls?.apiBaseUrl?.();
      return `${String(configuredApiBase || this.baseUrl).replace(/\/$/, "")}${this.apiPrefix}`;
    }

    _log(level, msg, data = null) {
      const levels = { debug: 0, info: 1, warn: 2, error: 3 };
      if (levels[level] >= levels[this.logLevel]) {
        const prefix = `[HTTPBridge:${this._clientType}]`;
        if (data !== null) {
          console.log(`${prefix} [${level.toUpperCase()}] ${msg}`, data);
        } else {
          console.log(`${prefix} [${level.toUpperCase()}] ${msg}`);
        }
      }
    }

    on(event, fn) {
      if (!this._listeners.has(event)) {
        this._listeners.set(event, new Set());
      }
      this._listeners.get(event).add(fn);
      return () => this.off(event, fn);
    }

    off(event, fn) {
      this._listeners.get(event)?.delete(fn);
    }

    _emitLocal(event, ...args) {
      this._listeners.get(event)?.forEach((fn) => {
        try {
          fn(...args);
        } catch (e) {
          this._log("error", `Listener error for "${event}"`, e);
        }
      });
    }

    async _checkConnection() {
      try {
        const resp = await this._makeRequest("GET", "/health", null, false);
        if (resp?.success || resp?.status === "ok") {
          this._isConnected = true;
          this._connectionAttempts = 0;
          this._log("info", "✓ Connected to HTTP Bridge");
        } else {
          throw new Error("Health check failed");
        }
      } catch (e) {
        this._isConnected = false;
        if (this._connectionAttempts < this._maxConnectionAttempts) {
          this._connectionAttempts++;
          this._log(
            "warn",
            `Connection attempt ${this._connectionAttempts}/${this._maxConnectionAttempts} failed`
          );
          setTimeout(() => this._checkConnection(), 2000);
        } else {
          this._log("error", "Failed to connect to HTTP Bridge after max attempts");
        }
      }
    }

    isConnected() {
      return this._isConnected;
    }

    async waitForReady(maxWait = 10000) {
      if (this._isFullyReady) return true;
      const timeout = new Promise((resolve) => setTimeout(() => resolve(false), maxWait));
      return Promise.race([this._readyPromise, timeout]);
    }

    async waitForConnection(maxWait = 10000) {
      const start = performance.now();
      while (!this._isConnected) {
        if (performance.now() - start > maxWait) {
          throw new Error("Timeout waiting for HTTP Bridge connection");
        }
        await new Promise((r) => setTimeout(r, 100));
      }
      return true;
    }

    async _makeRequest(method, endpoint, data = null, shouldRetry = true, attempt = 0) {
      const rawUrl = `${this.apiBaseUrl}/api${endpoint}`;
      const urlObject = new URL(rawUrl, window.location.origin);
      if (this.widgetSessionId) urlObject.searchParams.set('session', this.widgetSessionId);
      const url = urlObject.toString();
      const options = {
        method,
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
          "X-Client-Type": this._clientType,
          "X-Client-Id": this._clientId || "",
          "X-Session-Id": this._sessionId || "",
          "X-Room-Id": this._roomId || "default",
        },
      };

      const controlToken = window.QuizMasterURLs?.controlToken?.();
      if (controlToken) options.headers["X-QuizMaster-Control-Token"] = controlToken;

      if (data && (method === "POST" || method === "PUT")) {
        options.body = JSON.stringify(data);
      }

      try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), this.timeout);
        const response = await fetch(url, { ...options, signal: controller.signal });
        clearTimeout(timeoutId);
        if (this.widgetDebug) {
          console.info('[QuizMasterWidgetDebug]', 'http_status', {
            host: new URL(url).host,
            route: endpoint,
            status: response.status,
            publicWidgetIdPresent: Boolean(this.publicWidgetId)
          });
        }

        if (!response.ok && response.status !== 503) {
          throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }

        const result = await response.json().catch(() => ({}));
        this._log("debug", `${method} ${endpoint}`, result);
        return result;
      } catch (error) {
        if (shouldRetry && attempt < this.retries) {
          this._log("warn", `Request failed (attempt ${attempt + 1}), retrying...`);
          await new Promise((r) => setTimeout(r, this.retryDelay));
          return this._makeRequest(method, endpoint, data, true, attempt + 1);
        }
        this._log("error", `Request failed: ${method} ${endpoint}`, error?.message || error);
        throw error;
      }
    }

    async _get(endpoint) {
      return this._makeRequest("GET", endpoint);
    }

    async _post(endpoint, data = {}) {
      return this._makeRequest("POST", endpoint, data);
    }

    connectWebSocket() {
      if (typeof window.io !== "function") {
        this._log("error", "Socket.IO client not found");
        return;
      }
      if (this._io && this._ioConnected) {
        this._log("info", "WebSocket already connected");
        return;
      }

      this._log("info", `🔌 Connecting WebSocket as: ${this._clientType}`);

      try {
        this._io = window.io(this.baseUrl, {
          transports: ["websocket", "polling"],
          query: {
            type: this._clientType,
            room: this._roomId || "default",
            public_widget_id: this.publicWidgetId || "",
            session_id: this.widgetSessionId || "",
            widget_type: this.widgetType
          },
          reconnection: true,
          reconnectionDelay: 1000,
          reconnectionDelayMax: 5000,
          reconnectionAttempts: Infinity,
          timeout: 10000,
          autoConnect: true,
        });
      } catch (e) {
        this._log("error", "Socket.IO constructor error", e);
        return;
      }

      // mark ready immediately
      this._isFullyReady = true;
      if (this._readyPromiseResolve) {
        this._readyPromiseResolve(true);
        this._log("info", "✅ CLIENT MARKED READY (before connect)");
      }

      this._io.on("connect", () => {
        this._ioConnected = true;
        this._reconnectCount = 0;
        this._sessionId = this._io.id;

        this._log("info", `✓ WS CONNECTED as '${this._clientType}' (session: ${this._sessionId})`);
        if (this.widgetDebug) {
          console.info('[QuizMasterWidgetDebug]', 'connection_status', {
            connected: true,
            transport: this._io.io?.engine?.transport?.name || 'socket.io',
            websocketHost: new URL(this.baseUrl).host,
            publicWidgetIdPresent: Boolean(this.publicWidgetId)
          });
        }
        this._emitLocal("ws_open");
        this._emitLocal("ready", { snapshot: this._snapshot || {}, lastSeq: this._lastSeq });

        try {
          if (this._roomId) {
            this._io.emit("join_room", {
              room_id: this._roomId,
              public_widget_id: this.publicWidgetId || "",
              session_id: this.widgetSessionId || "",
              widget_type: this.widgetType,
              control_token: window.QuizMasterURLs?.controlToken?.() || ""
            });
          }
        } catch (e) {
          this._log("warn", "Failed to join room:", e);
        }

        this._setupSignalForwarding();
      });

      this._io.on("hello", (meta) => {
        this._serverProtocol =
          meta?.protocol_version || null;
        this._serverLastSeq = meta?.last_seq || 0;

        this._log("info", `Server protocol ${this._serverProtocol} (seq=${this._serverLastSeq})`);

        try {
          this._io.emit("request_snapshot", {});
        } catch {}
      });

      this._io.on("snapshot", (data) => {
        this._snapshot = data?.snapshot || null;
        this._log("info", "📸 Snapshot received", this._snapshot);

        try {
          this._io.emit("request_events_since", { since_seq: this._lastSeq });
        } catch {}
      });

      this._io.on("replay", (data) => {
        const events = Array.isArray(data?.events) ? data.events : [];
        this._log("info", `🔄 Replaying ${events.length} events`);

        for (const evt of events) {
          this._handleEventEnvelope(evt);
        }
        if (typeof data?.last_seq === "number") {
          this._lastSeq = Math.max(this._lastSeq, data.last_seq);
        }
        this._emitLocal("ready", {
          snapshot: this._snapshot,
          lastSeq: this._lastSeq,
        });

        this._checkQuizSignalsAndReplay();
      });

      this._io.on("signal", (event) => this._handleEventEnvelope(event));

      this._io.on("client_registered", (data) => {
        this._clientId = data.client_id || data.sid;
        this._clientInfo = data;
        this._log("info", `✓ Registered: ${data.type} (sid: ${data.sid})`);
      });

      this._io.on("room_joined", (data) => {
        this._log("info", `✓ Joined room: ${data.room_id}`);
      });

      this._io.on("disconnect", (reason) => {
        this._ioConnected = false;
        this._sessionId = null;
        this._signalForwardingSetup = false;
        this._emitLocal("ws_close");
        if (this.widgetDebug) {
          console.info('[QuizMasterWidgetDebug]', 'connection_status', {
            connected: false,
            websocketHost: new URL(this.baseUrl).host,
            closeReason: reason
          });
        }

        this._log("warn", `${reason} - will reconnect...`);

        if (reason === "io server disconnect") {
          setTimeout(() => this._io.connect(), 1000);
        }
      });

      this._io.on("connect_error", (error) => {
        this._log("error", `Connection error: ${error.message}`);
      });

      this._io.on("reconnect", (attemptNumber) => {
        this._log("info", `✓ RECONNECTED after ${attemptNumber} attempts`);
        this._emitLocal("ws_reconnect");

        try {
          this._io.emit("request_snapshot", {});
          this._log("debug", "Requested snapshot after reconnect");

          setTimeout(() => {
            this._setupSignalForwarding();
            this._log("debug", "Signal forwarding re-established");
          }, 150);
        } catch (e) {
          this._log("error", "Reconnect error:", e);
        }
      });

      this._startHeartbeat();
    }

    // ----------------- Signal Forwarding -----------------
    _setupSignalForwarding() {
      if (this._signalForwardingSetup) {
        this._log("debug", "Signal forwarding already setup");
        return;
      }

      const attemptSetup = (retryCount = 0) => {
        const locator = window.ServiceLocator?.get_instance?.();
        const quizSignals = locator?.get_service?.("QuizSignals");

        if (quizSignals && this._io) {
          this._log("info", "✅ QuizSignals found, setting up forwarding");

          const signalsToForward = [
            "quiz_started",
            "quiz_ended",
            "quiz_paused",
            "quiz_resumed",
            "question_changed",
            "answers_highlighted",
            "showing_answers",
            "timer_started",
            "timer_tick",
            "timer_ended",
            "timer_paused",
            "timer_resumed",
            "answer_display_complete",
            "state_changed",
            "leaderboard_updated",
            "message_ready",
            "quiz_display_ready",
            "question_number_changed",
            "quiz_data_loaded",
             "tiktok_gift",
             "tiktok:gift",
             "tiktok_chat_message",
             "tiktok:comment",
             "tiktok_follow",
             "tiktok:follow",
             "tiktok_share",
             "tiktok:share",
             "tiktok_like",
             "tiktok:like",
             "tiktok_join",
             "tiktok:join"
          ];

          // Fix: Avoid duplicate forwarding by relying on _handleEventEnvelope as the single forwarding path.
          signalsToForward.forEach((sig) => {
            this._io.off(`signal:${sig}`);
          });

          this._signalForwardingSetup = true;
          this._log("info", `Signal forwarding configured (${signalsToForward.length} signals)`);
          return;
        }

        if (retryCount < 50) {
          if (retryCount === 0 || retryCount % 10 === 0) {
            this._log("debug", `⏳ QuizSignals not ready, retry ${retryCount + 1}/50`);
          }
          setTimeout(() => attemptSetup(retryCount + 1), 100);
        } else {
          this._log(
            "warn",
            "⚠️ QuizSignals not available after 50 attempts - signals may not forward properly"
          );
        }
      };

      attemptSetup();
    }

    // ----------------- Heartbeat -----------------
    _startHeartbeat() {
      if (this._heartbeatTimer) {
        clearInterval(this._heartbeatTimer);
      }
      this._heartbeatTimer = setInterval(() => {
        if (this._ioConnected && this._io) {
          this._io.emit("heartbeat", {
            timestamp: Date.now(),
            type: this._clientType,
            client_id: this._clientId,
            session_id: this._sessionId,
            room_id: this._roomId,
          });
        }
      }, 30000);
    }

    emit(event, data = {}) {
      if (this._ioConnected && this._io) {
        this._io.emit(event, data);
        this._log("debug", `Socket.IO emit: ${event}`, data);
      } else {
        this._log("warn", `emit() failed, socket not connected: ${event}`);
      }
    }

    send(event, data = {}) {
      if (this._io && this._ioConnected) {
        return this._io.emit(event, data);
      }
      console.warn("[HTTPBridgeClient] Cannot send event:", event);
    }

    getConnectionStatus() {
      return {
        connected: this._ioConnected,
        fullyReady: this._isFullyReady,
        clientType: this._clientType,
        clientId: this._clientId,
        sessionId: this._sessionId,
        clientInfo: this._clientInfo || null,
        reconnectCount: this._reconnectCount || 0,
        socketId: this._io?.id || null,
        lastSeq: this._lastSeq,
        roomId: this._roomId || "default",
        snapshot: this._snapshot,
        signalForwardingSetup: this._signalForwardingSetup,
      };
    }

    _handleEventEnvelope(evt) {
      try {
        if (evt && typeof evt.seq === "number") {
          if (evt.seq <= this._lastSeq) return;
          this._lastSeq = evt.seq;
        }

        const { name, originalName, args, targetClientType } = this._normalizeWsMessage(evt);
        if (!name) return;
        if (targetClientType && targetClientType !== this._clientType) return;

        // Fix: Canonicalize TikTok aliases (e.g. tiktok:gift) so widgets subscribe to one stable event name.
        const canonicalName = this._canonicalizeSignalName(name);
        if (this.widgetDebug) {
          console.info('[QuizMasterWidgetDebug]', 'last_event', { type: canonicalName });
        }

        // Fix: Suppress duplicate gift aliases/payloads arriving within a short dedupe window.
        if (this._shouldSuppressDuplicateSignal(canonicalName, args)) {
          this._log("debug", `Skipping duplicate signal: ${canonicalName}`);
          return;
        }

        if (canonicalName === "state_changed" && typeof args[0] === "string") {
          args[0] = String(args[0]).toUpperCase();
        }

        this._emitLocal("signal", canonicalName, args);
        this._emitLocal(`signal:${canonicalName}`, args);

        if (canonicalName !== name) {
          // Fix: Preserve backward compatibility for legacy listeners while keeping canonical emission for widgets.
          this._emitLocal(`signal:${name}`, args);
        }

        this._forwardToQuizSignalsWithBuffer(canonicalName, ...args);
      } catch (e) {
        this._log("error", "Envelope handling error", e?.message || e);
      }
    }

    _canonicalizeSignalName(name) {
      const aliases = {
        "tiktok:gift": "tiktok_gift",
        "tiktok:comment": "tiktok_chat_message",
        "tiktok:follow": "tiktok_follow",
        "tiktok:share": "tiktok_share",
        "tiktok:like": "tiktok_like",
        "tiktok:join": "tiktok_join",
      };
      return aliases[name] || name;
    }

    _normalizeWsMessage(data) {
      const m = typeof data === "string" ? this._safeParseJSON(data) : data;
      const rawName = m?.signal || m?.signal_name || m?.name || m?.event;
      const name = this._canonicalSignalName(rawName);
      let args = m?.args ?? m?.payload ?? [];
      if (!Array.isArray(args)) args = [args];
      return { name, originalName: rawName, args, targetClientType: m?.target_client_type };
    }

    _canonicalSignalName(name) {
      const aliases = {
        // # Fix: Normalize legacy signal aliases to canonical snake_case names.
        timer_ended: "timer_expired",
        "tiktok:gift": "tiktok_gift",
        "tiktok:comment": "tiktok_chat_message",
        "tiktok:follow": "tiktok_follow",
        "tiktok:share": "tiktok_share",
        "tiktok:like": "tiktok_like",
        "tiktok:join": "tiktok_join",
      };
      return aliases[name] || name;
    }

    _shouldSuppressDuplicateSignal(signalName, args) {
      if (signalName !== "tiktok_gift") return false;

      const payload = args?.[0] || {};
      const userId = payload.uniqueId || payload.unique_id || payload.userId || payload.username || "unknown";
      const giftId = payload.giftId || payload.gift_id || payload.giftName || payload.gift_name || "unknown";
      const giftCount = payload.giftCount || payload.gift_count || 1;
      const fingerprint = `${signalName}:${userId}:${giftId}:${giftCount}`;
      const now = Date.now();

      for (const [key, seenAt] of this._recentSignalFingerprints.entries()) {
        if (now - seenAt > this._signalDedupeTtlMs) {
          this._recentSignalFingerprints.delete(key);
        }
      }

      const lastSeen = this._recentSignalFingerprints.get(fingerprint);
      if (lastSeen && now - lastSeen < this._signalDedupeTtlMs) {
        return true;
      }

      this._recentSignalFingerprints.set(fingerprint, now);
      return false;
    }

    _safeParseJSON(s) {
      try {
        return JSON.parse(s);
      } catch {
        return null;
      }
    }

    _checkQuizSignalsAndReplay() {
      const check = () => {
        const locator = window.ServiceLocator?.get_instance?.();
        const signals = locator?.get_service?.("QuizSignals");

        if (signals && signals.emit_signal) {
          this._quizSignalsReady = true;

          const buffer = [...this._signalBuffer];
          this._signalBuffer = [];

          for (const { name, args } of buffer) {
            try {
              signals.emit_signal(name, ...args);
            } catch {}
          }
        } else {
          setTimeout(check, 100);
        }
      };

      check();
    }

    _forwardToQuizSignalsWithBuffer(signalName, ...args) {
      try {
        const locator = window.ServiceLocator?.get_instance?.();
        const signals = locator?.get_service?.("QuizSignals");

        if (signals?.emit_signal) {
          this._quizSignalsReady = true;
          // # Fix: Forward exact argument cardinality without array-wrapping payload corruption.
          signals.emit_signal(signalName, ...args);
        } else {
          if (this._signalBuffer.length >= this._maxSignalBufferSize) {
            this._signalBuffer.shift();
            this._log("warn", `Signal buffer full (${this._maxSignalBufferSize}), dropping oldest event`);
          }
          this._signalBuffer.push({ name: signalName, args });
          if (!this._quizSignalsReady) {
            this._checkQuizSignalsAndReplay();
          }
        }
      } catch (e) {
        this._log("error", "Error forwarding to QuizSignals", e);
      }
    }

    isWebSocketConnected() {
      return !!this._ioConnected;
    }

    isFullyReady() {
      return this._isFullyReady;
    }

    async startQuiz() {
      return this._post("/quiz/start", {});
    }

    async stopQuiz() {
      return this._post("/quiz/stop", {});
    }

    async pauseQuiz() {
      return this._post("/quiz/pause", {});
    }

    async resumeQuiz() {
      return this._post("/quiz/resume", {});
    }

    async skipQuestion() {
      return this._post("/quiz/skip", {});
    }

    async loadQuiz(csv_text) {
      return this._post("/quiz/load", { csv_text });
    }

    async getTimerDuration() {
      return this._get("/quiz/timer/duration");
    }

    async getAnswerDisplayTime() {
      return this._get("/quiz/timer/answer-time");
    }

    async _getSessionSnapshot() {
      if (!this.widgetSessionId || !this.widgetType || !this.publicWidgetId) return null;
      const url = `${this.apiBaseUrl}/api/widget-sessions/${encodeURIComponent(this.widgetType)}/${encodeURIComponent(this.widgetSessionId)}/snapshot`;
      const response = await fetch(url, { cache: "no-store" });
      if (!response.ok) throw new Error(`Snapshot request failed: HTTP ${response.status}`);
      const payload = await response.json();
      this._snapshot = payload.snapshot || {};
      this._lastSeq = Math.max(this._lastSeq, Number(payload.version || 0));
      return this._snapshot;
    }

    async getState() {
      return (await this._getSessionSnapshot()) || this._get("/state");
    }

    async getFullState() {
      return (await this._getSessionSnapshot()) || this._get("/state/full");
    }

    async loadSettings() {
      const resp = await this._get("/settings");
      if (!resp) return {};
      if (resp.success === false) throw new Error(resp.error || "Failed to load settings");
      return resp.settings || {};
    }

    async saveSettings(settings) {
      const resp = await this._post("/settings", settings);
      if (resp && resp.success === false) throw new Error(resp.error || "Failed to save settings");
      return resp;
    }

    sendSignalWS(name, args = []) {
      try {
        if (this._ioConnected && this._io) {
          this._io.emit("signal", {
            signal: name,
            args: args,
            room_id: this._roomId || "default",
          });
          this._log("debug", `WS signal sent: ${name}`, args);
          return true;
        }
        this._log("warn", `Cannot send WS signal (socket not connected): ${name}`);
        return false;
      } catch (e) {
        this._log("error", `WS signal send error: ${name}`, e);
        return false;
      }
    }

    notifyTimerExpired() {
      return this.sendSignalWS("timer_expired");
    }

    notifyAnswerDisplayComplete() {
      return this.sendSignalWS("answer_display_complete");
    }

    getClientType() {
      return this._clientType;
    }

    getRoomId() {
      return this._roomId;
    }

    cleanup() {
      try {
        if (this._heartbeatTimer) clearInterval(this._heartbeatTimer);
        if (this._io) {
          this._io.disconnect();
          this._io = null;
        }
        this._ioConnected = false;
        this._isFullyReady = false;
        this._signalForwardingSetup = false;
        this._listeners.clear();
        this._signalBuffer = [];
        this._log("info", "Cleaned up");
      } catch (e) {
        this._log("error", "Error during cleanup", e);
      }
    }
  }

  window.httpBridgeClient = null;

  async function initHTTPBridge(
    baseUrl = window.QuizMasterURLs?.activeBaseUrl?.() || window.location.origin,
    options = {}
  ) {
    if (window.httpBridgeClient) {
      console.log("[HTTP Bridge] Reusing existing client");
      return window.httpBridgeClient;
    }

    if (typeof window.io !== "function") {
      console.error("[HTTP Bridge] ❌ Socket.IO not loaded!");
      const client = new HTTPBridgeClient(baseUrl, options);
      window.httpBridgeClient = client;
      return client;
    }

    const client = new HTTPBridgeClient(baseUrl, {
      timeout: options.timeout ?? 5000,
      retries: options.retries ?? 2,
      retryDelay: options.retryDelay ?? 500,
      logLevel: options.logLevel ?? "info",
      roomId: options.roomId || "default",
    });

    window.httpBridgeClient = client;

    try {
      console.log("[HTTP Bridge] Checking HTTP connection...");
      await client.waitForConnection(5000);

      console.log("[HTTP Bridge] Connecting WebSocket...");
      client.connectWebSocket();

      const ready = await client.waitForReady(3000);
      if (!ready) {
        console.warn("[HTTP Bridge] ⚠ Ready timeout");
      } else {
        console.log("[HTTP Bridge] ✅ Fully ready");
      }

      return client;
    } catch (e) {
      console.error("[HTTP Bridge] Failed:", e);
      return client;
    }
  }

  window.HTTPBridgeClient = HTTPBridgeClient;
  window.initHTTPBridge = initHTTPBridge;
  window.getHTTPBridgeClient = () => window.httpBridgeClient;

  (function bootstrapServices() {
    const waitForServiceLocator = () =>
      new Promise((resolve) => {
        const check = () => {
          const locator = window.ServiceLocator?.get_instance?.();
          if (locator) resolve(locator);
          else setTimeout(check, 50);
        };
        check();
      });

    waitForServiceLocator().then((locator) => {
      try {
        if (!locator.get_service("QuizSignals") && window.QuizSignals) {
          locator.register_service("QuizSignals", new window.QuizSignals());
          console.log("[bootstrap] QuizSignals registered");
        }

        if (!locator.get_service("CircleTimerWidget") && window.CircleTimerWidget) {
          locator.register_service("CircleTimerWidget", window.CircleTimerWidget.getInstance());
          console.log("[bootstrap] CircleTimerWidget registered");
        }

        console.log("[bootstrap] Bootstrap complete");
      } catch (e) {
        console.error("[bootstrap] Bootstrap failed:", e);
      }
    });
  })();
}