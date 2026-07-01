import type { ReactNode } from 'react';
import { RotateCcw, Search } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { cn } from '@/lib/utils';

export interface FilterFieldProps {
  /** 字段标签，渲染在控件上方 */
  label: ReactNode;
  /** 跨列设置，如 'md:col-span-2' */
  className?: string;
  children: ReactNode;
}

/**
 * FilterField 字段：标签在上、控件在下，供 FilterBar 栅格内使用。
 * 对齐报表导出页的字段布局（label + 控件）。
 */
export function FilterField({ label, className, children }: FilterFieldProps) {
  return (
    <label className={cn('flex flex-col gap-1.5', className)}>
      <span className="text-caption font-medium text-muted-foreground">{label}</span>
      {children}
    </label>
  );
}

export interface FilterBarProps {
  /** 字段控件，建议用 FilterField 包裹 */
  children?: ReactNode;
  /** 筛选区说明 */
  title?: ReactNode;
  description?: ReactNode;
  /** 当前已选条件数 */
  activeCount?: number;
  /** 右侧额外操作 */
  extra?: ReactNode;
  /** 查询回调 */
  onSearch?: () => void;
  /** 重置回调 */
  onReset?: () => void;
  searchText?: string;
  resetText?: string;
  loading?: boolean;
  disabled?: boolean;
  className?: string;
  /** 字段栅格额外类名，缺省两列（md:grid-cols-2） */
  contentClassName?: string;
}

/**
 * FilterBar 筛选区：Card 容器 + 标题/说明/条件计数 + 字段栅格 + 底部右对齐重置/查询。
 * 布局对齐报表导出页（ReportsExport）的“导出条件”卡片。
 */
export function FilterBar({
  children,
  title,
  description,
  activeCount,
  extra,
  onSearch,
  onReset,
  searchText = '查询',
  resetText = '重置',
  loading,
  disabled,
  className,
  contentClassName,
}: FilterBarProps) {
  const hasHeader = Boolean(title ?? description ?? activeCount ?? extra);

  return (
    <Card className={className}>
      {hasHeader ? (
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              {title ? <CardTitle>{title}</CardTitle> : null}
              {description ? <CardDescription>{description}</CardDescription> : null}
            </div>
            {activeCount || extra ? (
              <div className="flex shrink-0 items-center gap-2">
                {activeCount ? (
                  <span className="rounded-full bg-primary/10 px-2 py-0.5 text-caption font-medium text-primary">
                    已选 {activeCount} 项
                  </span>
                ) : null}
                {extra}
              </div>
            ) : null}
          </div>
        </CardHeader>
      ) : null}
      <CardContent className={cn(!hasHeader && 'pt-4')}>
        <form
          className="flex flex-col gap-5"
          onSubmit={(event) => {
            event.preventDefault();
            onSearch?.();
          }}
        >
          <div className={cn('grid grid-cols-1 gap-4 md:grid-cols-2', contentClassName)}>
            {children}
          </div>
          <div className="flex flex-wrap items-center justify-end gap-2">
            <Button
              disabled={disabled || loading}
              type="button"
              variant="outline"
              onClick={onReset}
            >
              <RotateCcw className="h-3.5 w-3.5" />
              {resetText}
            </Button>
            <Button disabled={disabled || loading} type="submit">
              <Search className="h-3.5 w-3.5" />
              {loading ? '查询中' : searchText}
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}
