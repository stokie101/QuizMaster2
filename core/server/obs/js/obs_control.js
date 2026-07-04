window.initObsControl = function () {
  const byId = (id) => document.getElementById(id);
  const socket = io();

  let scenes = [];
  let triggers = [];
  let currentScene = '';

  async function api(url, method = 'GET', body = null) {
    const opts = { method, headers: { 'Content-Type': 'application/json' } };
    if (body) opts.body = JSON.stringify(body);
    const res = await fetch(url, opts);
    return res.json();
  }

  function setStatus(text) {
    byId('dockStatus').textContent = text;
  }

  function setConnection(connected) {
    byId('connectionDot').innerHTML = `<span style="width:.7rem;height:.7rem;border-radius:999px;background:${connected ? '#10b981' : '#ef4444'};display:inline-block;"></span><span>${connected ? 'Connected' : 'Disconnected'}</span>`;
  }

  function renderScenes() {
    const wrap = byId('sceneButtons');
    wrap.innerHTML = '';
    scenes.forEach((name) => {
      const active = name === currentScene;
      const btn = document.createElement('button');
      btn.textContent = name;
      btn.style.cssText = `padding:.55rem;border-radius:.5rem;border:1px solid ${active ? 'rgba(6,182,212,.55)' : 'rgba(148,163,184,.28)'};background:${active ? 'rgba(6,182,212,.15)' : 'rgba(15,23,42,.8)'};color:${active ? '#06b6d4' : '#e2e8f0'};font-weight:700;cursor:pointer;`;
      btn.onclick = () => switchScene(name);
      wrap.appendChild(btn);
    });
  }

  function renderTriggers() {
    const wrap = byId('quickTriggers');
    const enabled = triggers.filter((t) => t.enabled);
    if (!enabled.length) {
      wrap.innerHTML = '<div style="color:#64748b;font-size:.8rem;">No enabled triggers.</div>';
      return;
    }
    wrap.innerHTML = '';
    enabled.forEach((trigger) => {
      const btn = document.createElement('button');
      btn.textContent = `${trigger.name || 'Trigger'} → ${trigger.sceneName || 'Scene'}`;
      btn.style.cssText = 'padding:.5rem;border-radius:.45rem;border:1px solid rgba(139,92,246,.35);background:rgba(139,92,246,.12);color:#e2e8f0;text-align:left;cursor:pointer;';
      btn.onclick = () => fireTrigger(trigger.id);
      wrap.appendChild(btn);
    });
  }

  async function loadCurrentScene() {
    const data = await api('/api/obs/current_scene');
    if (data.success) {
      currentScene = data.sceneName || '';
      byId('dockCurrentScene').textContent = currentScene || '—';
      byId('dockLastSwitched').textContent = `Last switched: ${new Date().toLocaleTimeString()}`;
      renderScenes();
    }
  }

  async function loadScenes() {
    const data = await api('/api/obs/scenes');
    if (data.success) {
      scenes = data.scenes || [];
      renderScenes();
    }
  }

  async function loadTriggers() {
    const data = await api('/api/obs/triggers');
    if (data.success) {
      triggers = data.triggers || [];
      renderTriggers();
    }
  }

  async function switchScene(name) {
    const data = await api('/api/obs/switch_scene', 'POST', { sceneName: name });
    if (data.success) {
      setStatus(`Switched to ${name}`);
      await loadCurrentScene();
    } else {
      setStatus(data.error || 'Failed to switch scene');
    }
  }

  async function fireTrigger(id) {
    const data = await api(`/api/obs/triggers/${encodeURIComponent(id)}/test`, 'POST');
    if (data.success) {
      setStatus(`Trigger fired: ${data.sceneName}`);
      await loadCurrentScene();
    } else {
      setStatus(data.error || 'Failed to fire trigger');
    }
  }

  socket.on('obs:scene_changed', (data) => {
    currentScene = data?.sceneName || '';
    byId('dockCurrentScene').textContent = currentScene || '—';
    byId('dockLastSwitched').textContent = `Last switched: ${new Date().toLocaleTimeString()}`;
    renderScenes();
  });

  socket.on('obs:connected', async () => {
    setConnection(true);
    await loadScenes();
    await loadCurrentScene();
  });

  socket.on('obs:disconnected', () => {
    setConnection(false);
  });

  socket.on('obs:scenes_updated', (data) => {
    scenes = data?.scenes || [];
    renderScenes();
  });

  (async () => {
    await loadScenes();
    await loadTriggers();
    await loadCurrentScene();
    const cfg = await api('/api/obs/config');
    setConnection(!!cfg?.config?.connected);
  })();
};
