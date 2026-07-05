const SETTINGS_PAGES = {
  general: {
    title: 'General',
    description: 'Choose how QuizMaster starts, alerts you, and behaves during everyday stream prep.',
    render: () => grid([
      card('🚀', 'Startup behaviour', 'These local preferences help QuizMaster get out of your way when you begin a stream.', controls([
        toggleRow('Start QuizMaster with Windows', 'Open the app automatically when you sign in to your computer.', false, true),
        toggleRow('Minimise to tray', 'Keep QuizMaster running quietly when you close the window during a stream.', true, true),
        toggleRow('Launch dashboard on startup', 'Show the main command dashboard first whenever QuizMaster opens.', true, true),
      ]), 'wide'),
      card('🔔', 'Notifications', 'Keep important stream events visible without overwhelming your workflow.', controls([
        toggleRow('Update notifications', 'Show a friendly prompt when a new QuizMaster update is available.', true, true),
        toggleRow('Sound notifications', 'Play subtle confirmation sounds for important actions and alerts.', false, true),
      ])),
      card('💡', 'Helpful links', 'Quick access to QuizMaster resources while setup documentation is expanded.', actionRow([
        button('Open quizmaster.liveforge.online', () => openExternal(localBaseUrl())),
        button('Open widgets.liveforge.online', () => openExternal(localBaseUrl()), 'secondary'),
      ])),
    ])
  },
  accounts: {
    title: 'Accounts & Cloud',
    description: 'Review the connected account, widget routing identity, local fallback, and cloud-sync status.',
    render: renderAccountsCloud
  },
  diagnostics: {
    title: 'Diagnostics',
    description: 'Run safe checks when links, QuizMaster media, or local overlays need a health review.',
    render: renderDiagnostics
  },
  data: {
    title: 'Data & Backups',
    description: 'Review saved local data, create backups, and run safe checks before future cloud features.',
    render: renderDataBackups
  },
  integrations: {
    title: 'Integrations',
    description: 'Review launch-ready integrations and the public URL services that support widgets.',
    render: renderIntegrations
  },
  appearance: {
    title: 'Appearance',
    description: 'Preview the visual controls planned for tuning QuizMaster’s neon/dark interface.',
    render: () => grid([
      card('🌙', 'Theme', 'QuizMaster currently uses the polished neon dark theme throughout the app.', controls([toggleRow('Neon dark theme', 'Current production theme. Additional themes are planned.', true, true), toggleRow('High contrast mode', 'Future accessibility option for stronger borders and text contrast.', false, true)])),
      card('🎨', 'Accent colour', 'Preview accent options for future personalisation.', `<div class="swatches"><span class="swatch cyan"></span><span class="swatch violet"></span><span class="swatch emerald"></span><span class="swatch rose"></span></div><p class="muted" style="margin-top:1rem">Accent selection is a visual placeholder for now.</p>`),
      card('✨', 'Animation intensity', 'Control how energetic transitions and ambient effects feel.', `<div class="range-row"><label class="settings-label">Balanced</label><input type="range" min="0" max="100" value="65" disabled></div><p class="muted">Placeholder control; existing app animations remain unchanged.</p>`),
      card('🌌', 'Background effects', 'Ambient space background, scanlines, and subtle glow effects match the rest of QuizMaster.', controls([toggleRow('Background effects', 'Show the space backdrop and neon gradients.', true, true), toggleRow('Compact mode', 'Future denser layout for smaller displays.', false, true)])),
    ])
  },
  advanced: {
    title: 'Advanced',
    description: 'Advanced support options and technical details for troubleshooting.',
    render: renderAdvanced
  },
  about: {
    title: 'About QuizMaster',
    description: 'Product information, support links, credits, and release notes.',
    render: () => grid([
      card('⚡', 'QuizMaster', 'TikTok LIVE quiz control with OBS-ready overlays, chat answers, and live leaderboard workflows.', `<div class="info-list"><div class="info-row"><strong>App version</strong><span>Local build</span></div><div class="info-row"><strong>Build channel</strong><span>Desktop</span></div><div class="info-row"><strong>Copyright</strong><span>© 2026 QuizMaster</span></div></div>`, 'wide'),
      card('🌐', 'Links', 'Open official QuizMaster web destinations and support resources.', actionRow([button('Open quizmaster.liveforge.online', () => openExternal(localBaseUrl())), button('Open widgets.liveforge.online', () => openExternal(localBaseUrl()), 'secondary'), button('Open support page', () => placeholder('Open support page'), 'secondary')]), 'wide'),
      card('📝', 'Changelog', 'Release notes will appear here when update delivery is connected.', `<span class="status-badge future">Placeholder</span><p class="muted" style="margin-top:1rem">No changelog is bundled in this local settings view yet.</p>`),
      card('🙏', 'Credits', 'QuizMaster includes open-source libraries and platform integrations credited in bundled license files.', `<span class="status-badge online">Third-party credits available</span><p class="muted" style="margin-top:1rem">See the included third-party license documentation for detailed attribution.</p>`),
    ])
  }
};

