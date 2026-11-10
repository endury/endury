/* Endury — shared SVG icons (24×24 viewBox, stroke-based). */

const S = (size, stroke, inner, extra = '') =>
  `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${stroke}" stroke-linecap="round" stroke-linejoin="round"${extra}>${inner}</svg>`;

export const icons = {
  check: (s = 16, w = 3) => S(s, w, '<path d="M5 13l4 4L19 7"/>'),
  chevronRight: (s = 12, w = 2) => S(s, w, '<path d="M9 6l6 6-6 6"/>'),
  chevronLeft: (s = 13, w = 2.4) => S(s, w, '<path d="M15 6l-6 6 6 6"/>'),
  close: (s = 15, w = 2) => S(s, w, '<path d="M6 6l12 12M18 6L6 18"/>'),
  trash: (s = 15, w = 2) => S(s, w, '<path d="M4 7h16M9 7V5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M10 11v6M14 11v6"/>'),
  send: (s = 15, w = 2.5) => S(s, w, '<line x1="12" y1="19" x2="12" y2="5"/><polyline points="5 12 12 5 19 12"/>'),
  digest: (s = 12, w = 2) => S(s, w, '<path d="M4 6h16M4 12h16M4 18h10"/>'),
  bookmark: (s = 12, w = 2) => S(s, w, '<path d="M7 4h10v16l-5-4-5 4z"/>'),
  newChat: (s = 12, w = 2.2) => S(s, w, '<path d="M12 5v14M5 12h14"/>'),
  bulb: (s = 16, w = 2) => S(s, w, '<path d="M9 18h6M10 21h4"/><path d="M12 3a6 6 0 0 0-3.6 10.8c.7.5 1.1 1.2 1.1 2V16h5v-.2c0-.8.4-1.5 1.1-2A6 6 0 0 0 12 3z"/>'),
  chat: (s = 22, w = 2) => S(s, w, '<path d="M5 4h14a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-8l-5 4v-4H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z"/>'),
  gauge: (s = 21, w = 2) => S(s, w, '<path d="M4 13a8 8 0 1 1 16 0"/><path d="M12 13l3-4"/><circle cx="12" cy="13" r="1" fill="currentColor" stroke="none"/>'),
  refresh: (s = 17, w = 2) => S(s, w, '<path d="M20 12a8 8 0 1 1-2.3-5.7"/><path d="M20 4v4.5h-4.5"/>'),
};
