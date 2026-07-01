import { useEffect } from 'react';
import { CheckCircle2, AlertTriangle, Info, X, XCircle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { useAlertStore, type AlertItem, type AlertTone } from './alertStore';

const toneConfig: Record<
  AlertTone,
  { icon: typeof CheckCircle2; container: string; iconClass: string }
> = {
  success: {
    icon: CheckCircle2,
    container: 'border-success/30 bg-success-soft',
    iconClass: 'text-success',
  },
  error: {
    icon: XCircle,
    container: 'border-danger/30 bg-danger-soft',
    iconClass: 'text-danger',
  },
  warning: {
    icon: AlertTriangle,
    container: 'border-warning/30 bg-warning-soft',
    iconClass: 'text-warning',
  },
  info: {
    icon: Info,
    container: 'border-info/30 bg-info/10',
    iconClass: 'text-info',
  },
};

function AlertToast({ alert }: { alert: AlertItem }) {
  const dismiss = useAlertStore((state) => state.dismiss);
  const config = toneConfig[alert.tone];
  const Icon = config.icon;

  useEffect(() => {
    if (alert.duration <= 0) return undefined;
    const timer = window.setTimeout(() => dismiss(alert.id), alert.duration);
    return () => window.clearTimeout(timer);
  }, [alert.id, alert.duration, dismiss]);

  return (
    <div
      className={cn(
        'pointer-events-auto flex w-[360px] max-w-[calc(100vw-2rem)] items-start gap-3 rounded-xl border p-4 shadow-lg',
        'data-[state=open]:animate-in data-[state=open]:slide-in-from-right-4 data-[state=open]:fade-in-0',
        config.container,
      )}
      data-state="open"
      role="alert"
    >
      <Icon className={cn('mt-0.5 h-5 w-5 shrink-0', config.iconClass)} />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-semibold text-foreground">{alert.title}</p>
        {alert.description ? (
          <p className="mt-1 break-words text-caption text-muted-foreground">
            {alert.description}
          </p>
        ) : null}
      </div>
      <Button
        aria-label="关闭提示"
        className="-mr-1 -mt-1 h-7 w-7 shrink-0 text-muted-foreground hover:text-foreground"
        size="icon"
        type="button"
        variant="ghost"
        onClick={() => dismiss(alert.id)}
      >
        <X className="h-4 w-4" />
      </Button>
    </div>
  );
}

/**
 * AlertViewport 全局反馈容器：固定在右上角，承接所有接口动作反馈。
 * 在 AppProviders 中挂载一次即可，页面通过 useAlert() 推送提示。
 */
export function AlertViewport() {
  const alerts = useAlertStore((state) => state.alerts);

  if (!alerts.length) return null;

  return (
    <div className="pointer-events-none fixed right-4 top-4 z-[100] flex flex-col gap-3">
      {alerts.map((alert) => (
        <AlertToast key={alert.id} alert={alert} />
      ))}
    </div>
  );
}
