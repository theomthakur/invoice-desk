export type Theme = 'light' | 'dark';
const key = 'invoice_desk_theme';
export function readTheme(): Theme {
  try { return localStorage.getItem(key) === 'dark' ? 'dark' : 'light'; }
  catch { return 'light'; }
}
export function applyTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem(key, theme); } catch { /* Theme still works without storage. */ }
}
