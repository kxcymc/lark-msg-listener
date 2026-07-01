import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import type { ColumnDef } from '@tanstack/react-table';
import { AlertTriangle, Clock3, ListFilter } from 'lucide-react';
import { ChartCard } from '@/components/ChartCard';
import { DataTable } from '@/components/DataTable';
import { EmptyState } from '@/components/EmptyState';
import { StatusBadge } from '@/components/StatusBadge';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { getJson } from '@/services';

const REFRESH_INTERVAL_MS = 10000;
const LOG_LINE_LIMIT = 300;
const PROCESS_SERVICES = ['main', 'bot', 'collector'];
const LOG_SERVICES = ['all', 'main', 'bot', 'collector', 'dashboard-api', 'system', 'mywork'];

type UnknownRecord = Record<string, unknown>;
type ProcessStatus = 'success' | 'failed' | 'running' | 'pending' | 'timeout';

interface ProcessRow {
  id: string;
  name: string;
  label: string;
  status: ProcessStatus;
  statusLabel: string;
  pid?: number;
  pids: number[];
  uptime?: string;
  command?: string;
  checkedAt?: string;
}

interface LogEntry {
  id: string;
  service: string;
  line: string;
  level: 'error' | 'warn' | 'info' | 'debug';
}

const serviceLabel: Record<string, string> = {
  all: '全部',
  main: 'Main',
  bot: 'Bot',
  collector: 'Collector',
  'dashboard-api': 'Dashboard API',
  system: 'System',
  mywork: 'Mywork',
};

function isRecord(value: unknown): value is UnknownRecord {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function asString(value: unknown) {
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return undefined;
}

function asNumber(value: unknown) {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : undefined;
  }
  return undefined;
}

function asBoolean(value: unknown) {
  if (typeof value === 'boolean') return value;
  if (typeof value === 'string') {
    const normalized = value.toLowerCase();
    if (['true', 'yes', 'running', 'alive', 'ok', 'healthy', 'success'].includes(normalized)) {
      return true;
    }
    if (['false', 'no', 'stopped', 'dead', 'error', 'failed', 'danger'].includes(normalized)) {
      return false;
    }
  }
  return undefined;
}

function readPath(root: unknown, path: string): unknown {
  return path.split('.').reduce<unknown>((current, key) => {
    if (!isRecord(current)) return undefined;
    return current[key];
  }, root);
}

function firstString(root: unknown, paths: string[]) {
  for (const path of paths) {
    const value = asString(readPath(root, path));
    if (value) return value;
  }
  return undefined;
}

function firstNumber(root: unknown, paths: string[]) {
  for (const path of paths) {
    const value = asNumber(readPath(root, path));
    if (value !== undefined) return value;
  }
  return undefined;
}

function firstBoolean(root: unknown, paths: string[]) {
  for (const path of paths) {
    const value = asBoolean(readPath(root, path));
    if (value !== undefined) return value;
  }
  return undefined;
}

function pickCollection(payload: unknown, keys: string[]) {
  if (Array.isArray(payload)) {
    return payload.filter(isRecord);
  }

  if (!isRecord(payload)) {
    return [];
  }

  for (const key of keys) {
    const value = payload[key];
    if (Array.isArray(value)) {
      return value.filter(isRecord);
    }
  }

  return [];
}

function formatNumber(value?: number) {
  return value === undefined ? '--' : value.toLocaleString('zh-CN');
}

function normalizeStatus(record: UnknownRecord): Pick<ProcessRow, 'status' | 'statusLabel'> {
  const explicitStatus = firstString(record, ['status', 'state', 'health'])?.toLowerCase();
  const alive = firstBoolean(record, ['alive', 'running', 'ok', 'healthy', 'ready']);

  if (alive === true || ['running', 'alive', 'ok', 'healthy', 'success'].includes(explicitStatus ?? '')) {
    return { status: 'running', statusLabel: '运行中' };
  }

  if (alive === false || ['failed', 'error', 'stopped', 'dead', 'danger'].includes(explicitStatus ?? '')) {
    return { status: 'failed', statusLabel: '未运行' };
  }

  if (['pending', 'starting'].includes(explicitStatus ?? '')) {
    return { status: 'pending', statusLabel: '启动中' };
  }

  return { status: 'timeout', statusLabel: '未确认' };
}

