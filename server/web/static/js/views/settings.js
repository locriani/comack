/**
 * Settings view: every Settings field, grouped like server/config.py, backed by
 * GET/PATCH /api/config. Saves send only changed fields (so masked secrets are
 * never echoed back), and show per-field apply status from the PATCH response.
 * Each backend group gets a read-only connection test (POST /api/config/test/*).
 */
import { apiJson, ApiError, isMasked } from '../api.js';
import { el } from '../dom.js';

// Presentation-only grouping, mirroring the sections in server/config.py.
// Story LLM sits near the top so URL / model / API key are editable without
// scrolling past server plumbing.
const GROUPS = [
  { id: 'access', title: 'Dashboard & Access', icon: '\u{1F510}', fields: ['ONBOARDED', 'WEB_TOKEN'] },
  {
    id: 'llm',
    title: 'Story LLM',
    icon: '\u{1F9E0}',
    blurb: 'Any OpenAI-compatible chat endpoint (llama.cpp, vLLM, Ollama, hosted APIs\u2026). Edit here; Save applies immediately.',
    fields: ['LLM_URL', 'LLM_MODEL', 'LLM_API_KEY', 'MINER_LLM_URL', 'MINER_LLM_MODEL'],
    test: 'llm',
  },
  { id: 'hf', title: 'Hugging Face', icon: '\u{1F917}', fields: ['HF_TOKEN'] },
  { id: 'whisper', title: 'Speech-to-text (Whisper)', icon: '\u{1F399}', fields: [
    'WHISPER_MODEL_SIZE', 'WHISPER_DEVICE', 'WHISPER_MIN_AVG_LOGPROB', 'WHISPER_MAX_COMPRESSION', 'WHISPER_MAX_NO_SPEECH'], test: 'whisper' },
  {
    id: 'image',
    title: 'Image generation',
    icon: '\u{1F3A8}',
    blurb: 'Any OpenAI-compatible image endpoint (/v1/images/generations). Set API URL for remote-only renders; leave it empty to use the local knobs below.',
    fields: [
      'FLUX_API_URL', 'FLUX_API_MODEL', 'FLUX_API_KEY',
      'FLUX_MODEL_ID', 'FLUX_DEVICE', 'FLUX_INFERENCE_STEPS', 'FLUX_GUIDANCE_SCALE',
      'FLUX_GGUF_URL', 'FLUX_MIN_FREE_VRAM_GB', 'FLUX_VRAM_WAIT_SECONDS'],
    test: 'image',
  },
  { id: 'cadence', title: 'Comic Cadence & Triggers', icon: '\u{23F1}', fields: [
    'SNEAKY_SILENCE_SECONDS', 'SECRET_PHRASES', 'COMIC_MIN_MINUTES', 'COMIC_MAX_MINUTES',
    'DEFAULT_LINES_WANTED', 'PROCESS_INTERVAL_MINUTES'] },
  { id: 'privacy', title: 'Privacy Scramble', icon: '\u{1F7E4}', fields: ['POS_SCRAMBLE_RATE'] },
  { id: 'arcs', title: 'Story Arcs', icon: '\u{1F4DA}', fields: ['ARC_MIN_PANELS', 'ARC_MAX_PANELS'] },
  { id: 'server', title: 'Server', icon: '\u{1F310}', fields: ['HOST', 'PORT'] },
];

const FIELD_LABELS = {
  LLM_URL: 'API URL',
  LLM_MODEL: 'Model',
  LLM_API_KEY: 'API key',
  MINER_LLM_URL: 'Miner URL (optional)',
  MINER_LLM_MODEL: 'Miner model (optional)',
  HF_TOKEN: 'Hugging Face token',
  WEB_TOKEN: 'Dashboard token',
  FLUX_API_URL: 'API URL',
  FLUX_API_MODEL: 'Model',
  FLUX_API_KEY: 'API key',
  FLUX_MODEL_ID: 'Local model id',
  FLUX_DEVICE: 'Device',
};

