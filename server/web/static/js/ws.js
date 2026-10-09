/**
 * Event-feed WebSocket (/ws/events) with pub-sub and auto-reconnect.
 * Browsers cannot set headers on a WebSocket, so the optional WEB_TOKEN rides
 * in ?token= — the only place the server accepts the query form.
 */
import { getToken } from './auth.js';

const listeners = new Map();  // event name -> Set<fn>
let connectionHandler = null;

export function onEvent(name, fn) {
  if (!listeners.has(name)) listeners.set(name, new Set());
  listeners.get(name).add(fn);
  return () => listeners.get(name)?.delete(fn);
}

/** connectionHandler('connected' | 'disconnected') */
export function onConnectionStatus(fn) {
  connectionHandler = fn;
}

function dispatch(data) {
  listeners.get(data.event)?.forEach((fn) => fn(data));
}

export function connect() {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const token = getToken();
  const url = `${proto}//${window.location.host}/ws/events` +
    (token ? `?token=${encodeURIComponent(token)}` : '');

  const ws = new WebSocket(url);

  ws.onopen = () => connectionHandler?.('connected');
  ws.onmessage = (event) => {
    try { dispatch(JSON.parse(event.data)); } catch (e) { console.error('Bad event payload:', e); }
  };
  ws.onclose = () => {
    connectionHandler?.('disconnected');
    setTimeout(connect, 2500);
  };
  ws.onerror = () => ws.close();
  return ws;
}
