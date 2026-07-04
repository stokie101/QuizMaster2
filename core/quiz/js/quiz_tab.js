/**
 * Quiz Tab - FIXED VERSION
 * - Only creates ONE instance
 * - Properly finds socket from bridge client
 * - Full debugging
 */
class QuizTab {
    constructor() {
        console.log("🔧 QuizTab constructor called");

        this.apiBase = (window.BRIDGE_ORIGIN || window.location.origin).replace(/\/$/, "");
        console.log("📍 API Base:", this.apiBase);

        this.socket = null;

        this.settingsMap = {
            "timerDuration":       ["TIMER", "duration", "int"],
            "answerDisplayTime":   ["TIMER", "answer_display_time", "int"],
            "enableTimerSound":      ["SOUND", "enable_timer_sound", "bool"],
            "enableBackgroundSound": ["SOUND", "enable_background_sound", "bool"],
            "enableEffectsSound":    ["SOUND", "enable_effects_sound", "bool"],
            "timerVolume":           ["SOUND", "timer_volume", "int"],
            "backgroundVolume":      ["SOUND", "background_volume", "int"],
            "effectsVolume":         ["SOUND", "effects_volume", "int"],
            "timerResponsivePoints": ["POINTS", "timer_responsive_points_enabled", "bool"],
            "fastestFingerEnabled":  ["POINTS", "fastest_finger_enabled", "bool"],
            "maxPoints":             ["POINTS", "max_points", "int"],
            "minPoints":             ["POINTS", "min_points", "int"],
            "fastestFingerBonus":    ["POINTS", "fastest_finger_bonus", "int"],
            "fastestFingerThresholdSeconds": ["POINTS", "fastest_finger_threshold_seconds", "int"],
            "maxIncorrectAttempts":  ["POINTS", "max_incorrect_attempts", "int"]
        };

        console.log("📋 Settings map created:", Object.keys(this.settingsMap));

        this.init();
    }

    async init() {
        console.log("⚙️ QuizTab.init() starting...");

        console.log("🔍 Checking for DOM elements...");
        Object.keys(this.settingsMap).forEach(id => {
            const el = document.getElementById(id);
            console.log(`  - ${id}: ${el ? '✅ Found' : '❌ Missing'}`);
        });

        await this.loadSettings();
        this.bindEvents();
        this.initSocket();

        console.log("✅ QuizTab.init() complete");
    }

    initSocket() {
        console.log("🔌 Initializing socket...");
        console.log("window.httpBridgeClient:", window.httpBridgeClient);

        if (window.httpBridgeClient) {
            // ✅ FIX: Socket is stored as _io, not socket!
            const bridgeSocket = window.httpBridgeClient._io;

            console.log("Bridge client _io:", bridgeSocket);

            if (bridgeSocket && bridgeSocket.connected) {
                this.socket = bridgeSocket;
                console.log("✅ Socket connected:", this.socket.id);

                this.socket.on('settings_changed', (data) => {
                    console.log("🔥 Remote settings update:", data);
                    this.updateUIFromData(data, true);
                });

                return; // Success!
            }
        }

        console.log("⏳ Socket not ready, retrying...");

        // Stop retrying after 30 attempts (15 seconds)
        if (!this._socketRetries) this._socketRetries = 0;
        this._socketRetries++;

        if (this._socketRetries > 30) {
            console.warn("⚠️ Socket connection failed after 30 attempts - continuing without live updates");
            return;
        }

        setTimeout(() => this.initSocket(), 500);
    }

    async loadSettings() {
        console.log("=".repeat(60));
        console.log("📥 Loading settings from server...");

        try {
            const url = `${this.apiBase}/api/settings`;
            console.log("🌐 Fetching from:", url);

            const res = await fetch(url);
            console.log("📡 Response status:", res.status, res.statusText);

            const json = await res.json();
            console.log("📦 Response data:", json);

            if (json.success) {
                console.log("✅ Settings loaded successfully");
                console.log("📊 Settings object:", json.settings);

                Object.keys(json.settings).forEach(section => {
                    console.log(`  [${section}]:`, json.settings[section]);
                });

                this.updateUIFromData(json.settings, true);
            } else {
                console.error("❌ Settings load failed:", json.error);
            }
        } catch (e) {
            console.error("❌ Load settings error:", e);
        }

        console.log("=".repeat(60));
    }

