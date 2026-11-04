import { afterEach, describe, expect, it, vi } from 'vitest';
import { applyTheme, readTheme } from '../src/theme';

afterEach(() => { localStorage.removeItem('invoice_desk_theme'); delete document.documentElement.dataset.theme; vi.restoreAllMocks(); });
describe('appearance preference', () => {
  it('defaults to light, ignores invalid preferences and restores explicit dark', () => {
    localStorage.removeItem('invoice_desk_theme');
    expect(readTheme()).toBe('light');
    localStorage.setItem('invoice_desk_theme', 'system');
    expect(readTheme()).toBe('light');
    applyTheme('dark');
    expect(document.documentElement).toHaveAttribute('data-theme','dark');
    expect(readTheme()).toBe('dark');
    applyTheme('light');
    expect(readTheme()).toBe('light');
  });
  it('still changes the appearance when storage is blocked', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {throw new Error('Blocked');});
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {throw new Error('Blocked');});
    expect(readTheme()).toBe('light');
    expect(() => applyTheme('dark')).not.toThrow();
    expect(document.documentElement).toHaveAttribute('data-theme','dark');
  });
});
