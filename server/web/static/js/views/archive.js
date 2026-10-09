/**
 * Archive view: a thumbnail gallery over /api/comics/history plus /api/arcs as
 * a story-arc timeline. Every card links into the existing reader experience
 * (/reader#issue). Images use the tokenless per-issue png endpoints, so they
 * work under WEB_TOKEN without headers.
 */
import { apiJson } from '../api.js';
import { el, fmtDateTime } from '../dom.js';

export function initArchive(section) {
  const grid = section.querySelector('#archiveGrid');
  const timeline = section.querySelector('#arcTimeline');
  const filterRow = section.querySelector('#arcFilter');
  const empty = section.querySelector('#archiveEmpty');

  let arcs = [];
  let issues = [];
  let activeFilter = null;   // arc id or null = all

  async function refresh() {
    try {
      [arcs, issues] = await Promise.all([
        apiJson('/api/arcs'),
        apiJson('/api/comics/history?limit=200'),
      ]);
      render();
    } catch (e) {
      console.error('Error loading archive:', e);
      empty.classList.remove('hidden');
      empty.textContent = 'Could not load the archive.';
    }
  }

  function issueNumbersFor(arc) {
    return new Set((arc?.comics ?? []).map((c) => c.issue_number));
  }

  function render() {
    empty.classList.toggle('hidden', issues.length > 0);
    renderTimeline();
    renderFilters();
    const visible = activeFilter
      ? issues.filter((c) => issueNumbersFor(arcs.find((a) => a.id === activeFilter)).has(c.issue_number))
      : issues;
    grid.replaceChildren(...visible.map(issueCard));
  }

  function renderTimeline() {
    if (!arcs.length) {
      timeline.replaceChildren(el('p', { class: 'text-gray-500 italic text-sm' }, 'No story arcs yet — they start with the first comic.'));
      return;
    }
    timeline.replaceChildren(...arcs.map((arc) => {
      const panels = arc.comics.reduce((sum, c) => sum + (c.panel_count ?? 0), 0);
      const active = arc.status.toLowerCase() === 'active';
      return el('button', {
        class: `text-left shrink-0 w-64 border rounded-xl p-3 space-y-1 transition ${active
          ? 'border-indigo-500 bg-indigo-950/40 shadow-[0_0_12px_rgba(99,102,241,0.35)]'
          : 'border-gray-800 bg-[#161922] hover:border-gray-600'}`,
        onclick: () => { activeFilter = arc.id; render(); },
        title: arc.premise,
      },
        el('div', { class: 'flex items-center justify-between gap-2' },
          el('span', { class: 'text-xs mono text-gray-400' }, `${arc.comics.length} issues \u00b7 ${panels} panels`),
          el('span', { class: `text-[10px] mono px-2 py-0.5 rounded ${active ? 'bg-indigo-950 text-indigo-300 border border-indigo-700' : 'bg-gray-800 text-gray-400'}` }, arc.status)),
        el('div', { class: 'text-sm font-bold text-white truncate' }, arc.title),
        el('div', { class: 'text-[10px] mono text-indigo-400' }, arc.genre));
    }));
  }

  function renderFilters() {
    const chip = (label, value) => el('button', {
      class: `text-xs mono px-3 py-1 rounded-full border transition ${activeFilter === value
        ? 'border-indigo-500 text-indigo-300 bg-indigo-950/40'
        : 'border-gray-700 text-gray-400 hover:border-gray-500'}`,
      onclick: () => { activeFilter = value; render(); },
    }, label);
    filterRow.replaceChildren(
      chip(`All (${issues.length})`, null),
      ...arcs.map((arc) => chip(`${arc.title} (${arc.comics.length})`, arc.id)));
  }

  function issueCard(c) {
    const held = c.status === 'STEALTH_HOLD';
    return el('a', {
      href: `/reader#${c.issue_number}`,
      class: 'group bg-[#161922] border border-gray-800 rounded-xl overflow-hidden hover:border-indigo-500 transition flex flex-col',
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
        el('div', { class: 'text-[10px] mono text-gray-500' },
          `${c.layout_type} \u00b7 ${fmtDateTime(c.created_at)}`)));
  }

  window.addEventListener('comics-changed', refresh);
  return { refresh };
}
