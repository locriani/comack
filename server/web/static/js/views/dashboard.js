/**
 * Dashboard view: live e-paper preview, story-arc status, sneaky-mode radar,
 * mined story elements, prompt debug log, and the speech simulator.
 * A port of the original single-file dashboard script onto the shared
 * api/ws/dom modules; the DOM lives in index.html (#view-dashboard).
 */
import { apiJson } from '../api.js';
import { el, fmtTime } from '../dom.js';
import { onEvent } from '../ws.js';

const $ = (id) => document.getElementById(id);

export function setConnectionBadge(state) {
  const badge = $('connectionStatus');
  if (state === 'connected') {
    badge.className = 'flex items-center space-x-2 text-xs mono px-3 py-1 rounded-full bg-emerald-950/80 text-emerald-400 border border-emerald-800';
    badge.replaceChildren(el('span', { class: 'w-2 h-2 rounded-full bg-emerald-400' }), el('span', {}, 'CONNECTED'));
  } else {
    badge.className = 'flex items-center space-x-2 text-xs mono px-3 py-1 rounded-full bg-red-950/80 text-red-400 border border-red-800';
    badge.replaceChildren(el('span', { class: 'w-2 h-2 rounded-full bg-red-500 animate-pulse' }), el('span', {}, state.toUpperCase()));
  }
}

function updateSneakyStatus(status) {
  if (!status) return;

  const vad = $('vadIndicator');
  if (status.is_speaking) {
    vad.className = 'flex items-center space-x-1.5 text-xs mono px-2.5 py-0.5 rounded-full bg-red-950 text-red-400 border border-red-800';
    vad.replaceChildren(el('span', { class: 'w-2 h-2 rounded-full bg-red-500 animate-ping' }), el('span', {}, 'VOICE DETECTED'));
  } else {
    vad.className = 'flex items-center space-x-1.5 text-xs mono px-2.5 py-0.5 rounded-full bg-emerald-950 text-emerald-400 border border-emerald-800';
    vad.replaceChildren(el('span', { class: 'w-2 h-2 rounded-full bg-emerald-400' }), el('span', {}, 'ROOM QUIET'));
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
    badge.className = 'text-xs mono font-bold px-2 py-0.5 rounded bg-amber-950 text-amber-400 border border-amber-800';
    badge.innerText = 'STEALTH HOLD';
  } else {
    alert.classList.add('hidden');
    badge.className = 'text-xs mono font-bold px-2 py-0.5 rounded bg-gray-800 text-gray-400';
    badge.innerText = 'IDLE';
  }
}

function updateArc(arc) {
  if (!arc) {  // the last arc concluded; the next comic seeds a new one
    $('arcTitle').innerText = 'Generating next arc';
    $('arcGenre').innerText = '--';
    $('arcPremise').innerText = 'A new story starts with the next comic.';
    $('arcDurationText').innerText = '--';
    $('arcProgressBar').style.width = '0%';
    return;
  }
  $('arcTitle').innerText = arc.title;
  $('arcGenre').innerText = arc.genre;
  $('arcPremise').innerText = `"${arc.premise}"`;
  $('arcDurationText').innerText = `${arc.panels_used} / ${arc.target_panels} panels`;
  $('arcProgressBar').style.width = `${Math.min(100, 100 * arc.panels_used / arc.target_panels)}%`;
}

function showOriginals(issue, panelCount) {
  const base = `/api/comics/${issue}`;
  const box = $('originals');
  const color = $('colorImage');
  color.onload = () => box.classList.remove('hidden');
  color.onerror = () => box.classList.add('hidden');   // older issues have no originals
  color.src = `${base}/color.png`;
  $('panelList').replaceChildren(...Array.from({ length: panelCount || 0 }, (_, i) => {
    const a = el('a', { href: `${base}/panel${i + 1}.png`, target: '_blank' });
    const img = el('img', {
      src: a.href, alt: `Panel ${i + 1}, raw`, loading: 'lazy',
      class: 'w-full h-auto rounded border border-gray-700',
    });
    img.onerror = () => a.remove();   // procedural panels have no raw art
    a.append(img);
    return a;
  }));
}

