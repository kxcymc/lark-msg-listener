import type { ReactNode } from 'react';
import { CheckCircle2, Circle, Clock3, Loader2, XCircle } from 'lucide-react';
import { EmptyState } from '@/components/EmptyState';
import { cn } from '@/lib/utils';

type TimelineStatus = 'success' | 'failed' | 'running' | 'pending';

export interface TimelineItem {
  id: string;
  time?: string;
  title: ReactNode;
  description?: ReactNode;
  meta?: ReactNode;
  icon?: ReactNode;
  /** 节点语义色 */
  status?: TimelineStatus;
}

export interface TimelineProps {
  items: TimelineItem[];
  compact?: boolean;
  showConnector?: boolean;
  emptyTitle?: string;
  emptyDescription?: string;
  className?: string;
}

const statusConfig: Record<
  TimelineStatus,
  {
    dot: string;
    soft: string;
    text: string;
    icon: ReactNode;
  }
> = {
  success: {
    dot: 'bg-success',
    soft: 'bg-success-soft',
    text: 'text-success',
    icon: <CheckCircle2 className="h-3.5 w-3.5" />,
  },
  failed: {
    dot: 'bg-danger',
    soft: 'bg-danger-soft',
    text: 'text-danger',
    icon: <XCircle className="h-3.5 w-3.5" />,
  },
  running: {
    dot: 'bg-primary',
    soft: 'bg-primary/10',
    text: 'text-primary',
    icon: <Loader2 className="h-3.5 w-3.5 animate-spin" />,
  },
  pending: {
    dot: 'bg-warning',
    soft: 'bg-warning-soft',
    text: 'text-warning',
    icon: <Clock3 className="h-3.5 w-3.5" />,
  },
};

/**
 * Timeline 时间线：纵向节点 + 时间 + 标题 + 描述。
 * 用于执行过程、事件流展示。
 */
export function Timeline({
  items,
  compact,
  showConnector = true,
  emptyTitle = '暂无事件',
  emptyDescription = '当前时间范围内没有可展示的执行记录',
  className,
}: TimelineProps) {
  if (!items.length) {
    return (
      <EmptyState
        className={cn('min-h-[180px]', className)}
        description={emptyDescription}
        icon={<Circle className="h-6 w-6" />}
        title={emptyTitle}
      />
    );
  }

  return (
    <ol className={cn('flex flex-col', className)}>
      {items.map((item, index) => {
        const status = item.status ?? 'running';
        const cfg = statusConfig[status];
        return (
          <li
            key={item.id}
            className={cn('relative flex gap-3', compact ? 'pb-3 last:pb-0' : 'pb-5 last:pb-0')}
          >
            <div className="flex flex-col items-center">
              <span
                className={cn(
                  'mt-0.5 flex h-7 w-7 items-center justify-center rounded-full ring-4 ring-background',
                  cfg.soft,
                  cfg.text,
                )}
              >
                {item.icon ?? cfg.icon}
              </span>
              {showConnector && index < items.length - 1 ? (
                <span className="my-1 w-px flex-1 bg-border" />
              ) : null}
            </div>
            <div
              className={cn(
                'min-w-0 flex-1 rounded-lg border border-border bg-card shadow-sm',
                compact ? 'px-3 py-2' : 'px-4 py-3',
              )}
            >
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0">
                  {item.time ? (
                    <span className="text-caption text-muted-foreground">{item.time}</span>
                  ) : null}
                  <div className="text-body font-medium text-foreground">{item.title}</div>
                </div>
                {item.meta ? (
                  <div className="shrink-0 text-caption text-muted-foreground">{item.meta}</div>
                ) : null}
              </div>
              {item.description ? (
                <div className="mt-1 text-caption text-muted-foreground">
                  {item.description}
                </div>
              ) : null}
              <span
                className={cn(
                  'absolute left-[13px] top-3 h-2 w-2 rounded-full border border-background',
                  cfg.dot,
                )}
              />
            </div>
          </li>
        );
      })}
    </ol>
  );
}