const SHOW_FUTURE_INTEGRATION_CARDS = false;

let latestAudit = null;
let latestBackupStatus = null;
let latestIdentityStatus = null;
let latestAccountStatus = null;
let latestCloudStatus = null;
let runtimeStatus = { health: null, tiktok: null, urls: null };

function grid(cards) { return `<div class="settings-grid">${cards.join('')}</div>`; }
function card(icon, title, copy, body = '', span = '') {
  return `<article class="lf-card ${span}"><div class="card-top"><div style="display:flex;gap:.85rem;align-items:flex-start"><div class="card-icon">${icon}</div><div class="card-title-line"><h2>${title}</h2><p>${copy}</p></div></div></div>${body}</article>`;
}
function controls(rows) { return `<div class="control-list">${rows.join('')}</div>`; }
function toggleRow(label, copy, checked = false, disabled = false) {
  return `<div class="control-row"><div><span class="control-label">${label}</span><p class="control-copy">${copy}</p></div><label class="lf-toggle"><input type="checkbox" ${checked ? 'checked' : ''} ${disabled ? 'disabled' : ''}><span></span></label></div>`;
}
function actionRow(buttons) { return `<div class="action-row">${buttons.join('')}</div>`; }
function button(label, handler, style = '', disabled = false, tooltip = '') {
  const id = `btn_${Math.random().toString(36).slice(2)}`;
  queueMicrotask(() => {
    const el = document.getElementById(id);
    if (el && handler) el.addEventListener('click', handler);
  });
  const title = tooltip ? ` title="${safeAttr(tooltip)}" aria-label="${safeAttr(`${label}: ${tooltip}`)}"` : '';
  return `<button id="${id}" class="lf-button ${style}" ${disabled ? 'disabled' : ''}${title}>${label}</button>`;
}
function placeholder(label) { toast(`${label} is planned and not implemented yet.`); }
function openExternal(url) { window.open(url, '_blank', 'noopener,noreferrer'); }
function toast(message) {
  const el = document.getElementById('settingsToast');
  el.textContent = message;
  el.classList.add('show');
  clearTimeout(window.__settingsToastTimer);
  window.__settingsToastTimer = setTimeout(() => el.classList.remove('show'), 2800);
}
function safe(value, fallback = '—') { return value === undefined || value === null || value === '' ? fallback : value; }
function safeAttr(value) { return String(safe(value, '')).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }

async function refreshRuntimeStatus() {
  try { runtimeStatus.health = await fetch('/api/health', { cache: 'no-store' }).then(r => ({ ok: r.ok })); } catch { runtimeStatus.health = { ok: false }; }
  try { runtimeStatus.tiktok = await fetch('/api/tiktok/status', { cache: 'no-store' }).then(r => r.json()); } catch { runtimeStatus.tiktok = null; }
  try { runtimeStatus.urls = await fetch('/api/quizmaster/url-config', { cache: 'no-store' }).then(r => r.json()).then(d => d.config || d); } catch { runtimeStatus.urls = null; }
  try { latestBackupStatus = await fetch('/api/actions_events/pre_cloud_backup/status', { cache: 'no-store' }).then(r => r.json()).then(d => d.status || null); } catch { latestBackupStatus = null; }
  try { latestIdentityStatus = await fetch('/api/local_identity/status', { cache: 'no-store' }).then(r => r.json()).then(d => d.identity || null); } catch { latestIdentityStatus = null; }
  try { latestAccountStatus = await fetch('/api/account/status', { cache: 'no-store' }).then(r => r.json()).then(d => d.account || null); } catch { latestAccountStatus = null; }
  try { latestCloudStatus = await fetch('/api/cloud/status', { cache: 'no-store' }).then(r => r.json()).then(d => d.cloud || null); } catch { latestCloudStatus = null; }
  const badge = document.getElementById('bridgeHealthBadge');
  if (badge) {
    badge.textContent = runtimeStatus.health?.ok ? 'Bridge active' : 'Bridge offline';
    badge.className = `status-badge ${runtimeStatus.health?.ok ? 'online' : 'offline'}`;
  }
}


