import type { ReactNode } from 'react';
import { AlertCircle, Inbox, SearchX } from 'lucide-react';
import { cn } from '@/lib/utils';

type EmptyStateTone = 'default' | 'muted' | 'warning' | 'danger';

export interface EmptyStateProps {
  /** 说明文字 */
  title?: string;
  description?: string;
  icon?: ReactNode;
  /** 可选操作按钮 */
  action?: ReactNode;
  tone?: EmptyStateTone;
  compact?: boolean;
  className?: string;
}

const toneClassName: Record<
  EmptyStateTone,
  {
    shell: string;
    icon: string;
    defaultIcon: ReactNode;
  }
> = {
  default: {
    shell: 'bg-primary/10 text-primary',
    icon: 'text-primary',
    defaultIcon: <Inbox className="h-6 w-6" />,
  },
  muted: {
    shell: 'bg-muted text-muted-foreground',
    icon: 'text-muted-foreground',
    defaultIcon: <SearchX className="h-6 w-6" />,
  },
  warning: {
    shell: 'bg-warning-soft text-warning',
    icon: 'text-warning',
    defaultIcon: <AlertCircle className="h-6 w-6" />,
  },
  danger: {
    shell: 'bg-danger-soft text-danger',
    icon: 'text-danger',
    defaultIcon: <AlertCircle className="h-6 w-6" />,
  },
};

/**
 * EmptyState 空状态：图标 + 说明 + 可选操作。
 */
export function EmptyState({
  title = '暂无数据',
  description,
  icon,
  action,
  tone = 'muted',
  compact,
  className,
}: EmptyStateProps) {
  const cfg = toneClassName[tone];

  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center gap-3 text-center',
        compact ? 'min-h-[120px] py-6' : 'min-h-[240px] px-6 py-10',
        className,
      )}
    >
      <div
        className={cn(
          'relative flex items-center justify-center rounded-full',
          compact ? 'h-10 w-10' : 'h-12 w-12',
          cfg.shell,
        )}
      >
        <span className={cfg.icon}>{icon ?? cfg.defaultIcon}</span>
      </div>
      <div className="flex max-w-[360px] flex-col gap-1">
        <p className="text-body font-medium text-foreground">{title}</p>
        {description ? (
          <p className="text-caption text-muted-foreground">{description}</p>
        ) : null}
      </div>
      {action ? <div className="mt-1 flex items-center justify-center gap-2">{action}</div> : null}
    </div>
  );
}
