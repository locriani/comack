/**
 * Dashboard view: live e-paper preview, per-device story-arc cards and comic
 * previews, sneaky-mode radar, mined story elements, prompt debug log, and
 * the speech simulator. The DOM lives in index.html (#view-dashboard).
 */
import { apiJson } from '../api.js';
import { arcBibleDetails } from '../arc-bible.js';
import { el, fmtTime } from '../dom.js';
import { bindLightbox } from '../lightbox.js';
import { onEvent } from '../ws.js';

const $ = (id) => document.getElementById(id);

export function setConnectionBadge(state) {
  const badge = $('connectionStatus');
  if (state === 'connected') {
    badge.className = 'flex items-center gap-2 text-xs mono px-2.5 py-1 rounded-full bg-emerald-950/70 text-emerald-300 border border-emerald-900/80';
    badge.replaceChildren(el('span', { class: 'w-2 h-2 rounded-full bg-emerald-400' }), el('span', {}, 'Live'));
  } else {
    const label = state === 'connecting' ? 'Connecting…' : state.charAt(0).toUpperCase() + state.slice(1);
    badge.className = 'flex items-center gap-2 text-xs mono px-2.5 py-1 rounded-full bg-red-950/70 text-red-300 border border-red-900/80';
    badge.replaceChildren(el('span', { class: 'w-2 h-2 rounded-full bg-red-400 animate-pulse' }), el('span', {}, label));
  }
}

// Button-press pulse: a device press surfaces as a transient chip beside the
// VAD indicator — visible ~3 s, then fades; a fresh press relabels and
// restarts the dwell. A pulse like VOICE DETECTED, not a log.
const PRESS_LABELS = { forward: 'Forward', back: 'Back', trigger: 'Green' };
const PRESS_CHIP_DWELL_MS = 3000;
const PRESS_CHIP_FADE_MS = 500;
const pressChipBase = 'items-center gap-1.5 text-xs mono px-2.5 py-0.5 rounded-full bg-indigo-950 text-indigo-300 border border-indigo-800 transition-opacity duration-500';
let pressChipTimer = null;

function showButtonPress(button) {
  const chip = $('buttonPressChip');
  const label = PRESS_LABELS[button];
  if (!label) return;

  chip.className = `flex ${pressChipBase}`;
  chip.replaceChildren(
    el('span', { class: 'w-2 h-2 rounded-full bg-indigo-400' }),
    el('span', {}, label)
  );
  clearTimeout(pressChipTimer);
  pressChipTimer = setTimeout(() => {
    chip.className = `flex ${pressChipBase} opacity-0`;
    pressChipTimer = setTimeout(() => { chip.className = 'hidden'; }, PRESS_CHIP_FADE_MS);
  }, PRESS_CHIP_DWELL_MS);
}

function updateSneakyStatus(status) {
  if (!status) return;

  const vad = $('vadIndicator');
  if (status.is_speaking) {
    vad.className = 'flex items-center gap-1.5 text-xs mono px-2.5 py-0.5 rounded-full bg-red-950/80 text-red-300 border border-red-900/80';
    vad.replaceChildren(el('span', { class: 'w-2 h-2 rounded-full bg-red-400 animate-ping' }), el('span', {}, 'Hearing talk'));
  } else {
    vad.className = 'flex items-center gap-1.5 text-xs mono px-2.5 py-0.5 rounded-full bg-emerald-950/70 text-emerald-300 border border-emerald-900/80';
    vad.replaceChildren(el('span', { class: 'w-2 h-2 rounded-full bg-emerald-400' }), el('span', {}, 'Quiet'));
  }

  $('sneakyCard').classList.toggle('hidden', !status.required_silence_seconds);
  $('currentSilence').innerText = status.current_silence_seconds.toFixed(1);
  $('reqSilence').innerText = status.required_silence_seconds.toFixed(0);
  $('silenceProgressBar').style.width = `${status.silence_progress_pct}%`;

  const alert = $('stealthQueueAlert');
  const badge = $('sneakyBadge');
  if (status.has_stealth_comic) {
    alert.classList.remove('hidden');
    $('pendingIssue').innerText = `#${status.pending_series_issue ?? status.pending_issue_number}`;
    badge.className = 'text-xs mono font-semibold px-2 py-0.5 rounded-md bg-amber-950 text-amber-300 border border-amber-800';
    badge.innerText = 'Holding';
  } else {
    alert.classList.add('hidden');
    badge.className = 'text-xs mono font-semibold px-2 py-0.5 rounded-md bg-[var(--ink-3)] text-[var(--fog)]';
    badge.innerText = 'Waiting';
  }
}