function identityProfile() { return (latestIdentityStatus && latestIdentityStatus.profile) || {}; }
function identityPaths() { return (latestIdentityStatus && latestIdentityStatus.paths) || {}; }
function identityReadyLabel() { return latestIdentityStatus?.ready ? 'Ready' : 'Warning'; }
function identityReadyClass() { return latestIdentityStatus?.ready ? 'online' : 'warn'; }
function cloudStatusLabel(value) { return value === 'not_connected' ? 'Not connected' : safe(value, 'Not connected'); }
function syncStatusLabel(value) { return value ? 'Enabled' : 'Disabled'; }
async function refreshIdentityStatus() {
  const data = await fetch('/api/local_identity/status', { cache: 'no-store' }).then(r => r.json());
  if (!data.success) throw new Error(data.error || 'Identity status failed');
  latestIdentityStatus = data.identity || null;
}
async function refreshAccountStatus() {
  const data = await fetch('/api/account/status', { cache: 'no-store' }).then(r => r.json());
  if (!data.success) throw new Error(data.error || 'Account status failed');
  latestAccountStatus = data.account || null;
}
async function refreshCloudStatus() {
  const data = await fetch('/api/cloud/status', { cache: 'no-store' }).then(r => r.json());
  if (!data.success) throw new Error(data.error || 'Cloud status failed');
  latestCloudStatus = data.cloud || null;
}
async function refreshAccountsCloud() {
  toast('Refreshing local account architecture status…');
  try {
    await Promise.all([refreshIdentityStatus(), refreshAccountStatus(), refreshCloudStatus()]);
    renderPage('accounts');
    toast(latestAccountStatus?.ready ? 'Account architecture is ready.' : 'Account architecture needs attention.');
  } catch (error) { toast(`Account refresh failed: ${error.message}`); }
}
async function copyText(value, label) {
  if (!value) { toast(`${label} is not available yet.`); return; }
  try {
    await navigator.clipboard.writeText(value);
    toast(`${label} copied.`);
  } catch {
    const area = document.createElement('textarea');
    area.value = value;
    document.body.appendChild(area);
    area.select();
    document.execCommand('copy');
    area.remove();
    toast(`${label} copied.`);
  }
}
function accountState() { return (latestAccountStatus && latestAccountStatus.account_state) || {}; }
function cloudState() { return (latestCloudStatus && latestCloudStatus.cloud_state) || {}; }
function syncQueue() { return (latestCloudStatus && latestCloudStatus.sync_queue) || {}; }
function cloudPaths() { return (latestCloudStatus && latestCloudStatus.paths) || {}; }
function pendingSyncCount() { return latestCloudStatus?.pending_sync_count ?? (Array.isArray(syncQueue().pending_operations) ? syncQueue().pending_operations.length : 0); }
function accountReadyClass() { return latestAccountStatus?.ready ? 'online' : 'warn'; }
function resolvedIdentity() { return (latestAccountStatus && latestAccountStatus.identity) || (runtimeStatus.urls && runtimeStatus.urls.IDENTITY) || {}; }
function accountStatusLabel(value) {
  if (value === 'signed_in') return 'Signed in';
  if (value === 'signed_in_missing_public_widget_id') return 'Signed in — routing warning';
  if (value === 'not_signed_in') return 'Not signed in';
  if (value === 'local_identity_unavailable') return 'Local identity unavailable';
  return safe(value, 'Not signed in');
}
function profileButton(label, section = '') { return button(label, () => openExternal(`/account.html${section}`), 'secondary'); }
function developerDetailsEnabled() { return localStorage.getItem('quizmaster.developerDetails') === 'true'; }
function setDeveloperDetailsEnabled(enabled) {
  localStorage.setItem('quizmaster.developerDetails', enabled ? 'true' : 'false');
  renderPage(currentPageKey());
  toast(enabled ? 'Developer Details enabled for support.' : 'Developer Details hidden.');
}
function developerToggleRow() {
  const checked = developerDetailsEnabled();
  const id = `developer_details_${Math.random().toString(36).slice(2)}`;
  queueMicrotask(() => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('change', event => setDeveloperDetailsEnabled(event.target.checked));
  });
  return `<div class="control-row"><div><span class="control-label">Developer Details</span><p class="control-copy">Show raw IDs, file paths, technical versions, and setup stages only when QuizMaster Support asks for them.</p></div><label class="lf-toggle"><input id="${id}" type="checkbox" ${checked ? 'checked' : ''}><span></span></label></div>`;
}
function comingSoonButton(label) { return button(`${label} — Coming Soon`, null, 'secondary', true, 'Coming in a future update.'); }
function renderAccountsCloud() {
  const identity = latestIdentityStatus || {};
  const account = accountState();
  const cloud = cloudState();
  const owner = resolvedIdentity();
  const signedIn = !!owner.authenticated;
  const hasPublicWidgetId = !!owner.public_widget_id;
  const routingStatus = signedIn
    ? (hasPublicWidgetId ? `Account-owned: /u/${safe(owner.active_runtime_id)}` : 'Blocked — missing public_widget_id')
    : (owner.local_profile_id ? `Local fallback: /u/${safe(owner.local_profile_id)}` : 'Unavailable — local profile missing');
  const accountWarning = owner.warning || latestCloudStatus?.error || latestCloudStatus?.warning || latestAccountStatus?.error || latestAccountStatus?.warning || latestIdentityStatus?.error || latestIdentityStatus?.warning;
  const urlConfig = runtimeStatus.urls || {};
  const publicBase = urlConfig.ACTIVE_BASE_URL || urlConfig.LOCAL_BASE_URL || urlConfig.PUBLIC_BASE_URL || urlConfig.public_base || localBaseUrl();
  return grid([
    card('☁️', 'Cloud Readiness', 'Cloud sync is not enabled yet; local data remains on this device.', `<div class="badge-row"><span class="status-badge future">Sync Status: Not enabled yet</span><span class="status-badge future">Cloud Sync: Coming later</span><span class="status-badge offline">Uploads: Disabled</span></div><div class="info-list"><div class="info-row"><strong>Cloud Availability</strong><span>Local-first desktop mode</span></div><div class="info-row"><strong>Connection State</strong><span>${signedIn ? 'Account connected' : 'Not signed in'}</span></div><div class="info-row"><strong>Sync State</strong><span>Not enabled yet</span></div><div class="info-row"><strong>Pending Sync Items</strong><span>${pendingSyncCount()}</span></div><div class="info-row"><strong>Cloud Sync</strong><span>Coming later</span></div><div class="info-row"><strong>Dashboard</strong><span>localhost:5555/dashboard</span></div></div><p class="muted" style="margin-top:1rem">No cloud sync, uploads, billing, or Stripe features are enabled by this screen.</p>${accountWarning ? `<p class="muted warn-text">Setup notice: ${safe(accountWarning)}</p>` : ''}`, 'wide'),
    card('👤', 'Account Status', signedIn ? 'This desktop app is connected to your QuizMaster account.' : 'This desktop app is using the local/offline fallback identity.', `<div class="badge-row"><span class="status-badge ${signedIn ? 'online' : 'future'}">Status: ${accountStatusLabel(owner.account_status)}</span><span class="status-badge online">Plan: ${safe(owner.plan || account.subscription_tier, 'Free')}</span><span class="status-badge future">Cloud Sync: Not enabled yet</span></div><div class="info-list"><div class="info-row"><strong>Email</strong><span>${signedIn ? safe(owner.email) : 'Not signed in'}</span></div><div class="info-row"><strong>Plan</strong><span>${safe(owner.plan || account.subscription_tier, 'Free')}</span></div><div class="info-row"><strong>Widget Routing Status</strong><span>${routingStatus}</span></div><div class="info-row"><strong>Sync Status</strong><span>Not enabled yet</span></div><div class="info-row"><strong>Cloud Sync</strong><span>Coming later</span></div><div class="info-row"><strong>Public Base URL</strong><span>${safe(publicBase)}</span></div></div>${!hasPublicWidgetId && signedIn ? '<p class="muted warn-text" style="margin-top:1rem">Warning: public_widget_id is missing, so QuizMaster will not generate account-owned public widget URLs.</p>' : ''}`, 'wide'),
    card('🪪', 'Runtime Ownership', 'Widgets, actions, analytics, and future cloud routing use this single resolved owner ID.', `<div class="badge-row"><span class="status-badge ${owner.active_runtime_id ? 'online' : 'warn'}">Active Runtime ID: ${owner.active_runtime_id ? 'Ready' : 'Blocked'}</span><span class="status-badge ${identity.ready ? 'online' : 'warn'}">Local Profile: ${identity.ready ? 'Ready' : 'Needs Attention'}</span><span class="status-badge online">Local Data: Protected</span></div><div class="info-list"><div class="info-row"><strong>active_runtime_id</strong><span>${safe(owner.active_runtime_id)}</span></div><div class="info-row"><strong>public_widget_id</strong><span>${safe(owner.public_widget_id)}</span></div><div class="info-row"><strong>local_profile_id</strong><span>${safe(owner.local_profile_id || identity.profile?.profile_id)}</span></div><div class="info-row"><strong>URL Mode</strong><span>${safe(owner.url_mode)}</span></div><div class="info-row"><strong>Local Data</strong><span>Protected; logout does not delete local widgets/actions/events.</span></div></div>`, 'wide'),
    card('🧭', 'Account Actions', signedIn ? 'Manage the connected desktop account session.' : 'Sign in from startup or continue using local/offline mode.', actionRow(signedIn ? [profileButton('Open Profile'), button('Open Dashboard', () => openExternal(localUrl('/dashboard')), 'secondary'), button('Refresh Status', refreshAccountsCloud, 'secondary')] : [profileButton('Open Profile'), button('Refresh Status', refreshAccountsCloud, 'secondary')]), 'wide'),
  ]);
}

