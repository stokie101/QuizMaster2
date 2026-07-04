function applyGlobalTheme(theme) {
  if (!theme) return;

  console.log('[Theme] 🎨 Applying global theme:', theme);

  document.body.classList.remove(
    'theme-default',
    'theme-halloween',
    'theme-christmas',
    'theme-birthday'
  );

  if (theme !== 'default' && theme !== 'auto') {
    document.body.classList.add(`theme-${theme}`);
  }

  // Use ThemeManager for synchronization
  if (window.themeManager) {
    console.log('[Theme] 🔗 Using ThemeManager to broadcast');
    window.themeManager.setTheme(theme);
  } else {
    // Fallback: manual broadcast
    console.log('[Theme] ⚠️ ThemeManager not found, using fallback');
    broadcastThemeToAllViews(theme);
  }

  console.log('[Theme] ✅ Theme applied and broadcasted:', theme);
}

// Fallback broadcast function
function broadcastThemeToAllViews(theme) {
  console.log('[Theme] 📡 Broadcasting theme to all views:', theme);

  const payload = {
    type: 'THEME_SYNC',
    theme: theme,
    activeTheme: theme,
    timestamp: Date.now()
  };

  // Broadcast to all iframes
  const iframes = document.querySelectorAll('iframe');
  iframes.forEach((iframe, index) => {
    try {
      iframe.contentWindow.postMessage(payload, '*');
      console.log(`[Theme] ✅ Sent to iframe ${index + 1}`);
    } catch (e) {
      console.warn(`[Theme] ⚠️ Failed to send to iframe ${index + 1}:`, e);
    }
  });

  // Emit via WebSocket
  if (window.httpBridgeClient && window.httpBridgeClient.emit) {
    try {
      window.httpBridgeClient.emit('theme_sync', { theme, activeTheme: theme });
      console.log('[Theme] 🌐 Emitted via WebSocket');
    } catch (e) {
      console.warn('[Theme] ⚠️ WebSocket emit failed:', e);
    }
  }

  // Dispatch custom event
  window.dispatchEvent(new CustomEvent('themeChanged', { detail: theme }));
}

// Listen for theme requests from iframes
window.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'REQUEST_THEME_SYNC') {
    console.log('[Theme] 📬 Received theme request from iframe');

    let currentTheme = 'default';

    // Try ThemeManager first
    if (window.themeManager) {
      const themeInfo = window.themeManager.getCurrentTheme();
      currentTheme = themeInfo.active || themeInfo.selection;
    } else {
      // Fallback: get from UI
      const themeSelect = document.querySelector('[data-setting="THEME.mode"]');
      currentTheme = themeSelect ? themeSelect.value : 'default';
    }

    // Send current theme back
    if (event.source) {
      event.source.postMessage({
        type: 'THEME_SYNC',
        theme: currentTheme,
        activeTheme: currentTheme,
        timestamp: Date.now()
      }, '*');
      console.log('[Theme] ✅ Sent current theme:', currentTheme);
    }
  }
});

class QuizSettingsManager {
  constructor(controls) {
    this.controls = controls;
    this.apiBase = `${window.location.origin}${window.QuizMasterURLs?.apiPrefix?.() || ''}`;
    this.saveTimeout = null;
    this.isLoading = false;
    this.lastTheme = null;
    this.themeUpdateLock = false;

    this.init();
  }

  // helper → supports BOTH qs-* and qc-* classes
  _selectAll(classes) {
    return document.querySelectorAll(
      classes.map(c => `.${c}`).join(',')
    );
  }

  init() {
    console.log('[Settings] Initializing settings manager (with theme sync)');

    // ✅ ADD GEAR BUTTON AND PANEL HANDLERS
    this.gearButton = document.querySelector('.qs-gear');
    this.closeButton = document.querySelector('.qs-close');
    this.panel = document.querySelector('[data-qs-panel]');
    this.controlsView = document.querySelector('[data-qc-controls-view]');

    if (this.gearButton) {
      this.gearButton.addEventListener('click', () => this.openPanel());
      console.log('[Settings] ✅ Gear button bound');
    }

    if (this.closeButton) {
      this.closeButton.addEventListener('click', () => this.closePanel());
      console.log('[Settings] ✅ Close button bound');
    }

    this.setupToggles();
    this.setupSliders();
    this.setupSelects();
    this.setupNumbers();

    // Listen for external theme changes
    this._setupThemeListeners();

    this.loadSettings();
  }

