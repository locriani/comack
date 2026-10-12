/**
 * Reader page logic — a faithful port of the original inline reader script
 * onto the shared api module (so /api/arcs works under optional WEB_TOKEN),
 * keeping the existing markup, navigation, and hash deep-linking intact.
 * Arcs carry their owning device, so the header's device select scopes the
 * bookshelf to one room; the select hides itself for single-device libraries.
 */
import { apiJson } from '../api.js';
import { arcBibleDetails } from '../arc-bible.js';
import { bindLightbox } from '../lightbox.js';

const $ = (id) => document.getElementById(id);
let arcs = [], issues = [], at = 0, color = false;
let deviceNames = {};      // device id -> user-visible name (from /api/devices)
let deviceFilter = null;   // device id or null = all devices

const visibleArcs = () => (deviceFilter ? arcs.filter((a) => a.device_id === deviceFilter) : arcs);

function show(k) {
  at = Math.max(0, Math.min(issues.length - 1, k));
  const c = issues[at], arc = c.arc;
  const scoped = visibleArcs();
  const base = `/api/comics/${c.issue_number}`;
  const page = $('page');
  page.onerror = color ? () => { page.onerror = null; page.src = `${base}/png`; page.className = 'eink'; } : null;
  page.src = color ? `${base}/color.png` : `${base}/png`;   // issues before originals were kept fall back to e-ink
  page.className = color ? '' : 'eink';
  page.alt = c.title;
  $('viewEink').className = color ? '' : 'on';
  $('viewColor').className = color ? 'on' : '';
  $('panels').replaceChildren(...Array.from({ length: c.panel_count }, (_, i) => {
    const src = `${base}/panel${i + 1}.png`;
    const img = Object.assign(document.createElement('img'), {
      src, alt: `Panel ${i + 1}, raw`, loading: 'lazy',
    });
    img.onerror = () => img.remove();   // procedural panels and older issues have no raw art
    bindLightbox(img, src, `Panel ${i + 1}`);
    return img;
  }));
  bindLightbox($('page'), () => $('page').src, c.title);
  $('arcTitle').textContent = arc.title;
  $('arcMeta').textContent = `${arc.genre} \u00b7 ${arc.status.toLowerCase()} \u00b7 ${deviceNames[arc.device_id] ?? arc.device_id ?? ''}`;
  const bibleHost = $('arcBible');
  const wasOpen = !!bibleHost.querySelector('details[open]');
  const bible = arcBibleDetails(arc, { open: wasOpen, summary: 'Full series bible — tone, art, cast, premise' });
  bibleHost.replaceChildren(...(bible ? [bible] : []));
  $('issueLabel').replaceChildren(
    Object.assign(document.createElement('b'), { textContent: `#${String(c.series_issue).padStart(3, '0')} ${c.title}` }),
    document.createElement('br'),
    `${c.layout_type} \u00b7 ${new Date(c.created_at * 1000).toLocaleString()}${c.status === 'STEALTH_HOLD' ? ' \u00b7 held' : ''}`);
  $('arcSelect').value = arc.id;
  $('prev').disabled = at === 0;
  $('next').disabled = at === issues.length - 1;
  $('prevArc').disabled = arc === scoped[0];
  $('nextArc').disabled = arc === scoped[scoped.length - 1];
  $('chips').replaceChildren(...arc.comics.map((ac) => {
    const b = document.createElement('button');
    b.textContent = `#${ac.series_issue}`;
    b.title = ac.title;
    b.className = ac.issue_number === c.issue_number ? 'on' : '';
    b.onclick = () => show(issues.findIndex((x) => x.issue_number === ac.issue_number));
    return b;
  }));
  history.replaceState(null, '', `#${c.issue_number}`);
}

// Jump to the first issue of an arc
const toArc = (arc) => show(issues.findIndex((c) => c.arc === arc));
const arcStep = (d) => toArc(visibleArcs()[visibleArcs().indexOf(issues[at].arc) + d]);

// Rebuild the bookshelf for the chosen device (or all), then open its last
// issue — or the deep-linked one when it survives the filter.
function applyDeviceFilter(wantIssue) {
  issues = visibleArcs().flatMap((arc) => arc.comics.map((c) => ({ ...c, arc })));
  $('arcSelect').replaceChildren(...visibleArcs().map((a) => new Option(`${a.title} (${a.comics.length})`, a.id)));
  if (!issues.length) {
    $('empty').hidden = false;
    $('reader').hidden = true;
    return;
  }
  $('empty').hidden = true;
  $('reader').hidden = false;
  const k = wantIssue ? issues.findIndex((c) => c.issue_number === wantIssue) : -1;
  show(k >= 0 ? k : issues.length - 1);
}

function populateDeviceSelect() {
  const ids = [...new Set(arcs.map((a) => a.device_id))];
  const select = $('deviceSelect');
  if (ids.length <= 1) { select.hidden = true; return; }   // one room: a filter would be noise
  select.replaceChildren(
    new Option(`All devices (${arcs.length})`, ''),
    ...ids.map((id) => new Option(`${deviceNames[id] ?? id} (${arcs.filter((a) => a.device_id === id).length})`, id)));
  select.value = deviceFilter ?? '';
}

export async function initReader() {
  const [allArcs, registry] = await Promise.all([
    apiJson('/api/arcs'),
    apiJson('/api/devices').catch(() => ({ devices: [] })),   // the filter needs names; labels fall back to ids
  ]);
  deviceNames = Object.fromEntries((registry.devices ?? []).map((d) => [d.id, d.name]));
  arcs = allArcs.filter((a) => a.comics.length);
  if (!arcs.length) { $('empty').hidden = false; return; }
  populateDeviceSelect();
  applyDeviceFilter(parseInt(location.hash.slice(1), 10) || undefined);
}

$('viewEink').onclick = () => { color = false; show(at); };
$('viewColor').onclick = () => { color = true; show(at); };
$('prev').onclick = () => show(at - 1);
$('next').onclick = () => show(at + 1);
$('prevArc').onclick = () => arcStep(-1);
$('nextArc').onclick = () => arcStep(1);
$('deviceSelect').onchange = (e) => {
  deviceFilter = e.target.value || null;
  applyDeviceFilter();
};
$('arcSelect').onchange = (e) => toArc(arcs.find((a) => String(a.id) === e.target.value));
document.addEventListener('keydown', (e) => {
  if (e.target.tagName === 'SELECT') return;
  if (e.key === 'ArrowLeft') show(at - 1);
  else if (e.key === 'ArrowRight') show(at + 1);
  else if (e.key === 'Home') show(0);
  else if (e.key === 'End') show(issues.length - 1);
});
initReader();