function renderDataBackups() {
  const status = latestBackupStatus || {};
  const lastBackup = status.last_backup || {};
  const lastSnapshot = status.last_snapshot || {};
  const counts = status.counts || {};
  const localBackupsAvailable = lastBackup.status === 'available' ? 'Available' : 'Ready to Create';
  const mediaIssueCount = counts.media_issue_count ?? latestAudit?.media?.issue_count;
  return grid([
    card('🛡️', 'Local Data Protection', 'QuizMaster keeps your saved QuizMaster on this device and can create a safe local backup before future cloud features arrive.', `<div class="badge-row"><span class="status-badge online">Local Data: Protected</span><span class="status-badge ${lastBackup.status === 'available' ? 'online' : 'future'}">Backups: ${localBackupsAvailable}</span><span class="status-badge future">Cloud Sync: Coming Soon</span></div><div class="info-list"><div class="info-row"><strong>Cloud Backup</strong><span>Coming Soon</span></div><div class="info-row"><strong>Local Backup</strong><span>Available</span></div><div class="info-row"><strong>Sync Queue</strong><span>Disabled</span></div><div class="info-row"><strong>Local backups available</strong><span>${localBackupsAvailable}</span></div><div class="info-row"><strong>Last backup status</strong><span>${formatBackupStatus(lastBackup)}</span></div><div class="info-row"><strong>Saved data snapshot</strong><span>${formatBackupStatus(lastSnapshot)}</span></div></div>`, 'wide'),
    card('📊', 'Saved Data Summary', 'A quick count of the saved automations and media checks QuizMaster can see locally.', `<div class="audit-grid"><div class="audit-metric"><span class="audit-label">Actions saved</span><strong>${safe(counts.actions)}</strong></div><div class="audit-metric"><span class="audit-label">Events saved</span><strong>${safe(counts.events)}</strong></div><div class="audit-metric"><span class="audit-label">Media issues</span><strong>${safe(mediaIssueCount)}</strong></div><div class="audit-metric"><span class="audit-label">Safety Check</span><strong>${latestAudit ? (latestAudit.summary?.status === 'pass' ? 'Pass' : 'Review') : 'Not Run'}</strong></div></div>`, 'wide'),
    card('💾', 'Local Backup', 'Create a local copy of your current saved setup. Existing settings, actions, events, and media references are not rewritten.', `<p class="muted">Local Backup creates a saved data snapshot for support and future setup readiness while keeping QuizMaster local-only.</p>${actionRow([button('Create Backup', createPreCloudBackup), button('Run Safety Check', runMigrationAudit, 'secondary')])}`, 'wide'),
  ]);
}