  openPanel() {
    console.log('[Settings] 🎛️ Opening panel');
    if (this.panel) {
      this.panel.setAttribute('aria-hidden', 'false');
    }
    if (this.controlsView) {
      this.controlsView.setAttribute('aria-hidden', 'true');
    }
    if (this.gearButton) {
      this.gearButton.setAttribute('aria-expanded', 'true');
    }
  }

  closePanel() {
    console.log('[Settings] 🎛️ Closing panel');
    if (this.panel) {
      this.panel.setAttribute('aria-hidden', 'true');
    }
    if (this.controlsView) {
      this.controlsView.setAttribute('aria-hidden', 'false');
    }
    if (this.gearButton) {
      this.gearButton.setAttribute('aria-expanded', 'false');
    }
  }


  _setupThemeListeners() {
    // Listen for WebSocket theme updates
    if (window.httpBridgeClient) {
      const reloadSettings = () => {
        console.log('[Settings] 🌐 Settings update received; reloading settings');
        this.loadSettings();
      };
      window.httpBridgeClient.on('signal:config_updated', reloadSettings);
      window.httpBridgeClient.on('signal:settings_changed', reloadSettings);

      window.httpBridgeClient.on('signal:theme_sync', (payload) => {
        const theme = payload?.activeTheme || payload?.theme;
        if (theme && !this.themeUpdateLock) {
          console.log('[Settings] 🌐 WS theme update:', theme);
          this._updateThemeUI(theme);
        }
      });
    }

    // Listen for postMessage theme updates
    window.addEventListener('message', (e) => {
      if (e.data?.type === 'THEME_SYNC' && !this.themeUpdateLock) {
        const theme = e.data.activeTheme || e.data.theme;
        console.log('[Settings] 📨 PostMessage theme update:', theme);
        this._updateThemeUI(theme);
      }
    });
  }

  _updateThemeUI(theme) {
    if (this.themeUpdateLock || this.isLoading) return;

    const themeSelect = document.querySelector('[data-setting="THEME.mode"]');
    if (themeSelect && themeSelect.value !== theme) {
      console.log('[Settings] 🎨 Updating theme UI to:', theme);

      this.themeUpdateLock = true;
      themeSelect.value = theme;
      this.lastTheme = theme;

      setTimeout(() => {
        this.themeUpdateLock = false;
      }, 100);
    }
  }

  setupToggles() {
    this._selectAll(['qs-toggle', 'qc-toggle']).forEach(toggle => {
      toggle.addEventListener('click', () => {
        toggle.classList.toggle('active');
        toggle.setAttribute('aria-pressed', String(toggle.classList.contains('active')));
        this.debounceSave();
      });
    });
  }

  setupSliders() {
    this._selectAll(['qs-slider', 'qc-slider']).forEach(slider => {
      const valueDisplay =
        slider.parentElement.querySelector('.qs-slider-value') ||
        slider.parentElement.querySelector('.qc-slider-value');

      slider.addEventListener('input', (e) => {
        if (valueDisplay) {
          valueDisplay.textContent = e.target.value + '%';
        }
        this.debounceSave();
      });
    });
  }

  setupSelects() {
    this._selectAll(['qs-select', 'qc-select']).forEach(select => {
      if (!select.dataset.setting) return;

      // ✅ Special handling for theme changes
      if (select.dataset.setting === 'THEME.mode') {
        select.addEventListener('change', (e) => {
          if (this.themeUpdateLock) {
            console.log('[Settings] 🔒 Theme change ignored (locked)');
            return;
          }

          const newTheme = e.target.value;
          console.log('[Settings] 🎨 Theme changed to:', newTheme);

          this.themeUpdateLock = true;
          this.lastTheme = newTheme;

          applyGlobalTheme(newTheme);
          this.debounceSave();

          setTimeout(() => {
            this.themeUpdateLock = false;
          }, 500);
        });
      } else {
        select.addEventListener('change', () => this.debounceSave());
      }
    });
  }

