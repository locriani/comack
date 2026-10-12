/**
 * Archive view: a thumbnail gallery over /api/comics/history plus /api/arcs as
 * a story-arc timeline. Every card links into the existing reader experience
 * (/reader#issue). Images use the tokenless per-issue png endpoints, so they
 * work under WEB_TOKEN without headers. Both the grid and the timeline filter
 * by device (the registry from /api/devices supplies the labels).
 */
import { apiJson } from '../api.js';
import { el, fmtDateTime } from '../dom.js';

export function initArchive(section) {
  const grid = section.querySelector('#archiveGrid');
  const timeline = section.querySelector('#arcTimeline');
  const filterRow = section.querySelector('#arcFilter');
  const deviceFilterRow = section.querySelector('#deviceFilter');
  const empty = section.querySelector('#archiveEmpty');

  let arcs = [];
  let issues = [];
  let devices = [];          // the registry: id -> user-visible name
  let activeFilter = null;   // arc id or null = all
  let activeDevice = null;   // device id or null = all

  async function refresh() {
    try {
      [arcs, issues, devices] = await Promise.all([
        apiJson('/api/arcs'),
        apiJson('/api/comics/history?limit=200'),
        apiJson('/api/devices').then((d) => d.devices ?? []),
      ]);
      render();
    } catch (e) {
      console.error('Error loading archive:', e);
      empty.classList.remove('hidden');
      empty.textContent = 'Could not load the archive.';
    }
  }

  const deviceName = (id) => devices.find((d) => d.id === id)?.name ?? id ?? '—';

  function issueNumbersFor(arc) {
    return new Set((arc?.comics ?? []).map((c) => c.issue_number));
  }

  // Filtering is conjunctive: a device chip narrows both the timeline and the
  // grid to that room; an arc chip then narrows the grid within it.
  function visibleIssues() {
    const deviceArcs = activeDevice ? arcs.filter((a) => a.device_id === activeDevice) : arcs;
    let visible = activeDevice ? issues.filter((c) => c.device_id === activeDevice) : issues;
    if (activeFilter) {
      const numbers = issueNumbersFor(deviceArcs.find((a) => a.id === activeFilter));
      visible = visible.filter((c) => numbers.has(c.issue_number));
    }
    return visible;
  }

  function render() {
    const visible = visibleIssues();
    empty.classList.toggle('hidden', visible.length > 0);
    empty.textContent = issues.length
      ? 'No issues match the current filters.'
      : 'No comics yet — trigger one with \u201cGenerate Comic Now\u201d or wait for the 30-minute cycle.';
    renderTimeline();
    renderFilters();
    grid.replaceChildren(...visible.map(issueCard));
  }

  function deviceTag(device_id) {
    return el('span', { class: 'text-[10px] mono px-1.5 py-0.5 rounded bg-gray-800 text-gray-400 border border-gray-700' },
      `\u{1F4FA} ${deviceName(device_id)}`);
  }

  function renderTimeline() {
    if (!arcs.length) {
      timeline.replaceChildren(el('p', { class: 'text-gray-500 italic text-sm' }, 'No story arcs yet — they start with the first comic.'));
      return;
    }
    const shown = activeDevice ? arcs.filter((a) => a.device_id === activeDevice) : arcs;
    timeline.replaceChildren(...shown.map((arc) => {
      const panels = arc.comics.reduce((sum, c) => sum + (c.panel_count ?? 0), 0);
      const active = arc.status.toLowerCase() === 'active';
      return el('button', {
        class: `text-left shrink-0 w-64 border rounded-xl p-3 space-y-1 transition ${active
          ? 'border-indigo-500 bg-indigo-950/40 shadow-[0_0_16px_rgba(42,157,143,0.22)]'
          : 'border-[var(--line)] bg-[var(--ink-2)] hover:border-indigo-700'}`,
        onclick: () => { activeFilter = arc.id; render(); },
        title: arc.premise,
      },
        el('div', { class: 'flex items-center justify-between gap-2' },
          el('span', { class: 'text-xs mono text-gray-400' }, `${arc.comics.length} issues \u00b7 ${panels} panels`),
          el('span', { class: `text-[10px] mono px-2 py-0.5 rounded ${active ? 'bg-indigo-950 text-indigo-300 border border-indigo-700' : 'bg-gray-800 text-gray-400'}` }, arc.status)),
        el('div', { class: 'text-sm font-bold text-white truncate' }, arc.title),
        el('div', { class: 'flex items-center justify-between gap-2' },
          el('div', { class: 'text-[10px] mono text-indigo-400' }, arc.genre),
          deviceTag(arc.device_id)));
    }));
  }

  function chip(label, value, active) {
    return el('button', {
      class: `text-xs mono px-3 py-1 rounded-full border transition ${active
        ? 'border-indigo-500 text-indigo-300 bg-indigo-950/40'
        : 'border-gray-700 text-gray-400 hover:border-gray-500'}`,
      onclick: () => { activeFilter = value; render(); },
    }, label);
  }

  function renderFilters() {
    const deviceChip = (label, value) => el('button', {
      class: `text-xs mono px-3 py-1 rounded-full border transition ${activeDevice === value
        ? 'border-indigo-500 text-indigo-300 bg-indigo-950/40'
        : 'border-gray-700 text-gray-400 hover:border-gray-500'}`,
      onclick: () => { activeDevice = value; activeFilter = null; render(); },
    }, label);

    deviceFilterRow.replaceChildren(
      deviceChip(`All devices (${issues.length})`, null),
      ...devices.map((d) => deviceChip(
        `${d.name} (${issues.filter((c) => c.device_id === d.id).length})`, d.id)));

    // Arc chips live inside the chosen device: only its arcs are offered.
    const scopedArcs = activeDevice ? arcs.filter((a) => a.device_id === activeDevice) : arcs;
    filterRow.replaceChildren(
      chip(`All arcs (${scopedArcs.reduce((sum, a) => sum + a.comics.length, 0)})`, null, activeFilter === null),
      ...scopedArcs.map((arc) => chip(`${arc.title} (${arc.comics.length})`, arc.id, activeFilter === arc.id)));
  }

  function issueCard(c) {
    const held = c.status === 'STEALTH_HOLD';
    return el('a', {
      href: `/reader#${c.issue_number}`,
      class: 'group card overflow-hidden hover:border-indigo-500 transition flex flex-col',
    },
      el('div', { class: 'bg-[#e2e4e9] p-1' },
        el('img', {
          src: `/api/comics/${c.issue_number}/png`, loading: 'lazy',
          alt: `Issue #${c.series_issue}: ${c.title}`,
          class: 'w-full h-auto aspect-[800/480] object-cover bg-white rounded-sm',
        })),
      el('div', { class: 'p-2.5 space-y-0.5' },
        el('div', { class: 'flex items-center justify-between gap-2' },
          el('span', { class: 'text-sm font-bold text-white truncate' }, `#${c.series_issue} ${c.title}`),
          held ? el('span', { class: 'text-[10px] mono px-1.5 py-0.5 rounded bg-amber-950 text-amber-400 border border-amber-800' }, 'held') : null),
        el('div', { class: 'flex items-center justify-between gap-2' },
          el('div', { class: 'text-[10px] mono text-gray-500' },
            `${c.layout_type} \u00b7 ${fmtDateTime(c.created_at)}`),
          deviceTag(c.device_id))));
  }

  window.addEventListener('comics-changed', refresh);
  return { refresh };
}
