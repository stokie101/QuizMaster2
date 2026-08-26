console.log("=== TIKTOK TAB MODULE LOADED ===");

const CONFIG = {
    // Older messages are dropped from the DOM only; scoring reads the socket
    // feed, so the visible history never affects a running quiz.
    CHAT_HISTORY_LIMIT: 300,
    AVATAR_CACHE_SIZE: 100,
    API_BASE: window.location.origin
};

class AvatarCache {
    constructor(maxSize = CONFIG.AVATAR_CACHE_SIZE) {
        this.maxSize = maxSize;
        this.cache = new Map();
    }

    get(username) {
        const entry = this.cache.get(username);
        if (entry) {
            entry.lastUsed = Date.now();
            return entry.url;
        }
        return null;
    }

    set(username, url) {
        if (this.cache.size >= this.maxSize) this._cleanup();
        this.cache.set(username, { url, lastUsed: Date.now() });
    }

    _cleanup() {
        const entries = Array.from(this.cache.entries()).sort((a, b) => a[1].lastUsed - b[1].lastUsed);
        const removeCount = Math.floor(this.maxSize * 0.2);
        for (let i = 0; i < removeCount; i++) this.cache.delete(entries[i][0]);
    }

    clear() {
        this.cache.clear();
    }
}

class MessageManager {
    constructor(container, scrollArea) {
        this.container = container;
        this.scrollArea = scrollArea;
        this.messagePool = [];
    }

    addMessage(username, text, avatarUrl, isSystem = false) {
        if (!this.container) return;
        const el = this.messagePool.pop() || document.createElement("div");
        el.className = isSystem ? "chat-message system" : "chat-message";
        const safeName = String(username || "System");
        const avatarHtml = isSystem ? '' : (avatarUrl
            ? `<img class="avatar" src="${avatarUrl}" onerror="this.style.display='none'" loading="lazy">`
            : `<div class="avatar-placeholder">${safeName[0]?.toUpperCase() || '?'}</div>`
        );
        el.innerHTML = `
            ${avatarHtml}
            <div class="message-body">
                <div class="message-header">
                    <span class="username">${this._escapeHtml(safeName)}</span>
                    <span class="timestamp">${new Date().toLocaleTimeString()}</span>
                </div>
                <div class="message-text">${this._escapeHtml(String(text || ""))}</div>
            </div>
        `;
        this.container.appendChild(el);
        this._cleanup();
        this._scrollToBottom();
    }

    clear() {
        if (this.container) this.container.innerHTML = "";
        this.messagePool = [];
    }

    _cleanup() {
        const limit = CONFIG.CHAT_HISTORY_LIMIT;
        while (this.container.children.length > limit) {
            const node = this.container.firstChild;
            this.container.removeChild(node);
            if (this.messagePool.length < 50) this.messagePool.push(node);
        }
    }

    _scrollToBottom() {
        if (!this.scrollArea) return;
        requestAnimationFrame(() => {
            this.scrollArea.scrollTop = this.scrollArea.scrollHeight;
            if (this.container) this.container.scrollTop = this.container.scrollHeight;
        });
        setTimeout(() => {
            this.scrollArea.scrollTop = this.scrollArea.scrollHeight;
            if (this.container) this.container.scrollTop = this.container.scrollHeight;
        }, 50);
    }

    _escapeHtml(text) {
        return String(text || "")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }
}

class TikTokTabManager {
    constructor() {
        this.wsClient = null;
        this.avatarCache = new AvatarCache();
        this.messageManager = null;
        this.isInitialized = false;
        this.apiBase = (window.BRIDGE_ORIGIN || CONFIG.API_BASE).replace(/\/$/, "");
        this.ui = {};
        this.lastState = "disconnected";
    }

    async init() {
        if (this.isInitialized) return;
        if (typeof HTTPBridgeClient === 'undefined') {
            console.warn("⏳ Waiting for HTTPBridgeClient...");
            setTimeout(() => this.init(), 250);
            return;
        }
        this._bindUI();
        this.messageManager = new MessageManager(this.ui.chatContainer, this.ui.scrollArea);
        this.wsClient = window.httpBridgeClient || new HTTPBridgeClient(this.apiBase);
        if (!this.wsClient.isWebSocketConnected()) this.wsClient.connectWebSocket();
        this._setupDOMListeners();
        this._setupSocketListeners();
        setTimeout(() => this._loadSavedUsernameWithRetry(), 500);
        setTimeout(() => this._checkStatus(), 500);
        setInterval(() => this._checkStatus(), 2000);
        this.isInitialized = true;
        this._systemChat("TikTok panel ready. Enter a username and press Connect.");
    }

