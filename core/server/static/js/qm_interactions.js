(function () {
  "use strict";

  const FLAG = "__QUIZMASTER_INTERACTIONS__";
  if (window[FLAG]) return;
  window[FLAG] = true;

  const PRESS_CLASS = "qm-pressed";
  const BUSY_CLASS = "qm-busy";

  function isControl(node) {
    return node && (node.tagName === "BUTTON" || node.classList?.contains("button") ||
      node.classList?.contains("nav-btn") || node.getAttribute?.("role") === "button");
  }

  // A CSS :active rule alone is easy to miss on a fast click, so every press
  // also gets a short flash that plays out regardless of pointer position.
  document.addEventListener("pointerdown", (event) => {
    const control = event.target.closest?.("button, .button, .nav-btn, [role='button']");
    if (!control || control.disabled) return;
    control.classList.remove(PRESS_CLASS);
    // Restart the animation even on a rapid second press.
    void control.offsetWidth;
    control.classList.add(PRESS_CLASS);
    setTimeout(() => control.classList.remove(PRESS_CLASS), 420);
  }, true);

  /**
   * Run an async action with the button showing a spinner until it settles, so
   * a slow backend call reads as "working", never as "nothing happened".
   */
  async function withBusy(control, action) {
    if (!control) return action();
    const wasDisabled = control.disabled;
    control.classList.add(BUSY_CLASS);
    control.disabled = true;
    try {
      return await action();
    } finally {
      control.classList.remove(BUSY_CLASS);
      control.disabled = wasDisabled;
    }
  }

  window.QuizMasterUI = { withBusy, isControl, PRESS_CLASS, BUSY_CLASS };
})();
