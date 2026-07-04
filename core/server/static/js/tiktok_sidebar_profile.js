(function () {
  "use strict";

  const INSTALL_FLAG = "__QUIZMASTER_TIKTOK_SIDEBAR_PROFILE__";
  if (window[INSTALL_FLAG]) return;
  window[INSTALL_FLAG] = true;

  const escapeHtml = (value) => String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\"/g, "&quot;")
    .replace(/'/g, "&#039;");

  const formatNumber = (value) => {
    const n = Number(value);
    return Number.isFinite(n) ? n.toLocaleString() : "—";
  };

  function injectStyles() {
    if (document.getElementById("tiktokSidebarProfileStyles")) return;
    const style = document.createElement("style");
    style.id = "tiktokSidebarProfileStyles";
    style.textContent = `
      .qm-tiktok-sidebar-profile { margin:.65rem .85rem; padding:.75rem; border:1px solid rgba(236,72,153,.32); border-radius:.85rem; background:linear-gradient(135deg, rgba(236,72,153,.12), rgba(6,182,212,.06)); }
      .qm-tiktok-sidebar-profile.compact { padding:.55rem; }
      .qm-tiktok-sidebar-top { display:flex; align-items:center; gap:.55rem; min-width:0; }
      .qm-tiktok-sidebar-avatar { width:38px; height:38px; border-radius:50%; object-fit:cover; border:1px solid rgba(6,182,212,.55); flex:0 0 38px; }
      .qm-tiktok-sidebar-avatar-placeholder { width:38px; height:38px; border-radius:50%; display:flex; align-items:center; justify-content:center; background:rgba(15,23,42,.85); color:#ec4899; font-weight:900; border:1px solid rgba(236,72,153,.45); flex:0 0 38px; }
      .qm-tiktok-sidebar-copy { min-width:0; flex:1; }
      .qm-tiktok-sidebar-state { font-family:'Share Tech Mono', monospace; color:#10b981; font-size:.6rem; text-transform:uppercase; letter-spacing:.08em; font-weight:900; }
      .qm-tiktok-sidebar-state.waiting { color:#f59e0b; }
      .qm-tiktok-sidebar-name { color:#e2e8f0; font-weight:900; font-size:.78rem; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
      .qm-tiktok-sidebar-user { color:#94a3b8; font-size:.68rem; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
      .qm-tiktok-sidebar-followers { margin-top:.55rem; display:flex; align-items:center; justify-content:space-between; gap:.4rem; background:rgba(15,23,42,.65); border:1px solid rgba(30,41,59,.7); border-radius:.55rem; padding:.45rem .5rem; }
      .qm-tiktok-sidebar-followers span:first-child { color:#64748b; font-size:.62rem; text-transform:uppercase; letter-spacing:.08em; }
      .qm-tiktok-sidebar-followers span:last-child { color:#e2e8f0; font-size:.88rem; font-weight:900; }
      .qm-tiktok-sidebar-actions { margin-top:.55rem; display:flex; gap:.4rem; }
      .qm-tiktok-sidebar-btn { flex:1; font-family:'Share Tech Mono', monospace; font-size:.6rem; font-weight:900; text-transform:uppercase; border-radius:.45rem; padding:.35rem .4rem; cursor:pointer; border:1px solid rgba(148,163,184,.24); background:rgba(30,41,59,.75); color:#cbd5e1; }
      .qm-tiktok-sidebar-btn.primary { border-color:rgba(236,72,153,.45); background:rgba(236,72,153,.15); color:#f9a8d4; }
      .qm-tiktok-sidebar-btn:hover { border-color:rgba(6,182,212,.55); color:#06b6d4; }
      .qm-tiktok-sidebar-muted { color:#94a3b8; font-size:.68rem; line-height:1.35; margin-top:.45rem; }
    `;
    document.head.appendChild(style);
  }

  function ensureCard() {
    injectStyles();
    let card = document.getElementById("tiktokSidebarProfile");
    if (card) return card;
    const sidebar = document.querySelector(".sidebar");
    const statusBlock = document.querySelector(".sidebar-status");
    if (!sidebar) return null;
    card = document.createElement("div");
    card.id = "tiktokSidebarProfile";
    card.className = "qm-tiktok-sidebar-profile";
    if (statusBlock && statusBlock.parentNode === sidebar) sidebar.insertBefore(card, statusBlock);
    else sidebar.appendChild(card);
    return card;
  }

  function render(status) {
    const card = ensureCard();
    if (!card) return;
    const current = status || window.QuizMasterTikTokMonitor?.getState?.() || {};

    if (!current.linked) {
      card.innerHTML = `
        <div class="qm-tiktok-sidebar-top">
          <div class="qm-tiktok-sidebar-avatar-placeholder">🎵</div>
          <div class="qm-tiktok-sidebar-copy">
            <div class="qm-tiktok-sidebar-state waiting">TikTok official</div>
            <div class="qm-tiktok-sidebar-name">Not linked</div>
            <div class="qm-tiktok-sidebar-user">Connect account profile</div>
          </div>
        </div>
        <div class="qm-tiktok-sidebar-actions">
          <button class="qm-tiktok-sidebar-btn primary" id="tiktokSidebarActionBtn">Log in</button>
        </div>
      `;
      // Logged out: the single action button starts the official TikTok login.
      document.getElementById("tiktokSidebarActionBtn")?.addEventListener("click", openLogin);
      return;
    }

    const avatar = current.avatar || "";
    const username = current.username ? `@${current.username}` : "TikTok account";
    const displayName = current.displayName || username;
    const stateClass = current.liveConnected ? "" : "waiting";
    const stateText = current.liveConnected ? "TikTok LIVE connected ✅" : "TikTok linked ✅";
    const userLine = current.liveConnected ? `${username} · chat ready` : `${username} · waiting for LIVE`;
    const avatarHtml = avatar
      ? `<img class="qm-tiktok-sidebar-avatar" src="${escapeHtml(avatar)}" alt="TikTok avatar">`
      : `<div class="qm-tiktok-sidebar-avatar-placeholder">🎵</div>`;

    card.innerHTML = `
      <div class="qm-tiktok-sidebar-top">
        ${avatarHtml}
        <div class="qm-tiktok-sidebar-copy">
          <div class="qm-tiktok-sidebar-state ${stateClass}">${escapeHtml(stateText)}</div>
          <div class="qm-tiktok-sidebar-name">${escapeHtml(displayName)}</div>
          <div class="qm-tiktok-sidebar-user">${escapeHtml(userLine)}</div>
        </div>
      </div>
      <div class="qm-tiktok-sidebar-followers"><span>Followers</span><span>${formatNumber(current.followers)}</span></div>
      <div class="qm-tiktok-sidebar-actions"><button class="qm-tiktok-sidebar-btn" id="tiktokSidebarActionBtn">Refresh</button></div>
      <div class="qm-tiktok-sidebar-muted">${escapeHtml(current.liveConnected ? "Live chat is ready." : "Live chat will connect automatically when your TikTok LIVE is available.")}</div>
    `;
    // Logged in: the same action button becomes Refresh.
    document.getElementById("tiktokSidebarActionBtn")?.addEventListener("click", refreshNow);
  }

  async function openLogin() {
    render({ linked: false, message: "Opening login..." });
    try { await window.QuizMasterTikTokMonitor?.openLogin?.(); }
    catch (_) { render({ linked: false, message: "Login failed" }); }
  }

  function refreshNow() {
    const card = ensureCard();
    if (card) card.classList.add("compact");
    window.QuizMasterTikTokMonitor?.refreshNow?.();
  }

  function init() {
    if (!ensureCard()) return false;
    render();
    window.QuizMasterTikTokMonitor?.subscribe?.(render);
    window.QuizMasterTikTokMonitor?.start?.();
    return true;
  }

  let tries = 0;
  const timer = setInterval(() => {
    tries += 1;
    if (init() || tries > 80) clearInterval(timer);
  }, 250);
})();
