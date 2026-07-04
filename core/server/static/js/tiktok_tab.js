console.log("=== TIKTOK TAB MODULE LOADED ===");

const CONFIG = {
    CHAT_LIMIT_STRICT: 50,
    CHAT_LIMIT_LOOSE: 500,
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
    constructor(container, scrollArea, limitCheckbox) {
        this.container = container;
        this.scrollArea = scrollArea;
        this.limitCheckbox = limitCheckbox;
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
        const limit = Number.POSITIVE_INFINITY;
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
        this.linkedAccount = null;
    }

    async init() {
        if (this.isInitialized) return;
        if (typeof HTTPBridgeClient === 'undefined') {
            console.warn("⏳ Waiting for HTTPBridgeClient...");
            setTimeout(() => this.init(), 250);
            return;
        }
        this._bindUI();
        this.messageManager = new MessageManager(this.ui.chatContainer, this.ui.scrollArea, this.ui.limitChat);
        this.wsClient = window.httpBridgeClient || new HTTPBridgeClient(this.apiBase);
        if (!this.wsClient.isWebSocketConnected()) this.wsClient.connectWebSocket();
        this._setupDOMListeners();
        this._setupSocketListeners();
        setTimeout(() => this.refreshLinkedAccount(), 400);
        setTimeout(() => this._loadSavedUsernameWithRetry(), 500);
        setTimeout(() => this._checkStatus(), 500);
        setInterval(() => this._checkStatus(), 2000);
        this.isInitialized = true;
        this._log("TikTok panel ready", "success");
        this._systemChat("TikTok panel ready. Link your TikTok account, then connect live chat.");
    }

    _bindUI() {
        this.ui = {
            connectBtn: document.getElementById("connectBtn"),
            manualConnectBtn: document.getElementById("manualConnectBtn"),
            disconnectBtn: document.getElementById("disconnectBtn"),
            usernameInput: document.getElementById("usernameInput"),
            linkedUsernameInput: document.getElementById("linkedUsernameInput"),
            statusIndicator: document.getElementById("tiktokStatus"),
            statusText: document.getElementById("statusText"),
            headerStatusText: document.getElementById("headerStatusText"),
            logBox: document.getElementById("connectionLog"),
            chatContainer: document.getElementById("chatContainer"),
            scrollArea: document.getElementById("chatScrollArea"),
            toggleChat: document.getElementById("toggleChat"),
            limitChat: document.getElementById("limitChat"),
            clearChat: document.getElementById("clearChatBtn"),
            officialLoginBtn: document.getElementById("officialLoginBtn"),
            officialStatusBtn: document.getElementById("officialStatusBtn"),
            officialLoginStatus: document.getElementById("officialLoginStatus"),
            linkedAccountCard: document.getElementById("linkedAccountCard"),
            linkedAvatar: document.getElementById("linkedAvatar"),
            linkedAvatarFallback: document.getElementById("linkedAvatarFallback"),
            linkedDisplayName: document.getElementById("linkedDisplayName"),
            linkedUsername: document.getElementById("linkedUsername"),
            linkedStats: document.getElementById("linkedStats")
        };
    }

    _setupDOMListeners() {
        this.ui.connectBtn?.addEventListener("click", () => this.connectLinkedLiveChat());
        this.ui.manualConnectBtn?.addEventListener("click", () => this.connectManualFallback());
        this.ui.disconnectBtn?.addEventListener("click", () => this.disconnect());
        this.ui.clearChat?.addEventListener("click", () => this.clearChat());
        this.ui.officialLoginBtn?.addEventListener("click", () => this.openOfficialLogin());
        this.ui.officialStatusBtn?.addEventListener("click", () => this.refreshLinkedAccount());
        this.ui.usernameInput?.addEventListener("keypress", (e) => { if (e.key === "Enter") this.connectManualFallback(); });
        this.ui.toggleChat?.addEventListener("change", () => {
            this._systemChat(this.ui.toggleChat.checked ? "Chat feed shown" : "Chat feed hidden, but messages still stay logged");
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

    async openOfficialLogin() {
        try {
            this._setOfficialLoginStatus("Opening QuizMaster TikTok account login in your browser...");
            const res = await this._apiCall("POST", "/api/tiktok/official-login/open");
            const broker = res.broker_url || "https://auth.quizmaster.online";
            this._setOfficialLoginStatus(`Browser opened via ${broker}. Complete TikTok login, then click Refresh Linked Account.`);
            this._log(`Official TikTok login opened via ${broker}`, "info");
            setTimeout(() => this.refreshLinkedAccount(), 3000);
        } catch (e) {
            this._setOfficialLoginStatus(`Official TikTok login failed: ${e.message}`);
        }
    }

    async refreshLinkedAccount() {
        try {
            this._setOfficialLoginStatus("Checking linked TikTok account...");
            const snapshot = await this._apiCall("GET", "/api/tiktok/account-snapshot");
            this._applyLinkedAccount(snapshot);
            if (snapshot.available && snapshot.username) {
                this._setOfficialLoginStatus(`Linked account ready: @${snapshot.username}`);
                this._log(`Linked TikTok account: @${snapshot.username}`, "success");
            } else if (snapshot.connected) {
                this._setOfficialLoginStatus("TikTok login is connected, but account snapshot is not available yet. Cloudflare broker/account-stats may still need setup.");
                this._log("TikTok login connected but account snapshot unavailable", "info");
            } else {
                this._setOfficialLoginStatus("No linked TikTok account found yet. Connect TikTok Account first.");
            }
        } catch (e) {
            this._clearLinkedAccount();
            this._setOfficialLoginStatus(`Linked TikTok account unavailable: ${e.message}. This is expected until the QuizMaster Cloudflare broker is deployed.`);
        }
    }

    async checkOfficialLoginStatus() {
        return this.refreshLinkedAccount();
    }

    async connectLinkedLiveChat() {
        const username = this.linkedAccount?.username || this.ui.linkedUsernameInput?.value?.trim();
        if (!username) {
            alert("Connect and refresh your linked TikTok account first.");
            return;
        }
        this._setUiState("connecting", `Connecting live chat for @${username}...`);
        this._systemChat(`Connecting live chat for @${username}...`);
        try {
            const res = await this._apiCall("POST", "/api/tiktok/connect", {});
            if (!res.success) throw new Error(res.error || "Failed to start connection");
            const connectedUsername = res.username || username;
            this._log(`Live chat connect requested for linked account @${connectedUsername}`, "success");
        } catch (e) {
            this._setUiState("error", e.message);
            this._systemChat(`Live chat connection failed: ${e.message}`);
        }
    }

    async connectManualFallback() {
        const username = this.ui.usernameInput?.value?.trim();
        if (!username) { alert("Enter username"); return; }
        this._setUiState("connecting", `Connecting manual fallback @${username}...`);
        this._systemChat(`Manual fallback connecting to @${username}...`);
        try {
            await fetch(`${this.apiBase}/api/settings`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ "TikTokLive": { "last_username": username } })
            });
            const res = await this._apiCall("POST", "/api/tiktok/connect", { username, manual_fallback: true });
            if (!res.success) throw new Error(res.error || "Failed to start connection");
            this._log(`Manual fallback connect requested for @${username}`, "success");
        } catch (e) {
            this._setUiState("error", e.message);
            this._systemChat(`Manual fallback failed: ${e.message}`);
        }
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

    _applyLinkedAccount(snapshot) {
        const available = !!(snapshot && snapshot.available && snapshot.username);
        this.linkedAccount = available ? snapshot : null;
        if (this.ui.linkedAccountCard) this.ui.linkedAccountCard.hidden = false;
        const username = available ? snapshot.username : "";
        const displayName = available ? (snapshot.display_name || snapshot.username) : "No TikTok account linked";
        if (this.ui.linkedDisplayName) this.ui.linkedDisplayName.textContent = displayName;
        if (this.ui.linkedUsername) this.ui.linkedUsername.textContent = available ? `@${username}` : "Use Connect TikTok Account first.";
        if (this.ui.linkedStats) {
            const parts = [];
            if (snapshot?.followers !== null && snapshot?.followers !== undefined) parts.push(`${Number(snapshot.followers).toLocaleString()} followers`);
            if (snapshot?.verified) parts.push("verified");
            if (snapshot?.updated_at) parts.push(`updated ${snapshot.updated_at}`);
            this.ui.linkedStats.textContent = parts.length ? parts.join(" · ") : (snapshot?.connected ? "Login connected; account stats unavailable." : "Account snapshot unavailable.");
        }
        if (this.ui.linkedUsernameInput) this.ui.linkedUsernameInput.value = available ? `@${username}` : "";
        if (this.ui.connectBtn) this.ui.connectBtn.disabled = !available;
        if (this.ui.linkedAvatar && this.ui.linkedAvatarFallback) {
            if (available && snapshot.avatar_url) {
                this.ui.linkedAvatar.src = snapshot.avatar_url;
                this.ui.linkedAvatar.hidden = false;
                this.ui.linkedAvatarFallback.hidden = true;
            } else {
                this.ui.linkedAvatar.hidden = true;
                this.ui.linkedAvatarFallback.hidden = false;
                this.ui.linkedAvatarFallback.textContent = available ? username.slice(0, 1).toUpperCase() : "@";
            }
        }
    }

    _clearLinkedAccount() {
        this.linkedAccount = null;
        if (this.ui.linkedAccountCard) this.ui.linkedAccountCard.hidden = false;
        if (this.ui.linkedDisplayName) this.ui.linkedDisplayName.textContent = "No TikTok account linked";
        if (this.ui.linkedUsername) this.ui.linkedUsername.textContent = "Use Connect TikTok Account first.";
        if (this.ui.linkedStats) this.ui.linkedStats.textContent = "Account snapshot unavailable.";
        if (this.ui.linkedUsernameInput) this.ui.linkedUsernameInput.value = "";
        if (this.ui.connectBtn) this.ui.connectBtn.disabled = true;
        if (this.ui.linkedAvatar) this.ui.linkedAvatar.hidden = true;
        if (this.ui.linkedAvatarFallback) {
            this.ui.linkedAvatarFallback.hidden = false;
            this.ui.linkedAvatarFallback.textContent = "@";
        }
    }

    _setOfficialLoginStatus(message) {
        if (this.ui.officialLoginStatus) this.ui.officialLoginStatus.textContent = message;
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
            if (res.success && res.connected) {
                this._setUiState("connected", `Connected to @${res.username || "live"}`);
                if (this.ui.linkedUsernameInput && res.username) this.ui.linkedUsernameInput.value = `@${res.username}`;
            } else if (res.success) {
                this._setUiState("disconnected", "Disconnected");
            }
        } catch (e) {
            this._setUiState("error", "Status check failed");
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
        this._setUiState(state, message);
        if (state !== this.lastState) {
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
        if (this.ui.statusIndicator) this.ui.statusIndicator.className = `status-indicator ${cls}`;
        if (this.ui.statusText) this.ui.statusText.textContent = message || state;
        if (this.ui.headerStatusText) {
            this.ui.headerStatusText.textContent = message || state;
            this.ui.headerStatusText.className = `pill ${cls === "connected" ? "success" : cls === "connecting" ? "warning" : cls === "error" ? "danger" : "danger"}`;
        }
        const isConnected = cls === "connected";
        const isConnecting = cls === "connecting";
        const hasLinkedAccount = !!(this.linkedAccount && this.linkedAccount.username);
        if (this.ui.connectBtn) this.ui.connectBtn.disabled = isConnected || isConnecting || !hasLinkedAccount;
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
