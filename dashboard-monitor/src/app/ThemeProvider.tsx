import type { ReactNode } from 'react';
import { useMemo, useState } from 'react';
import {
  applyTheme,
  getSystemTheme,
  ThemeContext,
  type ThemeContextValue,
  type ThemeMode,
} from '@/app/theme';

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<ThemeMode>(() => getSystemTheme());

  const value = useMemo<ThemeContextValue>(() => {
    const setTheme = (nextTheme: ThemeMode) => {
      applyTheme(nextTheme);
      setThemeState(nextTheme);
    };

    return {
      theme,
      setTheme,
      toggleTheme: () => setTheme(theme === 'dark' ? 'light' : 'dark'),
    };
  }, [theme]);

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}