    _bindUI() {
        this.ui = {
            manualConnectBtn: document.getElementById("manualConnectBtn"),
            disconnectBtn: document.getElementById("disconnectBtn"),
            usernameInput: document.getElementById("usernameInput"),
            statusIndicator: document.getElementById("tiktokStatus"),
            statusText: document.getElementById("statusText"),
            headerStatusText: document.getElementById("headerStatusText"),
            logBox: document.getElementById("connectionLog"),
            chatContainer: document.getElementById("chatContainer"),
            scrollArea: document.getElementById("chatScrollArea"),
            toggleChat: document.getElementById("toggleChat"),
            clearChat: document.getElementById("clearChatBtn"),
            alert: document.getElementById("tiktokAlert"),
            alertTitle: document.getElementById("tiktokAlertTitle"),
            alertMessage: document.getElementById("tiktokAlertMessage"),
            alertHint: document.getElementById("tiktokAlertHint"),
            alertClose: document.getElementById("tiktokAlertClose")
        };
    }

    _setupDOMListeners() {
        const busy = (button, action) => window.QuizMasterUI?.withBusy
            ? window.QuizMasterUI.withBusy(button, action)
            : action();
        this.ui.manualConnectBtn?.addEventListener("click", () => busy(this.ui.manualConnectBtn, () => this.connect()));
        this.ui.disconnectBtn?.addEventListener("click", () => busy(this.ui.disconnectBtn, () => this.disconnect()));
        this.ui.clearChat?.addEventListener("click", () => this.clearChat());
        this.ui.alertClose?.addEventListener("click", () => this.hideAlert());
        this.ui.usernameInput?.addEventListener("input", () => this.hideAlert());
        this.ui.usernameInput?.addEventListener("keypress", (e) => { if (e.key === "Enter") this.connect(); });
        this.ui.toggleChat?.addEventListener("change", () => {
            const shown = this.ui.toggleChat.checked;
            this.ui.scrollArea?.classList.toggle("hidden", !shown);
            this._systemChat(shown ? "Chat feed shown" : "Chat feed hidden, but messages still stay logged");
        });
    }

    _setupSocketListeners() {
        if (!this.wsClient) return;
        this.wsClient.on("signal:tiktok_status", (data) => this._handleStatus(data));
        this.wsClient.on("tiktok_status", (data) => this._handleStatus(data));
        this.wsClient.on("signal:tiktok_chat_message", (data) => this._handleChat(data));
        this.wsClient.on("tiktok_chat_message", (data) => this._handleChat(data));
        this.wsClient.on("signal:tiktok_debug", (data) => this._handleDebug(data));
        this.wsClient.on("tiktok_debug", (data) => this._handleDebug(data));
        this.wsClient.on("ws_open", () => this._systemChat("Bridge websocket connected"));
        this.wsClient.on("ws_close", () => this._systemChat("Bridge websocket disconnected"));
    }

    async _apiCall(method, endpoint, body = null) {
        try {
            const url = `${this.apiBase}${endpoint}`;
            const headers = { "Content-Type": "application/json" };
            const options = { method, headers };
            if (body && method !== 'GET') options.body = JSON.stringify(body);
            const response = await fetch(url, options);
            let data = {};
            try { data = await response.json(); } catch (_) {}
            if (!response.ok) throw new Error(data.detail || data.error || `HTTP ${response.status}: ${response.statusText}`);
            return data;
        } catch (error) {
            this._log(`API Error: ${error.message}`, "error");
            throw error;
        }
    }

