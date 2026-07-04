/* =============================================================================
   PHASE 1: FORCE CLIENT_TYPE EARLY
   ============================================================================= */
(function fixClientTypeEarly() {
    if (!window.CLIENT_TYPE) {
        const path = window.location.pathname;

        if (path.includes("openmic")) {
            window.CLIENT_TYPE = "openmic_overlay";
        } else if (path.includes("quiz_display")) {
            window.CLIENT_TYPE = "quiz_display";
        } else if (path.includes("leaderboard")) {
            window.CLIENT_TYPE = "leaderboard";
        } else if (path.includes("display")) {
            window.CLIENT_TYPE = "main_display";
        } else if (path.includes("controls")) {
            window.CLIENT_TYPE = "controls_window";
        } else if (path.includes("main_window")) {
            window.CLIENT_TYPE = "main_window";
        } else {
            window.CLIENT_TYPE = "generic";
        }

        console.log("[Theme] Forced CLIENT_TYPE early →", window.CLIENT_TYPE);
    }
})();

window.cleanupAllThemeFX = function () {
    console.log("[ThemeFX] 🔥 Global cleanup triggered (FULL)");

    // 1) Remove all theme-tagged FX
    document.querySelectorAll(
        ".xmas-fx, " +
        ".halloween-fx, " +
        ".birthday-fx, " +
        ".theme-module"
    ).forEach(el => el.remove());

    // 2) Remove leftover containers by ID or class
    [
        "#snow-layer",
        ".xmas-corner",
        ".xmas-border",
        ".halloween-border",
        ".birthday-border",
        ".xmas-layer",
        ".halloween-layer",
        ".birthday-layer"
    ].forEach(sel => {
        document.querySelectorAll(sel).forEach(el => el.remove());
    });

    // 3) Clear intervals
    if (window.__XMAS_SNOW_INTERVAL__) {
        clearInterval(window.__XMAS_SNOW_INTERVAL__);
        window.__XMAS_SNOW_INTERVAL__ = null;
        console.log("[ThemeFX] ⛔ Snow interval cleared");
    }

    if (window.__BIRTHDAY_CONFETTI_INTERVAL__) {
        clearInterval(window.__BIRTHDAY_CONFETTI_INTERVAL__);
        window.__BIRTHDAY_CONFETTI_INTERVAL__ = null;
        console.log("[ThemeFX] ⛔ Confetti interval cleared");
    }

    if (window.__BIRTHDAY_BALLOON_INTERVAL__) {
        clearInterval(window.__BIRTHDAY_BALLOON_INTERVAL__);
        window.__BIRTHDAY_BALLOON_INTERVAL__ = null;
        console.log("[ThemeFX] ⛔ Balloon interval cleared");
    }

    // 4) Disconnect observers
    if (window.__XMAS_OBSERVER__) {
        try { window.__XMAS_OBSERVER__.disconnect(); } catch {}
        window.__XMAS_OBSERVER__ = null;
        console.log("[ThemeFX] ⛔ Christmas observer disconnected");
    }

    if (window.__BIRTHDAY_OBSERVER__) {
        try { window.__BIRTHDAY_OBSERVER__.disconnect(); } catch {}
        window.__BIRTHDAY_OBSERVER__ = null;
        console.log("[ThemeFX] ⛔ Birthday observer disconnected");
    }

    // 5) Reset flags
    window.__XMAS_LOADED__ = false;
    window.__BIRTHDAY_LOADED__ = false;

    console.log("[ThemeFX] 🧹 Cleanup complete");
};

/* =============================================================================
   PHASE 2: BLOCK THEME MANAGER FROM MAIN WINDOW
   ============================================================================= */
(function blockMainWindow() {
    const isMainWindow = window.location.pathname.includes('main_window.html') ||
                         window.CLIENT_TYPE === "main_window" ||
                         document.querySelector('[data-client="main_window"]');

    if (isMainWindow) {
        console.log("[ThemeManager] ❌ BLOCKED in main_window - creating dummy ThemeManager");

        window.ThemeManager = class DummyThemeManager {
            constructor() {
                console.log("[ThemeManager] Dummy mode - no theme changes will apply");
            }
            init() {}
            getSavedTheme() { return "default"; }
            saveTheme() {}
            applyTheme() {}
            broadcast() {}
            setTheme() {}
            forceTheme() {}
            connectSelector() {}
            getCurrentTheme() { return { selection: "default", active: "default", seasonal: "default" }; }
            cleanup() {}
        };

        // Block all display loaders too
        window.THEME_LOADERS_BLOCKED = true;
        return;
    }
})();

