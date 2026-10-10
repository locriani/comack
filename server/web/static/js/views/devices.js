/**
 * Devices card (settings view): the registry behind per-device identity.
 * Lists registered devices with last-seen, adds a device (the token is shown
 * exactly once — only its hash is stored server-side, server/devices.py),
 * and renames. Backed by GET/POST/PATCH /api/devices.
 */
import { apiJson, ApiError } from '../api.js';
import { el } from '../dom.js';

const INPUT_CLASS = 'bg-gray-900 border border-gray-700 rounded px-3 py-1.5 text-sm text-white flex-1 focus:outline-none focus:border-indigo-500';

const fmtLastSeen = (iso) => (iso ? new Date(iso).toLocaleString() : 'never connected');

export function initDevicesCard(section) {
  const card = section.querySelector('#devicesCard');
  if (!card) return;

  let devices = [];
  const notices = el('div', { class: 'space-y-2' });
  const deviceRows = el('div', { class: 'divide-y divide-gray-800' });
  const count = el('span', { class: 'text-xs mono text-gray-400' });
  const newName = el('input', { type: 'text', placeholder: 'e.g. Living Room', class: INPUT_CLASS, spellcheck: 'false' });

  function addNotice(text, kind = 'error') {
    notices.replaceChildren(el('p', {
      class: `text-xs px-3 py-2 rounded border ${kind === 'error'
        ? 'bg-red-950/60 border-red-800 text-red-300'
        : 'bg-emerald-950/60 border-emerald-800 text-emerald-300'}`,
    }, text));
  }

  // ── token reveal: the one moment the credential exists in plaintext ──
  function tokenPanel(deviceId, token) {
    const shown = el('input', { type: 'text', readonly: 'readonly', value: token, class: `${INPUT_CLASS} font-mono text-xs` });
    const copy = el('button', {
      class: 'text-xs mono bg-gray-700 hover:bg-gray-600 text-white px-3 py-1.5 rounded font-mono shrink-0',
      onclick: async () => {
        shown.select();
        try { await navigator.clipboard.writeText(token); copy.textContent = 'copied'; }
        catch { document.execCommand('copy'); copy.textContent = 'copied'; }
        setTimeout(() => { copy.textContent = 'copy'; }, 1500);
      },
    }, 'copy');
    return el('div', { class: 'bg-amber-950/40 border border-amber-800/80 rounded-lg p-3 space-y-2' },
      el('p', { class: 'text-xs font-bold text-amber-300' }, `\u{1F511} Token for \u201c${deviceId}\u201d — copy it now; it is shown only once.`),
      el('p', { class: 'text-[11px] text-amber-400/80' },
        'Store it on the device (firmware constants or simulator flags). Only its SHA-256 hash is kept by the server.'),
      el('div', { class: 'flex space-x-2' }, shown, copy),
      el('button', {
        class: 'text-xs mono bg-amber-600 hover:bg-amber-500 text-black font-bold px-3 py-1.5 rounded',
        onclick: () => panel.replaceChildren(),
      }, 'I\u2019ve stored it — close'));
  }

  // ── rows ────────────────────────────────────────────────────────────────
  function nameCell(device) {
    return el('div', { class: 'flex-1 min-w-0' },
      el('div', { class: 'text-sm font-bold text-white truncate' }, device.name),
      el('div', { class: 'text-[10px] mono text-gray-500' }, device.id));
  }

  function renameRow(device, row) {
    const input = el('input', { type: 'text', value: device.name, class: INPUT_CLASS, spellcheck: 'false' });
    const buttons = el('div', { class: 'flex items-center gap-2 shrink-0' },
      el('button', {
        class: 'text-xs mono bg-indigo-600 hover:bg-indigo-500 text-white px-3 py-1.5 rounded',
        onclick: async () => {
          try {
            await apiJson(`/api/devices/${device.id}`, { method: 'PATCH', json: { name: input.value } });
            refresh();
          } catch (e) {
            addNotice(e instanceof ApiError ? e.errorLines().join(' ') : String(e));
          }
        },
      }, 'save'),
      el('button', {
        class: 'text-xs mono border border-gray-700 hover:border-gray-500 text-gray-400 px-3 py-1.5 rounded',
        onclick: () => refresh(),   // re-render abandons the edit
      }, 'cancel'));
    row.replaceChildren(input, buttons);
    input.focus();
    input.select();
  }

  function deviceRow(device) {
    const live = device.connections.length > 0;
    const row = el('div', { class: 'flex items-center gap-3 py-2.5' },
      el('span', {
        class: `text-[10px] mono px-2 py-0.5 rounded shrink-0 ${live
          ? 'bg-emerald-950 text-emerald-400 border border-emerald-800'
          : 'bg-gray-800 text-gray-400'}`,
        title: live ? 'its audio stream is connected' : 'no live audio stream',
      }, live ? 'LIVE' : 'IDLE'),
      nameCell(device),
      el('div', { class: 'text-xs mono text-gray-500 shrink-0' }, `last seen: ${fmtLastSeen(device.last_seen_at)}`),
      el('button', {
        class: 'text-xs mono border border-gray-700 hover:border-indigo-500 hover:text-indigo-300 text-gray-400 px-3 py-1.5 rounded shrink-0',
        onclick: () => renameRow(device, row),
      }, 'rename'));
    return row;
  }

  async function refresh() {
    try {
      const body = await apiJson('/api/devices');
      devices = body.devices ?? [];
      count.textContent = `${devices.length} registered`;
      deviceRows.replaceChildren(...devices.map(deviceRow));
      if (!devices.length) {
        deviceRows.replaceChildren(el('p', { class: 'text-sm text-gray-500 italic py-2' },
          'No devices registered yet — add one to give a room its own story.'));
      }
    } catch (e) {
      deviceRows.replaceChildren(el('p', { class: 'text-xs text-red-400 py-2' }, 'Could not load devices.'));
      console.error('Error loading devices:', e);
    }
  }

  async function addDevice() {
    const name = newName.value;
    if (!name.trim()) { addNotice('Give the device a name first.'); return; }
    try {
      const created = await apiJson('/api/devices', { method: 'POST', json: { name } });
      newName.value = '';
      addNotice(`Registered \u201c${created.name}\u201d (${created.id}).`, 'ok');
      panel.replaceChildren(tokenPanel(created.name, created.token));
      refresh();
    } catch (e) {
      addNotice(e instanceof ApiError ? e.errorLines().join(' ') : String(e));
    }
  }

  const panel = el('div', { class: 'space-y-2' });
  const addRow = el('div', { class: 'flex space-x-2' },
    newName,
    el('button', {
      class: 'bg-indigo-600 hover:bg-indigo-500 text-white text-xs px-4 py-1.5 rounded font-medium shrink-0',
      onclick: addDevice,
    }, 'Add device'));
  newName.addEventListener('keydown', (e) => { if (e.key === 'Enter') addDevice(); });

  card.replaceChildren(
    el('div', { class: 'flex items-center justify-between' },
      el('h3', { class: 'text-sm font-bold text-white' }, '\u{1F4FA} Devices'),
      count),
    el('p', { class: 'text-[11px] text-gray-500' },
      'Each device gets its own audio pipeline, story arc, and comic feed. It presents its id and token on the audio stream and comic requests.'),
    notices,
    addRow,
    panel,
    deviceRows);

  refresh();
}
