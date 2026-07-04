"use strict";
console.log("✅ quiz_display.js loaded and running");
(function () {
  class PythonLogger {
    constructor(name) { this.name = name; }
    _tag(lvl) { return "[" + this.name + "] " + lvl + ": "; }
    info(msg, a, b, c, d) { try { console.log(this._tag("INFO") + String(msg), a, b, c, d); } catch (e) { console.log(this._tag("INFO") + String(msg)); } }
    debug(msg, a, b, c, d) { try { console.log(this._tag("DEBUG") + String(msg), a, b, c, d); } catch (e) { console.log(this._tag("DEBUG") + String(msg)); } }
    warn(msg, a, b, c, d) { try { console.warn(this._tag("WARN") + String(msg), a, b, c, d); } catch (e) { console.warn(this._tag("WARN") + String(msg)); } }
    error(msg, a, b, c, d) { try { console.error(this._tag("ERROR") + String(msg), a, b, c, d); } catch (e) { console.error(this._tag("ERROR") + String(msg)); } }
  }
  window.quizDisplayReady = false;

  class QuizDisplay {
    constructor(options) {
      if (QuizDisplay._instance) throw new Error("QuizDisplay is a singleton. Use getInstance().");
      options = options || {};

      if (options.services) {
        const services = options.services;

        if (services.bridgeClient && !window.httpBridgeClient) {
          window.httpBridgeClient = services.bridgeClient;
          console.log("[QuizDisplay] 🌉 Attached injected bridgeClient to window.httpBridgeClient");
        }

        if (services.serviceLocator && !window.ServiceLocator) {
          window.ServiceLocator = {
            get_instance: () => services.serviceLocator
          };
          console.log("[QuizDisplay] 🧭 Attached injected ServiceLocator facade");
        }
      }

      this.logger = new PythonLogger("QuizDisplay");
      this.signals = null;
      this.current_question = null;
      this.answer_labels = [];
      this._initialization_complete = false;
      this._bridgeUnsubs = [];
      this.useGridLayout = true;

      this.root = null; this.quiz_frame = null;
      this.top_container = null; this.top_inner = null;
      this.timer_mount = null; this.image_display = null;
      this.message_display = null; this.question_label = null;
      this.question_text = null; this.answer_container = null; this.branding_label = null;

      // 🚀 PERFORMANCE: Timer optimization with RAF batching
      this._pendingTimerUpdate = null;
      this._lastTimerUpdate = 0;
      this._timerUpdateThrottle = 100;
      this._rafScheduled = false;

      this.circle_timer = null;
      this.quiz_has_started = false;

      this.question_timer_bar = null;
      this.timer_total = 0;
      this.timer_remaining = 0;

      // 🚀 PERFORMANCE: Debounced resize with passive listener
      this._resizeTimeout = null;
      this._onResize = () => {
        if (this._resizeTimeout) clearTimeout(this._resizeTimeout);
        this._resizeTimeout = setTimeout(() => this._update_layout_metrics(), 150);
      };
      window.addEventListener("resize", this._onResize, { passive: true });

      this._init_signal_handlers();
      this._build_dom(options.parent);
      this._initialize_sequence();
    }

    static _instance = null;
    static getInstance(options) {
      if (!QuizDisplay._instance) QuizDisplay._instance = new QuizDisplay(options || {});
      return QuizDisplay._instance;
    }

    _init_signal_handlers() {
      const self = this;
      this._signal_handlers = {
        answers_highlighted(answers) {
          self.logger.info("Signal: answers_highlighted", answers);
          self.highlight_correct_answers(answers);
        },
        showing_answers(answers) {
          self.logger.info("Signal: showing_answers", answers);
          self.highlight_correct_answers(answers);
        },
        answer_display_complete() {
          self.logger.info("Signal: answer_display_complete");
          if (self.quiz_has_started) {
            self.logger.info("🔄 Clearing answers for next question (quiz active)");
            self.clear_answer_labels();
          } else {
            self.reset_display();
          }
        },
        quiz_paused() {
          self.logger.info("Signal: quiz_paused");
        },
        quiz_resumed() {
          self.logger.info("Signal: quiz_resumed");
        },
        quiz_started() {
          console.log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
          console.log("🔔 [QuizDisplay] quiz_started HANDLER CALLED");
          console.log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");

          self.quiz_has_started = true;
          self.logger.info("✅ Quiz officially started");

          if (self.question_text) {
            self.question_text.textContent = "Loading first question...";
          }

          self.on_quiz_started();
          console.log("✅ [QuizDisplay] quiz_started handler COMPLETED");
        },

        quiz_ended() {
          self.quiz_has_started = false;
          self.logger.info("✅ Quiz ended");
          self.on_quiz_ended();
        },

        question_changed(q) {
          console.log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
          console.log("🔔 [QuizDisplay] question_changed HANDLER CALLED");
          console.log("🔔 Quiz started flag:", self.quiz_has_started);
          console.log("🔔 Question data:", q);
          console.log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");

          self.logger.info("Signal: question_changed", q);

          if (!q || typeof q !== 'object') {
            self.logger.error("❌ Invalid question data received:", q);
            return;
          }

          if (!q.question || !q.answers) {
            self.logger.error("❌ Question missing required fields:", q);
            return;
          }

          self.current_question = q;
          self.load_current_question(q);

          console.log("✅ [QuizDisplay] question_changed handler COMPLETED");
        },

        message_ready(msg) {
          self.logger.info("Signal: message_ready", msg);
          self.show_message(msg);
        },
        state_changed(st) {
          self.logger.info("Signal: state_changed = " + st);
          self.on_state_changed(st);
        },
        timer_started(duration) {
          self.logger.info("⏱️ timer_started: " + duration + "s");
          self._startQuestionTimer(duration);
        },
        timer_tick(remaining) {
          self._updateQuestionTimer(remaining);
        },
        timer_expired() {
          // # Fix: Canonical timer completion signal.
          self._endQuestionTimer();
        },
        timer_ended() {
          // # Fix: Legacy alias maintained for backward compatibility.
          self._endQuestionTimer();
        },
        timer_paused() { self.logger.info("Timer paused"); },
        timer_resumed() { self.logger.info("Timer resumed"); },
      };
    }

    // 🚀 OPTIMIZED: Ultra-efficient timer with single RAF loop
    _startQuestionTimer(duration) {
      this.timer_total = Number(duration) || 30;
      this.timer_remaining = this.timer_total;
      this._updateQuestionTimerBar();
    }

    _updateQuestionTimer(remaining) {
      this.timer_remaining = Math.max(0, Number(remaining) || 0);

      // 🚀 Skip if already scheduled
      if (this._rafScheduled) return;

      // 🚀 Throttle to ~10 FPS for timer (enough for smooth appearance)
      const now = performance.now();
      if (now - this._lastTimerUpdate < this._timerUpdateThrottle) return;

      this._rafScheduled = true;
      requestAnimationFrame(() => {
        this._rafScheduled = false;
        this._lastTimerUpdate = performance.now();
        this._updateQuestionTimerBarOptimized();
      });
    }

    // 🚀 HYPER-OPTIMIZED: Minimal DOM manipulation, GPU-accelerated transforms
    _updateQuestionTimerBarOptimized() {
      if (!this.question_label) return;

      const remaining = Math.max(0, this.timer_remaining || 0);
      const total = Math.max(1, this.timer_total || 30);
      const percentRemaining = (remaining / total) * 100;

      // Create bar only once
      if (!this.question_timer_bar) {
        this.question_timer_bar = document.createElement('div');
        this.question_timer_bar.className = 'question-timer-bar';
        this.question_label.insertBefore(
          this.question_timer_bar,
          this.question_label.firstChild
        );
        this.question_label.classList.add('has-real-bar');
      }

      // 🚀 USE GPU: transform instead of width (way faster!)
      const scaleX = percentRemaining / 100;
      this.question_timer_bar.style.transform = `scaleX(${scaleX})`;
      this.question_timer_bar.style.transformOrigin = 'left';

      // 🚀 Batch class updates
      const isWarning = percentRemaining <= 30 && percentRemaining > 10;
      const isCritical = percentRemaining <= 10;

      // Only update if state actually changed
      const label = this.question_label;
      const hasWarning = label.classList.contains('timer-warning');
      const hasCritical = label.classList.contains('timer-critical');

      if (isCritical !== hasCritical || isWarning !== hasWarning) {
        label.classList.toggle('timer-warning', isWarning);
        label.classList.toggle('timer-critical', isCritical);
      }
    }

    _endQuestionTimer() {
      this.timer_remaining = 0;
      this._updateQuestionTimerBar();
    }

    _updateQuestionTimerBar() {
      if (!this.question_label) return;

      const remaining = Math.max(0, this.timer_remaining || 0);
      const total = Math.max(1, this.timer_total || 30);
      const percentRemaining = (remaining / total) * 100;

      if (!this.question_timer_bar) {
        this.question_timer_bar = document.createElement('div');
        this.question_timer_bar.className = 'question-timer-bar';
        this.question_label.insertBefore(this.question_timer_bar, this.question_label.firstChild);
        this.question_label.classList.add('has-real-bar');
        this.logger.info("✅ Created question timer bar element");
      }

      // 🚀 GPU-accelerated
      this.question_timer_bar.style.transform = `scaleX(${percentRemaining / 100})`;
      this.question_timer_bar.style.transformOrigin = 'left';

      this.question_label.classList.remove('timer-warning', 'timer-critical');
      if (percentRemaining <= 10) {
        this.question_label.classList.add('timer-critical');
      } else if (percentRemaining <= 30) {
        this.question_label.classList.add('timer-warning');
      }

      this.logger.debug("Timer bar updated: " + percentRemaining.toFixed(1) + "% (" + remaining.toFixed(1) + "s remaining)");
    }

    _initialize_sequence() {
      const self = this;

      if (self._initialization_started) {
        self.logger.warn("⚠️ Initialization already in progress, skipping");
        return;
      }
      self._initialization_started = true;

      self.logger.info("============================================================");
      self.logger.info("🚀 Starting QuizDisplay initialization...");
      self.logger.info("============================================================");
      const afterBridge = () => {
        self._wait_for_service_locator().then(() => {
          self.logger.info("✓ ServiceLocator ready");
          self._connect_signals_async().then(() => {
            self._register_with_service_locator();
            self._notify_display_ready();
            self._update_layout_metrics();
            self._initialization_complete = true;
            self.logger.info("✅ QuizDisplay initialization COMPLETE");
          });
        });
      };
      try {
        if (window.httpBridgeClient && typeof window.httpBridgeClient.waitForReady === "function") {
          window.httpBridgeClient.waitForReady(15000).then((ready) => {
            if (!ready) {
              self.logger.error("❌ Bridge client not ready after 15s");
              afterBridge();
              return;
            }
            self.logger.info("✅ Bridge client ready");
            afterBridge();
          });
        } else {
          afterBridge();
        }
      } catch (e) {
        self.logger.error("Initialization failed: " + e);
        console.error("Full error:", e);
      }
    }

    _wait_for_service_locator() {
      const self = this;
      return new Promise((resolve) => {
        function check() {
          try {
            const locator = window.ServiceLocator && window.ServiceLocator.get_instance && window.ServiceLocator.get_instance();
            if (locator) {
              self.logger.info("✓ ServiceLocator found");
              resolve(locator);
              return;
            }
          } catch (e) {}
          setTimeout(check, 50);
        }
        check();
      });
    }

    _connect_signals_async() {
      const self = this;

      if (self._signals_connected) {
        self.logger.warn("⚠️ Signals already connected, skipping");
        return Promise.resolve(true);
      }

      return new Promise((resolve) => {
        const wanted = [
          "answers_highlighted", "showing_answers", "answer_display_complete",
          "quiz_started", "quiz_ended", "quiz_paused", "quiz_resumed",
          "question_changed", "message_ready", "state_changed",
          "timer_started", "timer_paused", "timer_resumed", "timer_tick", "timer_ended"
        ];
        function tryQuizSignals() {
          try {
            const loc = window.ServiceLocator && window.ServiceLocator.get_instance && window.ServiceLocator.get_instance();
            const signals = loc && typeof loc.get_service === "function" ? loc.get_service("QuizSignals") : null;
            if (signals) {
              self.signals = signals;
              self._disconnect_bridge_fallback();
              let connected = 0;
              for (let i = 0; i < wanted.length; i++) {
                const name = wanted[i];
                const handler = self._signal_handlers[name] || self._make_handler_for(name);
                try {
                  if (typeof signals.connect_signal === "function") {
                    signals.connect_signal(name, handler, "quiz_display");
                    connected++;
                  }
                } catch (e) {
                  self.logger.error("Failed to connect signal '" + name + "': " + e);
                }
              }
              self._signals_connected = true;
              self.logger.info("✓ Connected " + connected + "/" + wanted.length + " signals");
              resolve(true);
              return true;
            }
          } catch (e) {}
          return false;
        }
        if (tryQuizSignals()) return;
        self._connect_bridge_fallback(wanted);
        let attempts = 0;
        (function poll() {
          attempts++;
          if (tryQuizSignals()) return;
          setTimeout(poll, 50);
        })();
      });
    }

    _connect_bridge_fallback(names) {
      try {
        const client = window.httpBridgeClient;
        if (!client || typeof client.on !== "function") return;
        for (let i = 0; i < names.length; i++) {
          const name = names[i];
          const handler = this._signal_handlers[name] || this._make_handler_for(name);
          const off = client.on("signal:" + name, (...args) => {
            try {
              handler.apply(null, args);
            } catch (e) {
              try { this.logger && this.logger.error("Bridge handler error for " + name + ": " + e); } catch (_e) {}
            }
          });
          this._bridgeUnsubs.push(off);
        }
      } catch (e) {
        this.logger.error("Bridge fallback error: " + e);
      }
    }

    _make_handler_for(name) {
      const self = this;
      return function () {
        self.logger.debug("Unhandled signal '" + name + "' with args:", arguments);
      };
    }

    _disconnect_bridge_fallback() {
      if (Object.prototype.toString.call(this._bridgeUnsubs) === "[object Array]") {
        for (let i = 0; i < this._bridgeUnsubs.length; i++) {
          const off = this._bridgeUnsubs[i];
          try { if (typeof off === "function") off(); } catch (e) {}
        }
      }
      this._bridgeUnsubs = [];
    }

    _register_with_service_locator() {
      try {
        const locator = window.ServiceLocator && window.ServiceLocator.get_instance && window.ServiceLocator.get_instance();
        if (locator && !locator.get_service("QuizDisplay") && typeof locator.register_service === "function") {
          locator.register_service("QuizDisplay", this);
          this.logger.info("✓ Registered with ServiceLocator");
        }
      } catch (e) {
        this.logger.error("ServiceLocator registration error: " + e);
      }
    }

    _notify_display_ready() {
      try {
        window.quizDisplayReady = true;
        if (this.signals && typeof this.signals.emit_signal === "function") {
          this.signals.emit_signal("quiz_display_ready");
        }
        if (window.pythonQuizManager && typeof window.pythonQuizManager.quiz_display_ready === "function") {
          window.pythonQuizManager.quiz_display_ready();
        }
      } catch (e) {
        this.logger.error("Display ready notification error: " + e);
      }
    }

    _build_dom(parent) {
      this.root = document.createElement("div");
      this.root.className = "qd-root";
      this.root.style.width = "100%";
      this.root.style.height = "100%";

      this.quiz_frame = document.createElement("div");
      this.quiz_frame.id = "quizFrame";

      this.top_container = document.createElement("div");
      this.top_container.className = "qd-top";
      this.top_container.style.display = "none";

      this.timer_mount = document.createElement("div");
      this.timer_mount.className = "qd-timer-mount";

      this.image_display = document.createElement("img");
      this.image_display.className = "qd-image";
      this.image_display.alt = "Question image";
      this.image_display.crossOrigin = "anonymous";

      this.top_inner = document.createElement("div");
      this.top_inner.className = "qd-top-inner";
      this.top_inner.appendChild(this.timer_mount);
      this.top_inner.appendChild(this.image_display);
      this.top_container.appendChild(this.top_inner);

      const message_container = document.createElement("div");
      message_container.className = "qd-message-container";

      this.message_display = document.createElement("div");
      this.message_display.id = "messageDisplay";
      message_container.appendChild(this.message_display);

      this.question_label = document.createElement("div");
      this.question_label.id = "questionLabel";

      this.question_text = document.createElement("span");
      this.question_text.className = "question-text";
      this.question_text.textContent = "Get Ready!";
      this.question_label.appendChild(this.question_text);

      this.answer_container = document.createElement("div");
      this.answer_container.className = "qd-answers";

      this.branding_label = document.createElement("div");
      this.branding_label.id = "brandingLabel";
      this.branding_label.textContent = "🔹 Powered by QuizMaster 🔹";

      this.quiz_frame.appendChild(this.top_container);
      this.quiz_frame.appendChild(message_container);
      this.quiz_frame.appendChild(this.question_label);
      this.quiz_frame.appendChild(this.answer_container);
      this.quiz_frame.appendChild(this.branding_label);

      this.root.appendChild(this.quiz_frame);
      (parent instanceof HTMLElement ? parent : document.body).appendChild(this.root);
    }

    // 🚀 OPTIMIZED: Heavy debouncing for layout updates
    _update_layout_metrics() {
      if (!this._initialization_complete) return;

      if (this._layoutUpdateTimeout) {
        clearTimeout(this._layoutUpdateTimeout);
      }

      this._layoutUpdateTimeout = setTimeout(() => {
        this._doLayoutUpdate();
      }, 150); // Increased from 50ms for better batching
    }

    // 🚀 OPTIMIZED: Read-then-write pattern to prevent layout thrashing
    _doLayoutUpdate() {
      try {
        const hasImage = !!(
          this.image_display &&
          this.image_display.style.display !== "none"
        );

        // 🚀 READ phase (batch all reads)
        const frameH = this.quiz_frame.clientHeight || 600;

        // 🚀 WRITE phase (batch all writes in RAF)
        requestAnimationFrame(() => {
          if (hasImage) {
            const topH = Math.max(120, Math.min(220, Math.round(frameH * 0.22)));
            this.top_container.style.display = "flex";
            this.top_container.style.minHeight = topH + "px";
            document.body.classList.add("qd-has-image");
          } else {
            this.top_container.style.minHeight = "0px";
            this.top_container.style.display = "none";
            document.body.classList.remove("qd-has-image");
          }
        });
      } catch (e) {
        this.logger.error("Layout metrics error: " + e);
      }
    }

    show_timer() {
      if (this.message_display) this.message_display.style.display = "none";
    }

    show_message(message) {
      try {
        if (this.message_display) {
          this.message_display.style.display = "block";
          if (message != null) this.message_display.textContent = String(message);
        }
      } catch (e) {
        this.logger.error("Show message error: " + e);
      }
    }

    reset_display() {
      try {
        this.clear_answer_labels();

        if (this.question_text) {
          this.question_text.textContent = "Get Ready!";
        }

        this._clear_image();
        if (this.message_display) this.message_display.style.display = "none";

        this.show_timer();
        this.current_question = null;
        this._update_layout_metrics();
      } catch (e) {
        this.logger.error("Reset display error: " + e);
      }
    }

    on_quiz_started() {
      try {
        this.quiz_has_started = true;
        this.reset_display();
      }
      catch (e) { this.logger.error("Quiz started handler error: " + e); }
    }

    on_quiz_ended() {
      try {
        this.quiz_has_started = false;
        this.clear_answer_labels();
        if (this.question_text) this.question_text.textContent = "Quiz Complete! Thanks for playing!";
        this._clear_image();
        this.current_question = null;
        this._update_layout_metrics();

        this.logger.info("Quiz ended - handlers remain connected for next quiz");
      } catch (e) {
        this.logger.error("Quiz ended handler error: " + e);
      }
    }

    on_state_changed(new_state) {
      try {
        const st = String(new_state || "").toLowerCase();
        if (st === "question_active" || st === "running") {
          this.show_timer();
        }
      } catch (e) {
        this.logger.error("State change handler error: " + e);
      }
    }

    load_current_question(question_data) {
      try {
        if (!question_data) {
          this.logger.error("❌ load_current_question called with no data");
          return;
        }

        if (typeof question_data !== 'object') {
          this.logger.error("❌ Question data is not an object:", question_data);
          return;
        }

        if (!question_data.question) {
          this.logger.error("❌ Question data missing 'question' field:", question_data);
          return;
        }

        if (!Array.isArray(question_data.answers)) {
          this.logger.error("❌ Question data missing 'answers' array:", question_data);
          return;
        }

        this.logger.info("✅ Loading question:", question_data.question.substring(0, 50) + "...");

        this.current_question = question_data;
        this.show_timer();
        this._handle_image_display(question_data);
        this._update_question_text(question_data);
        this.clear_answer_labels();
        this.create_answer_labels();
        this._update_layout_metrics();

        this.logger.info("✅ Question loaded successfully");
      } catch (e) {
        this.logger.error("❌ Error loading question:", e);

        if (this.question_text) {
          this.question_text.textContent = "Error loading question - please skip";
        }
      }
    }

    _resolveAssetUrl(path) {
      try {
        if (!path) return "";
        const s = String(path).trim();
        if (/^(data:|blob:|https?:\/\/)/i.test(s)) return s;
        const base = window.BRIDGE_ORIGIN || window.location.origin;
        if (s.startsWith("/")) return new URL(s, base).toString();
        return new URL(s, base + "/").toString();
      } catch (e) {
        this.logger.error("URL resolve error for: " + path + " -> " + e);
        return String(path || "");
      }
    }

    // 🚀 OPTIMIZED: Image preloading with memory cleanup
    _preloadImage(url) {
      return new Promise((resolve, reject) => {
        const img = new Image();
        img.crossOrigin = "anonymous";

        const cleanup = () => {
          img.onload = null;
          img.onerror = null;
        };

        img.onload = () => {
          cleanup();
          resolve(url);
        };

        img.onerror = (e) => {
          cleanup();
          reject(e);
        };

        img.src = url + (url.includes("?") ? "&" : "?") + "cb=" + Date.now();
      });
    }

    _handle_image_display(question_data) {
      try {
        const wantsImage = !!(question_data && question_data.is_picture && question_data.image_path);
        if (!wantsImage) {
          this._clear_image();
          this._update_layout_metrics();
          return;
        }

        const resolved = this._resolveAssetUrl(question_data.image_path);
        this._preloadImage(resolved)
          .then((finalUrl) => {
            this.image_display.src = finalUrl;
            this.image_display.style.display = "block";
            document.body.classList.add("qd-has-image");
            this._update_layout_metrics();
          })
          .catch((err) => {
            this.logger.warn("Image failed to load:", resolved, err);
            this._clear_image();
            this._update_layout_metrics();
          });
      } catch (e) {
        this.logger.error("Image display error: " + e);
      }
    }

    _update_question_text(question_data) {
      const text = (question_data && question_data.question) ? String(question_data.question) : "Question missing!";
      if (this.question_text) this.question_text.textContent = text;
    }

    create_answer_labels() {
      try {
        if (!this.current_question || !this.current_question.answers) return;
        const answers = this.current_question.answers;
        this.answer_labels = [];
        for (let idx = 0; idx < answers.length; idx++) {
          const ans = String(answers[idx] == null ? "" : answers[idx]);
          const el = document.createElement("div");
          el.className = "qd-answer";
          const parts = ans.split(",");
          if (parts.length >= 2) {
            const letter = (parts[0] || "").trim();
            const text = parts.slice(1).join(",").trim();
            el.dataset.raw_answer = (letter || "").toLowerCase().replace(/[.]+$/, "");
            el.textContent = (letter ? letter + ". " : "") + text;
          } else {
            const letter2 = String.fromCharCode(65 + idx);
            const text2 = ans.trim();
            el.dataset.raw_answer = (text2 || "").toLowerCase().replace(/[.]+$/, "");
            el.textContent = letter2 + ". " + text2;
          }

          // AUTO-SIZE based on length
          const len = el.textContent.length;
          if (len > 60) {
            el.setAttribute('data-length', 'very-long');
          } else if (len > 35) {
            el.setAttribute('data-length', 'long');
          }

          this.answer_container.appendChild(el);
          this.answer_labels.push(el);
        }
        this.logger.info("✓ Created " + this.answer_labels.length + " answer labels");
      } catch (e) {
        this.logger.error("Create answer labels error: " + e);
      }
    }

   highlight_correct_answers(answers) {
    try {
        if (!answers || typeof answers !== "object") {
            this.logger.warn("Highlight called with invalid answers payload:", answers);
            return;
        }

        if (!this.answer_labels.length) {
            this.logger.warn("No answer labels to highlight");
            return;
        }

        const normalize = (s) => {
            if (s == null) return "";
            return String(s).toLowerCase().trim().replace(/[.]+$/, "");
        };

        // Build normalized backend answer map
        const normalizedAnswers = {};
        Object.keys(answers).forEach((k) => {
            const key = normalize(k);     // "a" or "true"
            normalizedAnswers[key] = !!answers[k];
        });

        let correctCount = 0;

        for (let el of this.answer_labels) {
            const letter = normalize(el.dataset.letter || "");
            const text = normalize(el.dataset.raw_answer || "");

            // Priority 1: match by letter (MCQ)
            let is_correct = false;
            if (letter && normalizedAnswers.hasOwnProperty(letter)) {
                is_correct = normalizedAnswers[letter];
            }
            else {
                // Priority 2: match by raw text (True/False / Short-answer)
                if (normalizedAnswers.hasOwnProperty(text)) {
                    is_correct = normalizedAnswers[text];
                }
            }

            if (is_correct) correctCount++;

            el.classList.toggle("correct", is_correct);
            el.classList.toggle("incorrect", !is_correct);
        }

        this.logger.info("✓ Highlighted " + correctCount + " answer(s)");
    }
    catch (e) {
        this.logger.error("Highlight answers error:", e);
    }
}


    clear_answer_labels() {
      try {
        for (let i = 0; i < this.answer_labels.length; i++) {
          const el = this.answer_labels[i];
          if (el && el.parentNode) el.parentNode.removeChild(el);
        }
        this.answer_labels = [];
      } catch (e) {
        this.logger.error("Clear answer labels error: " + e);
      }
    }

    stop_quiz() {
      this.on_quiz_ended();
    }

     // OPTIMIZED: Cleanup on destroy
    cleanup() {
        try {
            // Cancel any pending animation frames
            if (this._pendingTimerUpdate) {
                cancelAnimationFrame(this._pendingTimerUpdate);
                this._pendingTimerUpdate = null;
            }

            if (this._layoutUpdateTimeout) {
                clearTimeout(this._layoutUpdateTimeout);
                this._layoutUpdateTimeout = null;
            }

        if (this.signals && typeof this.signals.disconnect_signal === "function") {
          const names = [
            "answers_highlighted","quiz_started","quiz_ended","question_changed",
            "message_ready","state_changed","timer_started","timer_paused","timer_resumed",
            "answer_display_complete","showing_answers","timer_tick","timer_ended"
          ];
          for (let i = 0; i < names.length; i++) {
            this.signals.disconnect_signal(names[i], null, "quiz_display");
          }
        }
        this._disconnect_bridge_fallback();
        this.clear_answer_labels();
        if (this.root && this.root.parentNode) this.root.parentNode.removeChild(this.root);
        window.removeEventListener("resize", this._onResize);
      } catch (e) {
        this.logger.error("Cleanup error: " + e);
      }
    }

    // ✅ NEW: Debug helper for console
    debugSignals() {
      console.group("🔍 QuizDisplay Signal Debug Info");
      console.log("Initialization complete:", this._initialization_complete);
      console.log("Signals connected:", this._signals_connected);
      console.log("Quiz has started:", this.quiz_has_started);
      console.log("Current question:", this.current_question);
      console.log("Signals object:", this.signals);

      if (this.signals && typeof this.signals.get_stats === "function") {
        console.log("QuizSignals stats:", this.signals.get_stats());
      }

      // Test if handlers are actually registered
      const testSignals = ["quiz_started", "question_changed", "quiz_ended"];
      console.log("\nTesting signal handler registration:");
      for (const sig of testSignals) {
        if (this.signals && this.signals._handlers) {
          const handlers = this.signals._handlers.get(sig);
          console.log(`  ${sig}:`, handlers ? `${handlers.length} handler(s)` : "NO HANDLERS");
          if (handlers) {
            handlers.forEach((h, i) => {
              console.log(`    [${i}] owner: ${h.owner}, calls: ${h.call_count}`);
            });
          }
        }
      }

      console.groupEnd();
    }

    _clear_image() {
      try {
        this.image_display.removeAttribute("src");
        this.image_display.style.display = "none";
        document.body.classList.remove("qd-has-image");
      } catch (e) {
        this.logger.error("Clear image error: " + e);
      }
    }

    _center_timer_only() {
      // No longer needed - timer is integrated in question label
    }
  }

  window.QuizDisplay = QuizDisplay;

  // ✅ Global debug helper - call from console with: debugQuizDisplay()
  window.debugQuizDisplay = function() {
    const instance = QuizDisplay._instance;
    if (!instance) {
      console.error("❌ QuizDisplay not initialized yet");
      return;
    }
    instance.debugSignals();
  };

  console.log("✅ QuizDisplay loaded - use debugQuizDisplay() to check signal state");
})();
// =========================================================
// THEME SYSTEM
// =========================================================