/* =============================================================================
   PHASE 3: MAIN THEME MANAGER (LOADS FIRST)
   ============================================================================= */
(function () {
    "use strict";

    if (window.THEME_LOADERS_BLOCKED) return;

    if (["quiz_display", "leaderboard", "timer_display"].includes(window.CLIENT_TYPE)) {
        console.log("[ThemeFX] Overlay Themes source detected — legacy display/leaderboard FX disabled");
        return;
    }

    const THEME_KEY = "quizmaster_theme_override";
    const THEMES = {
        AUTO: "auto",
        DEFAULT: "default",
        HALLOWEEN: "halloween",
        CHRISTMAS: "christmas",
        BIRTHDAY: "birthday",
    };

    window.THEMES = THEMES;

    class ThemeManager {
        constructor() {
            this.currentTheme = THEMES.DEFAULT;
            this.isApplying = false;
            this.lastBroadcast = null;
            this.lastBroadcastTheme = null;
            this.lastAppliedTheme = null;
            this.lastAppliedActive = null;
            this._quizActive = false;
            this._broadcastInterval = null;
            this._controlsThemeLoaded = null;
            this._controlsLoadLock = false;

            // Event listener references for cleanup
            this._storageHandler = null;
            this._messageHandler = null;
            this._quizMessageHandler = null;
            this._wsQuizStartHandler = null;
            this._wsQuizEndHandler = null;
            this._wsThemeSyncHandler = null;
        }

        init() {
            console.log("[ThemeManager] 🎨 Initializing...");

            const saved = this.getSavedTheme();
            this.applyTheme(saved, false, true);
            this._setupQuizMonitoring();
            this._setupWebSocketSync();

            this._storageHandler = (e) => {
                if (e.key === THEME_KEY && !this.isApplying) {
                    const newTheme = e.newValue;

                    if (this._controlsThemeLoaded === newTheme) {
                        return; // Silent skip - already loaded
                    }

                    console.log("[ThemeManager] Storage changed to:", newTheme);
                    this.applyTheme(newTheme, false, true);
                }
            };
            window.addEventListener("storage", this._storageHandler);

            if (window.httpBridgeClient) {
                this._wsThemeSyncHandler = (payload) => {
                    try {
                        const { theme, activeTheme } = payload;
                        // Only log if theme actually changed
                        if (this.lastAppliedActive !== activeTheme) {
                            console.log("[ThemeManager] WS theme sync:", activeTheme);
                        }

                        const evt = new CustomEvent("THEME_SYNC", {
                            detail: { theme, activeTheme }
                        });

                        window.dispatchEvent(evt);
                        window.postMessage({ type: "THEME_SYNC", theme, activeTheme }, "*");

                    } catch (err) {
                        console.warn("[ThemeManager] Cannot dispatch THEME_SYNC:", err);
                    }
                };
                window.httpBridgeClient.on("signal:theme_sync", this._wsThemeSyncHandler);
            }

            this._messageHandler = (e) => {
                if (e.data?.type === "REQUEST_THEME_SYNC") {
                    const currentTheme = this.getCurrentTheme();
                    const msg = {
                        type: "THEME_SYNC",
                        theme: currentTheme.selection,
                        activeTheme: currentTheme.active,
                        timestamp: Date.now(),
                    };

                    try {
                        e.source.postMessage(msg, "*");
                    } catch (err) {
                        // Silent fail - requester might not be accessible
                    }
                }
            };
            window.addEventListener("message", this._messageHandler);

            setTimeout(() => {
                this.broadcast();
                this.broadcastToAllIframes();
            }, 100);

            this._startConditionalBroadcasting();
            this.connectSelector();

            console.log("[ThemeManager] ✅ Initialized with theme:", this.currentTheme);
        }

        _setupQuizMonitoring() {
            this._quizMessageHandler = (event) => {
                if (event.data?.signal === "quiz_started") {
                    this._quizActive = true;
                    this._pauseBroadcasting();
                } else if (event.data?.signal === "quiz_ended") {
                    this._quizActive = false;
                    this._resumeBroadcasting();
                    setTimeout(() => this.broadcastToAllIframes(), 500);
                }
            };
            window.addEventListener("message", this._quizMessageHandler);

            if (window.httpBridgeClient) {
                this._wsQuizStartHandler = () => {
                    this._quizActive = true;
                    this._pauseBroadcasting();
                };

                this._wsQuizEndHandler = () => {
                    this._quizActive = false;
                    this._resumeBroadcasting();
                    setTimeout(() => this.broadcastToAllIframes(), 500);
                };

                window.httpBridgeClient.on("signal:quiz_started", this._wsQuizStartHandler);
                window.httpBridgeClient.on("signal:quiz_ended", this._wsQuizEndHandler);
            }
        }

        _setupWebSocketSync() {
            setTimeout(() => {
                if (window.httpBridgeClient && window.httpBridgeClient.isFullyReady()) {
                    console.log("[ThemeManager] 🔌 WebSocket theme sync enabled");

                    window.httpBridgeClient.on("signal:request_theme_sync", () => {
                        console.log("[ThemeManager] 📨 Received theme request via WS");
                        const theme = this.getSavedTheme();
                        const active = theme === THEMES.AUTO ? this.getSeasonalTheme() : theme;

                        try {
                            window.httpBridgeClient.sendSignalWS("theme_sync", [{
                                theme,
                                activeTheme: active,
                                timestamp: Date.now(),
                            }]);
                        } catch {}
                    });
                }
            }, 1000);
        }

        _startConditionalBroadcasting() {
            // Removed: No longer broadcasting every 5 seconds
            // Theme will broadcast only when it actually changes
        }

        _pauseBroadcasting() {
            if (this._broadcastInterval) {
                clearInterval(this._broadcastInterval);
                this._broadcastInterval = null;
            }
        }

        _resumeBroadcasting() {
            if (!this._broadcastInterval) {
                this._startConditionalBroadcasting();
            }
        }

        broadcastToAllIframes() {
            if (this._quizActive) return;

            const theme = this.getSavedTheme();
            const active = theme === THEMES.AUTO ? this.getSeasonalTheme() : theme;

            // Only broadcast if something changed
            if (this.lastBroadcast === active && this.lastBroadcastTheme === theme) {
                return; // No change, skip broadcast
            }

            this.lastBroadcast = active;
            this.lastBroadcastTheme = theme;

            const msg = {
                type: "THEME_SYNC",
                theme,
                activeTheme: active,
                timestamp: Date.now(),
            };

            console.log("[ThemeManager] 📡 Broadcasting theme change:", active);

            document.querySelectorAll("iframe").forEach((f) => {
                try {
                    f.contentWindow.postMessage(msg, "*");
                } catch (e) {
                    // Silent fail - iframe might not be accessible
                }
            });
        }

        getSavedTheme() {
            return localStorage.getItem(THEME_KEY) || THEMES.AUTO;
        }

        saveTheme(theme) {
            localStorage.setItem(THEME_KEY, theme);
            console.log("[ThemeManager] 💾 Saved:", theme);
        }

        getSeasonalTheme() {
            const now = new Date();
            const m = now.getMonth();
            const d = now.getDate();

            const halloween = m === 9 && d >= 15 && d <= 31;
            const christmas = m === 11 || (m === 0 && d <= 6);

            if (christmas) return THEMES.CHRISTMAS;
            if (halloween) return THEMES.HALLOWEEN;
            return THEMES.DEFAULT;
        }

        applyTheme(themeSelection, save = true, broadcast = true) {
            if (this.isApplying) return;
            this.isApplying = true;

            themeSelection = themeSelection || THEMES.AUTO;
            let active = themeSelection === THEMES.AUTO ? this.getSeasonalTheme() : themeSelection;

            console.log(`[ThemeManager] 🎨 Applying: ${themeSelection} → ${active}`);

            const file = window.location.pathname.split("/").pop();
            const isControls = (file === "quiz_controls");

            /* CONTROLS WINDOW HANDLING */
            if (isControls) {
                console.log("[ThemeManager] 🎛 Controls detected");

                document.body.classList.remove(
                    "theme-default", "theme-halloween", "theme-christmas", "theme-birthday",
                    "default-theme", "halloween-theme", "christmas-theme", "birthday-theme"
                );
                document.body.classList.add(`theme-${active}`, `${active}-theme`);

                if (this._controlsThemeLoaded === active && !this._controlsLoadLock) {
                    console.log("[ThemeManager] 🔒 Already loaded:", active);
                    this.isApplying = false;
                    return;
                }

                if (this._controlsLoadLock) {
                    console.log("[ThemeManager] ⏳ Locked, retrying...");
                    setTimeout(() => this.applyTheme(themeSelection, save, broadcast), 150);
                    this.isApplying = false;
                    return;
                }

                this._controlsLoadLock = true;

                let mount = document.getElementById("controls-theme");
                if (!mount) {
                    mount = document.createElement("div");
                    mount.id = "controls-theme";
                    document.body.appendChild(mount);
                }

                let cssLink = document.getElementById("controls-theme-css");
                if (!cssLink) {
                    cssLink = document.createElement("link");
                    cssLink.id = "controls-theme-css";
                    cssLink.rel = "stylesheet";
                    document.head.appendChild(cssLink);
                }

                let htmlFile, cssFile;

                if (active === THEMES.HALLOWEEN) {
                    htmlFile = "/themes/halloween/controls/controls_halloween.html";
                    cssFile = "/themes/halloween/controls/controls_halloween.css";
                } else if (active === THEMES.CHRISTMAS) {
                    htmlFile = "/themes/christmas/controls/controls_christmas.html";
                    cssFile = "/themes/christmas/controls/controls_christmas.css";
                } else if (active === THEMES.BIRTHDAY) {
                    htmlFile = "/themes/custom/birthday/controls/controls_birthday.html";
                    cssFile = "/themes/custom/birthday/controls/controls_birthday.css";
                } else {
                    mount.innerHTML = "";
                    cssLink.href = "";
                    this._controlsThemeLoaded = active;
                    this._controlsLoadLock = false;
                    this.isApplying = false;
                    return;
                }

                cssLink.href = cssFile;

                fetch(htmlFile)
                    .then(r => r.ok ? r.text() : Promise.reject(r.status))
                    .then(html => {
                        mount.innerHTML = html;
                        this._controlsThemeLoaded = active;
                        this.currentTheme = active;

                        if (save) this.saveTheme(themeSelection);
                        if (broadcast) this.broadcast(themeSelection, active);
                    })
                    .catch(err => {
                        console.error("[ThemeManager] ❌ Controls load failed:", err);
                        this._controlsThemeLoaded = null;
                    })
                    .finally(() => {
                        this._controlsLoadLock = false;
                        this.isApplying = false;
                    });

                return;
            }

            /* NORMAL WINDOWS - JUST APPLY CLASSES */
            const old = [
                "theme-default", "theme-halloween", "theme-christmas", "theme-birthday",
                "default-theme", "halloween-theme", "christmas-theme", "birthday-theme"
            ];

            old.forEach((cls) => {
                document.body.classList.remove(cls);
                document.documentElement.classList.remove(cls);
            });

            document.body.classList.add(`theme-${active}`, `${active}-theme`);
            document.documentElement.classList.add(`theme-${active}`, `${active}-theme`);

            document.querySelectorAll("iframe").forEach((iframe) => {
                try {
                    const doc = iframe.contentDocument || iframe.contentWindow?.document;
                    if (doc?.body) {
                        old.forEach(cls => {
                            doc.body.classList.remove(cls);
                            doc.documentElement.classList.remove(cls);
                        });
                        doc.body.classList.add(`theme-${active}`, `${active}-theme`);
                        doc.documentElement.classList.add(`theme-${active}`, `${active}-theme`);
                    }
                } catch {}
            });

            this.currentTheme = active;

            if (save) this.saveTheme(themeSelection);
            if (broadcast) this.broadcast(themeSelection, active);

            window.dispatchEvent(new CustomEvent("themeChanged", {
                detail: active,
                bubbles: true,
            }));

            setTimeout(() => {
                this.broadcastToAllIframes();
                window.dispatchEvent(new CustomEvent("themeChanged", { detail: active }));
                this.isApplying = false;
            }, 50);
        }

        broadcast(themeSelection, activeTheme) {
            const now = Date.now();
            if (now - this.lastBroadcast < 800) return;
            this.lastBroadcast = now;

            const theme = themeSelection || this.getSavedTheme();
            const active = activeTheme || (theme === THEMES.AUTO ? this.getSeasonalTheme() : theme);

            const msg = {
                type: "THEME_SYNC",
                theme,
                activeTheme: active,
                timestamp: now,
            };

            console.log("[ThemeManager] 📡 Broadcasting theme:", msg);

            if (window.httpBridgeClient && window.httpBridgeClient.isWebSocketConnected()) {
                try {
                    window.httpBridgeClient.sendSignalWS("theme_sync", [{
                        theme,
                        activeTheme: active,
                        timestamp: now,
                    }]);
                } catch {}
            }

            document.querySelectorAll("iframe").forEach((f) => {
                try { f.contentWindow.postMessage(msg, "*"); } catch {}
            });

            window.postMessage(msg, "*");

            if (window.parent !== window) {
                try { window.parent.postMessage(msg, "*"); } catch {}
            }

            if (window.opener) {
                try { window.opener.postMessage(msg, "*"); } catch {}
            }

            window.dispatchEvent(new CustomEvent("themeChanged", { detail: active }));
        }

        setTheme(theme) {
            if (!Object.values(THEMES).includes(theme)) {
                console.warn("[ThemeManager] ⚠️ Invalid theme:", theme);
                return;
            }

            console.log("[ThemeManager] 🎯 User selected:", theme);

            this.saveTheme(theme);
            this.applyTheme(theme, false, true);

            setTimeout(() => {
                const activeTheme = theme === THEMES.AUTO ? this.getSeasonalTheme() : theme;
                this.broadcast(theme, activeTheme);

                const before = this._quizActive;
                this._quizActive = false;
                this.broadcastToAllIframes();
                this._quizActive = before;
            }, 100);
        }

        forceTheme(themeName) {
            console.log(`[ThemeManager] 🔧 FORCE: ${themeName}`);
            if (!Object.values(THEMES).includes(themeName)) {
                console.error("[ThemeManager] ❌ Invalid theme:", themeName);
                return;
            }
            this.setTheme(themeName);
        }

        connectSelector() {
            const inIframe = window.self !== window.top;
            const isDisplay =
                location.pathname.includes("display") ||
                location.pathname.includes("leaderboard") ||
                location.pathname.includes("quiz_display");

            if (inIframe || isDisplay) return;

            setTimeout(() => {
                const select = document.getElementById("theme-select");
                if (select) {
                    select.value = this.getSavedTheme();
                    select.addEventListener("change", (e) => {
                        this.setTheme(e.target.value);
                        this.showNotification(this.currentTheme);
                    });
                }
            }, 300);
        }

        showNotification(theme) {
            const div = document.createElement("div");
            div.className = "theme-popup";

            let emoji = "✨";
            if (theme === "halloween") emoji = "🎃";
            if (theme === "christmas") emoji = "🎄";
            if (theme === "birthday") emoji = "🎂";

            div.textContent = `${emoji} ${theme.toUpperCase()} THEME!`;

            div.style.cssText = `
                position: fixed;
                bottom: 20px;
                right: 20px;
                background: rgba(0,0,0,0.85);
                color: #00ffc8;
                padding: 12px 18px;
                border-radius: 8px;
                z-index: 99999;
                font-weight: 600;
                font-size: 14px;
                border: 2px solid #00ffc8;
            `;

            document.body.appendChild(div);
            setTimeout(() => div.remove(), 2500);
        }

        getCurrentTheme() {
            return {
                selection: this.getSavedTheme(),
                active: this.currentTheme,
                seasonal: this.getSeasonalTheme(),
            };
        }

        cleanup() {
            // Clear broadcast interval
            if (this._broadcastInterval) {
                clearInterval(this._broadcastInterval);
                this._broadcastInterval = null;
            }

            // Remove event listeners
            if (this._storageHandler) {
                window.removeEventListener("storage", this._storageHandler);
                this._storageHandler = null;
            }

            if (this._messageHandler) {
                window.removeEventListener("message", this._messageHandler);
                this._messageHandler = null;
            }

            if (this._quizMessageHandler) {
                window.removeEventListener("message", this._quizMessageHandler);
                this._quizMessageHandler = null;
            }

            // Remove WebSocket handlers
            if (window.httpBridgeClient) {
                if (this._wsQuizStartHandler) {
                    window.httpBridgeClient.off("signal:quiz_started", this._wsQuizStartHandler);
                    this._wsQuizStartHandler = null;
                }

                if (this._wsQuizEndHandler) {
                    window.httpBridgeClient.off("signal:quiz_ended", this._wsQuizEndHandler);
                    this._wsQuizEndHandler = null;
                }

                if (this._wsThemeSyncHandler) {
                    window.httpBridgeClient.off("signal:theme_sync", this._wsThemeSyncHandler);
                    this._wsThemeSyncHandler = null;
                }
            }

            console.log("[ThemeManager] Cleanup complete");
        }
    }

    window.ThemeManager = ThemeManager;
})();