function normalizeProcesses(payload: unknown): ProcessRow[] {
  const collection = pickCollection(payload, ['services', 'processes', 'items', 'data']);
  const objectRows =
    collection.length === 0 && isRecord(payload)
      ? PROCESS_SERVICES.flatMap((service) => {
          const value = payload[service];
          return isRecord(value) ? [{ ...value, name: service }] : [];
        })
      : [];

  return [...collection, ...objectRows].map((record, index) => {
    const name = firstString(record, ['name', 'service', 'key', 'process']) ?? `service-${index + 1}`;
    const status = normalizeStatus(record);
    const rawPids = (record as UnknownRecord).pids;
    const pids = Array.isArray(rawPids)
      ? rawPids.map((pid) => asNumber(pid)).filter((pid): pid is number => pid !== undefined)
      : [];

    return {
      id: `${name}-${index}`,
      name,
      label: serviceLabel[name] ?? name,
      pid: pids[0] ?? firstNumber(record, ['pid', 'processId']),
      pids,
      uptime: firstString(record, ['uptime', 'uptimeText', 'startedAt', 'updatedAt']),
      command: firstString(record, ['command', 'cmdline', 'path', 'executable']),
      checkedAt: firstString(record, ['checkedAt', 'sampledAt', 'updatedAt']),
      ...status,
    };
  });
}

function lineLevel(line: string): LogEntry['level'] {
  const normalized = line.toLowerCase();
  if (normalized.includes('error') || normalized.includes('exception') || normalized.includes('traceback')) {
    return 'error';
  }
  if (normalized.includes('warn')) {
    return 'warn';
  }
  if (normalized.includes('debug')) {
    return 'debug';
  }
  return 'info';
}

function normalizeLogs(payload: unknown, fallbackService: string): LogEntry[] {
  // 后端返回 { service, date, lines: string[], known_services: string[] }，
  // 只取 lines 渲染，service 用于标签；不把 known_services 等当作日志行。
  if (isRecord(payload) && Array.isArray(payload.lines)) {
    const service = asString(payload.service) ?? fallbackService;
    return payload.lines.map((line, index) => {
      const text = asString(line) ?? JSON.stringify(line);
      return {
        id: `${service}-${index}`,
        service,
        line: text,
        level: lineLevel(text),
      };
    });
  }

  const entries = pickCollection(payload, ['entries', 'logs', 'items', 'data']);
  if (entries.length) {
    return entries.map((record, index) => {
      const line =
        firstString(record, ['line', 'message', 'text', 'content']) ?? JSON.stringify(record);
      const service = firstString(record, ['service', 'name', 'source']) ?? fallbackService;
      return {
        id: `${service}-${index}`,
        service,
        line,
        level: lineLevel(line),
      };
    });
  }

  return [];
}

function getErrorMessage(error: unknown) {
  return error instanceof Error ? error.message : '请求失败';
}

function QueryErrorPanel({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <Card className="border-danger bg-danger-soft/50">
      <CardContent className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-start gap-3">
          <AlertTriangle className="mt-0.5 h-5 w-5 text-danger" />
          <div>
            <p className="text-sm font-semibold text-danger">实时数据暂不可用</p>
            <p className="text-caption text-danger">{message}</p>
          </div>
        </div>
        <Button variant="outline" size="sm" onClick={onRetry}>
          重试
        </Button>
      </CardContent>
    </Card>
  );
}