function setActiveIssue(issue, seriesIssue, title) {
  $('currentIssueTag').innerText = `Active Issue: #${seriesIssue ?? issue} — ${title}`;
  $('comicImage').src = `/api/comics/current.png?t=${Date.now()}`;
}

function updateComic(comic) {
  showOriginals(comic.issue_number, comic.panel_count);
  setActiveIssue(comic.issue_number, comic.series_issue, comic.title);
  if (comic.displayed_at) {
    $('lastRefreshedAt').innerText = `Last Screen Update: ${fmtTime(comic.displayed_at)}`;
  }
}

function refreshComicImage(data) {
  showOriginals(data.issue_number, data.panel_count);
  setActiveIssue(data.issue_number, data.series_issue, data.title);
  $('lastRefreshedAt').innerText = `Last Screen Update: ${new Date().toLocaleTimeString()} (Silent for ${data.silence_seconds}s)`;
  window.dispatchEvent(new CustomEvent('comics-changed'));
}

function addTranscriptItem(item) {
  const list = $('transcriptList');
  if (list.dataset.empty === 'true') list.replaceChildren();
  list.dataset.empty = 'false';
  list.prepend(el('div', { class: 'p-2.5 rounded bg-gray-900 border border-gray-800 flex items-start space-x-2' },
    el('span', { class: 'text-indigo-400 font-bold' }, '\u{1F4AC}'),
    el('div', { class: 'flex-1' },
      el('div', { class: 'text-gray-200' }, `"${item.scrambled_text}"`),
      el('div', { class: 'text-[10px] text-gray-500 mt-1' },
        `${fmtTime(item.timestamp)} \u2022 ${item.duration_seconds.toFixed(1)}s speech`))));
}

async function loadPrompts() {
  try {
    const entries = await apiJson('/api/prompts');
    const list = $('promptList');
    $('promptCount').textContent = `${entries.length} calls`;
    if (entries.length === 0) {
      list.replaceChildren(el('div', { class: 'text-gray-500 italic' }, 'No model calls in the last 15 minutes.'));
      return;
    }
    const open = new Set([...list.querySelectorAll('details[open]')].map((d) => d.dataset.at));
    list.replaceChildren(...entries.map((entry) => {
      const d = el('details', { class: 'p-2 rounded bg-gray-900/60 border border-gray-800', 'data-at': String(entry.at) });
      d.open = open.has(d.dataset.at);
      const summary = el('summary', { class: 'cursor-pointer text-gray-300 truncate' },
        `${fmtTime(entry.at)} \u2022 ${entry.model} \u2022 ${entry.prompt.slice(0, 90)}`);
      d.append(summary);
      for (const [label, text] of [['SYSTEM', entry.system], ['PROMPT', entry.prompt], ['OUTPUT', entry.output ?? '(waiting for the model\u2026)']]) {
        if (!text) continue;
        d.append(
          el('div', { class: 'text-[10px] font-bold text-indigo-400 mt-2' }, label),
          el('pre', { class: 'whitespace-pre-wrap text-gray-200' }, text));
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
    updateArc(data.active_arc);
    if (data.current_comic) updateComic(data.current_comic);
  });
  onEvent('SNEAKY_COMIC_REFRESH', refreshComicImage);
  onEvent('NEW_COMIC_QUEUED', (data) => updateSneakyStatus(data.status));
  onEvent('NEW_TRANSCRIPT', (data) => addTranscriptItem(data.transcript));

  // Polling heartbeat for the silence timer and arc progress (covers ws gaps)
  setInterval(async () => {
    try {
      const data = await apiJson('/api/status');
      updateSneakyStatus(data.sneaky);
      updateArc(data.active_arc);
    } catch { /* transient; the badge already shows connection state */ }
  }, 1000);

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
