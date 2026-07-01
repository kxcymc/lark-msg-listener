import type { ReactNode } from 'react';
import { AlertTriangle, CheckCircle2, Server } from 'lucide-react';
import { StatusBadge } from '@/components/StatusBadge';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { useDashboardConfigQuery, useDashboardHealthQuery } from '@/services';
import type { DashboardApiConfig, HealthLevel } from '@/types';

// /api/health 返回的 checks 布尔项中文标签。
const CHECK_LABELS: Record<string, string> = {
  bot_ready: 'bot.ready 就绪标记',
  index_db_readable: 'index.sqlite 可读',
  out_dir_readable: 'out 目录可读',
  logs_dir_readable: 'logs 目录可读',
};

const levelLabel: Record<HealthLevel, string> = {
  success: '正常',
  warning: '警告',
  danger: '异常',
  info: '信息',
};

const badgeVariant: Record<HealthLevel, 'success' | 'warning' | 'danger' | 'info'> = {
  success: 'success',
  warning: 'warning',
  danger: 'danger',
  info: 'info',
};

function getOverallLevel(status?: string): HealthLevel {
  if (status === 'ok') {
    return 'success';
  }
  if (status === 'degraded' || status === 'warning') {
    return 'warning';
  }
  if (status === 'error' || status === 'danger') {
    return 'danger';
  }
  return 'info';
}

function getConfigValue(value: string | number | boolean | undefined, suffix = '') {
  if (value === undefined) {
    return '--';
  }
  if (typeof value === 'boolean') {
    return value ? '开启' : '关闭';
  }
  return `${value}${suffix}`;
}

function SectionTitle({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-3 border-b border-border p-4 sm:flex-row sm:items-center sm:justify-between">
      <div>
        <h2 className="text-h2 text-foreground">{title}</h2>
        {description ? (
          <p className="mt-1 text-caption text-muted-foreground">{description}</p>
        ) : null}
      </div>
      {action}
    </div>
  );
}

function LoadingCard() {
  return (
    <Card>
      <CardHeader>
        <Skeleton className="h-5 w-32" />
        <Skeleton className="h-4 w-56" />
      </CardHeader>
      <CardContent className="space-y-3">
        <Skeleton className="h-12 w-full" />
        <Skeleton className="h-12 w-full" />
        <Skeleton className="h-12 w-full" />
      </CardContent>
    </Card>
  );
}

function ErrorPanel({ onRetry }: { onRetry: () => void }) {
  return (
    <Card className="border-danger bg-danger-soft/50">
      <CardContent className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-start gap-3">
          <AlertTriangle className="mt-0.5 h-5 w-5 text-danger" />
          <div>
            <p className="text-sm font-semibold text-danger">设置数据加载失败</p>
            <p className="text-caption text-danger">
              数据服务暂不可用，请稍后重试。
            </p>
          </div>
        </div>
        <Button variant="outline" size="sm" onClick={onRetry}>
          重试
        </Button>
      </CardContent>
    </Card>
  );
}

function ConfigField({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div>
      <p className="mb-2 text-caption text-muted-foreground">{label}</p>
      <div className="flex min-h-9 items-center justify-between rounded-md border border-border bg-background px-3">
        <span className="font-mono text-code text-foreground">{value}</span>
        {hint ? <span className="text-caption text-muted-foreground">{hint}</span> : null}
      </div>
    </div>
  );
}

function ReadonlySwitch({ label, checked }: { label: string; checked?: boolean }) {
  return (
    <div className="flex items-center justify-between rounded-md border border-border bg-background px-3 py-2">
      <span className="text-sm text-foreground">{label}</span>
      {checked ? (
        <StatusBadge status="success" label="已启用" />
      ) : (
        <StatusBadge status="pending" label="未启用" />
      )}
    </div>
  );
}

