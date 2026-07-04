(function () {
  "use strict";

  const INSTALL_FLAG = "__QUIZMASTER_TIKTOK_ACCOUNT_ONLY_PATCH__";
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

  async function fetchJson(method, url, body) {
    const options = {
      method,
      headers: { "Content-Type": "application/json" },
      cache: "no-store",
    };
    if (body !== undefined) options.body = JSON.stringify(body);
    const response = await fetch(url, options);
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.detail || payload.error || payload.reason || `HTTP ${response.status}`);
    }
    return payload;
  }

  function hideManualLoginUi() {
    const debugFallback = document.querySelector(".debug-fallback");
    if (debugFallback) debugFallback.style.display = "none";

    const officialActions = document.querySelector(".official-actions");
    if (officialActions) officialActions.style.display = "none";

    const officialPanel = document.querySelector(".official-panel");
    if (officialPanel) {
      officialPanel.setAttribute("aria-label", "Linked TikTok account status");
    }
  }

  function unwrapSnapshot(payload) {
    if (payload?.account_snapshot) return payload.account_snapshot;
    return payload || {};
  }

  function renderAccountStatus(manager, snapshot, liveStatus) {
    const statusEl = manager?.ui?.officialLoginStatus || document.getElementById("officialLoginStatus");
    if (!statusEl) return;

    if (!snapshot?.available) {
      statusEl.innerHTML = `
        <div style="color:#f59e0b;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;line-height:1.5;">
          TikTok official account not linked. Use the sidebar TikTok Login button first.
        </div>
      `;
      return;
    }

    const liveConnected = !!(liveStatus?.connected || liveStatus?.is_connected || liveStatus?.status === "connected");
    const avatar = snapshot.avatar_url || snapshot.avatar_url_100 || snapshot.avatar_large_url || "";
    const username = snapshot.username ? `@${snapshot.username}` : "TikTok account";
    const followers = snapshot.followers ?? snapshot.exact_current_followers ?? snapshot.follower_count;
    statusEl.innerHTML = `
      <div style="display:flex;gap:.75rem;align-items:center;margin-top:.25rem;">
        ${avatar ? `<img src="${escapeHtml(avatar)}" alt="TikTok avatar" style="width:44px;height:44px;border-radius:50%;object-fit:cover;border:1px solid rgba(236,72,153,.45);" onerror="this.style.display='none'">` : ""}
        <div style="min-width:0;flex:1;">
          <div style="color:#10b981;font-weight:900;text-transform:uppercase;letter-spacing:.08em;">Official TikTok account linked ✅</div>
          <div style="color:#f8fafc;font-weight:900;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${escapeHtml(snapshot.display_name || username)}</div>
          <div style="color:#94a3b8;">${escapeHtml(username)} · ${formatNumber(followers)} followers</div>
        </div>
      </div>
      <div style="margin-top:.65rem;color:${liveConnected ? "#10b981" : "#f59e0b"};font-family:ui-monospace,SFMono-Regular,Menlo,monospace;">
        Live chat: ${liveConnected ? "connected" : "waiting / connecting from linked account"}
      </div>
    `;
  }

  async function refreshAccountAndLive(manager) {
    const snapshotPayload = await fetchJson("GET", "/api/tiktok/account-snapshot");
    const snapshot = unwrapSnapshot(snapshotPayload);
    const liveStatus = await fetchJson("GET", "/api/tiktok/status").catch(() => ({}));
    renderAccountStatus(manager, snapshot, liveStatus);

    if (snapshot?.available) {
      if (manager?.ui?.linkedUsernameInput) manager.ui.linkedUsernameInput.value = snapshot.username ? `@${snapshot.username}` : "";
      if (manager?.ui?.usernameInput) manager.ui.usernameInput.value = snapshot.username || "";
      manager?._applyLinkedAccount?.(snapshot);
      if (liveStatus?.connected) {
        manager?._setUiState?.("connected", `Connected to @${liveStatus.username || snapshot.username}`);
      } else {
        manager?._setUiState?.("disconnected", `Linked TikTok @${snapshot.username}`);
      }
    }

    return { snapshot, liveStatus };
  }

  async function connectFromLinkedAccount(manager) {
    manager?._setUiState?.("connecting", "Connecting from linked TikTok account...");
    const result = await fetchJson("POST", "/api/tiktok/connect-linked", {});
    if (!result.success) throw new Error(result.error || result.message || "Failed to connect TikTok live chat");
    manager?._log?.(`TikTokLive connecting from linked account @${result.username || "unknown"}`, "success");
    setTimeout(() => refreshAccountAndLive(manager).catch(() => {}), 2000);
    setTimeout(() => refreshAccountAndLive(manager).catch(() => {}), 6000);
    return result;
  }

  function install() {
    const manager = window.tiktokTabManager;
    if (!manager || !manager.ui) return false;

    hideManualLoginUi();

    manager.connectLinkedLiveChat = async function () {
      try {
        await connectFromLinkedAccount(manager);
      } catch (error) {
        manager._setUiState?.("error", error.message);
        manager._log?.(`TikTok official account connect failed: ${error.message}`, "error");
      }
    };

    manager.checkOfficialLoginStatus = async function () {
      try {
        return await refreshAccountAndLive(manager);
      } catch (error) {
        manager._setOfficialLoginStatus?.(`TikTok official account status unavailable: ${error.message}`);
        manager._log?.(`TikTok official account status unavailable: ${error.message}`, "error");
        return null;
      }
    };

    manager.openOfficialLogin = async function () {
      manager._setOfficialLoginStatus?.("Use the sidebar TikTok Login button to link the official account.");
    };

    refreshAccountAndLive(manager)
      .then(({ snapshot, liveStatus }) => {
        if (snapshot?.available && !liveStatus?.connected) {
          return connectFromLinkedAccount(manager);
        }
        return null;
      })
      .catch((error) => {
        manager._log?.(`TikTok account-only startup check failed: ${error.message}`, "error");
      });

    console.log("[QuizMaster TikTok Account Only Patch] installed");
    return true;
  }

  let tries = 0;
  const timer = setInterval(() => {
    tries += 1;
    if (install() || tries > 80) clearInterval(timer);
  }, 250);
})();
