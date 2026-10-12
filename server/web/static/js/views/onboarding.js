/**
 * First-run onboarding wizard. Shown while the ONBOARDED config flag is false:
 * welcome -> story LLM -> speech-to-text -> image backend (preset prefill) ->
 * capture consent -> save. The final PATCH applies everything and sets
 * ONBOARDED=true; apply/restart statuses are shown per field before finishing.
 * The confirm step is deliberately unmistakable: this appliance listens to a room.
 */
import { apiJson, ApiError, isMasked } from '../api.js';
import { el } from '../dom.js';

const INPUT = 'bg-gray-900 border border-gray-700 rounded px-3 py-2 text-sm text-white w-full focus:outline-none focus:border-indigo-500';
const WHISPER_SIZES = ['tiny.en', 'base.en', 'small.en', 'medium.en'];
const PRESETS = [
  { key: 'local-gpu', name: 'Local GPU', blurb: 'Prefills local rendering on this machine\'s NVIDIA GPU (leave API URL empty).' },
  { key: 'cpu-only', name: 'CPU only', blurb: 'Prefills local rendering on CPU — slower, no GPU required.' },
];
const IMAGE_KEYS = ['FLUX_MODEL_ID', 'FLUX_DEVICE', 'FLUX_INFERENCE_STEPS', 'FLUX_MIN_FREE_VRAM_GB'];

// ── finish-merge (pure, exported for tests) ────────────────────────────
// Preset values spread first so typed fields win: the external-api preset
// ships sample values like http://your-llm-host:8080/ that must never
// overwrite what the user typed in earlier steps. Non-empty typed image
// values still beat the preset. A masked value (api.js MASKED) is the
// server's placeholder, never new content — echoing one back would
// overwrite the stored secret with asterisks, so it is dropped.
// Placeholder sample values (your-llm-host, your-image-host, …) are
// scrubbed out so Finish never persists them into .env.
const TYPED_LLM_KEYS = ['LLM_URL', 'LLM_MODEL', 'LLM_API_KEY'];

export function isPlaceholderValue(value) {
  return typeof value === 'string' && /\byour[-_]/i.test(value);
}

export function scrubPresetValues(values) {
  const out = {};
  for (const [key, value] of Object.entries(values ?? {})) {
    if (isPlaceholderValue(value)) continue;
    out[key] = value;
  }
  return out;
}

export function nonEmptyImageOverrides(state) {
  const out = {};
  for (const k of IMAGE_KEYS) {
    const v = String(state[k] ?? '').trim();
    if (v !== '' && !isPlaceholderValue(v)) out[k] = v;
  }
  return out;
}

export function buildFinishUpdates(state) {
  const updates = {
    ...(state.preset ? { ...scrubPresetValues(state.presetValues), ...nonEmptyImageOverrides(state) } : {}),
    LLM_URL: state.LLM_URL.trim(),
    LLM_MODEL: state.LLM_MODEL.trim(),
    LLM_API_KEY: state.LLM_API_KEY.trim(),
    // Image API fields are first-class like Story LLM — not gated on a preset.
    FLUX_API_URL: state.FLUX_API_URL.trim(),
    FLUX_API_MODEL: state.FLUX_API_MODEL.trim(),
    FLUX_API_KEY: state.FLUX_API_KEY.trim(),
    WHISPER_MODEL_SIZE: state.WHISPER_MODEL_SIZE,
    ONBOARDED: true,
  };
  for (const key of ['LLM_URL', 'LLM_MODEL', 'LLM_API_KEY', 'FLUX_API_URL', 'FLUX_API_KEY']) {
    if (isMasked(updates[key])) delete updates[key];
  }
  return updates;
}

