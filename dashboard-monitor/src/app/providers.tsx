import type { ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ThemeProvider } from '@/app/ThemeProvider';
import { AlertViewport } from '@/components/Alert';
import { TooltipProvider } from '@/components/ui/tooltip';

const ONE_HOUR_MS = 60 * 60 * 1000;

// 全局 TanStack Query 客户端：默认每 1 小时自动刷新一次，各页面可按需覆盖。
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchInterval: ONE_HOUR_MS,
      refetchOnWindowFocus: false,
      retry: 1,
      staleTime: 3000,
    },
  },
});

/** 全局 Provider：数据请求、Tooltip 等跨页面上下文。 */
export function AppProviders({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <TooltipProvider delayDuration={200}>{children}</TooltipProvider>
        <AlertViewport />
      </ThemeProvider>
    </QueryClientProvider>
  );
}
