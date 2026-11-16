// Light and dark themes: the choice applies as `data-theme` on <html>, and is
// remembered in this browser when storage allows. Without a remembered choice,
// the system's preference applies.

import { useCallback, useState } from 'react';

const STORAGE_KEY = 'topoview-theme';

export type Theme = 'light' | 'dark';

function initialTheme(): Theme {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === 'light' || stored === 'dark') return stored;
  } catch {
    // Storage may be unavailable (private windows, file pages); fall through.
  }
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

export function applyTheme(theme: Theme): void {
  document.documentElement.setAttribute('data-theme', theme);
}

export function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(() => {
    const theme = initialTheme();
    applyTheme(theme);
    return theme;
  });
  const toggle = useCallback(() => {
    setTheme((current) => {
      const next: Theme = current === 'dark' ? 'light' : 'dark';
      applyTheme(next);
      try {
        localStorage.setItem(STORAGE_KEY, next);
      } catch {
        // Not remembered, but still applied.
      }
      return next;
    });
  }, []);
  return [theme, toggle];
}