const FIELD_PLACEHOLDERS = {
  LLM_URL: 'http://localhost:8080/v1',
  LLM_MODEL: 'model-id',
  LLM_API_KEY: 'blank if the endpoint does not require auth',
  MINER_LLM_URL: 'empty = same as API URL',
  MINER_LLM_MODEL: 'empty = same as Model; e.g. openai/gpt-oss-20b:free',
  HF_TOKEN: 'hf_…',
  FLUX_API_URL: 'http://localhost:8000/v1',
  FLUX_API_MODEL: 'model-id',
  FLUX_API_KEY: 'blank if the endpoint does not require auth',
};

const INPUT_CLASS = 'bg-gray-900 border border-gray-700 rounded px-3 py-1.5 text-sm text-white w-full focus:outline-none focus:border-indigo-500';

export function initSettings(root, initialConfig) {
  let values = { ...initialConfig.values };
  let fields = initialConfig.fields;
  const inputs = new Map();       // name -> input element
  const originals = new Map();    // name -> value as last saved/loaded
  const dirty = new Set();        // names the user touched (kept across reloads)
  const statusChips = new Map();  // name -> apply-status chip element

  const groupsBox = root.querySelector('#settingsGroups');
  const notices = root.querySelector('#settingsNotices');
  const revealToggle = root.querySelector('#revealSecrets');

  // ── value plumbing ────────────────────────────────────────────────────
  const isList = (field) => (field.value_type ?? '').startsWith('list');

  function displayValue(field, value) {
    // Masked values (server/config_api.py MASKED) arrive as strings even for
    // list-typed fields — join real lists, render everything else as-is.
    return Array.isArray(value) ? value.join(', ') : value;
  }

  function readValue(input, field) {
    if (field.value_type === 'bool') return input.checked;
    if (field.value_type === 'int') return input.value.trim() === '' ? null : parseInt(input.value, 10);
    if (field.value_type === 'float') return input.value.trim() === '' ? null : parseFloat(input.value);
    if (isList(field)) return input.value.split(',').map((s) => s.trim()).filter(Boolean);
    return input.value;
  }

  function same(a, b) {
    return JSON.stringify(a) === JSON.stringify(b);
  }

  function collectChanges() {
    const updates = {};
    for (const [name, input] of inputs) {
      const current = readValue(input, fields[name]);
      if (!same(current, originals.get(name))) updates[name] = current;
    }
    // A still-masked value is the server's placeholder, not new content —
    // sending it would overwrite the real secret with asterisks.
    for (const name of Object.keys(updates)) {
      if (isMasked(updates[name])) delete updates[name];
    }
    return updates;
  }

  // ── rendering ─────────────────────────────────────────────────────────
  function buildInput(name) {
    const field = fields[name];
    const value = displayValue(field, values[name]);
    if (field.value_type === 'bool') {
      return el('input', { type: 'checkbox', checked: value, class: 'w-5 h-5 accent-indigo-500' });
    }
    if (field.value_type === 'enum') {
      return el('select', { class: INPUT_CLASS },
        field.choices.map((choice) => el('option', { value: choice, selected: choice === value }, choice)));
    }
    const type = field.secret ? 'password'
      : (field.value_type === 'int' || field.value_type === 'float') ? 'number' : 'text';
    return el('input', {
      type, value: value ?? '',
      placeholder: FIELD_PLACEHOLDERS[name] ?? '',
      autocomplete: field.secret ? 'off' : null,
      step: field.value_type === 'float' ? 'any' : field.value_type === 'int' ? '1' : null,
      class: INPUT_CLASS, spellcheck: 'false',
    });
  }

  function fieldRow(name) {
    const field = fields[name];
    const input = buildInput(name);
    input.addEventListener('input', () => dirty.add(name));
    input.addEventListener('change', () => dirty.add(name));
    inputs.set(name, input);
    originals.set(name, readValue(input, field));

    const chip = el('span', { class: 'hidden text-[10px] mono px-2 py-0.5 rounded-full' });
    statusChips.set(name, chip);
    const label = FIELD_LABELS[name] ?? name;

    return el('div', { class: 'border border-gray-800 rounded-lg p-3 space-y-1.5 bg-gray-900/40' },
      el('div', { class: 'flex items-center justify-between gap-2' },
        el('label', { class: 'text-xs font-bold text-indigo-300' },
          label,
          el('span', { class: 'ml-2 mono font-normal text-gray-600' }, name)),
        el('div', { class: 'flex items-center gap-2' },
          field.runtime_adjustable ? null
            : el('span', { class: 'text-[10px] mono px-2 py-0.5 rounded bg-amber-950 text-amber-400 border border-amber-800', title: 'Takes effect when the server restarts' }, 'restart'),
          chip)),
      field.value_type === 'bool' ? input : el('div', {}, input),
      el('p', { class: 'text-[11px] text-gray-500 leading-snug' }, field.description ?? ''));
  }

  function applyPreset(preset) {
    for (const [name, value] of Object.entries(preset.values ?? {})) {
      const input = inputs.get(name);
      if (!input || !fields[name]) continue;
      if (fields[name].value_type === 'bool') input.checked = Boolean(value);
      else input.value = value ?? '';
      dirty.add(name);
    }
    if (preset.focus) inputs.get(preset.focus)?.focus();
  }

  function render() {
    groupsBox.replaceChildren(...GROUPS.map((group) => {
      const present = group.fields.filter((name) => fields[name]);
      const extra = Object.keys(fields).filter((name) => !GROUPS.some((g) => g.fields.includes(name)));
      const all = [...present, ...(group.id === 'other' ? extra : [])];
      return el('div', { class: 'card p-4 space-y-3', 'data-group': group.id },
        el('div', { class: 'flex items-center justify-between gap-2 flex-wrap' },
          el('h3', { class: 'text-sm font-bold text-white' }, `${group.icon} ${group.title}`),
          el('div', { class: 'flex items-center gap-2 flex-wrap' },
            ...(group.presets ?? []).map((preset) => el('button', {
              type: 'button',
              class: 'text-xs mono px-3 py-1.5 rounded-md border border-indigo-700 bg-indigo-950/40 text-indigo-200 hover:border-indigo-400',
              onclick: () => applyPreset(preset),
            }, preset.label)),
            group.test ? testButton(group) : null)),
        group.blurb
          ? el('p', { class: 'text-[11px] text-gray-400 leading-snug' }, group.blurb)
          : null,
        el('div', { class: 'grid grid-cols-1 xl:grid-cols-2 gap-3' }, all.map(fieldRow)),
        group.test ? el('div', { 'data-test-result': '', class: 'hidden' }) : null);
    }));
  }

  function testButton(group) {
    return el('button', {
      type: 'button',
      class: 'text-xs mono px-3 py-1.5 rounded-md border border-gray-700 hover:border-indigo-500 hover:text-indigo-300 text-gray-400',
      onclick: (e) => runTest(group, e.target),
    }, 'Test connection');
  }

  // ── tests ─────────────────────────────────────────────────────────────
  function testOverrides(group) {
    const typed = (name) => (inputs.get(name) ? readValue(inputs.get(name), fields[name]) : values[name]);
    if (group.test === 'llm') {
      return {
        url: typed('LLM_URL') ?? '',
        model: typed('LLM_MODEL') ?? '',
        api_key: typed('LLM_API_KEY') ?? '',
      };
    }
    if (group.test === 'whisper') return { model_size: typed('WHISPER_MODEL_SIZE') ?? '' };
    if (group.test === 'image') {
      return { url: typed('FLUX_API_URL') ?? '', api_key: typed('FLUX_API_KEY') ?? '' };
    }
    return {};
  }

  async function runTest(group, button) {
    const card = button.closest('[data-group]');
    const box = card.querySelector('[data-test-result]');
    box.classList.remove('hidden');
    box.replaceChildren(el('p', { class: 'text-xs mono text-gray-400 animate-pulse' }, 'Testing\u2026'));
    button.disabled = true;
    try {
      const body = await apiJson(`/api/config/test/${group.test}`, { method: 'POST', json: testOverrides(group) });
      box.replaceChildren(testResult(body));
    } catch (e) {
      box.replaceChildren(el('p', { class: 'text-xs text-red-400' }, e instanceof ApiError ? e.errorLines().join(' ') : String(e)));
    } finally {
      button.disabled = false;
    }
  }

  function testResult(body) {
    const color = body.ok ? 'bg-emerald-950/60 border-emerald-800 text-emerald-300'
      : 'bg-red-950/60 border-red-800 text-red-300';
    return el('div', { class: `border rounded-lg p-3 space-y-1 ${color}` },
      el('p', { class: 'text-xs' }, (body.ok ? '\u2705 ' : '\u274C ') + body.message),
      el('details', {},
        el('summary', { class: 'text-[10px] mono cursor-pointer opacity-70' }, 'details'),
        el('pre', { class: 'whitespace-pre-wrap text-[10px] mt-1 opacity-80' }, JSON.stringify(body.detail, null, 2))));
  }

  // ── save ──────────────────────────────────────────────────────────────
  function setChip(name, status) {
    const chip = statusChips.get(name);
    chip.classList.remove('hidden');
    if (status === 'applied') {
      chip.className = 'text-[10px] mono px-2 py-0.5 rounded-full bg-emerald-950 text-emerald-400 border border-emerald-800';
      chip.textContent = 'applied';
    } else {
      chip.className = 'text-[10px] mono px-2 py-0.5 rounded-full bg-amber-950 text-amber-400 border border-amber-800';
      chip.textContent = 'restart required';
    }
  }

  function addNotice(text, kind = 'warn') {
    notices.append(el('p', {
      class: `text-xs px-3 py-2 rounded border ${kind === 'error' ? 'bg-red-950/60 border-red-800 text-red-300' : 'bg-amber-950/40 border-amber-800/80 text-amber-300'}`,
    }, text));
  }

  async function reloadValues() {
    const includeSecrets = revealToggle?.checked ? '?include_secrets=true' : '';
    const config = await apiJson(`/api/config${includeSecrets}`);
    values = config.values;
    fields = config.fields;
    for (const [name, input] of inputs) {
      if (dirty.has(name)) continue;  // never clobber an edit in progress
      const field = fields[name];
      if (field.value_type === 'bool') input.checked = values[name];
      else input.value = displayValue(field, values[name]) ?? '';
      originals.set(name, readValue(input, field));
    }
    return config;
  }

  async function save() {
    notices.replaceChildren();
    const updates = collectChanges();
    if (!Object.keys(updates).length) {
      addNotice('No changes to save.');
      return;
    }
    try {
      const body = await apiJson('/api/config', { method: 'PATCH', json: updates });
      for (const [name, status] of Object.entries(body.results)) setChip(name, status);
      for (const note of body.notes) addNotice(note);
      await reloadValues();
      if (updates.WEB_TOKEN !== undefined) {
        addNotice('WEB_TOKEN changed: your next click will ask for the new token (stored in this browser).');
      }
    } catch (e) {
      if (e instanceof ApiError) addNotice(e.errorLines().join(' \u2014 '), 'error');
      else addNotice(String(e), 'error');
    }
  }

  root.querySelector('#saveSettings').addEventListener('click', save);
  revealToggle?.addEventListener('change', () => { dirty.clear(); reloadValues(); });
  render();
}