/* =============================================================================
   PHASE 4: OPENMIC OVERLAY LOADER (ONLY FOR OPENMIC)
   ============================================================================= */
(function () {
    "use strict";
    if (window.OPENMIC_LOADER_ACTIVE) return;
    window.OPENMIC_LOADER_ACTIVE = true;

    console.log("[ThemeFX] OpenMic loader checking...");

    function waitForClientType(callback) {
        const check = () => {
            if (window.CLIENT_TYPE) {
                console.log("[ThemeFX] CLIENT_TYPE ready:", window.CLIENT_TYPE);
                callback();
            } else {
                requestAnimationFrame(check);
            }
        };
        check();
    }

    waitForClientType(startOpenMicLoader);

    function startOpenMicLoader() {
        if (window.CLIENT_TYPE !== "openmic_overlay") {
            console.log("[ThemeFX] ⛔ Not openmic_overlay → Skipping");
            return;
        }

        console.log("[ThemeFX] ✅ OpenMic overlay CONFIRMED — enabling theme loader");

        let currentTheme = null;
        window.cleanupThemeFX = window.cleanupThemeFX || null;

        function resolveTheme(t) {
            if (!t || t === "default" || t === "auto") return "default";
            return t;
        }

        function clearOldFX() {
            if (typeof window.cleanupThemeFX === "function") {
                try { window.cleanupThemeFX(); } catch {}
                window.cleanupThemeFX = null;
            }
            document.querySelectorAll(".theme-module").forEach(el => el.remove());
        }

        function getOverlayBase(t) {
            return `/overlays/openmicquiz/themes/${t}/display-openmic-${t}`;
        }

        function loadThemeFX(themeRaw) {
            const theme = resolveTheme(themeRaw);

            if (theme === currentTheme) {
                console.log("[ThemeFX] Theme already loaded:", theme);
                return;
            }

            currentTheme = theme;
            clearOldFX();

            const base = getOverlayBase(theme);

            console.log("[ThemeFX] Loading overlay theme:", theme, "from:", base);

            /* HTML */
            fetch(`${base}.html`)
                .then(async r => r.ok ? await r.text() : "")
                .then(html => {
                    if (!html) {
                        console.error("[ThemeFX] Missing HTML:", base + ".html");
                        return;
                    }

                    const wrap = document.createElement("div");
                    wrap.className = "theme-module";
                    wrap.innerHTML = html;
                    document.body.appendChild(wrap);

                    console.log("[ThemeFX] HTML injected (overlay), length:", html.length);
                });

            /* CSS */
            const css = document.createElement("link");
            css.rel = "stylesheet";
            css.className = "theme-module";
            css.href = `${base}.css`;
            css.onload = () => {
                console.log("[ThemeFX] CSS loaded:", css.href);
            };
            css.onerror = () => {
                console.warn("[ThemeFX] CSS missing:", css.href);
                css.href = "";
            };
            document.head.appendChild(css);

            /* JS FX */
            const js = document.createElement("script");
            js.className = "theme-module";
            js.src = `${base}-fx.js`;
            js.onload = () => {
                console.log("[ThemeFX] FX JS loaded:", js.src);
            };
            js.onerror = () => {
                console.warn("[ThemeFX] FX JS missing:", js.src);
            };
            document.body.appendChild(js);
        }

        /* Listen for THEME_SYNC */
        window.addEventListener("message", (e) => {
            if (!e.data || e.data.type !== "THEME_SYNC") return;
            console.log("[ThemeFX] THEME_SYNC received:", e.data);
            loadThemeFX(e.data.activeTheme || e.data.theme);
        });

        /* Request initial theme */
        if (window.self !== window.top) {
            setTimeout(() => {
                console.log("[ThemeFX] Requesting theme from parent...");
                window.parent.postMessage({ type: "REQUEST_THEME_SYNC" }, "*");
            }, 200);
        }
    }
})();