// ── Per-device cards (spec V8): one arc card and one preview card per device ──
// The registry (GET /api/devices) is the single source: each device's own
// active arc and the issue its display is showing. Previews use the tokenless
// per-issue png endpoint; the epoch in the query busts the cache only when a
// refresh event actually fired, so polling never flickers the images.
let previewEpoch = 0;

const deviceChip = (device) => el('span', {
  class: 'flex items-center gap-1.5 text-xs px-2 py-0.5 rounded-full bg-[var(--ink-3)] text-[var(--mist)] border border-[var(--line)]',
  title: `device: ${device.id}`,
}, el('span', { class: 'w-1.5 h-1.5 rounded-full bg-indigo-400' }), el('span', {}, device.name));

function arcCard(device, { bibleOpen = false } = {}) {
  const arc = device.active_arc;
  const progress = arc ? Math.min(100, 100 * arc.panels_used / arc.target_panels) : 0;
  const bible = arc ? arcBibleDetails(arc, { open: bibleOpen, summary: 'Full series bible — tone, art, cast' }) : null;
  return el('div', { class: 'card p-4 flex flex-col gap-2', 'data-device-arc': device.id },
    el('div', { class: 'flex items-center justify-between gap-2' },
      deviceChip(device),
      el('span', { class: 'text-xs px-2 py-0.5 rounded-md bg-[var(--ink-3)] text-[var(--fog)] truncate' }, arc ? arc.genre : '—')),
    el('h2', { class: 'text-lg font-bold' }, arc ? arc.title : 'Starting the next series…'),
    el('p', { class: 'text-sm text-[var(--fog)] italic leading-relaxed' },
      arc ? `"${arc.premise}"` : 'A brand-new arc begins with the next issue.'),
    el('div', { class: 'pt-1' },
      el('div', { class: 'flex justify-between text-xs text-[var(--fog)] mb-1' },
        el('span', {}, 'Through this arc'),
        el('span', { class: 'mono' }, arc ? `${arc.panels_used} / ${arc.target_panels} panels` : '—')),
      el('div', { class: 'w-full bg-[var(--ink)] rounded-full h-1.5 overflow-hidden' },
        el('div', { class: 'bg-indigo-500 h-1.5 rounded-full transition-all duration-500', style: `width: ${progress}%` })),
    ),
    bible);
}

function deviceCard(device) {
  const comic = device.current_comic;
  const live = device.connections.length > 0;
  const preview = comic
    ? el('img', {
        src: `/api/comics/${comic.issue_number}/png?e=${previewEpoch}`,
        alt: `Latest issue on ${device.name}`,
        class: 'w-full h-auto aspect-[800/480] object-cover bg-white rounded-sm',
      })
    : el('div', { class: 'w-full aspect-[800/480] bg-[var(--paper)] rounded-md flex items-center justify-center' },
        el('span', { class: 'text-xs text-[var(--fog)]' }, 'No comic yet'));
  return el('div', { class: 'card p-4 flex flex-col gap-2', 'data-device-card': device.id },
    el('div', { class: 'flex items-center justify-between gap-2' },
      el('span', { class: 'text-sm font-bold truncate' }, device.name),
      el('span', {
        class: `text-[10px] mono px-2 py-0.5 rounded-md ${live
          ? 'bg-emerald-950/70 text-emerald-300 border border-emerald-900/80'
          : 'bg-[var(--ink-3)] text-[var(--fog)]'}`,
      }, live ? 'Online' : 'Away')),
    el('div', { class: 'bg-[var(--paper)] p-1 rounded-lg' }, preview),
    el('div', { class: 'flex items-center justify-between gap-2 text-xs text-[var(--fog)]' },
      el('span', {}, comic ? `#${comic.series_issue ?? comic.issue_number} · ${comic.title}` : 'Waiting for its first issue'),
      el('a', { href: comic ? `/reader#${comic.issue_number}` : '/reader', class: 'text-indigo-300 hover:text-indigo-200' }, 'Open →')));
}

