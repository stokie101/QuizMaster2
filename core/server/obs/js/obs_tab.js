window.initObsTab = async function () {
  const byId = (id) => document.getElementById(id);
  const socket = io();
  let config = {};
  let isConnected = false;

  async function api(url, method = 'GET', body = null) {
    const opts = { method, headers: { 'Content-Type': 'application/json' } };
    if (body) opts.body = JSON.stringify(body);
    const res = await fetch(url, opts);
    return res.json();
  }

  function fullUrl(path) {
    return window.QuizMasterURLs?.get_public_url?.(path.split('?')[0], Object.fromEntries(new URLSearchParams(path.split('?')[1] || ''))) || `${window.location.origin}${path}`;
  }

  function updateConnectionUI() {
    const text = isConnected ? 'Connected' : 'Not connected';
    const cls = isConnected ? 'pill success' : 'pill neutral';
    ['obsStatusBadge', 'obsConnectionPill'].forEach((id) => { const el = byId(id); if (el) { el.textContent = text; el.className = cls; } });
    byId('connectBtn').textContent = isConnected ? 'Disconnect' : 'Connect';
  }

  async function loadConfig() {
    const data = await api('/api/obs/config');
    config = data.config || {};
    byId('obsHostInput').value = config.host || 'localhost';
    byId('obsPortInput').value = Number(config.port || 4455);
    byId('obsPasswordInput').value = '';
    byId('savedPasswordBadge').style.display = config.password ? 'inline' : 'none';
    byId('autoReconnectToggle').checked = !!config.autoReconnect;
    isConnected = !!config.connected;
    updateConnectionUI();
  }

  async function saveConnection() {
    await api('/api/obs/config', 'PUT', {
      host: byId('obsHostInput').value.trim(),
      port: Number(byId('obsPortInput').value || 4455),
      password: byId('obsPasswordInput').value,
      autoReconnect: byId('autoReconnectToggle').checked,
    });
    await loadConfig();
  }

  async function testConnection() {
    const result = byId('testResult');
    result.style.display = 'block';
    result.textContent = 'Testing OBS connection…';
    const data = await api('/api/obs/test', 'POST');
    result.textContent = data.success ? `Connected to OBS (${data.obsVersion || 'version unknown'})` : `Connection failed: ${data.error || 'Unable to connect'}`;
  }

  async function connectObs() {
    if (isConnected) { await api('/api/obs/disconnect', 'POST'); isConnected = false; }
    else { const resp = await api('/api/obs/connect', 'POST'); isConnected = !!resp.connected; }
    updateConnectionUI();
  }

  ['quizDisplayUrl','leaderboardUrl','quizControlsUrl'].forEach((id) => { const el = byId(id); el.textContent = fullUrl(el.textContent.trim()); });
  document.querySelectorAll('.copy-url').forEach((btn) => btn.addEventListener('click', () => navigator.clipboard.writeText(document.querySelector(btn.dataset.copy).textContent.trim())));
  byId('saveConnectionBtn').onclick = saveConnection;
  byId('testConnectionBtn').onclick = testConnection;
  byId('connectBtn').onclick = connectObs;
  socket.on('obs:connected', () => { isConnected = true; updateConnectionUI(); });
  socket.on('obs:disconnected', () => { isConnected = false; updateConnectionUI(); });
  await loadConfig();
};
