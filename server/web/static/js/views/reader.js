/**
 * Reader page logic — a faithful port of the original inline reader script
 * onto the shared api module (so /api/arcs works under optional WEB_TOKEN),
 * keeping the existing markup, navigation, and hash deep-linking intact.
 */
import { apiJson } from '../api.js';

const $ = (id) => document.getElementById(id);
let arcs = [], issues = [], at = 0, color = false;

function show(k) {
  at = Math.max(0, Math.min(issues.length - 1, k));
  const c = issues[at], arc = c.arc;
  const base = `/api/comics/${c.issue_number}`;
  const page = $('page');
  page.onerror = color ? () => { page.onerror = null; page.src = `${base}/png`; page.className = 'eink'; } : null;
  page.src = color ? `${base}/color.png` : `${base}/png`;   // issues before originals were kept fall back to e-ink
  page.className = color ? '' : 'eink';
  page.alt = c.title;
  $('viewEink').className = color ? '' : 'on';
  $('viewColor').className = color ? 'on' : '';
  $('panels').replaceChildren(...Array.from({ length: c.panel_count }, (_, i) => {
    const a = Object.assign(document.createElement('a'), { href: `${base}/panel${i + 1}.png`, target: '_blank' });
    const img = Object.assign(document.createElement('img'), { src: a.href, alt: `Panel ${i + 1}, raw`, loading: 'lazy' });
    img.onerror = () => a.remove();   // procedural panels and older issues have no raw art
    a.append(img);
    return a;
  }));
  $('arcTitle').textContent = arc.title;
  $('arcMeta').textContent = `${arc.genre} \u00b7 ${arc.status.toLowerCase()} \u00b7 ${arc.premise}`;
  $('issueLabel').replaceChildren(
    Object.assign(document.createElement('b'), { textContent: `#${String(c.series_issue).padStart(3, '0')} ${c.title}` }),
    document.createElement('br'),
    `${c.layout_type} \u00b7 ${new Date(c.created_at * 1000).toLocaleString()}${c.status === 'STEALTH_HOLD' ? ' \u00b7 held' : ''}`);
  $('arcSelect').value = arc.id;
  $('prev').disabled = at === 0;
  $('next').disabled = at === issues.length - 1;
  $('prevArc').disabled = arc === arcs[0];
  $('nextArc').disabled = arc === arcs[arcs.length - 1];
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
const arcStep = (d) => toArc(arcs[arcs.indexOf(issues[at].arc) + d]);

export async function initReader() {
  arcs = (await apiJson('/api/arcs')).filter((a) => a.comics.length);
  issues = arcs.flatMap((arc) => arc.comics.map((c) => ({ ...c, arc })));
  if (!issues.length) { $('empty').hidden = false; return; }
  $('reader').hidden = false;
  $('arcSelect').replaceChildren(...arcs.map((a) => new Option(`${a.title} (${a.comics.length})`, a.id)));
  const want = parseInt(location.hash.slice(1), 10);
  const k = issues.findIndex((c) => c.issue_number === want);
  show(k >= 0 ? k : issues.length - 1);
}

$('viewEink').onclick = () => { color = false; show(at); };
$('viewColor').onclick = () => { color = true; show(at); };
$('prev').onclick = () => show(at - 1);
$('next').onclick = () => show(at + 1);
$('prevArc').onclick = () => arcStep(-1);
$('nextArc').onclick = () => arcStep(1);
$('arcSelect').onchange = (e) => toArc(arcs.find((a) => String(a.id) === e.target.value));
document.addEventListener('keydown', (e) => {
  if (e.target.tagName === 'SELECT') return;
  if (e.key === 'ArrowLeft') show(at - 1);
  else if (e.key === 'ArrowRight') show(at + 1);
  else if (e.key === 'Home') show(0);
  else if (e.key === 'End') show(issues.length - 1);
});
initReader();