export default function SettingsPage() {
  const healthQuery = useDashboardHealthQuery();
  const configQuery = useDashboardConfigQuery();
  const health = healthQuery.data;
  const configResponse = configQuery.data;
  const config: DashboardApiConfig = configResponse?.config?.dashboard_api ?? {};
  const healthLevel = getOverallLevel(health?.status);
  const checks = health?.checks ?? {};
  const checkEntries = Object.entries(checks);

  if (healthQuery.isLoading || configQuery.isLoading) {
    return (
      <section className="flex flex-col gap-4">
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1.6fr)_minmax(360px,1fr)]">
          <LoadingCard />
          <LoadingCard />
        </div>
      </section>
    );
  }

  const hasError = healthQuery.isError || configQuery.isError;

  return (
    <section className="flex flex-col gap-4">
      {hasError ? (
        <ErrorPanel
          onRetry={() => {
            void healthQuery.refetch();
            void configQuery.refetch();
          }}
        />
      ) : null}

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1.6fr)_minmax(360px,1fr)]">
        <Card className="overflow-hidden">
          <SectionTitle
            title="数据源状态"
            description="来自 /api/health 的就绪标记与各数据源可读性检查"
            action={
              <Badge variant={badgeVariant[healthLevel]}>
                {health?.status ?? 'unknown'}
              </Badge>
            }
          />
          <CardContent className="space-y-3 p-4">
            {checkEntries.length ? (
              checkEntries.map(([key, value]) => (
                <div
                  key={key}
                  className="flex items-start justify-between gap-4 rounded-lg border border-border bg-background p-3"
                >
                  <div className="flex items-start gap-3">
                    {value ? (
                      <CheckCircle2 className="mt-0.5 h-4 w-4 text-success" />
                    ) : (
                      <AlertTriangle className="mt-0.5 h-4 w-4 text-danger" />
                    )}
                    <div>
                      <p className="text-sm font-medium text-foreground">
                        {CHECK_LABELS[key] ?? key}
                      </p>
                      <p className="mt-1 font-mono text-caption text-muted-foreground">{key}</p>
                    </div>
                  </div>
                  <Badge variant={value ? 'success' : 'danger'}>{value ? '通过' : '未通过'}</Badge>
                </div>
              ))
            ) : (
              <div className="rounded-lg border border-dashed border-border bg-muted/40 p-8 text-center text-caption text-muted-foreground">
                暂无检查结果
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>服务整体状态</CardTitle>
            <CardDescription>health.status 与各检查项汇总</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="rounded-lg border border-border bg-background p-4">
              <div className="flex items-start justify-between gap-3">
                <div className="flex items-start gap-3">
                  <div className="rounded-lg bg-muted p-2 text-muted-foreground">
                    <Server className="h-4 w-4" />
                  </div>
                  <div>
                    <p className="text-sm font-semibold text-foreground">API 服务状态</p>
                    <p className="mt-1 text-caption text-muted-foreground">
                      所有检查通过时为 ok，否则 degraded
                    </p>
                  </div>
                </div>
                <Badge variant={badgeVariant[healthLevel]}>{levelLabel[healthLevel]}</Badge>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="rounded-lg bg-muted p-4">
                <p className="text-caption text-muted-foreground">检查项</p>
                <p className="mt-3 text-metric text-foreground">{checkEntries.length}</p>
              </div>
              <div className="rounded-lg bg-muted p-4">
                <p className="text-caption text-muted-foreground">通过</p>
                <p className="mt-3 text-metric text-foreground">
                  {checkEntries.filter(([, value]) => value).length}/{checkEntries.length}
                </p>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card className="overflow-hidden">
          <SectionTitle title="服务端配置摘要" description="[dashboard_api] 只读运行参数" />
          <CardContent className="space-y-5 p-4">
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
              <ConfigField label="监听地址" value={getConfigValue(config.host)} />
              <ConfigField label="服务端口" value={getConfigValue(config.port)} />
              <ConfigField
                label="JSON 预览大小"
                value={getConfigValue(config.max_json_preview_bytes, ' B')}
                hint="超出由服务端截断"
              />
              <ConfigField
                label="日志读取行数"
                value={getConfigValue(config.tail_log_lines)}
                hint="tail 行数上限"
              />
            </div>
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
              <ReadonlySwitch label="API 服务启用" checked={config.enabled} />
              <ReadonlySwitch label="脱敏规则启用" checked={config.sanitize_enabled} />
            </div>
            <div className="rounded-lg border border-info/30 bg-info/10 p-3 text-caption text-info">
              Settings 页不持久化任何配置；如需修改服务端行为，请调整 msg-listener 的
              `config.toml`。
            </div>
          </CardContent>
        </Card>
      </div>
    </section>
  );
}