    updateUIFromData(settings, isProgrammatic = false) {
        console.log("🎨 Updating UI from data...");
        console.log("isProgrammatic:", isProgrammatic);

        this.isUpdatingUI = true;

        let updatedCount = 0;
        let missingCount = 0;

        Object.keys(this.settingsMap).forEach(domId => {
            const [section, key, type] = this.settingsMap[domId];

            console.log(`\n🔍 Processing ${domId}:`);
            console.log(`  - Section: ${section}, Key: ${key}, Type: ${type}`);

            if (!settings[section]) {
                console.warn(`  ⚠️ Section [${section}] not in settings`);
                missingCount++;
                return;
            }

            if (settings[section][key] === undefined) {
                console.warn(`  ⚠️ Key [${key}] not in section [${section}]`);
                console.log(`  Available keys:`, Object.keys(settings[section]));
                missingCount++;
                return;
            }

            const val = settings[section][key];
            console.log(`  ✅ Found value: ${val}`);

            const el = document.getElementById(domId);

            if (!el) {
                console.warn(`  ⚠️ DOM element #${domId} not found`);
                missingCount++;
                return;
            }

            console.log(`  📝 Element type: ${el.type}`);

            if (type === "bool") {
                const boolVal = (String(val).toLowerCase() === "true");
                el.checked = boolVal;
                console.log(`  ✅ Set checkbox to: ${boolVal}`);
            } else {
                el.value = val;
                console.log(`  ✅ Set value to: ${val}`);
            }

            updatedCount++;
        });

        console.log(`\n📊 Update summary: ${updatedCount} updated, ${missingCount} missing`);

        this.updateVolumeDisplays();

        setTimeout(() => {
            this.isUpdatingUI = false;
            console.log("🔓 isUpdatingUI unlocked");
        }, 50);
    }

    bindEvents() {
        console.log("🔗 Binding events to inputs...");

        let boundCount = 0;

        Object.keys(this.settingsMap).forEach(domId => {
            const el = document.getElementById(domId);

            if (!el) {
                console.warn(`  ⚠️ Cannot bind ${domId} - element not found`);
                return;
            }

            if (el.type === "range") {
                el.addEventListener("input", (e) => {
                    console.log(`📊 Slider changed: ${domId} = ${e.target.value}`);
                    this.updateVolumeDisplays();
                    this.debounceSave(domId);
                });
                console.log(`  ✅ Bound slider: ${domId}`);
            } else {
                el.addEventListener("change", (e) => {
                    console.log(`📝 Input changed: ${domId} = ${e.target.value}`);
                    this.saveSingleSetting(domId);
                });
                console.log(`  ✅ Bound input: ${domId}`);
            }

            boundCount++;
        });

        console.log(`✅ Bound ${boundCount} event listeners`);
    }

    debounceSave(domId) {
        console.log(`⏳ Debouncing save for ${domId}...`);
        if (this.debounceTimer) clearTimeout(this.debounceTimer);
        this.debounceTimer = setTimeout(() => this.saveSingleSetting(domId), 300);
    }

    async saveSingleSetting(domId) {
        console.log("=".repeat(60));
        console.log(`💾 SAVE TRIGGERED for ${domId}`);

        if (this.isUpdatingUI) {
            console.log("⏸️ Skipping save - UI is being updated programmatically");
            return;
        }

        const [section, key, type] = this.settingsMap[domId];
        console.log(`Section: ${section}, Key: ${key}, Type: ${type}`);

        const el = document.getElementById(domId);
        if (!el) {
            console.error(`❌ Element ${domId} not found`);
            return;
        }

        let value;
        if (type === "bool") {
            value = el.checked;
        } else if (type === "int") {
            value = parseInt(el.value, 10);
        } else {
            value = el.value;
        }

        console.log(`📦 Value to save: ${value} (type: ${typeof value})`);

        const payload = {
            [section]: {
                [key]: value
            }
        };

        console.log("📤 Payload:", JSON.stringify(payload, null, 2));

        try {
            const url = `${this.apiBase}/api/settings`;
            console.log(`🌐 Posting to: ${url}`);

            const res = await fetch(url, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            });

            console.log("📡 Response status:", res.status, res.statusText);

            const json = await res.json();
            console.log("📦 Response data:", json);

            if (json.success) {
                console.log("✅ Save successful!");
                this.showStatus(true);
            } else {
                console.error("❌ Save failed:", json);
                this.showStatus(false);
            }

        } catch (e) {
            console.error("❌ Save error:", e);
            this.showStatus(false);
        }

        console.log("=".repeat(60));
    }

    updateVolumeDisplays() {
        ["timer", "background", "effects"].forEach(type => {
            const el = document.getElementById(type + "Volume");
            const disp = document.querySelector(`#${type}Volume + .volume-display`);
            if(el && disp) {
                disp.textContent = el.value;
            }
        });
    }

    showStatus(success) {
        const status = document.getElementById("settingsStatus");
        if (status) {
            status.textContent = success ? "✅ Saved" : "❌ Error";
            status.style.opacity = "1";
            setTimeout(() => status.style.opacity = "0", 2000);
        }
    }
}

// ✅ FIX: Only create instance if not already created
console.log("🚀 Quiz Tab script loaded");

window.QuizTab = QuizTab;

// Prevent double instantiation
if (!window.quizTabInstance) {
    if (document.readyState === "complete" || document.readyState === "interactive") {
        console.log("✅ DOM ready, creating QuizTab immediately");
        window.quizTabInstance = new QuizTab();
    } else {
        console.log("⏳ Waiting for DOMContentLoaded...");
        document.addEventListener("DOMContentLoaded", () => {
            if (!window.quizTabInstance) {
                console.log("✅ DOMContentLoaded fired, creating QuizTab");
                window.quizTabInstance = new QuizTab();
            }
        });
    }
}