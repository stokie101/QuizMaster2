(function () {
  'use strict';

  if (window.__quizMasterTemplateSaveInstalled) return;
  window.__quizMasterTemplateSaveInstalled = true;

  function installStyles() {
    if (document.getElementById('quizTemplateSaveStyles')) return;
    const style = document.createElement('style');
    style.id = 'quizTemplateSaveStyles';
    style.textContent = `
      #downloadQuizTemplate {
        display: inline-flex !important;
        align-items: center !important;
        justify-content: center !important;
        gap: .45rem !important;
        border: 1px solid rgba(6, 182, 212, .55) !important;
        border-radius: .6rem !important;
        padding: .7rem 1rem !important;
        background: linear-gradient(135deg, rgba(6, 182, 212, .18), rgba(139, 92, 246, .16)) !important;
        color: #67e8f9 !important;
        font-family: 'Share Tech Mono', monospace !important;
        font-size: .72rem !important;
        font-weight: 900 !important;
        text-transform: uppercase !important;
        letter-spacing: .08em !important;
        cursor: pointer !important;
        box-shadow: 0 0 18px rgba(6, 182, 212, .12) !important;
      }
      #downloadQuizTemplate:hover:not(:disabled) {
        border-color: rgba(236, 72, 153, .7) !important;
        color: #f9a8d4 !important;
        box-shadow: 0 0 22px rgba(236, 72, 153, .18) !important;
      }
      #downloadQuizTemplate:disabled {
        opacity: .65 !important;
        cursor: wait !important;
      }
    `;
    document.head.appendChild(style);
  }

  function setButton(button, text, disabled) {
    button.disabled = !!disabled;
    button.innerHTML = text;
    try { window.lucide?.createIcons?.(); } catch (_) {}
  }

  async function saveTemplate(button) {
    const original = button.dataset.originalHtml || button.innerHTML;
    button.dataset.originalHtml = original;
    setButton(button, 'Saving...', true);

    try {
      const response = await fetch('/api/quiz/template/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({})
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || payload.success === false) throw new Error(payload.error || 'Save failed');
      setButton(button, payload.cancelled ? 'Cancelled' : 'Saved ✓', true);
      setTimeout(() => setButton(button, original, false), 1400);
    } catch (_) {
      setButton(button, 'Save Failed', true);
      setTimeout(() => setButton(button, original, false), 1800);
    }
  }

  installStyles();

  document.addEventListener('click', function (event) {
    const button = event.target && event.target.closest ? event.target.closest('#downloadQuizTemplate') : null;
    if (!button) return;
    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation();
    saveTemplate(button);
  }, true);
})();
