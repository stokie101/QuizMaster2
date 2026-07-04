const dashboardUrl = 'https://liveforge.online/dashboard?app=quizmaster';

const el = (id) => document.getElementById(id);

function setError(message) {
  const box = el('accountError');
  if (!box) return;
  if (!message) {
    box.style.display = 'none';
    box.textContent = '';
    return;
  }
  box.textContent = message;
  box.style.display = 'block';
}

function updateText(id, value) {
  const node = el(id);
  if (node) node.textContent = value;
}

function updateProfile(profile, syncStatus, identity) {
  const signedIn = !!identity?.authenticated;
  const appSlug = profile?.app_slug || 'quizmaster';
  const appName = profile?.app_name || 'QuizMaster';
  const name = profile?.display_name || (signedIn ? 'QuizMaster User' : 'QuizMaster account required');
  const email = signedIn ? (identity?.email || profile?.email || '—') : 'Not signed in';
  const plan = identity?.plan || profile?.subscription_tier || profile?.plan || 'Free';
  const subscriptionStatus = profile?.subscription_status || (signedIn ? 'active' : 'inactive');

  updateText('displayName', name);
  updateText('email', email);
  updateText('appName', appName);
  updateText('appSlug', appSlug);
  updateText('plan', plan);
  updateText('subscriptionStatus', subscriptionStatus);
  updateText('appStatus', signedIn ? (profile?.app_link_status || 'Linked') : 'Not signed in');
  updateText('widgetStatus', identity?.public_widget_id
    ? `Ready: ${identity.public_widget_id}`
    : (signedIn ? 'Blocked — missing QuizMaster public_widget_id' : 'Blocked — sign in to generate official profile URLs'));
  updateText('syncStatus', syncStatus || 'Not enabled yet');
  updateText('avatarInitial', (name || email || 'QM').trim().slice(0, 2).toUpperCase());
  updateText('connectionPill', signedIn ? 'Connected' : 'Not signed in');

  el('loadingState').hidden = true;
  el('profileState').hidden = false;
}

async function loadProfile() {
  setError('');
  el('loadingState').hidden = false;
  try {
    const response = await fetch('/api/account/profile', { cache: 'no-cache' });
    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error(data.error || 'Could not read QuizMaster account state.');
    }
    updateProfile(data.profile || {}, data.sync_status, data.identity || {});
    if (!data.identity?.authenticated) {
      setError('Not signed in. Official QuizMaster profile URLs are blocked until a paid account session is restored or you sign in again.');
    } else if (!data.identity?.public_widget_id) {
      setError('Warning: this QuizMaster account is missing public_widget_id, so official profile URLs cannot be generated. Repair the website profile routing and sign in again.');
    } else if ((data.profile?.app_slug || 'quizmaster') !== 'quizmaster') {
      setError('Warning: this desktop app did not receive a QuizMaster-scoped account profile. Check app_slug routing on the website backend.');
    }
  } catch (error) {
    updateText('connectionPill', 'Needs attention');
    el('loadingState').hidden = true;
    setError(`Could not load your QuizMaster profile: ${error.message}`);
  }
}

async function logout() {
  if (!confirm('Log out of the connected QuizMaster account on this desktop app?')) return;
  setError('');
  try {
    const response = await fetch('/api/account/logout', { method: 'POST' });
    const data = await response.json();
    if (!response.ok || !data.success) throw new Error(data.error || 'Logout failed.');
    updateText('connectionPill', 'Logged out');
    setError('You are logged out. Official QuizMaster profile URLs are blocked until you sign in again.');
    el('profileState').hidden = true;
  } catch (error) {
    setError(`Logout failed: ${error.message}`);
  }
}

document.addEventListener('DOMContentLoaded', () => {
  el('dashboardBtn')?.addEventListener('click', () => window.open(dashboardUrl, '_blank'));
  el('retryBtn')?.addEventListener('click', loadProfile);
  el('logoutBtn')?.addEventListener('click', logout);
  loadProfile();
});