(function initDisplayTheme() {
  'use strict';

  if (window.CLIENT_TYPE === 'quiz_display') {
    console.log("[QuizDisplay Theme] Overlay Themes renderer active; legacy ThemeManager bridge disabled");
    return;
  }

  console.log("[QuizDisplay Theme] 🎨 Initializing PostMessage-only theme system");

  let currentTheme = 'default';

  function applyQuizDisplayTheme(theme) {
    if (!theme || typeof theme !== 'string') {
      console.warn("[QuizDisplay Theme] ⚠️ Invalid theme:", theme);
      return;
    }

    console.log(`[QuizDisplay Theme] 🎯 Applying theme: ${theme}`);

    const allThemeClasses = [
      "default-theme", "halloween-theme", "christmas-theme",
      "theme-default", "theme-halloween", "theme-christmas"
    ];

    allThemeClasses.forEach(cls => {
      document.body.classList.remove(cls);
      document.documentElement.classList.remove(cls);
    });

    document.body.classList.add(`${theme}-theme`, `theme-${theme}`);
    document.documentElement.classList.add(`${theme}-theme`, `theme-${theme}`);

    currentTheme = theme;

    console.log(`[QuizDisplay Theme] ✅ Theme applied: ${theme}-theme, theme-${theme}`);
    console.log(`[QuizDisplay Theme] 📋 Body classes:`, document.body.className);
  }

  function resolveAutoTheme() {
    const now = new Date();
    const month = now.getMonth();
    const day = now.getDate();

    if (month === 9 && day >= 15 && day <= 31) {
      console.log("[QuizDisplay Theme] 🎃 Auto-resolved to: halloween");
      return "halloween";
    }

    if (month === 11 || (month === 0 && day <= 6)) {
      console.log("[QuizDisplay Theme] 🎄 Auto-resolved to: christmas");
      return "christmas";
    }

    console.log("[QuizDisplay Theme] ✨ Auto-resolved to: default");
    return "default";
  }

  window.addEventListener("message", function (event) {
    if (!event.data || event.data.type !== "THEME_SYNC") {
      return;
    }

    console.log("[QuizDisplay Theme] 📬 Received THEME_SYNC:", event.data);

    let theme = event.data.activeTheme || event.data.theme;

    if (theme === "auto") {
      theme = resolveAutoTheme();
    }

    applyQuizDisplayTheme(theme);
  });

  window.addEventListener("themeChanged", function (event) {
    const theme = event.detail;
    console.log("[QuizDisplay Theme] 🎨 Theme changed event received:", theme);
    applyQuizDisplayTheme(theme);
  });

  function requestThemeFromParent() {
    if (window.parent !== window) {
      console.log("[QuizDisplay Theme] 📨 Requesting theme from parent");
      try {
        window.parent.postMessage({ type: "REQUEST_THEME_SYNC" }, "*");
      } catch (e) {
        console.warn("[QuizDisplay Theme] ⚠️ Could not request theme:", e);
      }
    } else {
      console.log("[QuizDisplay Theme] ℹ️ Not in iframe, applying seasonal theme");
      applyQuizDisplayTheme(resolveAutoTheme());
    }
  }

  function applyInitialTheme() {
    console.log("[QuizDisplay Theme] 🚀 Applying initial theme...");

    if (window.themeManager) {
      const currentTheme = window.themeManager.getCurrentTheme();
      console.log("[QuizDisplay Theme] ✅ Using ThemeManager:", currentTheme);
      applyQuizDisplayTheme(currentTheme.active);
      return;
    }

    const inIframe = window.self !== window.top;

    if (inIframe) {
      console.log("[QuizDisplay Theme] 🖼️ Running in iframe - waiting for parent theme");
      applyQuizDisplayTheme('default');
      requestThemeFromParent();
    } else {
      console.log("[QuizDisplay Theme] 🌐 Running standalone - using seasonal theme");
      applyQuizDisplayTheme(resolveAutoTheme());
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', applyInitialTheme);
  } else {
    applyInitialTheme();
  }

  setTimeout(() => {
    if (window.themeManager) {
      const current = window.themeManager.getCurrentTheme();
      console.log("[QuizDisplay Theme] 🔄 Syncing with ThemeManager:", current);
      applyQuizDisplayTheme(current.active);
    } else if (window.self !== window.top) {
      console.log("[QuizDisplay Theme] 🔄 Re-requesting theme from parent");
      requestThemeFromParent();
    }
  }, 500);

  window.quizDisplayTheme = {
    apply: applyQuizDisplayTheme,
    current: () => currentTheme,
    request: requestThemeFromParent,
    resolve: resolveAutoTheme
  };

  console.log("[QuizDisplay Theme] ✅ Theme system initialized");
  console.log("[QuizDisplay Theme] 💡 Test in console: window.quizDisplayTheme.apply('halloween')");

})();
// Overlay Studio theme bridge: reuse the shared renderer for OBS presentation.
(function initThemeDrivenQuizOverlay(){
  async function start(){
    if(!window.OverlayTheme) return;
    let theme=await window.OverlayTheme.loadTheme();
    const mount=document.body;
    let latest=null;
    const render=()=>window.OverlayTheme.renderQuiz(mount,theme,latest);
    render();
    const hook=()=>{
      const c=window.httpBridgeClient; if(!c||!c.on) return setTimeout(hook,150);
      window.OverlayTheme.onThemeUpdate(c,(next)=>{theme=window.OverlayTheme.normTheme(next.theme||next); render();});
      const update=q=>{ if(q&&typeof q==='object'){ latest=q; render(); } };
      const clear=()=>{ latest=null; render(); };
      const refresh=async()=>{
        try{
          const r=await fetch('/api/debug/display_state',{cache:'no-store'});
          if(!r.ok) return;
          const j=await r.json();
          if(j&&j.has_current_question){
            const state=(j.state||'').toString().toLowerCase();
            if(state==='running'||state==='paused') {
              const q=(j.current_question||j.current_question_data||null);
              if(q) update(q);
            }
          }
        }catch(e){}
      };
      c.on('signal:question_changed',update);
      c.on('question_changed',update);
      c.on('signal:quiz_started',refresh);
      c.on('quiz_started',refresh);
      c.on('signal:state_changed',(st)=>{ const v=String(st||'').toLowerCase(); if(v==='running'||v==='question_active'||v==='paused') refresh(); else if(v==='idle'||v==='stopped'||v==='completed') clear(); });
      c.on('signal:quiz_ended',clear);
      c.on('quiz_ended',clear);
      refresh();
    }; hook();
  }
  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',start); else start();
})();
