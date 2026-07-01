import { useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import type { ColumnDef } from '@tanstack/react-table';
import * as echarts from 'echarts';
import type { EChartsOption } from 'echarts';
import { AlertTriangle, ExternalLink } from 'lucide-react';
import { ChartCard } from '@/components/ChartCard';
import { DataTable } from '@/components/DataTable';
import { EmptyState } from '@/components/EmptyState';
import { MetricCard } from '@/components/MetricCard';
import { StatusBadge } from '@/components/StatusBadge';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { useTheme } from '@/app/theme';
import { getJson } from '@/services/api';

const OVERVIEW_REFETCH_INTERVAL_MS = 60 * 1000;

type UnknownRecord = Record<string, unknown>;

type StatusKind = 'success' | 'failed' | 'running' | 'pending' | 'timeout';

interface DemandStatusItem {
  key: string;
  label: string;
  value: number;
}

interface TimeSeriesItem {
  date: string;
  records: number;
  demands: number;
  tasks: number;
  mergeRequests: number;
}

interface RecentTask {
  id: string;
  demand: string;
  repo: string;
  status: StatusKind;
  statusLabel: string;
  mrUrl?: string;
}

interface RecentRecord {
  id: string;
  time: string;
  chat: string;
  sender: string;
  anchor: string;
}

interface MetricDefinition {
  key: string;
  title: string;
  valuePaths: string[];
  deltaPaths: string[];
  fallback?: number;
  highlight?: 'success' | 'danger' | 'warning';
}

const metricDefinitions: MetricDefinition[] = [
  {
    key: 'records',
    title: '记录总数',
    valuePaths: ['records_total', 'recordsTotal'],
    deltaPaths: [],
  },
  {
    key: 'demands',
    title: '需求总数',
    valuePaths: ['demands_total', 'demandsTotal'],
    deltaPaths: [],
  },
  {
    key: 'mergeRequests',
    title: 'MR 总数',
    valuePaths: ['mr_total', 'mrTotal'],
    deltaPaths: [],
  },
  {
    key: 'tasks',
    title: '开发任务总数',
    valuePaths: ['dev_tasks_total', 'devTasksTotal'],
    deltaPaths: [],
  },
  {
    key: 'success',
    title: '执行成功数',
    valuePaths: ['dev_task_success.succeeded', 'devTaskSuccess.succeeded'],
    deltaPaths: [],
    highlight: 'success',
  },
  {
    key: 'failed',
    title: '执行失败数',
    valuePaths: ['dev_task_success.failed', 'devTaskSuccess.failed'],
    deltaPaths: [],
    highlight: 'danger',
  },
];

const demandStatusLabels: Record<string, string> = {
  demand: 'demand 已分发',
  card_sent: 'card_sent 已推送',
  dev_confirmed: 'dev_confirmed 已接单',
  cancelled: 'cancelled 已取消',
  dispatch: 'dispatch 派发中',
  ignored: 'ignored 已忽略',
  failed: 'failed 解析失败',
};

const chartClassNames = {
  primary: 'text-primary',
  success: 'text-success',
  warning: 'text-warning',
  danger: 'text-danger',
  info: 'text-info',
  muted: 'text-muted-foreground',
  border: 'border-border',
  foreground: 'text-foreground',
};

function isRecord(value: unknown): value is UnknownRecord {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function getPath(source: unknown, path: string) {
  return path.split('.').reduce<unknown>((current, segment) => {
    if (!isRecord(current)) {
      return undefined;
    }
    return current[segment];
  }, source);
}

function readNumber(value: unknown): number | undefined {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : undefined;
  }
  return undefined;
}

function firstNumber(source: unknown, paths: string[]) {
  for (const path of paths) {
    const value = readNumber(getPath(source, path));
    if (value !== undefined) {
      return value;
    }
  }
  return undefined;
}

function firstString(source: unknown, paths: string[]) {
  for (const path of paths) {
    const value = getPath(source, path);
    if (typeof value === 'string' && value.trim()) {
      return value;
    }
    if (typeof value === 'number') {
      return String(value);
    }
  }
  return undefined;
}

function firstArray(source: unknown, paths: string[]) {
  for (const path of paths) {
    const value = path ? getPath(source, path) : source;
    if (Array.isArray(value)) {
      return value;
    }
  }
  return [];
}

function normalizeDate(value: unknown) {
  if (typeof value !== 'string') {
    return '--';
  }
  return value.includes('T') ? value.slice(0, 10) : value;
}

function formatNumber(value?: number) {
  if (value === undefined) {
    return '--';
  }
  return new Intl.NumberFormat('zh-CN').format(value);
}

function formatDelta(value?: number) {
  if (value === undefined) {
    return '实时聚合';
  }
  if (value === 0) {
    return '较昨日持平';
  }
  return `${value > 0 ? '+' : ''}${formatNumber(value)} 较昨日`;
}

function getDeltaTrend(value?: number): 'up' | 'down' | 'flat' {
  if (value === undefined || value === 0) {
    return 'flat';
  }
  return value > 0 ? 'up' : 'down';
}

function mapStatusKind(value?: string): StatusKind {
  const normalized = value?.toLowerCase() ?? '';
  if (['success', 'succeeded', 'done', 'completed', 'merged'].includes(normalized)) {
    return 'success';
  }
  if (['failed', 'failure', 'error', 'danger'].includes(normalized)) {
    return 'failed';
  }
  if (['running', 'processing', 'dispatching'].includes(normalized)) {
    return 'running';
  }
  if (normalized === 'timeout') {
    return 'timeout';
  }
  return 'pending';
}

function mapStatusLabel(status: StatusKind, raw?: string) {
  if (raw) {
    return raw;
  }
  const labels: Record<StatusKind, string> = {
    success: '成功',
    failed: '失败',
    running: '运行中',
    pending: '等待中',
    timeout: '超时',
  };
  return labels[status];
}

function normalizeDemandStatus(overview: unknown): DemandStatusItem[] {
  const source =
    getPath(overview, 'demand_status_counts') ??
    getPath(overview, 'demandStatusCounts') ??
    {};

  if (!isRecord(source)) {
    return [];
  }

  return Object.entries(source)
    .map(([key, value]) => ({
      key,
      label: demandStatusLabels[key] ?? key,
      value: readNumber(value) ?? 0,
    }))
    .filter((item) => item.value > 0);
}

function normalizeTimeSeries(timeseries: unknown): TimeSeriesItem[] {
  return firstArray(timeseries, ['series', '', 'items', 'data']).map((item) => ({
    date: normalizeDate(firstString(item, ['date', 'day', 'time']) ?? ''),
    records: firstNumber(item, ['records', 'recordCount', 'record_count']) ?? 0,
    demands: firstNumber(item, ['demands', 'demandCount', 'demand_count']) ?? 0,
    tasks: firstNumber(item, ['dev_tasks', 'tasks', 'devTasks', 'task_count']) ?? 0,
    mergeRequests:
      firstNumber(item, ['mrs', 'mergeRequests', 'mrCount', 'mr_count']) ?? 0,
  }));
}

function normalizeRecentTasks(payload: unknown): RecentTask[] {
  return firstArray(payload, ['items', '', 'data'])
    .slice(0, 8)
    .map((item, index) => {
      const rawStatus = firstString(item, ['status', 'state']);
      const status = mapStatusKind(rawStatus);
      return {
        id: firstString(item, ['task_id', 'taskId', 'id']) ?? `task-${index + 1}`,
        demand:
          firstString(item, ['demand_id', 'demandId', 'summary', 'title']) ?? '--',
        repo: firstString(item, ['repo_id', 'repoId', 'repo', 'repository']) ?? '--',
        status,
        statusLabel: mapStatusLabel(status, rawStatus),
        mrUrl: firstString(item, ['mr_url', 'mrUrl', 'mergeRequestUrl', 'url']),
      };
    });
}

function normalizeRecentRecords(payload: unknown): RecentRecord[] {
  return firstArray(payload, ['items', '', 'data'])
    .slice(0, 8)
    .map((item, index) => ({
      id: firstString(item, ['record_id', 'recordId', 'id']) ?? `record-${index + 1}`,
      time: firstString(item, ['anchor_time', 'anchorTime', 'created_at', 'createdAt']) ?? '--',
      chat: firstString(item, ['chat_name', 'chatName', 'chat_id', 'chatId']) ?? '--',
      sender: firstString(item, ['sender_name', 'senderName', 'sender']) ?? '--',
      anchor: firstString(item, ['anchor_summary', 'summary', 'text']) ?? '--',
    }));
}

function readSemanticColor(className: string) {
  const element = document.createElement('span');
  element.className = className;
  element.style.position = 'absolute';
  element.style.pointerEvents = 'none';
  element.style.opacity = '0';
  document.body.appendChild(element);
  const styles = getComputedStyle(element);
  const color = className.startsWith('border-') ? styles.borderColor : styles.color;
  document.body.removeChild(element);
  return color;
}

function useChartColors() {
  const { theme } = useTheme();
  const [colors, setColors] = useState<Record<
    keyof typeof chartClassNames,
    string
  > | null>(null);

  useEffect(() => {
    setColors(
      Object.fromEntries(
        Object.entries(chartClassNames).map(([key, className]) => [
          key,
          readSemanticColor(className),
        ]),
      ) as Record<keyof typeof chartClassNames, string>,
    );
  }, [theme]);

  return colors;
}

function EChart({ option }: { option: EChartsOption }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<ReturnType<typeof echarts.init> | null>(null);

  useEffect(() => {
    if (!containerRef.current) {
      return undefined;
    }

    chartRef.current = echarts.init(containerRef.current);
    const resizeObserver = new ResizeObserver(() => chartRef.current?.resize());
    resizeObserver.observe(containerRef.current);

    return () => {
      resizeObserver.disconnect();
      chartRef.current?.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    chartRef.current?.setOption(option, true);
  }, [option]);

  return <div ref={containerRef} className="h-[240px] w-full" />;
}

function ChartContent({ empty, option }: { empty: boolean; option: EChartsOption }) {
  if (empty) {
    return (
      <EmptyState
        compact
        title="暂无图表数据"
        description="当前时间范围内暂无可展示的数据"
        tone="muted"
      />
    );
  }

  return <EChart option={option} />;
}

function ErrorPanel({ onRetry }: { onRetry: () => void }) {
  return (
    <Card className="border-danger bg-danger-soft/50">
      <CardContent className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-start gap-3">
          <AlertTriangle className="mt-0.5 h-5 w-5 text-danger" />
          <div>
            <p className="text-sm font-semibold text-danger">总览数据加载失败</p>
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

function OverviewSkeleton() {
  return (
    <section className="flex flex-col gap-4">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-6">
        {Array.from({ length: 6 }).map((_, index) => (
          <Skeleton key={index} className="h-[120px] rounded-xl" />
        ))}
      </div>
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Skeleton className="h-[300px] rounded-xl" />
        <Skeleton className="h-[300px] rounded-xl" />
      </div>
      <Skeleton className="h-[280px] rounded-xl" />
    </section>
  );
}

export default function OverviewPage() {
  const chartColors = useChartColors();
  const overviewQuery = useQuery({
    queryKey: ['overview'],
    queryFn: () => getJson<unknown>('/overview'),
    refetchInterval: OVERVIEW_REFETCH_INTERVAL_MS,
  });
  const timeseriesQuery = useQuery({
    queryKey: ['timeseries'],
    queryFn: () => getJson<unknown>('/timeseries'),
    refetchInterval: OVERVIEW_REFETCH_INTERVAL_MS,
  });
  const recentTasksQuery = useQuery({
    queryKey: ['overview', 'recent-tasks'],
    queryFn: () => getJson<unknown>('/dev-tasks', { page_size: 8 }),
    refetchInterval: OVERVIEW_REFETCH_INTERVAL_MS,
  });
  const recentRecordsQuery = useQuery({
    queryKey: ['overview', 'recent-records'],
    queryFn: () => getJson<unknown>('/records', { page_size: 8 }),
    refetchInterval: OVERVIEW_REFETCH_INTERVAL_MS,
  });

  const overview = overviewQuery.data;
  const demandStatus = useMemo(() => normalizeDemandStatus(overview), [overview]);
  const timeSeries = useMemo(
    () => normalizeTimeSeries(timeseriesQuery.data),
    [timeseriesQuery.data],
  );
  const recentTasks = useMemo(
    () => normalizeRecentTasks(recentTasksQuery.data),
    [recentTasksQuery.data],
  );
  const recentRecords = useMemo(
    () => normalizeRecentRecords(recentRecordsQuery.data),
    [recentRecordsQuery.data],
  );
  const updatedAt =
    firstString(overview, ['updatedAt', 'updated_at', 'checkedAt', 'generatedAt']) ??
    firstString(timeseriesQuery.data, [
      'updatedAt',
      'updated_at',
      'checkedAt',
      'generatedAt',
    ]);
  const hasError = overviewQuery.isError || timeseriesQuery.isError;
  const isLoading = overviewQuery.isLoading || timeseriesQuery.isLoading || !chartColors;

  const metrics = useMemo(
    () =>
      metricDefinitions.map((definition) => {
        const value = firstNumber(overview, definition.valuePaths) ?? definition.fallback;
        const delta = firstNumber(overview, definition.deltaPaths);
        return { ...definition, value, delta };
      }),
    [overview],
  );

  const pieOption = useMemo<EChartsOption>(() => {
    if (!chartColors) {
      return {};
    }
    return {
      color: [
        chartColors.primary,
        chartColors.success,
        chartColors.info,
        chartColors.danger,
      ],
      tooltip: { trigger: 'item' },
      legend: {
        orient: 'vertical',
        right: 8,
        top: 'middle',
        textStyle: { color: chartColors.muted },
      },
      series: [
        {
          name: '需求状态',
          type: 'pie',
          radius: ['46%', '72%'],
          center: ['34%', '50%'],
          avoidLabelOverlap: true,
          label: { formatter: '{b}\n{d}%', color: chartColors.foreground },
          data: demandStatus.map((item) => ({ name: item.label, value: item.value })),
        },
      ],
    };
  }, [chartColors, demandStatus]);

  const workloadOption = useMemo<EChartsOption>(() => {
    if (!chartColors) {
      return {};
    }
    const dates = timeSeries.map((item) => item.date);
    return {
      color: [
        chartColors.primary,
        chartColors.success,
        chartColors.info,
        chartColors.warning,
        chartColors.muted,
      ],
      tooltip: { trigger: 'axis' },
      legend: {
        top: 0,
        textStyle: { color: chartColors.muted },
      },
      grid: { top: 44, right: 16, bottom: 28, left: 36 },
      xAxis: {
        type: 'category',
        data: dates,
        axisLine: { lineStyle: { color: chartColors.border } },
        axisLabel: { color: chartColors.muted },
      },
      yAxis: {
        type: 'value',
        splitLine: { lineStyle: { color: chartColors.border } },
        axisLabel: { color: chartColors.muted },
      },
      series: [
        {
          name: '记录',
          type: 'bar',
          stack: 'workload',
          data: timeSeries.map((item) => item.records),
        },
        {
          name: '需求',
          type: 'bar',
          stack: 'workload',
          data: timeSeries.map((item) => item.demands),
        },
        {
          name: '任务',
          type: 'bar',
          stack: 'workload',
          data: timeSeries.map((item) => item.tasks),
        },
        {
          name: 'MR',
          type: 'bar',
          stack: 'workload',
          data: timeSeries.map((item) => item.mergeRequests),
        },
      ],
    };
  }, [chartColors, timeSeries]);

  const trendOption = useMemo<EChartsOption>(() => {
    if (!chartColors) {
      return {};
    }
    const dates = timeSeries.map((item) => item.date);
    return {
      color: [chartColors.primary, chartColors.success, chartColors.info],
      tooltip: { trigger: 'axis' },
      legend: {
        top: 0,
        textStyle: { color: chartColors.muted },
      },
      grid: { top: 44, right: 20, bottom: 28, left: 36 },
      xAxis: {
        type: 'category',
        boundaryGap: false,
        data: dates,
        axisLine: { lineStyle: { color: chartColors.border } },
        axisLabel: { color: chartColors.muted },
      },
      yAxis: {
        type: 'value',
        splitLine: { lineStyle: { color: chartColors.border } },
        axisLabel: { color: chartColors.muted },
      },
      series: [
        {
          name: '记录数',
          type: 'line',
          smooth: true,
          data: timeSeries.map((item) => item.records),
        },
        {
          name: '需求数',
          type: 'line',
          smooth: true,
          data: timeSeries.map((item) => item.demands),
        },
        {
          name: '任务数',
          type: 'line',
          smooth: true,
          data: timeSeries.map((item) => item.tasks),
        },
      ],
    };
  }, [chartColors, timeSeries]);

  const taskColumns = useMemo<ColumnDef<RecentTask>[]>(
    () => [
      {
        accessorKey: 'id',
        header: '任务 ID',
        cell: ({ row }) => <span className="font-mono text-code">{row.original.id}</span>,
      },
      { accessorKey: 'demand', header: '需求摘要' },
      { accessorKey: 'repo', header: '仓库' },
      {
        accessorKey: 'statusLabel',
        header: '状态',
        cell: ({ row }) => (
          <StatusBadge status={row.original.status} label={row.original.statusLabel} />
        ),
      },
      {
        accessorKey: 'mrUrl',
        header: 'MR',
        cell: ({ row }) =>
          row.original.mrUrl ? (
            <a
              className="inline-flex items-center gap-1 text-primary hover:underline"
              href={row.original.mrUrl}
              target="_blank"
              rel="noreferrer"
            >
              打开
              <ExternalLink className="h-3.5 w-3.5" />
            </a>
          ) : (
            <span className="text-muted-foreground">--</span>
          ),
      },
    ],
    [],
  );

  const recordColumns = useMemo<ColumnDef<RecentRecord>[]>(
    () => [
      { accessorKey: 'time', header: '时间' },
      { accessorKey: 'chat', header: '会话' },
      { accessorKey: 'sender', header: '发送人' },
      {
        accessorKey: 'anchor',
        header: '锚点文本',
        cell: ({ row }) => <span className="line-clamp-2">{row.original.anchor}</span>,
      },
    ],
    [],
  );

  if (isLoading) {
    return <OverviewSkeleton />;
  }

  return (
    <section className="flex flex-col gap-4">
      {updatedAt ? (
        <div className="flex flex-wrap items-center justify-end gap-2">
          <Badge variant="secondary" className="font-normal">
            最近更新：{updatedAt}
          </Badge>
        </div>
      ) : null}

      {hasError ? (
        <ErrorPanel
          onRetry={() => {
            void overviewQuery.refetch();
            void timeseriesQuery.refetch();
          }}
        />
      ) : null}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-6">
        {metrics.map((metric) => (
          <MetricCard
            key={metric.key}
            title={metric.title}
            value={
              <span
                className={
                  metric.highlight === 'success'
                    ? 'text-success'
                    : metric.highlight === 'danger'
                      ? 'text-danger'
                      : metric.highlight === 'warning'
                        ? 'text-warning'
                        : undefined
                }
              >
                {formatNumber(metric.value)}
              </span>
            }
            delta={formatDelta(metric.delta)}
            trend={getDeltaTrend(metric.delta)}
          />
        ))}
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <ChartCard
          title="需求状态分布"
          actions={
            <span className="text-caption text-muted-foreground">当前最终状态</span>
          }
        >
          <ChartContent empty={demandStatus.length === 0} option={pieOption} />
        </ChartCard>
        <ChartCard
          title="每日工作量"
          actions={
            <span className="text-caption text-muted-foreground">
              记录 / 需求 / 任务 / MR
            </span>
          }
        >
          <ChartContent empty={timeSeries.length === 0} option={workloadOption} />
        </ChartCard>
      </div>

      <ChartCard
        title="趋势折线"
        actions={
          <span className="text-caption text-muted-foreground">
            记录数 / 需求数 / 任务数
          </span>
        }
      >
        <ChartContent empty={timeSeries.length === 0} option={trendOption} />
      </ChartCard>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <DataTable
          columns={taskColumns}
          data={recentTasks}
          title="最近任务"
          description="task ID、关联需求、仓库、状态与 MR 链接"
          emptyTitle="暂无最近任务"
          emptyDescription="暂无开发任务数据"
          skeletonRows={4}
        />
        <DataTable
          columns={recordColumns}
          data={recentRecords}
          title="最近记录"
          description="时间、会话、发送人和锚点摘要"
          emptyTitle="暂无最近记录"
          emptyDescription="暂无记录数据"
          skeletonRows={4}
        />
      </div>
    </section>
  );
}
