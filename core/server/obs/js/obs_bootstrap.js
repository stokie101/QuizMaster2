(function () {
  'use strict';

  const pageType = document.body.dataset.obsPage;
  const BASE = '/obs/js';

  function checkSocketIO() {
    if (typeof io === 'undefined') {
      throw new Error('Socket.IO not loaded. Add CDN to HTML.');
    }
  }

  function loadScript(file) {
    return new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = `${BASE}/${file}?v=${Date.now()}`;
      script.onload = resolve;
      script.onerror = () => reject(new Error(`Failed to load ${script.src}`));
      document.head.appendChild(script);
    });
  }

  function fatal(title, error) {
    const div = document.createElement('div');
    div.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.85);color:#fff;display:flex;align-items:center;justify-content:center;font-family:sans-serif;z-index:99999;';
    div.innerHTML = `<div style="max-width:500px;text-align:center"><h2>${title}</h2><p>${error.message}</p><p style="opacity:.7">Check console for details</p></div>`;
    document.body.appendChild(div);
  }

  async function init() {
    try {
      checkSocketIO();
      if (pageType === 'tab') {
        await loadScript('obs_tab.js');
        if (!window.initObsTab) throw new Error('initObsTab missing');
        window.initObsTab();
        return;
      }
      if (pageType === 'control') {
        await loadScript('obs_control.js');
        if (!window.initObsControl) throw new Error('initObsControl missing');
        window.initObsControl();
        return;
      }
      throw new Error(`Unknown page type: ${pageType}`);
    } catch (err) {
      fatal('OBS overlay setup error', err);
    }
  }

  init();
})();