async function loadDevices() {
  try {
    const body = await apiJson('/api/devices');
    const devices = body.devices ?? [];
    $('deviceCount').textContent = devices.length === 1 ? '1 room' : `${devices.length} rooms`;
    const openBibles = new Set(
      [...$('arcCards').querySelectorAll('details[data-arc-bible][open]')].map((d) => d.dataset.arcBible));
    $('arcCards').replaceChildren(...devices.map((device) => arcCard(device, {
      bibleOpen: openBibles.has(String(device.active_arc?.id ?? '')),
    })));
    $('deviceCards').replaceChildren(...devices.map(deviceCard));
  } catch (e) {
    console.error('Error loading devices:', e);
  }
}

function showOriginals(issue, panelCount) {
  const base = `/api/comics/${issue}`;
  const box = $('originals');
  const color = $('colorImage');
  color.onload = () => box.classList.remove('hidden');
  color.onerror = () => box.classList.add('hidden');   // older issues have no originals
  color.src = `${base}/color.png`;
  bindLightbox(color, () => color.src, 'Colour composite');
  $('panelList').replaceChildren(...Array.from({ length: panelCount || 0 }, (_, i) => {
    const src = `${base}/panel${i + 1}.png`;
    // Panel 1 is usually the wide establishing shot — don't leave it half-width
    // in the 2/4-column thumbnail grid.
    const span = i === 0 ? ' col-span-2 sm:col-span-4' : '';
    const img = el('img', {
      src, alt: `Panel ${i + 1}, raw`, loading: 'lazy',
      class: `w-full h-auto rounded border border-gray-700${span}`,
    });
    img.onerror = () => img.remove();   // procedural panels have no raw art
    bindLightbox(img, src, `Panel ${i + 1}`);
    return img;
  }));
}

function setActiveIssue(issue, seriesIssue, title) {
  $('currentIssueTag').innerText = `#${seriesIssue ?? issue} — ${title}`;
  $('comicImage').src = `/api/comics/current.png?t=${Date.now()}`;
}

function updateComic(comic) {
  showOriginals(comic.issue_number, comic.panel_count);
  setActiveIssue(comic.issue_number, comic.series_issue, comic.title);
  if (comic.displayed_at) {
    $('lastRefreshedAt').innerText = `Updated ${fmtTime(comic.displayed_at)}`;
  }
}

function refreshComicImage(data) {
  showOriginals(data.issue_number, data.panel_count);
  setActiveIssue(data.issue_number, data.series_issue, data.title);
  $('lastRefreshedAt').innerText = `Updated ${new Date().toLocaleTimeString()} after ${data.silence_seconds}s quiet`;
  window.dispatchEvent(new CustomEvent('comics-changed'));
}

function addTranscriptItem(item) {
  const list = $('transcriptList');
  if (list.dataset.empty === 'true') list.replaceChildren();
  list.dataset.empty = 'false';
  const count = $('transcriptCount');
  if (count) {
    const n = list.children.length;
    count.textContent = n === 1 ? '1 note' : `${n} notes`;
  }
  list.prepend(el('div', { class: 'p-3 rounded-xl bg-[var(--ink)]/80 border border-[var(--line)]' },
    el('div', { class: 'text-[var(--paper)] leading-snug' }, item.scrambled_text),
    el('div', { class: 'text-[11px] text-[var(--fog)] mt-1.5' },
      `${fmtTime(item.timestamp)} · ${item.duration_seconds.toFixed(1)}s`)));
}