    async connect() {
        const username = String(this.ui.usernameInput?.value || "").trim().replace(/^@/, "");
        if (!username) {
            this.showAlert("Enter a username", "Type the TikTok username whose LIVE you want to read.",
                "It is the @name on their profile \u2014 you can leave the @ off.");
            this.ui.usernameInput?.focus();
            return;
        }

        this.hideAlert();
        this._lastReportedError = "";
        this._setUiState("connecting", `Connecting to @${username}\u2026`);
        this._systemChat(`Connecting to @${username}\u2026`);

        try {
            await fetch(`${this.apiBase}/api/settings`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ "TikTokLive": { "last_username": username } })
            });
            const res = await this._apiCall("POST", "/api/tiktok/connect", { username });
            if (!res.success) throw new Error(res.error || res.message || "The connection could not be started.");
            this._log(`Connect requested for @${username}`, "success");
            // The backend hands off to a worker, so success here only means the
            // attempt started. Say so if nothing has happened a few seconds on.
            this._watchConnectProgress(username);
        } catch (e) {
            this._setUiState("error", e.message);
            this.reportError(e.message, username);
        }
    }

    _watchConnectProgress(username) {
        clearTimeout(this._connectWatchdog);
        this._connectWatchdog = setTimeout(() => {
            if (this.lastState === "connected" || this.lastState === "error") return;
            this.showAlert(
                `Still trying to reach @${username}`,
                "TikTok has not accepted the connection yet.",
                "This nearly always means the creator is not live right now. QuizMaster keeps trying \u2014 start their LIVE, or check the username spelling."
            );
        }, 12000);
    }

    // Backend messages are technical. Map the ones we know to plain language,
    // and always show something rather than leaving the page silent.
    _friendlyError(raw, username) {
        const text = String(raw || "").trim();
        const lower = text.toLowerCase();
        const who = username ? `@${username}` : "that account";

        if (!text) {
            return { title: "Could not connect", message: "TikTok refused the connection and did not say why.",
                hint: `Check that ${who} is spelled correctly and is live right now.` };
        }
        if (lower.includes("user_not_found") || lower.includes("not found") || lower.includes("does not exist")) {
            return { title: "That username does not exist", message: `TikTok has no account called ${who}.`,
                hint: "Check the spelling. Use the @name from their profile, without the @." };
        }
        if (lower.includes("not live") || lower.includes("offline") || lower.includes("live has ended") || lower.includes("userofflineerror")) {
            return { title: `${who} is not live`, message: "Chat can only be read while a LIVE is running.",
                hint: "Start the LIVE on TikTok, then connect again." };
        }
        if (lower.includes("library not available") || lower.includes("tiktoklive")) {
            return { title: "TikTok chat library missing", message: "The component that reads TikTok chat did not load.",
                hint: "Restart QuizMaster. If it keeps happening, reinstall so the bundled library is restored." };
        }
        if (lower.includes("rate") && lower.includes("limit")) {
            return { title: "TikTok is rate limiting us", message: "Too many connection attempts in a short time.",
                hint: "Wait a couple of minutes before trying again." };
        }
        if (lower.includes("timeout") || lower.includes("timed out")) {
            return { title: "TikTok did not respond", message: "The connection attempt timed out.",
                hint: "Check your internet connection, then try again." };
        }
        if (lower.includes("failed to fetch") || lower.includes("networkerror") || lower.includes("connection refused")) {
            return { title: "Cannot reach QuizMaster", message: "The page could not talk to the QuizMaster app.",
                hint: "Make sure QuizMaster is still running, then reload this page." };
        }
        if (lower.includes("sign") || lower.includes("captcha")) {
            return { title: "TikTok asked for a verification", message: text,
                hint: "TikTok sometimes challenges automated connections. Wait a moment and try again." };
        }
        return { title: "Could not connect", message: text,
            hint: `Check that ${who} is live and the username is spelled correctly.` };
    }

    reportError(raw, username) {
        const text = String(raw || "").trim();
        if (text && text === this._lastReportedError) return;
        this._lastReportedError = text;
        clearTimeout(this._connectWatchdog);
        const friendly = this._friendlyError(text, username || this.ui.usernameInput?.value?.trim().replace(/^@/, ""));
        this.showAlert(friendly.title, friendly.message, friendly.hint);
        this._log(text || friendly.message, "error");
        this._systemChat(friendly.title + " \u2014 " + friendly.message);
    }

    showAlert(title, message, hint) {
        const ui = this.ui;
        if (!ui.alert) return;
        if (ui.alertTitle) ui.alertTitle.textContent = title;
        if (ui.alertMessage) ui.alertMessage.textContent = message;
        if (ui.alertHint) {
            ui.alertHint.textContent = hint || "";
            ui.alertHint.hidden = !hint;
        }
        ui.alert.hidden = false;
    }

    hideAlert() {
        if (this.ui.alert) this.ui.alert.hidden = true;
    }

    async _loadSavedUsernameWithRetry(attempt = 0) {
        try {
            const res = await fetch(`${this.apiBase}/api/settings`);
            const data = await res.json();
            if (data.success && data.settings) {
                const settings = data.settings;
                const section = settings.TikTokLive || settings.tiktoklive;
                if (section && section.last_username) {
                    this._setInputValueSafe(section.last_username);
                    return;
                }
            }
        } catch (e) {
            console.warn("Error loading settings:", e);
        }
        if (attempt < 3) setTimeout(() => this._loadSavedUsernameWithRetry(attempt + 1), 500);
    }

    _setInputValueSafe(value) {
        if (this.ui.usernameInput) this.ui.usernameInput.value = value;
    }

    async disconnect() {
        try {
            await this._apiCall("POST", "/api/tiktok/disconnect");
            this._log("Disconnect requested", "info");
            this._systemChat("Disconnect requested");
        } catch (e) {
            this._systemChat(`Disconnect failed: ${e.message}`);
        }
    }

    async _checkStatus() {
        try {
            const res = await this._apiCall("GET", "/api/tiktok/status");
            if (!res.success) return;
            if (res.connected) {
                clearTimeout(this._connectWatchdog);
                this._lastReportedError = "";
                this.hideAlert();
                this._setUiState("connected", `Connected to @${res.username || "live"}`);
                return;
            }
            // Polling is the safety net: a failure raised while the socket was
            // down still reaches the user through the stored last_error.
            if (res.last_error) {
                this._setUiState("error", res.last_error);
                this.reportError(res.last_error);
                return;
            }
            if (this.lastState !== "connecting") this._setUiState("disconnected", "Disconnected");
        } catch (e) {
            this._setUiState("error", e.message);
            this.reportError(e.message);
        }
    }

    clearChat() {
        this.messageManager.clear();
        this.avatarCache.clear();
        this._log("Chat cleared", "info");
    }

    _unwrap(data) {
        return Array.isArray(data) ? data[0] : data;
    }

    _handleStatus(data) {
        const payload = this._unwrap(data) || {};
        const state = payload.state || (payload.connected ? "connected" : "disconnected");
        const message = payload.message || state;
        const previous = this.lastState;
        this._setUiState(state, message);
        if (state === "error") {
            this.reportError(message, payload.username);
        } else if (state === "connected") {
            clearTimeout(this._connectWatchdog);
            this._lastReportedError = "";
            this.hideAlert();
        }
        if (state !== previous) {
            this.lastState = state;
            this._systemChat(`TikTok status: ${message}`);
        }
    }

    _handleDebug(data) {
        const payload = this._unwrap(data) || {};
        const msg = payload.message || JSON.stringify(payload);
        this._log(msg, payload.level || "debug");
        this._systemChat(msg);
    }

    _handleChat(data) {
        const payload = this._unwrap(data) || {};
        const username = payload.username || payload.uniqueId || payload.unique_id || "User";
        const text = payload.comment || payload.message || "";
        let avatar = payload.profilePictureUrl || payload.avatar_url;
        if (avatar) this.avatarCache.set(username, avatar);
        else avatar = this.avatarCache.get(username);
        this.messageManager.addMessage(username, text, avatar);
    }

    _systemChat(text) {
        this._log(text, "info");
        this.messageManager?.addMessage("System", text, null, true);
    }

    _setUiState(state, message) {
        const cls = String(state || "disconnected").toLowerCase();
        this.lastState = cls;
        // The status line is glanceable, so it never shows raw backend text.
        const label = cls === "error"
            ? this._friendlyError(message, this.ui.usernameInput?.value?.trim().replace(/^@/, "")).title
            : (message || state);
        if (this.ui.statusIndicator) this.ui.statusIndicator.className = `status-indicator ${cls}`;
        if (this.ui.statusText) this.ui.statusText.textContent = label;
        if (this.ui.headerStatusText) {
            this.ui.headerStatusText.textContent = label;
            this.ui.headerStatusText.className = `pill ${cls === "connected" ? "success" : cls === "connecting" ? "warning" : cls === "error" ? "danger" : "danger"}`;
        }
        const isConnected = cls === "connected";
        const isConnecting = cls === "connecting";
        if (this.ui.manualConnectBtn) this.ui.manualConnectBtn.disabled = isConnected || isConnecting;
        if (this.ui.disconnectBtn) this.ui.disconnectBtn.disabled = !isConnected;
        if (this.ui.usernameInput) this.ui.usernameInput.disabled = isConnecting;
    }

    _log(msg, type = "info") {
        if (!this.ui.logBox) return;
        const line = document.createElement("div");
        line.className = `log-line ${type}`;
        line.textContent = `[${new Date().toLocaleTimeString()}] ${msg}`;
        this.ui.logBox.appendChild(line);
        this.ui.logBox.scrollTop = this.ui.logBox.scrollHeight;
        while (this.ui.logBox.children.length > 100) this.ui.logBox.removeChild(this.ui.logBox.firstChild);
    }
}

window.tiktokTabManager = new TikTokTabManager();

if (document.readyState === "complete" || document.readyState === "interactive") {
    window.tiktokTabManager.init();
} else {
    document.addEventListener("DOMContentLoaded", () => window.tiktokTabManager.init());
}
