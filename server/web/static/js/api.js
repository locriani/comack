/**
 * Fetch wrapper: attaches the stored bearer token, funnels 401s to the auth
 * prompt, and turns error bodies (FastAPI {"detail": ...}) into ApiError.
 */
import { getToken, unauthorized } from './auth.js';

export class ApiError extends Error {
  constructor(status, body) {
    super(`HTTP ${status}`);
    this.status = status;
    this.body = body;
  }

  /** FastAPI error details: {"detail": "text"} or {"detail": {"errors": [..]}} */
  errorLines() {
    const detail = this.body?.detail;
    if (!detail) return [this.message];
    if (typeof detail === 'string') return [detail];
    return detail.errors ?? [JSON.stringify(detail)];
  }
}

async function safeJson(res) {
  try { return await res.json(); } catch { return null; }
}

export async function api(path, { method = 'GET', json, headers = {} } = {}) {
  const withHeaders = new Headers(headers);
  const token = getToken();
  if (token) withHeaders.set('Authorization', `Bearer ${token}`);
  if (json !== undefined) withHeaders.set('Content-Type', 'application/json');

  const res = await fetch(path, {
    method,
    headers: withHeaders,
    body: json !== undefined ? JSON.stringify(json) : undefined,
  });
  if (res.status === 401) {
    unauthorized();  // prompt once; the page reloads when a token is saved
    throw new ApiError(401, await safeJson(res));
  }
  return res;
}

export async function apiJson(path, options) {
  const res = await api(path, options);
  if (!res.ok) throw new ApiError(res.status, await safeJson(res));
  return res.json();
}

/** The server masks secret-shaped settings and URL credentials with this
 * sentinel before GET /api/config returns them (server/config_api.py MASKED).
 * A masked value is a placeholder, never the real thing — echoing one back
 * in a PATCH would overwrite the stored value with asterisks. */
export const MASKED = '********';

export const isMasked = (value) => (typeof value === 'string' && value.includes(MASKED))
  || (Array.isArray(value) && value.some((v) => isMasked(v)));