/* =============================================================================
   PHASE 5: UNIVERSAL DISPLAY FX LOADER (DISPLAY + LEADERBOARD) — FIXED
   ============================================================================= */
(function () {
    "use strict";

    if (window.THEME_LOADERS_BLOCKED) return;

    // OpenMic has its own loader → NEVER load display/leaderboard themes
    if (window.CLIENT_TYPE === "openmic_overlay") {
        console.log("[ThemeFX] Universal loader disabled for OpenMic overlay");
        return;
    }

    function waitForClientType(callback) {
        const check = () => window.CLIENT_TYPE ? callback() : requestAnimationFrame(check);
        check();
    }

    waitForClientType(startUniversalLoader);

    function startUniversalLoader() {

        console.log("[ThemeFX] Universal loader checking for:", window.CLIENT_TYPE);

        const DISPLAY_MAP = {
            "main_display": "display",
            "quiz_display": "display",
            "leaderboard": "leaderboard"
        };

        const type = DISPLAY_MAP[window.CLIENT_TYPE];

        if (!type) {
            console.log("[ThemeFX] ⛔ Not a display/leaderboard window");
            return;
        }

        console.log("[ThemeFX] ✅ Universal loader activated for:", type);

        let currentTheme = null;

        function getBase(theme) {
            // Birthday theme is in custom folder
            if (theme === "birthday") {
                return `/themes/custom/${theme}/${type}/${type}-${theme}`;
            }
            return `/themes/${theme}/${type}/${type}-${theme}`;
        }

        function loadDisplayFX(themeRaw) {
            const theme = themeRaw || "default";

            if (theme === currentTheme) {
                console.log(`[ThemeFX] ${type} already running: ${theme}`);
                return;
            }
            currentTheme = theme;

            console.log(`[ThemeFX] Preparing to load ${type}/${theme}`);

            /* ==========================================================
               ALWAYS CLEAN UP FIRST — THIS FIXES FX BLEED-THROUGH
               ========================================================== */
            if (window.cleanupAllThemeFX) {
                console.log("[ThemeFX] Cleaning old FX before loading new theme...");
                window.cleanupAllThemeFX();
            }
            document.querySelectorAll('.theme-module').forEach(el => el.remove());

            /* ==========================================================
               DEFAULT THEME HAS NO FX
               ========================================================== */
            if (theme === "default") {
                console.log("[ThemeFX] Default theme active — no FX needed");
                return;
            }

            /* ==========================================================
               LOAD DISPLAY THEME: HTML → CSS → JS
               ========================================================== */
            const base = getBase(theme);

            // 1. HTML
            fetch(`${base}.html`)
                .then(r => r.ok ? r.text() : "")
                .then(html => {
                    if (!html) {
                        console.warn(`[ThemeFX] Missing HTML at ${base}.html`);
                        return;
                    }

                    const wrap = document.createElement("div");
                    wrap.className = "theme-module";
                    wrap.innerHTML = html;
                    document.body.appendChild(wrap);
                    console.log(`[ThemeFX] Injected ${type} HTML`);
                    window.dispatchEvent(new Event("THEME_HTML_READY"));
                });

            // 2. CSS
            const css = document.createElement("link");
            css.rel = "stylesheet";
            css.className = "theme-module";
            css.href = `${base}.css`;
            css.onload = () => console.log(`[ThemeFX] ${type} CSS loaded`);
            css.onerror = () => console.warn(`[ThemeFX] Failed to load ${base}.css`);
            document.head.appendChild(css);

            // 3. FX JS
            const js = document.createElement("script");
            js.className = "theme-module";
            js.src = `${base}-fx.js`;
            js.onload = () => console.log(`[ThemeFX] ${type} FX JS loaded`);
            js.onerror = () => console.warn(`[ThemeFX] Failed to load ${base}-fx.js`);
            document.body.appendChild(js);
        }

        /* ==========================================================
           RECEIVE THEME SYNC FROM THEME MANAGER
           ========================================================== */
        window.addEventListener("message", (e) => {
            if (!e.data || e.data.type !== "THEME_SYNC") return;

            console.log(`[ThemeFX] ${type} received THEME_SYNC:`, e.data);
            loadDisplayFX(e.data.activeTheme || e.data.theme);
        });

        /* ==========================================================
           REQUEST INITIAL THEME FROM PARENT
           ========================================================== */
        if (window.self !== window.top) {
            setTimeout(() => {
                console.log(`[ThemeFX] ${type} requesting theme from parent...`);
                window.parent.postMessage({ type: "REQUEST_THEME_SYNC" }, "*");
            }, 200);
        } else {
            console.log(`[ThemeFX] ${type} standalone — waiting for ThemeManager`);
        }
    }
})();
