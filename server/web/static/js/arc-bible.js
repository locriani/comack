/**
 * Parse and render an arc's series bible (tone, art style, cast) for curious
 * readers. metadata_json is stored as a JSON string on the wire.
 * Uses inline styles so the same block works on the Tailwind dashboard and
 * the plain reader page.
 */
import { el } from './dom.js';

export function parseArcMeta(arc) {
  if (!arc) return {};
  const raw = arc.metadata_json;
  if (raw && typeof raw === 'object') return raw;
  try { return JSON.parse(raw || '{}'); } catch { return {}; }
}

/** Field rows for the bible — skips empties. */
export function bibleEntries(arc) {
  if (!arc) return [];
  const meta = parseArcMeta(arc);
  const cast = Array.isArray(meta.cast)
    ? meta.cast.map((c) => String(c).trim()).filter(Boolean)
    : [];
  const rows = [
    ['Genre', arc.genre],
    ['Tone', meta.tone],
    ['Art style', meta.art_style],
    ['Setting / premise', arc.premise],
  ];
  const out = rows
    .filter(([, v]) => v && String(v).trim())
    .map(([label, value]) => ({ label, value: String(value).trim() }));
  if (cast.length) out.push({ label: 'Cast', value: cast });
  return out;
}

/**
 * Collapsible series-bible block. Pass `open` to restore state across refreshes.
 */
export function arcBibleDetails(arc, { open = false, summary = 'Series bible' } = {}) {
  const entries = bibleEntries(arc);
  if (!entries.length) return null;
  const d = el('details', {
    'data-arc-bible': String(arc.id ?? ''),
    style: 'margin-top:8px;border:1px solid #3d362e;border-radius:8px;background:rgba(0,0,0,.18)',
  });
  d.open = open;
  d.append(el('summary', {
    style: 'cursor:pointer;padding:8px 12px;font-size:12px;font-weight:600;color:#818cf8;user-select:none',
  }, summary));
  const body = el('div', { style: 'padding:0 12px 12px;display:flex;flex-direction:column;gap:12px;font-size:13px' });
  for (const { label, value } of entries) {
    const block = el('div', {});
    block.append(el('div', {
      style: 'font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;color:#a39a8e;margin-bottom:2px',
    }, label));
    if (Array.isArray(value)) {
      const ul = el('ul', { style: 'margin:0;padding-left:1.1rem;display:flex;flex-direction:column;gap:4px;color:#e8e2d9;line-height:1.45' });
      for (const c of value) ul.append(el('li', {}, c));
      block.append(ul);
    } else {
      block.append(el('p', {
        style: 'margin:0;color:#e8e2d9;line-height:1.5;white-space:pre-wrap',
      }, value));
    }
    body.append(block);
  }
  d.append(body);
  return d;
}