function formatBackupStatus(item) {
  if (!item || item.status === 'not_created') return 'Not created';
  return item.created_at || item.captured_at || 'Available';
}
async function refreshBackupStatus() {
  const data = await fetch('/api/actions_events/pre_cloud_backup/status', { cache: 'no-store' }).then(r => r.json());
  if (!data.success) throw new Error(data.error || 'Backup status failed');
  latestBackupStatus = data.status || null;
}
async function createPreCloudBackup() {
  toast('Creating Local Backup…');
  try {
    const data = await fetch('/api/actions_events/pre_cloud_backup', { method: 'POST', cache: 'no-store' }).then(r => r.json());
    if (!data.success) throw new Error(data.error || 'Backup failed');
    await refreshBackupStatus();
    renderPage('data');
    toast('Local Backup created.');
  } catch (error) { toast(`Backup failed: ${error.message}`); }
}

function renderDiagnostics() {
  return grid([
    card('🧭', 'Safety Check', 'Diagnostics help identify missing media, broken references, or setup issues before cloud features are enabled.', `${auditControls()}${auditSummary()}`, 'wide'),
    card('🎞️', 'Media Reference Check', 'Review whether saved media references still point to available local files.', `<div class="info-list"><div class="info-row"><strong>Media Reference Check</strong><span>${latestAudit ? 'Included in last Safety Check' : 'Run Safety Check'}</span></div><div class="info-row"><strong>Issues Found</strong><span>${safe(latestAudit?.media?.issue_count)}</span></div></div>`),
    card('📋', 'Support Report', 'Generate Support Report is reserved for a future support workflow and does not expose raw report data by default.', actionRow([button('Generate Support Report — Coming Soon', () => placeholder('Generate Support Report'), 'secondary', true)])),
    card('🛡️', 'Last Check Status', 'Diagnostics are safe to run during stream preparation and do not repair, delete, rewrite, or migrate your saved setup.', `<div class="info-list"><div class="info-row"><strong>Last Check Status</strong><span>${latestAudit ? (latestAudit.summary?.status === 'pass' ? 'Pass' : 'Review Needed') : 'Not Run'}</span></div><div class="info-row"><strong>Issues Found</strong><span>${safe(latestAudit?.summary?.issue_count)}</span></div></div>`, 'wide'),
  ]);
}
function auditControls() { return actionRow([button('Run Safety Check', runMigrationAudit), button('Generate Support Report — Coming Soon', () => placeholder('Generate Support Report'), 'secondary', true)]); }
function auditSummary() {
  const audit = latestAudit || {};
  const summary = audit.summary || {};
  const counts = audit.counts || {};
  const media = audit.media || {};
  const assets = audit.assets || {};
  return `<div class="audit-grid"><div class="audit-metric"><span class="audit-label">Status</span><strong>${summary.status === 'pass' ? 'Pass' : latestAudit ? 'Review' : 'Not run'}</strong></div><div class="audit-metric"><span class="audit-label">Actions</span><strong>${safe(counts.actions)}</strong></div><div class="audit-metric"><span class="audit-label">Events</span><strong>${safe(counts.events)}</strong></div><div class="audit-metric"><span class="audit-label">Issues Found</span><strong>${safe(summary.issue_count)}</strong></div><div class="audit-metric"><span class="audit-label">Media Files</span><strong>${safe(assets.total_file_count)}</strong></div></div><div class="audit-details ${latestAudit ? '' : 'hidden'}"><div class="audit-detail-row"><strong>Generated</strong><span>${safe(audit.generated_at)}</span></div><div class="audit-detail-row"><strong>Read-only</strong><span>${audit.read_only ? 'Yes — saved settings, actions, events, links, and assets were not changed.' : '—'}</span></div><div class="audit-detail-row"><strong>Media Reference Check</strong><span>${media.valid_reference_count || 0} valid of ${media.total_references || 0} reference(s); ${media.issue_count || 0} issue(s)</span></div><div class="audit-issues">${auditIssues()}</div></div>`;
}
function auditIssues() {
  if (!latestAudit) return '';
  const links = latestAudit.links || {}; const media = latestAudit.media || {};
  const issues = [
    ['Disconnected action links', links.orphaned_link_count || 0],
    ['Events pointing to missing actions', links.event_action_id_issue_count || 0],
    ['Duplicate action entries', (links.duplicate_action_ids || []).length],
    ['Duplicate event entries', (links.duplicate_event_ids || []).length],
    ['Media reference issues', media.issue_count || 0],
  ].filter(([, c]) => c > 0).map(([l, c]) => `<div class="audit-issue">${l}: ${c}</div>`).join('');
  return issues || '<div class="audit-issue audit-pass">No issues detected.</div>';
}
async function runMigrationAudit() {
  toast('Running read-only Safety Check…');
  try {
    const data = await fetch('/api/actions_events/migration_audit', { cache: 'no-store' }).then(r => r.json());
    if (!data.success) throw new Error(data.error || 'Safety Check failed');
    latestAudit = data.audit || {};
    await refreshBackupStatus().catch(() => {});
    renderPage(currentPageKey());
    toast('Read-only Safety Check complete.');
  } catch (error) { toast(`Safety Check failed: ${error.message}`); }
}
function viewLatestReport() {
  if (!latestAudit) { toast('Run Safety Check first to create support details.'); return; }
  const blob = new Blob([JSON.stringify(latestAudit, null, 2)], { type: 'application/json' });
  openExternal(URL.createObjectURL(blob));
}


