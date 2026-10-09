/**
 * Tiny DOM helpers shared by every view. No framework: `el()` builds nodes,
 * `fmt*` render timestamps from epoch seconds.
 */
export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value == null || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2).toLowerCase(), value);
    else node.setAttribute(key, value === true ? '' : value);
  }
  for (const child of children.flat()) {
    if (child == null || child === false) continue;
    node.append(child.nodeType ? child : document.createTextNode(child));
  }
  return node;
}

export const fmtTime = (epochSeconds) => new Date(epochSeconds * 1000).toLocaleTimeString();
export const fmtDateTime = (epochSeconds) => new Date(epochSeconds * 1000).toLocaleString();