  setupNumbers() {
    this._selectAll(['qs-number-input', 'qc-number-input']).forEach(input => {
      if (!input.dataset.setting) return;
      input.addEventListener('change', () => this.debounceSave());
    });
  }

  debounceSave() {
    if (this.isLoading) return;

    clearTimeout(this.saveTimeout);
    this.saveTimeout = setTimeout(() => this.saveSettings(), 500);
  }

  async loadSettings() {
    this.isLoading = true;
    console.log('[Settings] 📥 Loading settings from server...');

    try {
      // ✅ FIXED: Fetch from actual API endpoint
      const response = await window.QuizMasterURLs.authorizedFetch('/api/config/all');

      if (!response.ok) {
        console.warn('[Settings] ⚠️ Config endpoint returned:', response.status);
        this.isLoading = false;
        return;
      }

      const result = await response.json();

      if (!result.success) {
        console.warn('[Settings] ⚠️ Config endpoint failed:', result.error);
        this.isLoading = false;
        return;
      }

      const data = result.config;

      if (data) {
        console.log('[Settings] ✅ Settings loaded:', data);
        this._applySettings(data);

        // ✅ Apply theme AFTER loading all settings
        if (data.THEME && data.THEME.mode) {
          this.lastTheme = data.THEME.mode;
          applyGlobalTheme(data.THEME.mode);
        }
      } else {
        console.log('[Settings] ℹ️ Using default settings');
      }

    } catch (err) {
      console.error('[Settings] ❌ Load error:', err);
    } finally {
      this.isLoading = false;
    }
  }

  _applySettings(data) {
    if (!data || typeof data !== 'object') return;

    console.log('[Settings] 🎨 Applying settings to UI');

    for (const [section, values] of Object.entries(data)) {
      if (!values || typeof values !== 'object') continue;

      for (const [key, value] of Object.entries(values)) {
        const setting = `${section}.${key}`;
        const element = document.querySelector(`[data-setting="${setting}"]`);

        if (!element) continue;

        if (element.classList.contains('qs-toggle') || element.classList.contains('qc-toggle')) {
          const isEnabled = value === true || String(value).toLowerCase() === 'true' || String(value) === '1';
          element.classList.toggle('active', isEnabled);
          element.setAttribute('aria-pressed', String(isEnabled));
        } else if (element.type === 'range') {
          element.value = value;
          const display = element.parentElement.querySelector('.qs-slider-value, .qc-slider-value');
          if (display) display.textContent = value + '%';
        } else if (element.type === 'number') {
          element.value = value;
        } else if (element.tagName === 'SELECT') {
          element.value = value;
        }
      }
    }

    console.log('[Settings] ✅ Settings applied to UI');
  }

  async saveSettings() {
    if (this.isLoading) return;

    this._showSaveStatus('Saving…', 'saving');
    const settingsObject = this._gatherSettings();
    console.log('[Settings] 💾 Saving settings:', settingsObject);

    // ✅ FIXED: Convert nested object to dot-notation format
    const dotNotationSettings = {};
    for (const [section, values] of Object.entries(settingsObject)) {
      for (const [key, value] of Object.entries(values)) {
        dotNotationSettings[`${section}.${key}`] = value;
      }
    }

    try {
      const response = await window.QuizMasterURLs.authorizedFetch('/api/config/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ settings: dotNotationSettings })
      });

      const result = await response.json();

      if (response.ok && result.success) {
        this._showSaveStatus('Saved', 'saved');
        console.log('[Settings] ✅ Settings saved successfully:', result.updates_count, 'updates');
      } else {
        this._showSaveStatus(`Failed${result.error ? ': ' + result.error : ''}`, 'failed');
        console.error('[Settings] ❌ Save failed:', result.error || response.status);
      }
    } catch (err) {
      this._showSaveStatus(`Failed: ${err.message || err}`, 'failed');
      console.error('[Settings] ❌ Save error:', err);
    }
  }

  _gatherSettings() {
    const settings = {};

    this._selectAll(['qs-toggle', 'qc-toggle', 'qs-select', 'qc-select', 'qs-number-input', 'qc-number-input', 'qs-slider', 'qc-slider'])
      .forEach(element => {
        const setting = element.dataset.setting;
        if (!setting) return;

        const [section, key] = setting.split('.');
        if (!section || !key) return;

        if (!settings[section]) settings[section] = {};

        if (element.classList.contains('qs-toggle') || element.classList.contains('qc-toggle')) {
          settings[section][key] = element.classList.contains('active');
        } else if (element.type === 'range' || element.type === 'number') {
          settings[section][key] = parseInt(element.value, 10);
        } else if (element.tagName === 'SELECT') {
          settings[section][key] = element.value;
        }
      });

    return settings;
  }

  _showSaveStatus(text, state = 'saved') {
    const status = document.getElementById('saveStatus');
    if (!status) return;

    status.textContent = text;
    status.dataset.state = state;
    status.classList.add('visible');

    clearTimeout(this.statusTimeout);
    if (state !== 'saving') {
      this.statusTimeout = setTimeout(() => {
        status.classList.remove('visible');
      }, state === 'failed' ? 4000 : 2000);
    }
  }
}