async function loadPrompts() {
  try {
    const entries = await apiJson('/api/prompts');
    const list = $('promptList');
    $('promptCount').textContent = `${entries.length} calls`;
    if (entries.length === 0) {
      list.replaceChildren(el('div', { class: 'text-[var(--fog)] italic' }, 'Nothing in the last 15 minutes.'));
      return;
    }
    const open = new Set([...list.querySelectorAll('details[open]')].map((d) => d.dataset.at));
    list.replaceChildren(...entries.map((entry) => {
      const d = el('details', { class: 'p-2.5 rounded-lg bg-[var(--ink)]/70 border border-[var(--line)]', 'data-at': String(entry.at) });
      d.open = open.has(d.dataset.at);
      const summary = el('summary', { class: 'cursor-pointer text-[var(--mist)] truncate' },
        `${fmtTime(entry.at)} · ${entry.model} · ${entry.prompt.slice(0, 90)}`);
      d.append(summary);
      for (const [label, text] of [['System', entry.system], ['Prompt', entry.prompt], ['Output', entry.output ?? '(waiting…)']]) {
        if (!text) continue;
        d.append(
          el('div', { class: 'text-[10px] font-bold text-indigo-300 mt-2' }, label),
          el('pre', { class: 'whitespace-pre-wrap text-[var(--paper)]' }, text));
      }
      return d;
    }));
  } catch (e) {
    console.error('Error loading prompts:', e);
  }
}

async function simulateSpeech() {
  const input = $('simInput');
  const text = input.value.trim();
  if (!text) return;
  try {
    await apiJson('/api/test/simulate_speech', { method: 'POST', json: { text, duration: 3.2 } });
    input.value = '';
  } catch (e) {
    alert('Error simulating speech: ' + e);
  }
}

export function initDashboard() {
  onEvent('INIT_SNAPSHOT', (data) => {
    updateSneakyStatus(data.sneaky_status);
    if (data.current_comic) updateComic(data.current_comic);
    loadDevices();
  });
  onEvent('SNEAKY_COMIC_REFRESH', (data) => {
    previewEpoch += 1;
    // The house viewer mirrors the default device's display; another room's
    // reveal must not relabel it (its own card updates via loadDevices).
    if (!data.device_id || data.device_id === 'default') refreshComicImage(data);
    loadDevices();
  });
  onEvent('NEW_COMIC_QUEUED', (data) => updateSneakyStatus(data.status));
  onEvent('NEW_TRANSCRIPT', (data) => addTranscriptItem(data.transcript));
  onEvent('BUTTON_PRESSED', (data) => showButtonPress(data.button));

  // Polling heartbeat for the silence timer (covers ws gaps)
  setInterval(async () => {
    try {
      const data = await apiJson('/api/status');
      updateSneakyStatus(data.sneaky);
    } catch { /* transient; the badge already shows connection state */ }
  }, 1000);

  // The registry's story state moves slower than the silence timer: arcs and
  // displayed issues refresh on their own cadence.
  setInterval(loadDevices, 5000);
  loadDevices();

  setInterval(loadPrompts, 5000);
  loadPrompts();

  $('sendSim').addEventListener('click', simulateSpeech);
  $('simInput').addEventListener('keydown', (e) => { if (e.key === 'Enter') simulateSpeech(); });
  $('generateBtn').addEventListener('click', async () => {
    try { await apiJson('/api/comics/generate', { method: 'POST' }); } catch (e) { alert('Error triggering generation: ' + e); }
  });
  $('forceRevealBtn').addEventListener('click', async () => {
    try { await apiJson('/api/comics/force_reveal', { method: 'POST' }); } catch (e) { alert('Error forcing reveal: ' + e); }
  });
}
