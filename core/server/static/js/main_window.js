window.CLIENT_TYPE = "main_window";
window.ROOM_ID = "default";

const TAB_ROUTES = {
  main: "/main_tab.html",
  builder: "/quiz_tab.html?mode=builder",
  control: "/core/quiz/html/controls.html",
  tiktok: "/tiktok_tab.html",
  overlay: "/overlay-studio",
  account: "/account.html",
  settings: "/settings"
};

const state = { activeTab: "main" };

function boolish(value, fallback = false) {
  if (value === undefined || value === null || value === "") return fallback;
  if (typeof value === "boolean") return value;
  return String(value).toLowerCase() === "true" || String(value) === "1";
}

function hideLoading() {
  const el = document.getElementById("loadingOverlay");
  if (el) { el.classList.add("fade-out"); setTimeout(() => el.remove(), 250); }
}

window.updateConnectionStatus = () => {};
window.updateStatusMessage = () => {};

function navigateToTab(tab) {
  const route = TAB_ROUTES[tab] || TAB_ROUTES.main;
  const frame = document.getElementById("mainContentFrame");
  const host = document.getElementById("contentTab");
  if (!frame) return;
  state.activeTab = TAB_ROUTES[tab] ? tab : "main";
  localStorage.setItem("quizmaster.lastTab", state.activeTab);
  document.querySelectorAll(".nav-btn").forEach((btn) => btn.classList.toggle("active", btn.dataset.tab === state.activeTab));
  if (host) host.classList.add("loading");
  if (frame.getAttribute("src") !== route) frame.setAttribute("src", route);
}

async function chooseStartupTab() {
  try {
    const data = await fetch("/api/settings", { cache: "no-store" }).then((r) => r.json());
    const appSettings = (data && data.settings && (data.settings.APP_SETTINGS || data.settings.app_settings)) || {};
    const launchDashboard = boolish(appSettings.launch_dashboard_on_startup, true);
    if (launchDashboard) return "main";
    const last = localStorage.getItem("quizmaster.lastTab");
    return TAB_ROUTES[last] ? last : "main";
  } catch (_) {
    return "main";
  }
}

document.addEventListener("DOMContentLoaded", async () => {
  document.querySelectorAll(".nav-btn").forEach((btn) => btn.addEventListener("click", () => navigateToTab(btn.dataset.tab)));
  const frame = document.getElementById("mainContentFrame");
  if (frame) {
    frame.addEventListener("load", () => {
      const host = document.getElementById("contentTab");
      if (host) host.classList.remove("loading");
    });
  }
  window.QuizMasterTikTokMonitor?.start?.();
  navigateToTab(await chooseStartupTab());
  setTimeout(hideLoading, 350);
});
