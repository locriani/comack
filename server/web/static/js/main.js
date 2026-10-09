/**
 * Dashboard entry point: wires the token challenge, tabs, WebSocket,
 * dashboard/archive/settings views, and the first-run onboarding wizard.
 */
import { apiJson } from './api.js';
import { wireTokenChallenge } from './token-prompt.js';
import { connect, onConnectionStatus } from './ws.js';
import { initDashboard, setConnectionBadge } from './views/dashboard.js';
import { initArchive } from './views/archive.js';
import { initSettings } from './views/settings.js';
import { initOnboarding } from './views/onboarding.js';

wireTokenChallenge();

const $ = (id) => document.getElementById(id);
const sections = {
  dashboard: $('view-dashboard'),
  archive: $('view-archive'),
  settings: $('view-settings'),
};
const tabs = [...document.querySelectorAll('[data-view]')];
let archiveRef = null;

function showView(name) {
  for (const [key, section] of Object.entries(sections)) {
    section.classList.toggle('hidden', key !== name);
  }
  tabs.forEach((t) => {
    const on = t.dataset.view === name;
    t.classList.toggle('text-indigo-300', on);
    t.classList.toggle('border-indigo-500', on);
    t.classList.toggle('text-gray-400', !on);
    t.setAttribute('aria-selected', String(on));
  });
  if (name === 'archive') archiveRef?.refresh();
}

for (const tab of tabs) {
  tab.addEventListener('click', () => showView(tab.dataset.view));
}

// Identity comes from the server (hostname, bound port, display model) — the
// header never hardcodes device labels.
async function renderIdentity() {
  try {
    const { identity } = await apiJson('/api/status');
    $('identityLine').textContent = `${identity.hostname}:${identity.port} \u2022 ${identity.display}`;
  } catch { /* a wrong label is worse than no label */ }
}

async function boot() {
  let config;
  try {
    config = await apiJson('/api/config');
  } catch (e) {
    if (e?.status === 401) return;  // token challenge is up; the page reloads after it is saved
    $('identityLine').textContent = 'dashboard offline';
    return;
  }

  try {
    initDashboard();
    archiveRef = initArchive($('view-archive'));
    initSettings($('view-settings'), config);
    onConnectionStatus(setConnectionBadge);
    connect();
    renderIdentity();
    showView('dashboard');
  } catch (e) {
    // A broken view must not fail silently: log it and say so in the header.
    console.error('dashboard boot failed:', e);
    $('identityLine').textContent = 'dashboard error (see console)';
  }

  if (!config.values.ONBOARDED) {
    initOnboarding(config, { onComplete: () => window.location.reload() });
  }
}

boot();