export function initOnboarding(config, { onComplete }) {
  const root = document.getElementById('onboarding-root');
  const state = {
    LLM_URL: config.values.LLM_URL ?? '',
    LLM_MODEL: config.values.LLM_MODEL ?? '',
    // LLM_API_KEY / FLUX_API_KEY arrive masked when already set;
    // buildFinishUpdates drops the echo so stored keys are never clobbered.
    LLM_API_KEY: config.values.LLM_API_KEY ?? '',
    WHISPER_MODEL_SIZE: config.values.WHISPER_MODEL_SIZE ?? 'base.en',
    FLUX_API_KEY: config.values.FLUX_API_KEY ?? '',
    FLUX_API_MODEL: config.values.FLUX_API_MODEL ?? '',
    preset: null,
    presetValues: {},      // values the chosen preset would set
    consent: false,
    ...Object.fromEntries(IMAGE_KEYS.map((k) => [k, ''])),
  };
  let presetsCache = null;
  let step = 0;

  const STEPS = ['Welcome', 'Story LLM', 'Speech-to-text', 'Images', 'Consent', 'Finish'];
  const body = el('div', { class: 'min-h-0' });
  const dots = el('div', { class: 'flex items-center justify-center gap-1.5' });
  const backBtn = el('button', { class: 'text-sm mono px-4 py-2 rounded-md border border-gray-700 text-gray-400 hover:border-gray-500' }, 'Back');
  const nextBtn = el('button', { class: 'text-sm mono px-5 py-2 rounded-md bg-indigo-600 hover:bg-indigo-500 text-white font-bold' }, 'Next');
  backBtn.addEventListener('click', () => { if (step > 0) { step -= 1; render(); } });
  nextBtn.addEventListener('click', next);

  const shell = el('div', { class: 'fixed inset-0 z-[60] bg-black/75 backdrop-blur-sm flex items-center justify-center p-4' },
    el('div', { class: 'bg-[var(--ink-2,#26211c)] border border-[var(--line,#3d362e)] rounded-2xl w-full max-w-2xl h-[40rem] max-h-[90vh] flex flex-col p-6 space-y-4 shadow-2xl' },
      el('div', { class: 'flex items-center justify-between' },
        el('div', { class: 'flex items-center gap-2' },
          el('span', { class: 'text-lg font-bold tracking-tight text-indigo-300' }, 'Comack setup')),
        dots),
      body,
      el('div', { class: 'flex items-center justify-between pt-2 border-t border-gray-800' },
        backBtn, nextBtn)));
  root.replaceChildren(shell);

  function label(text, hint) {
    return el('label', { class: 'block' },
      el('span', { class: 'text-xs mono font-bold text-indigo-300' }, text),
      hint ? el('span', { class: 'text-[11px] text-gray-500 ml-2' }, hint) : null);
  }

  function boundInput(key, { type = 'text', placeholder = '', autocomplete = 'off' } = {}) {
    const input = el('input', {
      type, value: state[key] ?? '', placeholder, autocomplete,
      class: INPUT, spellcheck: 'false',
    });
    input.addEventListener('input', () => { state[key] = input.value; });
    return input;
  }

  function probeResultBox() {
    return el('div', { class: 'hidden' });
  }

  async function probe(kind, overrides, box) {
    box.classList.remove('hidden');
    box.replaceChildren(el('p', { class: 'text-xs mono text-gray-400 animate-pulse' }, 'Testing\u2026'));
    try {
      const r = await apiJson(`/api/config/test/${kind}`, { method: 'POST', json: overrides });
      const color = r.ok ? 'bg-emerald-950/60 border-emerald-800 text-emerald-300' : 'bg-red-950/60 border-red-800 text-red-300';
      box.className = `border rounded-lg p-3 text-xs ${color}`;
      box.replaceChildren(
        el('p', {}, (r.ok ? '\u2705 ' : '\u274C ') + r.message),
        el('details', { class: 'mt-1' },
          el('summary', { class: 'cursor-pointer opacity-70' }, 'details'),
          el('pre', { class: 'whitespace-pre-wrap text-[10px] mt-1 opacity-80' }, JSON.stringify(r.detail, null, 2))));
    } catch (e) {
      box.className = 'border rounded-lg p-3 text-xs bg-red-950/60 border-red-800 text-red-300';
      box.replaceChildren(el('p', {}, e instanceof ApiError ? e.errorLines().join(' \u2014 ') : String(e)));
    }
  }

  const probeBtn = (kind, overrides, box) => el('button', {
    class: 'text-xs mono px-3 py-2 rounded-md border border-gray-700 hover:border-indigo-500 hover:text-indigo-300 text-gray-400 whitespace-nowrap',
    onclick: () => probe(kind, overrides(), box),
  }, 'Test');

  function card({ title, subtitle, children }) {
    return el('div', { class: 'space-y-4 h-full flex flex-col' },
      el('div', { class: 'space-y-1' },
        el('h2', { class: 'text-xl font-bold text-white' }, title),
        subtitle ? el('p', { class: 'text-sm text-gray-400' }, subtitle) : null),
      el('div', { class: 'space-y-4 overflow-y-auto pr-1 flex-1' }, children));
  }

  // ── steps ─────────────────────────────────────────────────────────────
  function stepWelcome() {
    return card({
      title: 'Welcome to Comack',
      subtitle: 'A quick once-through setup. You can change anything later in Settings.',
      children: [
        el('div', { class: 'bg-indigo-950/40 border border-indigo-900 rounded-xl p-4 text-sm text-indigo-200 space-y-2 leading-relaxed' },
          el('p', {}, 'Comack listens in your room, keeps only privacy-scrambled notes, and turns them into a comic on the e-paper display.'),
          el('p', { class: 'text-indigo-300' }, 'Next: pick how stories and pictures get made, choose a speech model, and confirm you are okay with capture.')),
        el('div', { class: 'grid grid-cols-3 gap-2 text-center text-xs text-gray-400' },
          ...[['Story', 'Writes each issue'], ['Speech', 'Hears the room'], ['Art', 'Draws the panels']].map(([title, text]) =>
            el('div', { class: 'border border-gray-800 rounded-xl p-3 bg-gray-900/40' },
              el('div', { class: 'text-sm font-semibold text-indigo-200 mb-1' }, title), text))),
      ],
    });
  }

  function stepLlm() {
    const box = probeResultBox();
    return card({
      title: 'Story LLM',
      subtitle: 'Any OpenAI-compatible chat endpoint (llama.cpp, vLLM, Ollama, hosted APIs\u2026). Leave empty to skip story mining.',
      children: [
        label('API URL', 'LLM_URL — base URL ending in /v1 when the host serves that way'),
        boundInput('LLM_URL', { placeholder: 'http://localhost:8080/v1' }),
        label('Model', 'LLM_MODEL — whatever the endpoint expects (some local servers ignore it)'),
        boundInput('LLM_MODEL', { placeholder: 'model-id' }),
        label('API key', 'LLM_API_KEY — Bearer token when the endpoint authenticates; blank for local'),
        boundInput('LLM_API_KEY', {
          type: 'password',
          placeholder: 'blank if the endpoint does not require auth',
          autocomplete: 'new-password',
        }),
        el('div', { class: 'flex items-start gap-2' },
          probeBtn('llm', () => ({
            url: state.LLM_URL, model: state.LLM_MODEL, api_key: state.LLM_API_KEY,
          }), box), box),
      ],
    });
  }

  function stepWhisper() {
    const box = probeResultBox();
    const choices = el('div', { class: 'grid grid-cols-4 gap-2' },
      ...WHISPER_SIZES.map((size) => {
        const selected = state.WHISPER_MODEL_SIZE === size;
        const short = size.replace('.en', '');
        return el('button', {
          class: `border rounded-lg p-2.5 text-center transition ${selected
            ? 'border-indigo-500 bg-indigo-950/40 text-indigo-200'
            : 'border-gray-800 bg-gray-900/40 text-gray-400 hover:border-gray-600'}`,
          onclick: () => { state.WHISPER_MODEL_SIZE = size; render(); },
        },
          el('div', { class: 'text-xs mono font-bold' }, short),
          el('div', { class: 'text-[10px] mt-0.5' }, { tiny: 'fastest', base: 'balanced', small: 'sharper', medium: 'sharpest' }[short]));
      }));
    return card({
      title: 'Speech-to-text',
      subtitle: 'Whisper runs locally on the server. Larger models transcribe more accurately and need more compute; English-only (.en) variants are the default trade. The model downloads on first use.',
      children: [choices, el('div', { class: 'flex items-start gap-2' },
        probeBtn('whisper', () => ({ model_size: state.WHISPER_MODEL_SIZE }), box), box)],
    });
  }

  function pickPreset(key) {
    state.preset = key;
    if (!presetsCache) {   // the background fetch may not have landed yet
      apiJson('/api/config/presets')
        .then((body) => {
          presetsCache = Object.fromEntries((body.presets ?? []).map((p) => [p.name, p.values ?? {}]));
        })
        .catch(() => { presetsCache = {}; })
        .finally(() => { applyPreset(key); });
      return;
    }
    applyPreset(key);
  }

  function applyPreset(key) {
    // presetsCache maps preset name -> its raw values dict (normalized from
    // GET /api/config/presets's [{name, values}] list).
    const values = presetsCache?.[key];
    state.presetValues = { ...(values ?? {}) };
    // Prefill the editable subset; placeholders (e.g. "your-remote-host") stay out.
    for (const k of IMAGE_KEYS) {
      const v = values?.[k];
      state[k] = typeof v === 'string' && v && !v.includes('your-') ? v : '';
    }
    render();
  }

  function stepImages() {
    const box = probeResultBox();
    const children = [
      label('API URL', 'FLUX_API_URL — OpenAI-compatible image endpoint (/v1/images/generations). Empty = render locally.'),
      boundInput('FLUX_API_URL', { placeholder: 'http://localhost:8000/v1' }),
      label('Model', 'FLUX_API_MODEL — whatever the endpoint expects (empty = provider default)'),
      boundInput('FLUX_API_MODEL', { placeholder: 'model-id' }),
      label('API key', 'FLUX_API_KEY — Bearer token when the endpoint authenticates; blank for local'),
      boundInput('FLUX_API_KEY', {
        type: 'password',
        placeholder: 'blank if the endpoint does not require auth',
        autocomplete: 'new-password',
      }),
      el('div', { class: 'flex items-start gap-2' },
        probeBtn('image', () => ({
          url: state.FLUX_API_URL,
          api_key: state.FLUX_API_KEY,
        }), box), box),
      el('p', { class: 'text-[11px] text-gray-500 pt-2' },
        'Optional: prefill local rendering knobs (only used when API URL is empty).'),
      el('div', { class: 'grid grid-cols-2 gap-2' },
        ...PRESETS.map(({ key, name, blurb }) => el('button', {
          type: 'button',
          class: `border rounded-lg p-3 text-left space-y-1 transition ${state.preset === key
            ? 'border-indigo-500 bg-indigo-950/40'
            : 'border-gray-800 bg-gray-900/40 hover:border-gray-600'}`,
          onclick: () => {
            state.FLUX_API_URL = '';
            pickPreset(key);
          },
        },
          el('div', { class: 'text-sm font-bold text-white' }, name),
          el('div', { class: 'text-[11px] text-gray-400 leading-snug' }, blurb)))),
    ];

    if (state.preset) {
      children.push(
        el('div', { class: 'grid grid-cols-2 gap-3' },
          el('div', {}, label('Local model', 'FLUX_MODEL_ID'),
            boundInput('FLUX_MODEL_ID', { placeholder: 'stabilityai/sd-turbo' })),
          el('div', {}, label('Device', 'FLUX_DEVICE'),
            boundInput('FLUX_DEVICE', { placeholder: 'cuda' }))),
        el('div', { class: 'grid grid-cols-2 gap-3' },
          el('div', {}, label('Steps', 'FLUX_INFERENCE_STEPS'),
            boundInput('FLUX_INFERENCE_STEPS', { type: 'number' })),
          el('div', {}, label('Min free VRAM (GB)', 'FLUX_MIN_FREE_VRAM_GB'),
            boundInput('FLUX_MIN_FREE_VRAM_GB', { type: 'number' }))));
    }

    return card({
      title: 'Image generation',
      subtitle: 'Any OpenAI-compatible image endpoint. Leave the URL empty to render on this machine instead.',
      children,
    });
  }

  function stepConsent() {
    const box = el('input', { type: 'checkbox', class: 'w-6 h-6 accent-indigo-500 mt-0.5' });
    box.addEventListener('change', () => { state.consent = box.checked; syncFooter(); });
    return card({
      title: 'Consent to capture',
      subtitle: 'One thing to confirm before the appliance starts.',
      children: [
        el('div', { class: 'border-2 border-amber-700 bg-amber-950/30 rounded-xl p-4 space-y-3' },
          el('p', { class: 'text-sm text-amber-200 font-bold' }, '\u{1F508} This device listens to the room.'),
          el('p', { class: 'text-sm text-gray-300 leading-relaxed' },
            'When sneaky mode is on, audio is continuously voice-activity-detected, transcribed by Whisper on this server, and distilled into comics. Everyone who can be heard in this space should know that. Transcripts and panels are stored locally on this machine.'),
          el('p', { class: 'text-sm text-gray-400 leading-relaxed' },
            'In default (non-sneaky) mode nothing is recorded until a secret phrase is spoken. Scrambled transcript text is designed to be implausible to read as fact — but the unscrambled text exists on the server.')),
        el('label', { class: 'flex items-start gap-3 p-4 border border-gray-700 rounded-lg cursor-pointer hover:border-indigo-600' },
          box,
          el('span', { class: 'text-sm text-gray-200' },
            'I confirm that everyone who can be heard here has been told a Comack appliance is operating, and I want to enable room capture.')),
      ],
    });
  }

  function stepFinish() {
    return card({
      title: 'Ready to save',
      subtitle: 'Comack will apply the configuration below. Fields marked "restart required" take effect after the server restarts; everything else applies immediately.',
      children: [el('div', { id: 'onboarding-summary', class: 'space-y-2' })],
    });
  }

  function nonEmptyOverridesRow() {
    return Object.entries({ ...scrubPresetValues(state.presetValues), ...nonEmptyImageOverrides(state) })
      .map(([k, v]) => `${k}=${v}`).join(', ');
  }

  function keyStatus(value) {
    const trimmed = String(value ?? '').trim();
    if (!trimmed) return '(empty)';
    return isMasked(trimmed) ? '(saved)' : '(set)';
  }

  function summary() {
    const row = (k, v) => el('div', { class: 'flex items-center justify-between gap-3 border border-gray-800 rounded-lg px-3 py-2 bg-gray-900/40' },
      el('span', { class: 'text-xs mono text-indigo-300' }, k),
      el('span', { class: 'text-xs mono text-gray-300 truncate max-w-[60%] text-right' }, String(v ?? '')));
    const presetRow = state.preset
      ? [row(`preset: ${state.preset}`, nonEmptyOverridesRow() || '(image fields only)')]
      : [];
    return [
      row('Story API URL', state.LLM_URL.trim() || '(empty — mining off)'),
      row('Story model', state.LLM_MODEL.trim() || '(empty)'),
      row('Story API key', keyStatus(state.LLM_API_KEY)),
      row('Image API URL', state.FLUX_API_URL.trim() || '(empty — local render)'),
      row('Image model', state.FLUX_API_MODEL.trim() || '(empty)'),
      row('Image API key', keyStatus(state.FLUX_API_KEY)),
      row('WHISPER_MODEL_SIZE', state.WHISPER_MODEL_SIZE),
      ...presetRow,
      row('capture consent', state.consent ? 'confirmed' : 'NOT confirmed'),
    ];
  }

  // ── flow control ──────────────────────────────────────────────────────
  function syncFooter() {
    backBtn.disabled = step === 0;
    backBtn.classList.toggle('opacity-40', step === 0);
    const last = step === STEPS.length - 1;
    nextBtn.textContent = last ? 'Done' : step === STEPS.length - 2 ? 'Save & apply' : 'Next';
    const consentBlock = !last && step >= STEPS.length - 2 && !state.consent;
    nextBtn.disabled = consentBlock;
    nextBtn.classList.toggle('opacity-40', consentBlock);
  }

  function next() {
    if (step === STEPS.length - 1) { onComplete(); return; }
    if (step === STEPS.length - 2) { finish(); return; }
    step += 1;
    render();
  }

  async function finish() {
    const updates = buildFinishUpdates(state);
    nextBtn.disabled = true;
    nextBtn.textContent = 'Saving\u2026';
    try {
      const body = await apiJson('/api/config', { method: 'PATCH', json: updates });
      step += 1;
      render();
      const summaryBox = document.getElementById('onboarding-summary');
      summaryBox.replaceChildren(
        el('p', { class: 'text-sm text-emerald-400' }, '\u2705 Configuration applied.'),
        ...Object.entries(body.results).map(([name, status]) => el('div', { class: 'flex items-center justify-between gap-3 border border-gray-800 rounded-lg px-3 py-2 bg-gray-900/40' },
          el('span', { class: 'text-xs mono text-indigo-300' }, name),
          el('span', { class: `text-xs mono ${status === 'applied' ? 'text-emerald-400' : 'text-amber-400'}` },
            status === 'applied' ? 'applied' : 'restart required'))),
        ...body.notes.map((n) => el('p', { class: 'text-xs text-amber-300' }, n)));
    } catch (e) {
      nextBtn.disabled = false;
      nextBtn.textContent = 'Save & apply';
      const err = e instanceof ApiError ? e.errorLines().join(' \u2014 ') : String(e);
      document.getElementById('onboarding-summary')?.replaceChildren(el('p', { class: 'text-sm text-red-400' }, `\u274C ${err}`));
    }
  }

  function render() {
    dots.replaceChildren(...STEPS.map((name, i) => el('div', {
      class: `h-1.5 rounded-full transition-all ${i === step ? 'w-6 bg-indigo-500' : i < step ? 'w-1.5 bg-indigo-800' : 'w-1.5 bg-gray-700'}`,
      title: name,
    })));
    const stepNode = [stepWelcome, stepLlm, stepWhisper, stepImages, stepConsent, stepFinish][step]();
    body.replaceChildren(stepNode);
    if (step === STEPS.length - 1) {
      document.getElementById('onboarding-summary')?.replaceChildren(...summary());
    }
    syncFooter();
  }

  // Presets feed the Images step; fetched lazily so offline flows still work.
  // GET /api/config/presets returns a list of {name, values} — normalize to a map.
  apiJson('/api/config/presets')
    .then((body) => {
      presetsCache = Object.fromEntries((body.presets ?? []).map((p) => [p.name, p.values ?? {}]));
    })
    .catch(() => { presetsCache = {}; });

  render();
}
