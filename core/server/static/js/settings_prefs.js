(function () {
  const SECTION = 'APP_SETTINGS';
  const DEFAULTS = {
    start_with_windows: false,
    minimise_to_tray: true,
    launch_dashboard_on_startup: true,
    update_notifications: true,
    sound_notifications: false,
    background_effects: true,
    high_contrast_mode: false,
    compact_mode: false,
  };

  let settingsCache = { ...DEFAULTS };
  let saving = false;

  function boolish(value, fallback = false) {
    if (value === undefined || value === null || value === '') return fallback;
    if (typeof value === 'boolean') return value;
    return String(value).toLowerCase() === 'true' || String(value) === '1';
  }

  function keyForLabel(label) {
    const text = String(label || '').trim().toLowerCase();
    if (text === 'start quizmaster with windows') return 'start_with_windows';
    if (text === 'minimise to tray') return 'minimise_to_tray';
    if (text === 'launch dashboard on startup') return 'launch_dashboard_on_startup';
    if (text === 'update notifications') return 'update_notifications';
    if (text === 'sound notifications') return 'sound_notifications';
    if (text === 'background effects') return 'background_effects';
    if (text === 'high contrast mode') return 'high_contrast_mode';
    if (text === 'compact mode') return 'compact_mode';
    return null;
  }

  async function loadSettings() {
    try {
      const data = await fetch('/api/settings', { cache: 'no-store' }).then((r) => r.json());
      const raw = data?.settings?.[SECTION] || data?.settings?.app_settings || {};
      settingsCache = { ...DEFAULTS };
      for (const key of Object.keys(DEFAULTS)) settingsCache[key] = boolish(raw[key], DEFAULTS[key]);
    } catch (error) {
      console.warn('[SettingsPrefs] Failed to load app settings:', error);
    }
    applyBodyPrefs();
  }

  async function saveSetting(key, value) {
    settingsCache[key] = !!value;
    applyBodyPrefs();
    saving = true;
    try {
      const response = await fetch('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ [SECTION]: { [key]: !!value } })
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || data.success === false) throw new Error(data.error || response.statusText || 'Save failed');
      toastSafe('Setting saved.');
    } catch (error) {
      toastSafe(`Setting save failed: ${error.message}`);
      console.error('[SettingsPrefs] Save failed:', error);
    } finally {
      saving = false;
    }
  }

  function toastSafe(message) {
    if (typeof window.toast === 'function') window.toast(message);
    else {
      const el = document.getElementById('settingsToast');
      if (el) {
        el.textContent = message;
        el.classList.add('show');
        clearTimeout(window.__settingsPrefsToastTimer);
        window.__settingsPrefsToastTimer = setTimeout(() => el.classList.remove('show'), 2800);
      }
    }
  }

  function applyBodyPrefs() {
    document.body.classList.toggle('settings-high-contrast', !!settingsCache.high_contrast_mode);
    document.body.classList.toggle('settings-compact', !!settingsCache.compact_mode);
    document.body.classList.toggle('settings-no-bg-effects', !settingsCache.background_effects);
  }

  function enhanceToggle(row) {
    const label = row.querySelector('.control-label')?.textContent || '';
    const key = keyForLabel(label);
    if (!key) return;
    const input = row.querySelector('input[type="checkbox"]');
    if (!input) return;

    input.disabled = false;
    input.checked = !!settingsCache[key];
    input.dataset.prefKey = key;
    input.addEventListener('change', () => saveSetting(key, input.checked));
  }

  function enhanceLinks(root) {
    root.querySelectorAll('button').forEach((btn) => {
      const label = (btn.textContent || '').trim().toLowerCase();
      if (label.includes('open quizmaster.online')) {
        btn.onclick = () => window.open('https://quizmaster.online', '_blank', 'noopener,noreferrer');
        btn.disabled = false;
      }
      if (label.includes('open widgets.quizmaster.online')) {
        btn.onclick = () => window.open('https://widgets.quizmaster.online', '_blank', 'noopener,noreferrer');
        btn.disabled = false;
      }
    });
  }

  function enhanceSettingsPage() {
    const root = document.getElementById('settingsContent');
    if (!root) return;
    root.querySelectorAll('.control-row').forEach(enhanceToggle);
    enhanceLinks(root);
    applyBodyPrefs();
  }

  function hookRenderPage() {
    if (typeof window.renderPage === 'function' && !window.renderPage.__prefsHooked) {
      const original = window.renderPage;
      window.renderPage = function (...args) {
        const result = original.apply(this, args);
        setTimeout(enhanceSettingsPage, 0);
        return result;
      };
      window.renderPage.__prefsHooked = true;
    }
  }

  document.addEventListener('DOMContentLoaded', async () => {
    await loadSettings();
    hookRenderPage();
    setTimeout(enhanceSettingsPage, 0);
    setTimeout(enhanceSettingsPage, 250);
  });
})();