class QuizControls {
  constructor() {
    this.quiz_loaded = false;
    this.quiz_started = false;
    this.quiz_paused = false;
    this.current_quiz_state = 'IDLE';

    this._state_sync_interval = null;
    this._lastStateSync = 0;
    this._missedSyncCount = 0;
    this._signals_connected = false;

    console.log('[QuizControls] Initializing...');
    const params = new URLSearchParams(window.location.search);
    this.isObsDock = window.QUIZ_CONTROLS_IS_OBS_DOCK === true || params.get('obs') === 'true';
    window.QUIZ_CONTROLS_IS_OBS_DOCK = this.isObsDock;
    window.CLIENT_TYPE = this.isObsDock ? 'quiz_controls' : 'quiz_settings';
    document.body.classList.toggle('obs-dock', this.isObsDock);
    document.body.classList.toggle('settings-page', !this.isObsDock);
    document.querySelectorAll('[data-obs-only]').forEach((el) => { el.hidden = !this.isObsDock; });
    if (this.isObsDock) {
      const settingsPanel = document.querySelector('[data-qs-panel]');
      const controlsView = document.querySelector('[data-qc-controls-view]');
      if (settingsPanel) settingsPanel.setAttribute('aria-hidden', 'true');
      if (controlsView) controlsView.setAttribute('aria-hidden', 'false');

      const title = document.querySelector('.qm-title');
      const intro = document.querySelector('[data-settings-intro]');
      const label = document.querySelector('[data-mode-label]');
      if (title) title.textContent = 'OBS Quiz Controls';
      if (intro) intro.textContent = 'Run the live quiz and make quick settings changes without leaving OBS.';
      if (label) label.textContent = 'Live dock';
    }

    this._setupDOMReferences();
    this._attachButtonHandlers();

    // ✅ CRITICAL FIX: Add force refresh button handler
    this._attachForceRefreshHandler();

    this.settings = new QuizSettingsManager(this);

    console.log('[QuizControls] DOM ready, waiting for services...');
    this._setupStateSync();
    this._syncState();
    setTimeout(() => {
      this._applyState(this.current_quiz_state || 'IDLE');
    }, 300);
    this._state_sync_interval = setInterval(() => this._syncState(), 5000);
  }

  _setupDOMReferences() {
    this.start_button = document.querySelector('[data-qc-start]');
    this.pause_resume_button = document.querySelector('[data-qc-pause]');
    this.stop_button = document.querySelector('[data-qc-stop]');
    this.skip_button = document.querySelector('[data-qc-skip]');
    this.load_quiz_button = document.querySelector('[data-qc-load]');
    this.file_input = document.getElementById('file-input');
    this.questions_loaded_label = document.querySelector('[data-qc-hint-quiz]');

    this.status_value = document.querySelector('[data-qc-status]');
    this.force_refresh_button = document.getElementById('forceQuestionBtn');

    console.log('[QuizControls] DOM references established');
  }

  _attachButtonHandlers() {
    if (this.start_button) {
      this.start_button.addEventListener('click', () => this._onStartClick());
    }
    if (this.pause_resume_button) {
      this.pause_resume_button.addEventListener('click', () => this._onPauseResumeClick());
    }
    if (this.stop_button) {
      this.stop_button.addEventListener('click', () => this._onStopClick());
    }
    if (this.skip_button) {
      this.skip_button.addEventListener('click', () => this._onSkipClick());
    }
    if (this.load_quiz_button) {
      this.load_quiz_button.addEventListener('click', () => this._onLoadQuizClick());
    }
    if (this.file_input) {
      this.file_input.addEventListener('change', (e) => this._onFileSelected(e));
    }

    console.log('[QuizControls] ✅ Button handlers attached');
  }