export default function RealtimeMonitorPage() {
  const [selectedLogService, setSelectedLogService] = useState('all');
  const [keyword, setKeyword] = useState('');

  const processesQuery = useQuery({
    queryKey: ['realtime-monitor', 'processes'],
    queryFn: () => getJson<unknown>('/monitor/processes'),
    refetchInterval: REFRESH_INTERVAL_MS,
  });
  const logsQuery = useQuery({
    queryKey: ['realtime-monitor', 'logs', selectedLogService],
    queryFn: () =>
      getJson<unknown>('/logs', {
        service: selectedLogService === 'all' ? undefined : selectedLogService,
        lines: LOG_LINE_LIMIT,
      }),
    refetchInterval: REFRESH_INTERVAL_MS,
  });

  const processes = useMemo(() => normalizeProcesses(processesQuery.data), [processesQuery.data]);
  const logs = useMemo(
    () => normalizeLogs(logsQuery.data, selectedLogService),
    [logsQuery.data, selectedLogService],
  );
  const filteredLogs = useMemo(() => {
    const normalizedKeyword = keyword.trim().toLowerCase();
    if (!normalizedKeyword) return logs;
    return logs.filter((entry) => entry.line.toLowerCase().includes(normalizedKeyword));
  }, [keyword, logs]);

  const processColumns = useMemo<ColumnDef<ProcessRow>[]>(
    () => [
      {
        accessorKey: 'label',
        header: '服务',
        cell: ({ row }) => (
          <div>
            <p className="font-medium text-foreground">{row.original.label}</p>
            <p className="font-mono text-code text-muted-foreground">{row.original.name}</p>
          </div>
        ),
      },
      {
        accessorKey: 'status',
        header: '状态',
        cell: ({ row }) => (
          <StatusBadge
            status={row.original.status}
            label={row.original.statusLabel}
            pulse={row.original.status === 'running'}
          />
        ),
      },
      {
        accessorKey: 'pid',
        header: '进程 PID',
        cell: ({ row }) => (
          <span className="font-mono text-code">{formatNumber(row.original.pid)}</span>
        ),
      },
      {
        accessorKey: 'uptime',
        header: '最近检查',
        cell: ({ row }) => row.original.uptime ?? row.original.checkedAt ?? '--',
      },
      {
        accessorKey: 'command',
        header: '命令 / 路径',
        cell: ({ row }) => (
          <span className="line-clamp-2 font-mono text-code text-muted-foreground">
            {row.original.command ?? '--'}
          </span>
        ),
      },
    ],
    [],
  );

  const hasError = processesQuery.isError || logsQuery.isError;

  const refetchAll = () => {
    void processesQuery.refetch();
    void logsQuery.refetch();
  };

  return (
    <section className="flex flex-col gap-4">
      {hasError ? (
        <QueryErrorPanel
          message={[processesQuery.error, logsQuery.error]
            .filter(Boolean)
            .map(getErrorMessage)
            .join('；')}
          onRetry={refetchAll}
        />
      ) : null}

      <DataTable
        title="服务健康"
        description="main、bot、collector 进程表即时探活"
        columns={processColumns}
        data={processes}
        loading={processesQuery.isLoading}
        skeletonRows={3}
        emptyTitle="暂无进程探活结果"
        emptyDescription="暂无进程数据，请稍后重试。"
      />

      <ChartCard
        title="日志尾部"
        actions={
          <span className="inline-flex items-center gap-1 text-caption text-muted-foreground">
            <Clock3 className="h-3.5 w-3.5" />
            {LOG_LINE_LIMIT} 行以内
          </span>
        }
        className="overflow-hidden"
      >
        <div className="flex flex-col gap-3">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
            <Tabs value={selectedLogService} onValueChange={setSelectedLogService}>
              <TabsList className="flex h-auto flex-wrap justify-start">
                {LOG_SERVICES.map((service) => (
                  <TabsTrigger key={service} value={service}>
                    {serviceLabel[service] ?? service}
                  </TabsTrigger>
                ))}
              </TabsList>
            </Tabs>
            <label className="flex min-w-[260px] items-center gap-2 rounded-lg border border-border bg-background px-3 py-2">
              <ListFilter className="h-4 w-4 text-muted-foreground" />
              <input
                className="w-full bg-transparent text-sm text-foreground outline-none placeholder:text-muted-foreground"
                value={keyword}
                onChange={(event) => setKeyword(event.target.value)}
                placeholder="按关键词过滤日志"
              />
            </label>
          </div>

          <div className="min-h-[320px] overflow-hidden rounded-xl border border-border bg-muted/40">
            {logsQuery.isLoading ? (
              <div className="space-y-2 p-4">
                {Array.from({ length: 8 }).map((_, index) => (
                  <Skeleton key={index} className="h-5 w-full" />
                ))}
              </div>
            ) : filteredLogs.length ? (
              <div className="max-h-[520px] overflow-auto p-3 font-mono text-code">
                {filteredLogs.map((entry) => (
                  <div
                    key={entry.id}
                    className="grid grid-cols-[88px_64px_minmax(0,1fr)] gap-3 border-b border-border/70 px-2 py-1.5 last:border-0"
                  >
                    <span className="text-muted-foreground">{serviceLabel[entry.service] ?? entry.service}</span>
                    <span
                      className={
                        entry.level === 'error'
                          ? 'text-danger'
                          : entry.level === 'warn'
                            ? 'text-warning'
                            : entry.level === 'debug'
                              ? 'text-muted-foreground'
                              : 'text-info'
                      }
                    >
                      {entry.level.toUpperCase()}
                    </span>
                    <span className="whitespace-pre-wrap break-words text-foreground">
                      {entry.line}
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              <EmptyState
                title="暂无日志"
                description="当前服务或关键词没有匹配的日志尾部内容。"
                compact
              />
            )}
          </div>
        </div>
      </ChartCard>
    </section>
  );
}
