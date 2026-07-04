(function initThemeDrivenLeaderboard(){
  'use strict';

  let latestEntries = [];
  let currentTheme = null;
  let initialLoadDone = false;
  let lastSignature = '';
  let pollTimer = null;

  function normalizeEntries(data){
    const entries = Array.isArray(data) ? data : (data?.entries || data?.leaderboard || []);
    return entries
      .map((entry) => ({
        user_id: entry.user_id || entry.id || entry.unique_id || '',
        username: entry.username || entry.display_name || entry.name || 'User',
        name: entry.name || entry.display_name || entry.username || 'User',
        score: Number(entry.score || 0),
        correct: Number(entry.correct || entry.correct_answers || 0),
        incorrect: Number(entry.incorrect || entry.incorrect_answers || 0),
        streak: Number(entry.streak || 0),
        avatar_url: entry.avatar_url || entry.avatar || ''
      }))
      .sort((a, b) => b.score - a.score);
  }

  function signature(entries){
    return JSON.stringify(entries.map((entry) => [entry.user_id, entry.username, entry.score, entry.correct, entry.incorrect, entry.streak]));
  }

  async function ensureTheme(){
    if (!currentTheme) currentTheme = await window.OverlayTheme.loadTheme();
    return currentTheme;
  }

  async function renderLeaderboard(data, force = false){
    const entries = normalizeEntries(data);
    const sig = signature(entries);
    if (!force && sig === lastSignature) return;
    lastSignature = sig;
    latestEntries = entries;
    const theme = await ensureTheme();
    window.OverlayTheme.renderLeaderboard(document.body, theme, { entries: latestEntries });
  }

  async function refreshTheme(themePayload){
    currentTheme = themePayload ? window.OverlayTheme.normTheme(themePayload.theme || themePayload) : await window.OverlayTheme.loadTheme();
    window.OverlayTheme.renderLeaderboard(document.body, currentTheme, { entries: latestEntries });
  }

  function handleLBUpdate(...args){
    const payload = Array.isArray(args[0]) ? args[0][0] : args[0];
    initialLoadDone = true;
    renderLeaderboard(payload, true);
  }

  async function fetchSnapshotLeaderboard(reason){
    try {
      const response = await fetch(`/api/snapshot?ts=${Date.now()}&reason=${encodeURIComponent(reason || 'leaderboard_poll')}`, { cache: 'no-store' });
      if (!response.ok) return;
      const data = await response.json();
      const snapshot = data.snapshot || data;
      const entries = snapshot.leaderboard || snapshot.entries || [];
      if (Array.isArray(entries)) {
        initialLoadDone = true;
        await renderLeaderboard({ entries });
      }
    } catch (error) {
      console.warn('[Leaderboard] Snapshot poll failed:', error);
    }
  }

  async function fetchSessionSnapshot(){
    try {
      if (!window.QuizMasterURLs?.sessionApiUrl || !window.QuizMasterURLs?.sessionId) return false;
      const sessionId = window.QuizMasterURLs.sessionId();
      if (!sessionId) return false;
      const url = window.QuizMasterURLs.sessionApiUrl(`/api/widget-sessions/quiz/${encodeURIComponent(sessionId)}/snapshot`);
      const response = await fetch(`${url}${url.includes('?') ? '&' : '?'}ts=${Date.now()}`, { cache: 'no-store' });
      if (!response.ok) return false;
      const data = await response.json();
      if (data.success && data.snapshot) {
        await renderLeaderboard({ entries: data.snapshot.leaderboard || [] }, true);
        initialLoadDone = true;
        return true;
      }
    } catch (error) {
      console.warn('[Leaderboard] Session snapshot failed:', error);
    }
    return false;
  }

  async function fetchInitialLeaderboard(){
    const gotSession = await fetchSessionSnapshot();
    if (!gotSession) await fetchSnapshotLeaderboard('initial');
    if (!initialLoadDone) await renderLeaderboard({ entries: [] }, true);
  }

  function startPolling(){
    if (pollTimer) return;
    pollTimer = setInterval(() => fetchSnapshotLeaderboard('interval'), 1000);
    fetchSnapshotLeaderboard('startup');
  }

  async function connectWhenReady(){
    await renderLeaderboard({ entries: [] }, true);

    if (!window.QuizBootstrap?.ready) return setTimeout(connectWhenReady, 100);
    const boot = await window.QuizBootstrap.ready();
    const client = boot.services?.bridgeClient;
    if (!client) return setTimeout(connectWhenReady, 100);

    window.httpBridgeClient = client;
    if (!client.isWebSocketConnected()) client.connectWebSocket();

    window.OverlayTheme.onThemeUpdate(client, refreshTheme);
    client.on('leaderboard_updated', handleLBUpdate);
    client.on('signal:leaderboard_updated', handleLBUpdate);
    client.on('leaderboard_reset', () => renderLeaderboard({ entries: [] }, true));
    client.on('signal:leaderboard_reset', () => renderLeaderboard({ entries: [] }, true));
    client.on('leaderboard_reset_requested', () => renderLeaderboard({ entries: [] }, true));
    client.on('signal:leaderboard_reset_requested', () => renderLeaderboard({ entries: [] }, true));
    client.on('quiz_started', () => fetchSnapshotLeaderboard('quiz_started'));
    client.on('signal:quiz_started', () => fetchSnapshotLeaderboard('quiz_started'));
    client.on('ws_open', () => fetchSnapshotLeaderboard('ws_open'));
    client.on('ready', () => fetchSnapshotLeaderboard('ready'));

    setTimeout(fetchInitialLeaderboard, 300);
    startPolling();
  }

  window.addEventListener('storage', (event) => { if (event.key === 'overlay_theme') refreshTheme(); });
  window.addEventListener('focus', () => { refreshTheme(); fetchSnapshotLeaderboard('focus'); });
  window.addEventListener('beforeunload', () => { if (pollTimer) clearInterval(pollTimer); });

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', connectWhenReady);
  else connectWhenReady();
})();