function localBaseUrl() {
  const urls = runtimeStatus.urls || {};
  return urls.ACTIVE_BASE_URL || urls.LOCAL_BASE_URL || urls.PUBLIC_BASE_URL || window.location.origin || 'http://localhost:5555';
}
function localUrl(path = '') {
  return `${String(localBaseUrl()).replace(/\/+$/, '')}${path}`;
}

function renderIntegrations() {
  const tk = runtimeStatus.tiktok || {};
  const urls = runtimeStatus.urls || {};
  const connected = !!(tk.connected || tk.is_connected || tk.status === 'connected');
  const widgetBase = urls.ACTIVE_BASE_URL || urls.LOCAL_BASE_URL || urls.public_base || urls.PUBLIC_BASE || localBaseUrl();
  const cards = [
    card('🎵', 'TikTok LIVE', 'Launch integration for live events, gifts, comments, likes, shares, and stream automation.', `<div class="badge-row"><span class="status-badge ${connected ? 'online' : 'offline'}">${connected ? 'Connected' : 'Not connected'}</span><span class="status-badge online">User-facing</span></div><div class="info-list"><div class="info-row"><strong>Username</strong><span>${safe(tk.username || tk.uniqueId || tk.saved_username)}</span></div><div class="info-row"><strong>Event bridge</strong><span>Available when connected</span></div></div>`, 'wide'),
    card('🌉', 'Bridge status', 'The local bridge moves TikTok events into widgets, overlays, and QuizMaster.', `<span class="status-badge ${runtimeStatus.health?.ok ? 'online' : 'offline'}">${runtimeStatus.health?.ok ? 'Bridge active' : 'Bridge offline'}</span>`),
    card('🔗', 'Widget URL status', 'Widget URLs are generated from QuizMaster URL configuration for OBS/browser sources.', `<span class="status-badge online">Configured</span><div class="url-box">${widgetBase}</div>`),
    card('☁️', 'Cloudflare/domain status', 'Public domain routing keeps shareable widget URLs stable when tunnel support is enabled.', `<div class="badge-row"><span class="status-badge future">Domain ready</span><span class="status-badge warn">Tunnel depends on environment</span></div><p class="muted" style="margin-top:1rem">Only QuizMaster integrations are shown in this lightweight build.</p>`, 'wide')
  ];
  if (SHOW_FUTURE_INTEGRATION_CARDS) {
    cards.push(
      card('🟣', '', 'Future integration card hidden unless explicitly enabled for internal testing.', `<span class="status-badge future">Future</span>`),
      card('🟢', '', 'Future integration card hidden unless explicitly enabled for internal testing.', `<span class="status-badge future">Future</span>`)
    );
  }
  return grid(cards);
}

