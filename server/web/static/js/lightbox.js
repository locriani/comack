/**
 * Full-viewport image lightbox. Replaces target=_blank panel links so looking
 * at art stays on the same page.
 */
import { el } from './dom.js';

let root = null;
let img = null;

function ensure() {
  if (root) return;
  img = el('img', {
    alt: '',
    style: 'max-width:min(96vw,1200px);max-height:90vh;width:auto;height:auto;object-fit:contain;'
      + 'border-radius:8px;box-shadow:0 12px 48px rgba(0,0,0,.55);background:#111',
  });
  const closeBtn = el('button', {
    type: 'button',
    'aria-label': 'Close',
    style: 'position:absolute;top:12px;right:14px;border:0;border-radius:999px;width:36px;height:36px;'
      + 'background:rgba(0,0,0,.55);color:#f0ebe3;font-size:20px;line-height:1;cursor:pointer',
  }, '×');
  closeBtn.addEventListener('click', (e) => { e.stopPropagation(); closeLightbox(); });
  root = el('div', {
    role: 'dialog',
    'aria-modal': 'true',
    'aria-label': 'Image preview',
    style: 'display:none;position:fixed;inset:0;z-index:80;background:rgba(12,10,8,.88);'
      + 'align-items:center;justify-content:center;padding:24px;cursor:zoom-out',
  }, closeBtn, img);
  root.addEventListener('click', closeLightbox);
  img.addEventListener('click', (e) => e.stopPropagation());
  document.body.append(root);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && root?.style.display === 'flex') closeLightbox();
  });
}

export function openLightbox(src, alt = '') {
  if (!src) return;
  ensure();
  img.src = src;
  img.alt = alt;
  root.style.display = 'flex';
  document.body.style.overflow = 'hidden';
}

export function closeLightbox() {
  if (!root) return;
  root.style.display = 'none';
  img.removeAttribute('src');
  document.body.style.overflow = '';
}

/** Click (or Enter/Space) on `node` opens `src` in the lightbox. Safe to call again. */
export function bindLightbox(node, src, alt = '') {
  if (!node) return;
  node._lightboxSrc = src;
  node._lightboxAlt = alt;
  if (node.dataset.lightboxBound === '1') return;
  node.dataset.lightboxBound = '1';
  node.style.cursor = 'zoom-in';
  if (!node.getAttribute('role') && node.tagName !== 'A' && node.tagName !== 'BUTTON') {
    node.setAttribute('role', 'button');
    node.tabIndex = 0;
  }
  const open = (e) => {
    e.preventDefault();
    e.stopPropagation();
    const next = node._lightboxSrc;
    const label = node._lightboxAlt ?? '';
    openLightbox(typeof next === 'function' ? next() : next, label);
  };
  node.addEventListener('click', open);
  node.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') open(e);
  });
}
