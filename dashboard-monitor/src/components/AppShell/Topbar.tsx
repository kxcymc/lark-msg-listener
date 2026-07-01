import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { Moon, RefreshCw, Sun } from 'lucide-react';
import { navGroups, type NavItem } from '@/app/navigation';
import { useTheme } from '@/app/theme';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { useDashboardHealthQuery } from '@/services';
import type { HealthLevel } from '@/types';

interface BreadcrumbItem {
  label: string;
  path?: string;
}

/** 根据当前路由计算所属分组、层级面包屑与唯一 H1。 */
function usePageMeta() {
  const { pathname } = useLocation();
  let matched:
    | {
        groupTitle: string;
        item: NavItem;
        parent?: NavItem;
      }
    | undefined;

  for (const group of navGroups) {
    for (const item of group.items) {
      const isMatch = pathname === item.path || pathname.startsWith(item.path + '/');
      if (!isMatch) continue;
      if (!matched || item.path.length > matched.item.path.length) {
        const parent = item.hiddenInSidebar
          ? group.items.find(
              (candidate) =>
                !candidate.hiddenInSidebar &&
                item.path.startsWith(`${candidate.path}/`),
            )
          : undefined;
        matched = { groupTitle: group.title, item, parent };
      }
    }
  }

  if (matched) {
    const breadcrumbs: BreadcrumbItem[] = [
      { label: matched.groupTitle },
      ...(matched.parent
        ? [{ label: matched.parent.label, path: matched.parent.path }]
        : []),
      { label: matched.item.label },
    ];
    return { breadcrumbs, title: matched.item.label };
  }

  return {
    breadcrumbs: [
      { label: navGroups[0]?.title ?? '概览' },
      { label: '总览' },
    ],
    title: '总览',
  };
}

interface HealthIndicator {
  level: HealthLevel;
  dotClass: string;
  label: string;
  pulse: boolean;
}

function getHealthIndicator(status: string | undefined, isLoading: boolean, isError: boolean): HealthIndicator {
  if (isLoading) {
    return { level: 'info', dotClass: 'bg-muted-foreground', label: '检查中', pulse: true };
  }
  if (isError || status === 'error') {
    return { level: 'danger', dotClass: 'bg-danger', label: '服务异常', pulse: false };
  }
  if (status === 'ok') {
    return { level: 'success', dotClass: 'bg-success', label: '服务正常', pulse: true };
  }
  if (status === 'degraded' || status === 'warning') {
    return { level: 'warning', dotClass: 'bg-warning', label: '部分降级', pulse: false };
  }
  return { level: 'info', dotClass: 'bg-muted-foreground', label: '状态未知', pulse: false };
}

/**
 * 顶栏 56px：左侧面包屑 + 唯一 H1，右侧刷新、深浅色切换、真实服务健康灯（点击跳转 /settings）。
 */
export function Topbar() {
  const { breadcrumbs, title } = usePageMeta();
  const { theme, toggleTheme } = useTheme();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const isDark = theme === 'dark';
  const [isRefreshing, setIsRefreshing] = useState(false);

  const handleRefresh = async () => {
    setIsRefreshing(true);
    try {
      await queryClient.invalidateQueries();
    } finally {
      setIsRefreshing(false);
    }
  };

  const healthQuery = useDashboardHealthQuery();
  const indicator = getHealthIndicator(
    healthQuery.data?.status,
    healthQuery.isLoading,
    healthQuery.isError,
  );

  return (
    <header className="flex h-topbar shrink-0 items-center gap-4 border-b border-border bg-card px-6">
      {/* 左侧唯一页面标题 + 跟随面包屑 */}
      <div className="flex min-w-0 flex-1 items-baseline gap-3">
        <h1 className="shrink-0 truncate text-h1 text-foreground">{title}</h1>
        <nav
          aria-label="面包屑"
          className="flex min-w-0 items-center gap-1 truncate text-caption text-muted-foreground"
        >
          {breadcrumbs.map((item, index) => (
            <span
              key={`${item.label}-${index}`}
              className="inline-flex min-w-0 items-center gap-1"
            >
              {index > 0 ? <span aria-hidden>/</span> : null}
              {item.path ? (
                <Link
                  className="truncate transition-colors hover:text-foreground"
                  to={item.path}
                >
                  {item.label}
                </Link>
              ) : (
                <span className="truncate">{item.label}</span>
              )}
            </span>
          ))}
        </nav>
      </div>

      {/* 右侧操作区 */}
      <div className="flex items-center gap-4">
        <Button disabled={isRefreshing} size="sm" onClick={() => void handleRefresh()}>
          <RefreshCw className={cn('h-3.5 w-3.5', isRefreshing && 'animate-spin')} />
          {isRefreshing ? '刷新中' : '刷新'}
        </Button>
        <Button
          aria-label={`切换到${isDark ? '浅色' : '深色'}模式`}
          size="sm"
          type="button"
          variant="outline"
          onClick={toggleTheme}
        >
          {isDark ? <Moon className="h-3.5 w-3.5" /> : <Sun className="h-3.5 w-3.5" />}
          {isDark ? '深色' : '浅色'}
        </Button>
        <button
          aria-label={`服务健康状态：${indicator.label}，点击查看检查项`}
          className="flex h-7 items-center gap-2 rounded-md border border-border bg-background px-3 text-[13px] font-medium text-foreground outline-none transition-colors hover:bg-accent/50 focus-visible:ring-2 focus-visible:ring-ring"
          title={`服务健康状态：${indicator.label}`}
          type="button"
          onClick={() => navigate('/settings')}
        >
          <span className="relative flex h-2.5 w-2.5 items-center justify-center">
            {indicator.pulse ? (
              <span className={cn('absolute h-2.5 w-2.5 animate-ping rounded-full opacity-35', indicator.dotClass)} />
            ) : null}
            <span className={cn('relative h-2.5 w-2.5 rounded-full', indicator.dotClass)} />
          </span>
          {indicator.label}
        </button>
      </div>
    </header>
  );
}
