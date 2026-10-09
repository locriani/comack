/**
 * Token lifecycle for the optional WEB_TOKEN auth.
 * The server answers 401 for browser-facing endpoints while a token is set;
 * api.js funnels those here, we prompt once, and the page reloads so every
 * view re-fetches with the header attached. The token lives in localStorage.
 */
const STORAGE_KEY = 'comack.web_token';

export function getToken() {
  return localStorage.getItem(STORAGE_KEY) ?? '';
}

export function setToken(token) {
  const trimmed = (token || '').trim();
  if (trimmed) localStorage.setItem(STORAGE_KEY, trimmed);
  else localStorage.removeItem(STORAGE_KEY);
}

let challengeUi = null;

/**
 * Registers the UI shown on a 401 (built once, reused for repeat failures).
 * Resolves true when a token was saved — the caller reloads the page.
 */
export function onUnauthorized(showPrompt) {
  challengeUi = showPrompt;
}

export function unauthorized() {
  challengeUi?.();
}
