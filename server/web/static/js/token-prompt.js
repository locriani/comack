/**
 * The 401 challenge UI: a single modal that asks for the WEB_TOKEN, stores it,
 * and reloads the page so every view re-fetches with the header attached.
 * Wired into api.js via onUnauthorized at boot (see main.js).
 */
import { el } from './dom.js';
import { getToken, setToken, onUnauthorized } from './auth.js';

export function wireTokenChallenge() {
  let overlay = null;

  onUnauthorized(() => {
    if (overlay) return;  // already asking

    const input = el('input', {
      type: 'password', value: getToken(), placeholder: 'WEB_TOKEN value',
      class: 'bg-gray-900 border border-gray-700 rounded px-3 py-2 text-sm text-white w-full focus:outline-none focus:border-indigo-500',
      'aria-label': 'Access token',
    });
    const submit = () => {
      if (!input.value.trim()) return;
      setToken(input.value);
      window.location.reload();  // re-fetch everything with the header attached
    };
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') submit(); });

    overlay = el('div', {
      class: 'fixed inset-0 z-[100] bg-black/80 flex items-center justify-center p-4',
      role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Access token required',
    },
      el('div', { class: 'card p-6 w-full max-w-md space-y-4 shadow-2xl' },
        el('h2', { class: 'text-lg font-bold text-white' }, '🔒 Access token required'),
        el('p', { class: 'text-sm text-gray-400' },
          'This Comack server is protected by WEB_TOKEN. Paste the token from the server\u2019s .env to continue.'),
        input,
        el('button', {
          class: 'w-full bg-indigo-600 hover:bg-indigo-500 text-white font-medium text-sm px-4 py-2 rounded-md transition',
          onclick: submit,
        }, 'Save & continue'),
      ),
    );
    document.body.append(overlay);
    input.focus();
  });
}
