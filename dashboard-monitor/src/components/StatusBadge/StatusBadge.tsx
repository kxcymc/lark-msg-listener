import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';

export type StatusKind = 'success' | 'failed' | 'running' | 'pending' | 'timeout';

export interface StatusBadgeProps {
  status: StatusKind;
  /** 自定义展示文字，缺省使用默认中文标签 */
  label?: string;
  size?: 'sm' | 'md';
  showDot?: boolean;
  pulse?: boolean;
  className?: string;
}

const statusConfig: Record<
  StatusKind,
  {
    dot: string;
    label: string;
    badge: 'success' | 'warning' | 'danger' | 'info';
  }
> = {
  success: { dot: 'bg-success', badge: 'success', label: '成功' },
  failed: { dot: 'bg-danger', badge: 'danger', label: '失败' },
  running: { dot: 'bg-primary', badge: 'info', label: '运行中' },
  pending: { dot: 'bg-warning', badge: 'warning', label: '等待中' },
  timeout: { dot: 'bg-warning', badge: 'warning', label: '超时' },
};

/**
 * StatusBadge 状态徽标：圆点 + 文案，按业务状态语义色展示。
 */
export function StatusBadge({
  status,
  label,
  size = 'sm',
  showDot = true,
  pulse,
  className,
}: StatusBadgeProps) {
  const cfg = statusConfig[status];
  const shouldPulse = pulse ?? status === 'running';

  return (
    <Badge
      className={cn(
        'gap-1.5 border font-medium',
        size === 'md' ? 'px-2.5 py-1 text-body' : 'px-2 py-0.5 text-caption',
        className,
      )}
      variant={cfg.badge}
    >
      {showDot ? (
        <span className="relative flex h-2 w-2 items-center justify-center">
          {shouldPulse ? (
            <span className={cn('absolute h-2 w-2 animate-ping rounded-full opacity-40', cfg.dot)} />
          ) : null}
          <span className={cn('relative h-2 w-2 rounded-full', cfg.dot)} />
        </span>
      ) : null}
      {label ?? cfg.label}
    </Badge>
  );
}