function renderAdvanced() {
  const urls = runtimeStatus.urls || {};
  const cards = [
    card('🛠️', 'Developer Details', 'Default is Off. Enable only for support/debugging to reveal raw local IDs, file paths, technical versions, and setup stages.', controls([developerToggleRow()]), 'wide'),
    card('🖥️', 'Local server status', 'Technical status for the in-app FastAPI bridge and local browser routes.', `<div class="badge-row"><span class="status-badge ${runtimeStatus.health?.ok ? 'online' : 'offline'}">${runtimeStatus.health?.ok ? 'Online' : 'Offline'}</span><span class="status-badge future">Port 5555 default</span></div>`, 'third'),
    card('🌐', 'Public URL base', 'Base URL used when QuizMaster creates public widget links.', `<div class="url-box">${safe(urls.ACTIVE_BASE_URL || urls.LOCAL_BASE_URL || urls.public_base || urls.PUBLIC_BASE, localBaseUrl())}</div>`, 'third'),
    card('🔌', 'Socket.IO status', 'Socket.IO is used for real-time widget and dashboard events.', `<span class="status-badge ${runtimeStatus.health?.ok ? 'online' : 'offline'}">${runtimeStatus.health?.ok ? 'Available' : 'Unavailable'}</span>`, 'third'),
    card('🧭', 'URL mode explanation', 'QuizMaster can reference local routes for in-app pages and public routes for widgets that need to be shared or loaded externally.', `<div class="info-list"><div class="info-row"><strong>Local route example</strong><span>${localUrl('/main_tab.html')}</span></div><div class="info-row"><strong>Public route example</strong><span>${localUrl('/widget')}</span></div></div>`, 'wide'),
  ];
  if (developerDetailsEnabled()) cards.push(renderDeveloperDetailsCard());
  return grid(cards);
}
function renderDeveloperDetailsCard() {
  const profile = identityProfile();
  const identityPath = identityPaths();
  const account = accountState();
  const cloud = cloudState();
  const accountPaths = (latestAccountStatus && latestAccountStatus.paths) || {};
  const cloudPath = cloudPaths();
  const backup = latestBackupStatus || {};
  const backupPaths = backup.appdata_paths || {};
  const lastBackup = backup.last_backup || {};
  return card('🧰', 'Developer Details', 'Support/debugging details. These raw values are hidden from normal users when Developer Details is Off.', `<div class="developer-details-card"><div class="info-list"><div class="info-row"><strong>auth_user_id present?</strong><span>${resolvedIdentity().auth_user_id_present ? 'yes' : 'no'}</span></div><div class="info-row"><strong>public_widget_id</strong><span>${safe(resolvedIdentity().public_widget_id)}</span></div><div class="info-row"><strong>local_profile_id</strong><span>${safe(resolvedIdentity().local_profile_id || profile.profile_id)}</span></div><div class="info-row"><strong>active_runtime_id</strong><span>${safe(resolvedIdentity().active_runtime_id)}</span></div><div class="info-row"><strong>account_status</strong><span>${safe(resolvedIdentity().account_status)}</span></div><div class="info-row"><strong>URL mode</strong><span>${safe(resolvedIdentity().url_mode)}</span></div><div class="info-row"><strong>current active base URL</strong><span>${safe((runtimeStatus.urls || {}).ACTIVE_BASE_URL || (runtimeStatus.urls || {}).LOCAL_BASE_URL || (runtimeStatus.urls || {}).PUBLIC_BASE_URL)}</span></div><div class="info-row"><strong>Profile ID</strong><span>${safe(profile.profile_id)}</span></div><div class="info-row"><strong>Installation ID</strong><span>${safe(profile.installation_id)}</span></div><div class="info-row"><strong>Device ID</strong><span>${safe(profile.device_id)}</span></div><div class="info-row"><strong>Local Profile File</strong><span>${safe(identityPath.profile_json)}</span></div><div class="info-row"><strong>Account Settings File</strong><span>${safe(accountPaths.account_state_json)}</span></div><div class="info-row"><strong>cloud_state.json path</strong><span>${safe(cloudPath.cloud_state_json)}</span></div><div class="info-row"><strong>sync_queue.json path</strong><span>${safe(cloudPath.sync_queue_json)}</span></div><div class="info-row"><strong>API base URL</strong><span>${safe(cloud.api_base_url)}</span></div><div class="info-row"><strong>widgets base URL</strong><span>${safe(cloud.widgets_base_url)}</span></div><div class="info-row"><strong>pending sync count</strong><span>${pendingSyncCount()}</span></div><div class="info-row"><strong>AppData root</strong><span>${safe(backupPaths.appdata_root || identityPath.appdata_root || accountPaths.appdata_root || cloudPath.appdata_root)}</span></div><div class="info-row"><strong>Latest backup path</strong><span>${safe(lastBackup.path)}</span></div><div class="info-row"><strong>Profile schema version</strong><span>${safe(profile.schema_version)}</span></div><div class="info-row"><strong>Account schema version</strong><span>${safe(account.schema_version)}</span></div><div class="info-row"><strong>Cloud schema version</strong><span>${safe(cloud.schema_version)}</span></div><div class="info-row"><strong>Snapshot schema version</strong><span>${safe(backup.last_snapshot?.schema_version)}</span></div><div class="info-row"><strong>Migration stages</strong><span>${safe(profile.migration_stage)}</span></div><div class="info-row"><strong>Setup Status</strong><span>${latestIdentityStatus?.ready && latestAccountStatus?.ready && latestCloudStatus?.ready ? 'Ready' : 'Needs Attention'}</span></div></div>${actionRow([button('Open diagnostics report', viewLatestReport, 'secondary'), button('Run Safety Check', runMigrationAudit)])}</div>`, 'wide');
}

function currentPageKey() {
  const params = new URLSearchParams(window.location.search);
  return params.get('section') || 'general';
}
function normalizePage(key) { return SETTINGS_PAGES[key] ? key : 'general'; }
function renderPage(key = currentPageKey()) {
  key = normalizePage(key);
  const page = SETTINGS_PAGES[key];
  document.getElementById('settingsTitle').textContent = page.title;
  document.getElementById('settingsDescription').textContent = page.description;
  document.getElementById('settingsContent').innerHTML = page.render();
}

document.addEventListener('DOMContentLoaded', async () => {
  await refreshRuntimeStatus();
  renderPage();
});
