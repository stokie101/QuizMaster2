(function patchQuizMasterLinkedTikTokFlow() {
  function patch() {
    const manager = window.tiktokTabManager;
    if (!manager) {
      window.setTimeout(patch, 100);
      return;
    }

    manager.connectLinkedLiveChat = async function connectLinkedLiveChat() {
      const username = this.linkedAccount?.username || this.ui.linkedUsernameInput?.value?.trim();
      if (!username) {
        alert("Connect and refresh your linked TikTok account first.");
        return;
      }
      this._setUiState("connecting", `Connecting live chat for @${username}...`);
      this._systemChat(`Connecting live chat for @${username}...`);
      try {
        const res = await this._apiCall("POST", "/api/tiktok/connect-linked", {});
        if (!res.success) throw new Error(res.error || "Failed to start connection");
        const connectedUsername = res.username || username;
        this._log(`Live chat connect requested for linked account @${connectedUsername}`, "success");
      } catch (error) {
        this._setUiState("error", error.message);
        this._systemChat(`Live chat connection failed: ${error.message}`);
      }
    };

    manager._log?.("QuizMaster linked-account live chat flow active", "success");
  }

  if (document.readyState === "complete" || document.readyState === "interactive") {
    patch();
  } else {
    document.addEventListener("DOMContentLoaded", patch);
  }
})();