 // ✅ CRITICAL FIX: Force refresh button handler
  _attachForceRefreshHandler() {
    console.log('[QuizControls] 🔍 Looking for force refresh button...');

    if (!this.force_refresh_button) {
      console.log('[QuizControls] Force refresh button not present on settings-only page');
      return;
    }

    console.log('[QuizControls] ✅ Found force refresh button:', this.force_refresh_button);

    this.force_refresh_button.addEventListener('click', async () => {
      console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');
      console.log('[QuizControls] 🔄 FORCE REFRESH CLICKED!');
      console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');

      try {
        // Visual feedback - START
        const originalText = this.force_refresh_button.textContent;
        this.force_refresh_button.textContent = '🔄 Refreshing...';
        this.force_refresh_button.disabled = true;

        // ✅ FIX: Call the API endpoint instead of local signal emit
        console.log('[QuizControls] 📡 Calling /api/quiz/force_refresh_display...');

        const response = await window.QuizMasterURLs.authorizedFetch('/api/quiz/force_refresh_display', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json'
          }
        });

        const result = await response.json();

        if (!response.ok || !result.success) {
          throw new Error(result.error || 'Refresh failed');
        }

        console.log('[QuizControls] ✅ Refresh successful:', result);

        // Visual feedback - SUCCESS
        this.force_refresh_button.textContent = '✅ Refreshed!';

        setTimeout(() => {
          this.force_refresh_button.textContent = originalText;
          this.force_refresh_button.disabled = false;
        }, 2000);

        console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');
        console.log('[QuizControls] ✅ FORCE REFRESH COMPLETE');
        console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');

      } catch (error) {
        console.error('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');
        console.error('[QuizControls] ❌ FORCE REFRESH ERROR:', error);
        console.error('Error stack:', error.stack);
        console.error('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');
        alert(`Force refresh failed: ${error.message}`);

        this.force_refresh_button.textContent = '❌ Failed';
        this.force_refresh_button.disabled = false;

        setTimeout(() => {
          this.force_refresh_button.textContent = '🔄 Force Refresh Display';
        }, 2000);
      }
    });

    console.log('[QuizControls] ✅ Force refresh button handler attached');
  }

  _setupStateSync() {
    const log = (...args) => console.log('[QuizControls]', ...args);

    log('Setting up state sync...');

    if (window.httpBridgeClient) {
      log('Subscribing to quiz signals...');

      window.httpBridgeClient.on('signal:quiz_started', () => {
        log('Received quiz_started');
        this._onQuizStarted();
      });

      window.httpBridgeClient.on('signal:quiz_ended', () => {
        log('Received quiz_ended');
        this._onQuizEnded();
      });

      window.httpBridgeClient.on('signal:quiz_paused', () => {
        log('Received quiz_paused');
        this._onQuizPaused();
      });

      window.httpBridgeClient.on('signal:quiz_resumed', () => {
        log('Received quiz_resumed');
        this._onQuizResumed();
      });

      window.httpBridgeClient.on('signal:state_changed', (state) => {
        log('Received state_changed:', state);
        this._applyState(state);
      });

      window.httpBridgeClient.on('signal:quiz_data_loaded', (count) => {
        log('Received quiz_data_loaded:', count);
        this._applyState(this.current_quiz_state || 'IDLE', null, true);

        if (this.questions_loaded_label) {
          this.questions_loaded_label.textContent = `${count} questions loaded`;
        }
      });

      // Listen for theme changes and apply them
      window.httpBridgeClient.on('signal:theme_sync', (data) => {
        if (data && data.THEME && data.THEME.mode) {
          applyGlobalTheme(data.THEME.mode);
        }
      });
    }

    log('State sync setup complete');
  }

  async _syncState() {
    const warn = (...args) => console.warn('[QuizControls]', ...args);
    const log = (...args) => console.log('[QuizControls]', ...args);

    try {
      const client = window.httpBridgeClient;
      if (!client) {
        warn('No bridge client available for sync');
        return;
      }

      const state = await client.getState();
      if (state) {
        this._lastStateSync = Date.now();
        this._missedSyncCount = 0;

        const stateStr = String(state.state || state.status || 'IDLE').toUpperCase();
        const paused = !!(state.paused || state.is_paused);
        const loaded = !!(state.quiz_loaded || state.quizLoaded || state.loaded || Number(state.total_questions || 0) > 0);

        log('State synced:', stateStr, 'paused=', paused, 'loaded=', loaded);
        this._applyState(stateStr, paused, loaded);
      }
    } catch (e) {
      warn('State sync failed:', e);
    }
  }

  _connectToSignals() {
    // Deprecated: HTTP bridge signal subscriptions are authoritative.
    this._signals_connected = true;
  }

  _onQuizLoaded(data) {
    console.log('[QuizControls] quiz_data_loaded:', data);
    this._applyState(this.current_quiz_state || 'IDLE', null, true);

    const count = typeof data === 'number' ? data : (data && data.count) || 0;
    if (this.questions_loaded_label) {
      this.questions_loaded_label.textContent = `${count} questions loaded`;
    }
  }

  _onQuizStarted() {
    console.log('[QuizControls] quiz_started');
    this._applyState('QUESTION_ACTIVE');
  }

  _onQuizEnded() {
    console.log('[QuizControls] quiz_ended');
    this._applyState('ENDED');
    this._syncState();
  }

  _onQuizPaused() {
    console.log('[QuizControls] quiz_paused');
    this._applyState('PAUSED');
  }

  _onQuizResumed() {
    console.log('[QuizControls] quiz_resumed');
    this._applyState('QUESTION_ACTIVE');
  }

  _normalizeState(state) {
    const raw = String(state || 'IDLE').toUpperCase();
    const aliases = {
      RUNNING: 'QUESTION_ACTIVE',
      STOPPED: 'ENDED',
      COMPLETED: 'ENDED',
      FINISHED: 'ENDED',
      LOADING: 'IDLE',
      LOADED: 'IDLE',
      INIT: 'IDLE',
      RESET: 'IDLE'
    };
    return aliases[raw] || raw;
  }

  _applyState(state, paused = null, loaded = null) {
    let normalized = String(this._normalizeState(state) || 'IDLE').toUpperCase();

    if (paused === true && normalized === 'QUESTION_ACTIVE') {
      normalized = 'PAUSED';
    }

    this.current_quiz_state = normalized;

    if (typeof loaded === 'boolean') {
      this.quiz_loaded = loaded;
    }

    if (normalized === 'PAUSED') {
      this.quiz_paused = true;
    } else {
      this.quiz_paused = false;
    }

    this.quiz_started = (normalized === 'QUESTION_ACTIVE' || normalized === 'PAUSED');

    this._updateUIForState(normalized, this.quiz_paused, this.quiz_loaded);
  }

  _updateUI() {
    this._updateUIForState(this.current_quiz_state, this.quiz_paused, this.quiz_loaded);
  }

  _updateUIForState(state, paused, loaded) {
    const s = String(this._normalizeState(state) || 'IDLE').toUpperCase();

    const isIdle = (s === 'IDLE' || s === 'ENDED');
    const isActive = (s === 'QUESTION_ACTIVE');
    const isPaused = (s === 'PAUSED');

    if (this.start_button) {
      this.start_button.disabled = !loaded || !isIdle;
    }

    if (this.pause_resume_button) {
      this.pause_resume_button.disabled = !(isActive || isPaused);

      const label = this.pause_resume_button.querySelector('.qc-label');
      const icon = this.pause_resume_button.querySelector('.qc-icon');

      if (label) label.textContent = isPaused ? 'Resume' : 'Pause';
      if (icon) icon.textContent = isPaused ? '▶️' : '⏸️';
    }

    if (this.stop_button) {
      this.stop_button.disabled = isIdle;
    }

    if (this.skip_button) {
      this.skip_button.disabled = !isActive;
    }

    if (this.load_quiz_button) {
      this.load_quiz_button.disabled = !isIdle;
    }

    if (this.status_value) {
      const labels = { IDLE: 'Ready', QUESTION_ACTIVE: 'Running', PAUSED: 'Paused', ENDED: 'Ready' };
      this.status_value.textContent = labels[s] || s.replace(/_/g, ' ');
      this.status_value.dataset.state = s.toLowerCase();
    }
  }

  async _onStartClick() {
    console.log('[QuizControls] Start button clicked');
    try {
      const response = await window.QuizMasterURLs.authorizedFetch('/api/quiz/start', { method: 'POST' });
      const result = await response.json();
      if (!response.ok || !result.success) {
        console.error('[QuizControls] Start failed:', result.error);
      }
    } catch (e) {
      console.error('[QuizControls] Start failed:', e);
    }
  }

  async _onPauseResumeClick() {
    console.log('[QuizControls] Pause/Resume clicked');
    try {
      const currentState = String(this.current_quiz_state || 'IDLE').toUpperCase();
      let endpoint = null;

      if (currentState === 'PAUSED') {
        endpoint = '/api/quiz/resume';
      } else if (currentState === 'QUESTION_ACTIVE') {
        endpoint = '/api/quiz/pause';
      } else {
        console.warn('[QuizControls] Pause/Resume ignored in state:', currentState);
        return;
      }

      const response = await window.QuizMasterURLs.authorizedFetch(endpoint, { method: 'POST' });
      const result = await response.json();
      if (!response.ok || !result.success) {
        console.error('[QuizControls] Pause/Resume failed:', result.error);
      }
    } catch (e) {
      console.error('[QuizControls] Pause/Resume failed:', e);
    }
  }

  async _onStopClick() {
    console.log('[QuizControls] Stop button clicked');
    try {
      const response = await window.QuizMasterURLs.authorizedFetch('/api/quiz/stop', { method: 'POST' });
      const result = await response.json();
      if (!response.ok || !result.success) {
        console.error('[QuizControls] Stop failed:', result.error);
      }
    } catch (e) {
      console.error('[QuizControls] Stop failed:', e);
    }
  }

  async _onSkipClick() {
    console.log('[QuizControls] Skip button clicked');
    try {
      const response = await window.QuizMasterURLs.authorizedFetch('/api/quiz/skip', { method: 'POST' });
      const result = await response.json();
      if (!response.ok || !result.success) {
        console.error('[QuizControls] Skip failed:', result.error);
      }
    } catch (e) {
      console.error('[QuizControls] Skip failed:', e);
    }
  }

  _onLoadQuizClick() {
    console.log('[QuizControls] Load quiz clicked');
    if (this.file_input) {
      this.file_input.click();
    }
  }

  async _onFileSelected(event) {
    const file = event.target.files[0];
    if (!file) return;

    console.log('[QuizControls] 📁 File selected:', file.name);

    try {
      // Read file as text
      const csvText = await file.text();

      if (!csvText || !csvText.trim()) {
        console.error('[QuizControls] ❌ File is empty');
        alert('The selected file is empty');
        this.file_input.value = '';
        return;
      }

      console.log('[QuizControls] 📤 Uploading quiz data...');

      // Send to correct endpoint with correct format
      const response = await window.QuizMasterURLs.authorizedFetch('/api/quiz/load', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          csv_text: csvText
        })
      });

      const result = await response.json();

      if (response.ok && result.success) {
        const count = result.questions || 0;
        console.log('[QuizControls] ✅ Quiz loaded:', count, 'questions');

        if (this.questions_loaded_label) {
          this.questions_loaded_label.textContent = `${count} questions loaded`;
        }

        this._applyState(this.current_quiz_state || 'IDLE', null, true);

        console.log('[QuizControls] 🎉 Quiz ready to start!');
      } else {
        const error = result.error || 'Unknown error';
        console.error('[QuizControls] ❌ Upload failed:', error);
        alert(`Failed to load quiz: ${error}`);
      }

    } catch (e) {
      console.error('[QuizControls] ❌ Upload error:', e);
      alert(`Error uploading quiz: ${e.message}`);
    }

    // Clear file input
    this.file_input.value = '';
  }
}

// Export class to window
window.QuizControls = QuizControls;

QuizControls.instance = null;
QuizControls.create_singleton = function() {
  if (!QuizControls.instance) {
    QuizControls.instance = new QuizControls();
  }
  return QuizControls.instance;
};

console.log('✅ quiz_controls.js loaded - QuizControls class exported');