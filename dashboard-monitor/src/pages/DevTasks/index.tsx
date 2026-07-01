import { useQuery } from '@tanstack/react-query';
import type { ColumnDef } from '@tanstack/react-table';
import { AlertTriangle, ExternalLink, GitCommit, SquareCheckBig } from 'lucide-react';
import { useMemo, useState } from 'react';
import { useAlert } from '@/components/Alert';
import { ChartCard } from '@/components/ChartCard';
import { DataTable } from '@/components/DataTable';
import { DatePicker } from '@/components/DatePicker';
import { DetailField, DetailGrid, DetailStack } from '@/components/DetailPanel';
import { Drawer } from '@/components/Drawer';
import { EmptyState } from '@/components/EmptyState';
import { FilterBar, FilterField } from '@/components/FilterBar';
import { JsonViewer } from '@/components/JsonViewer';
import { MetricCard } from '@/components/MetricCard';
import { Select, type SelectOption } from '@/components/Select';
import { StatusBadge, type StatusKind } from '@/components/StatusBadge';
import { Timeline, type TimelineItem } from '@/components/Timeline';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { getJson } from '@/services';
import type { ApiQueryParams } from '@/types';

type UnknownRecord = Record<string, unknown>;

interface DevTaskFilters {
  status: string;
  repo: string;
  branch: string;
  executorTaskId: string;
  hasMr: string;
  dateStart: string;
  dateEnd: string;
}

interface DevTaskListItem {
  taskId: string;
  executorTaskId?: string;
  demandId?: string;
  cardId?: string;
  repoId?: string;
  branch?: string;
  status?: string;
  attempts?: number;
  mrUrl?: string;
  commitSha?: string;
  error?: string;
  updatedAt?: string;
  raw: unknown;
}

const DEFAULT_FILTERS: DevTaskFilters = {
  status: '',
  repo: '',
  branch: '',
  executorTaskId: '',
  hasMr: '',
  dateStart: '',
  dateEnd: '',
};

const inputClassName =
  'h-9 w-full rounded-md border border-input bg-background px-3 text-sm text-foreground shadow-sm outline-none transition-colors placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring';

// 状态/MR 下拉选项：label 用纯中文，value 保持原始枚举与本地过滤逻辑一致。
const STATUS_OPTIONS: SelectOption[] = [
  { value: '', label: '全部状态' },
  { value: 'pending', label: '待处理' },
  { value: 'running', label: '执行中' },
  { value: 'succeeded', label: '成功' },
  { value: 'failed', label: '失败' },
  { value: 'timeout', label: '超时' },
];

const HAS_MR_OPTIONS: SelectOption[] = [
  { value: '', label: 'MR 不限' },
  { value: 'true', label: '有 MR' },
  { value: 'false', label: '无 MR' },
];

function isRecord(value: unknown): value is UnknownRecord {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function getNestedRecord(value: unknown, keys: string[]) {
  if (!isRecord(value)) return undefined;
  for (const key of keys) {
    const item = value[key];
    if (isRecord(item)) return item;
  }
  return undefined;
}

function getString(record: UnknownRecord, keys: string[]) {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === 'string' && value.trim()) return value;
    if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  }
  return undefined;
}

function getNumber(record: UnknownRecord, keys: string[]) {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === 'number' && Number.isFinite(value)) return value;
    if (typeof value === 'string' && value.trim() && Number.isFinite(Number(value))) {
      return Number(value);
    }
  }
  return undefined;
}

function getItems(response: unknown): unknown[] {
  if (Array.isArray(response)) return response;
  if (!isRecord(response)) return [];
  const data = response.data;
  if (Array.isArray(response.items)) return response.items;
  if (Array.isArray(response.list)) return response.list;
  if (Array.isArray(data)) return data;
  if (isRecord(data)) {
    if (Array.isArray(data.items)) return data.items;
    if (Array.isArray(data.list)) return data.list;
  }
  return [];
}

function normalizeDetail(response: unknown) {
  return getNestedRecord(response, ['item', 'data', 'detail', 'task', 'devTask']) ?? response;
}

function normalizeDevTask(value: unknown): DevTaskListItem | null {
  if (!isRecord(value)) return null;
  const taskId = getString(value, ['taskId', 'task_id', 'id']);
  if (!taskId) return null;
  return {
    taskId,
    executorTaskId: getString(value, ['executorTaskId', 'executor_task_id']),
    demandId: getString(value, ['demandId', 'demand_id']),
    cardId: getString(value, ['cardInstanceId', 'card_instance_id', 'cardId', 'card_id']),
    repoId: getString(value, ['repoId', 'repo_id', 'repo', 'repository']),
    branch: getString(value, ['workBranch', 'work_branch', 'baseBranch', 'base_branch', 'branch']),
    status: getString(value, ['status']),
    attempts: getNumber(value, ['attempts', 'attempt']),
    mrUrl: getString(value, ['mrUrl', 'mr_url', 'mergeRequestUrl', 'merge_request_url']),
    commitSha: getString(value, ['commitSha', 'commit_sha', 'commit']),
    error: getString(value, ['error', 'errorMessage', 'error_message', 'failureReason', 'failure_reason']),
    updatedAt: getString(value, ['updatedAt', 'updated_at', 'createdAt', 'created_at']),
    raw: value,
  };
}

function toQuery(_filters: DevTaskFilters): ApiQueryParams {
  // 后端 /api/dev-tasks 仅支持 page / page_size 分页，其余条件在前端本地过滤。
  return {
    page_size: 200,
  };
}

function applyLocalFilters(items: DevTaskListItem[], filters: DevTaskFilters): DevTaskListItem[] {
  // 后端不支持按条件过滤，统一在前端本地过滤，保证筛选 UI 生效。
  const repo = filters.repo.trim().toLowerCase();
  const branch = filters.branch.trim().toLowerCase();
  const executorTaskId = filters.executorTaskId.trim().toLowerCase();
  // 日期范围按 updatedAt 落在 [dateStart 00:00, dateEnd 23:59:59] 过滤。
  const startTime = filters.dateStart ? new Date(`${filters.dateStart}T00:00:00`).getTime() : undefined;
  const endTime = filters.dateEnd ? new Date(`${filters.dateEnd}T23:59:59.999`).getTime() : undefined;
  return items.filter((item) => {
    if (filters.status && (item.status ?? '').toLowerCase() !== filters.status.toLowerCase()) {
      return false;
    }
    if (repo && !(item.repoId ?? '').toLowerCase().includes(repo)) return false;
    if (branch && !(item.branch ?? '').toLowerCase().includes(branch)) return false;
    if (executorTaskId && !(item.executorTaskId ?? '').toLowerCase().includes(executorTaskId)) {
      return false;
    }
    if (filters.hasMr === 'true' && !item.mrUrl) return false;
    if (filters.hasMr === 'false' && item.mrUrl) return false;
    if (startTime !== undefined || endTime !== undefined) {
      const updatedTime = item.updatedAt ? new Date(item.updatedAt).getTime() : Number.NaN;
      if (Number.isNaN(updatedTime)) return false;
      if (startTime !== undefined && updatedTime < startTime) return false;
      if (endTime !== undefined && updatedTime > endTime) return false;
    }
    return true;
  });
}

function getDevTaskList(filters: DevTaskFilters) {
  return getJson<unknown>('/dev-tasks', toQuery(filters)).then((response) =>
    getItems(response).map(normalizeDevTask).filter((item): item is DevTaskListItem => Boolean(item)),
  );
}

function getDevTaskDetail(taskId: string) {
  return getJson<unknown>(`/dev-tasks/${encodeURIComponent(taskId)}`).then(normalizeDetail);
}

function getActiveFilterCount(filters: DevTaskFilters) {
  return Object.values(filters).filter(Boolean).length;
}

function formatText(value?: string) {
  return value?.trim() ? value : '--';
}

function formatDateTime(value?: string) {
  if (!value) return '--';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN', { hour12: false });
}

function shortSha(value?: string) {
  if (!value) return '--';
  return value.length > 10 ? value.slice(0, 10) : value;
}

function getStatusKind(status?: string): StatusKind {
  const normalized = status?.toLowerCase() ?? '';
  if (['success', 'succeeded', 'done', 'completed', 'merged'].some((key) => normalized.includes(key))) {
    return 'success';
  }
  if (['failed', 'error', 'reject'].some((key) => normalized.includes(key))) {
    return 'failed';
  }
  if (['running', 'processing', 'dispatch'].some((key) => normalized.includes(key))) {
    return 'running';
  }
  if (['timeout'].some((key) => normalized.includes(key))) {
    return 'timeout';
  }
  return 'pending';
}

function getTimelineStatus(status?: string): TimelineItem['status'] {
  const kind = getStatusKind(status);
  if (kind === 'success') return 'success';
  if (kind === 'failed' || kind === 'timeout') return 'failed';
  if (kind === 'running') return 'running';
  return 'pending';
}

function ChartBars({ data }: { data: Array<{ label: string; value: number }> }) {
  const max = Math.max(...data.map((item) => item.value), 1);
  if (!data.length) {
    return <EmptyState className="min-h-[180px]" title="暂无分布数据" description="当前筛选条件下没有可聚合的数据" />;
  }
  return (
    <div className="flex flex-col gap-3">
      {data.map((item) => (
        <div key={item.label} className="grid grid-cols-[96px_minmax(0,1fr)_40px] items-center gap-3">
          <span className="truncate text-caption text-muted-foreground" title={item.label}>
            {item.label}
          </span>
          <div className="h-2.5 overflow-hidden rounded-full bg-muted">
            <div
              className="h-full rounded-full bg-primary"
              style={{ width: `${Math.max((item.value / max) * 100, 6)}%` }}
            />
          </div>
          <span className="text-right text-caption font-medium text-foreground">{item.value}</span>
        </div>
      ))}
    </div>
  );
}

function DistributionSection({
  title,
  data,
}: {
  title: string;
  data: Array<{ label: string; value: number }>;
}) {
  return (
    <section className="flex flex-col gap-3">
      <div>
        <h3 className="text-sm font-medium text-foreground">{title}</h3>
      </div>
      <ChartBars data={data} />
    </section>
  );
}

function countBy(items: DevTaskListItem[], getKey: (item: DevTaskListItem) => string | undefined) {
  const counts = new Map<string, number>();
  items.forEach((item) => {
    const key = getKey(item) || '未填写';
    counts.set(key, (counts.get(key) ?? 0) + 1);
  });
  return Array.from(counts, ([label, value]) => ({ label, value })).sort((a, b) => b.value - a.value);
}

function getFailureReason(error?: string) {
  if (!error) return undefined;
  const firstLine = error.split('\n').find(Boolean) ?? error;
  return firstLine.length > 42 ? `${firstLine.slice(0, 42)}...` : firstLine;
}

function getTimelineArray(detail: unknown) {
  if (!isRecord(detail)) return [];
  const keys = ['timeline', 'events', 'steps', 'logs', 'executions'];
  for (const key of keys) {
    const value = detail[key];
    if (Array.isArray(value)) return value;
  }
  return [];
}

function buildTimeline(detail: unknown, fallback?: DevTaskListItem): TimelineItem[] {
  const rawItems = getTimelineArray(detail);
  if (rawItems.length) {
    return rawItems.map((item, index) => {
      if (!isRecord(item)) {
        return {
          id: `${index}`,
          title: String(item),
          status: 'pending',
        };
      }
      const title = getString(item, ['title', 'name', 'stage', 'step', 'event', 'message']) ?? `步骤 ${index + 1}`;
      return {
        id: getString(item, ['id', 'key']) ?? `${index}-${title}`,
        time: formatDateTime(getString(item, ['time', 'timestamp', 'createdAt', 'created_at', 'updatedAt', 'updated_at'])),
        title,
        description: getString(item, ['description', 'detail', 'message', 'error']),
        meta: getString(item, ['status', 'state']),
        status: getTimelineStatus(getString(item, ['status', 'state'])),
      };
    });
  }

  if (!fallback) return [];
  return [
    {
      id: `${fallback.taskId}-current`,
      time: formatDateTime(fallback.updatedAt),
      title: `当前状态：${formatText(fallback.status)}`,
      description: fallback.error,
      meta: fallback.attempts !== undefined ? `尝试 ${fallback.attempts} 次` : undefined,
      status: getTimelineStatus(fallback.status),
    },
  ];
}

function DetailContent({ detail, fallback }: { detail?: unknown; fallback?: DevTaskListItem }) {
  const detailTask = normalizeDevTask(detail) ?? fallback;
  const record = isRecord(detail) ? detail : isRecord(fallback?.raw) ? fallback.raw : {};
  const demandId = detailTask?.demandId ?? getString(record, ['demandId', 'demand_id']);
  const cardId =
    detailTask?.cardId ?? getString(record, ['cardInstanceId', 'card_instance_id', 'cardId', 'card_id']);
  const timeline = buildTimeline(detail, detailTask);

  return (
    <DetailStack>
      <DetailGrid>
        <DetailField label="执行器任务 ID" value={detailTask?.executorTaskId} />
        <DetailField label="关联需求" value={demandId} />
        <DetailField label="关联卡片" value={cardId} />
        <DetailField label="仓库" value={detailTask?.repoId} />
        <DetailField label="分支" value={detailTask?.branch} />
        <DetailField label="提交" value={detailTask?.commitSha} />
      </DetailGrid>
      {detailTask?.error ? (
        <Card className="border-danger bg-danger-soft/40 p-4">
          <div className="mb-2 flex items-center gap-2 text-sm font-medium text-danger">
            <AlertTriangle className="h-4 w-4" />
            错误详情
          </div>
          <pre className="whitespace-pre-wrap break-words font-mono text-code text-danger">
            {detailTask.error}
          </pre>
        </Card>
      ) : null}
      <Card className="p-4">
        <div className="mb-3 flex items-center gap-2 text-sm font-medium text-foreground">
          <SquareCheckBig className="h-4 w-4 text-primary" />
          执行时间线
        </div>
        <Timeline items={timeline} compact />
      </Card>
      <JsonViewer data={detail ?? fallback?.raw} title="原始数据" maxHeight={420} />
    </DetailStack>
  );
}

export default function DevTasksPage() {
  const alert = useAlert();
  const [draftFilters, setDraftFilters] = useState(DEFAULT_FILTERS);
  const [filters, setFilters] = useState(DEFAULT_FILTERS);
  const [selectedTask, setSelectedTask] = useState<DevTaskListItem | undefined>();
  const taskQuery = useQuery({
    queryKey: ['dev-tasks', filters],
    queryFn: () => getDevTaskList(filters),
  });
  const detailQuery = useQuery({
    queryKey: ['dev-tasks', selectedTask?.taskId],
    queryFn: () => getDevTaskDetail(selectedTask?.taskId ?? ''),
    enabled: Boolean(selectedTask?.taskId),
  });
  const tasks = useMemo(
    () => applyLocalFilters(taskQuery.data ?? [], filters),
    [taskQuery.data, filters],
  );
  const columns = useMemo<ColumnDef<DevTaskListItem>[]>(
    () => [
      {
        accessorKey: 'taskId',
        header: '任务 ID',
        cell: ({ row }) => (
          <span className="font-mono text-code text-primary">{row.original.taskId}</span>
        ),
      },
      {
        accessorKey: 'executorTaskId',
        header: '执行器',
        cell: ({ row }) => (
          <span className="font-mono text-code">{formatText(row.original.executorTaskId)}</span>
        ),
      },
      {
        accessorKey: 'repoId',
        header: '仓库',
        cell: ({ row }) => <span className="font-mono text-code">{formatText(row.original.repoId)}</span>,
      },
      {
        accessorKey: 'branch',
        header: '分支',
        cell: ({ row }) => <span className="font-mono text-code">{formatText(row.original.branch)}</span>,
      },
      {
        accessorKey: 'status',
        header: '状态',
        cell: ({ row }) => (
          <StatusBadge
            label={formatText(row.original.status)}
            status={getStatusKind(row.original.status)}
          />
        ),
      },
      {
        accessorKey: 'attempts',
        header: '尝试',
        cell: ({ row }) => <span>{row.original.attempts ?? '--'}</span>,
      },
      {
        accessorKey: 'mrUrl',
        header: 'MR',
        enableSorting: false,
        cell: ({ row }) =>
          row.original.mrUrl ? (
            <Button asChild size="sm" variant="outline" onClick={(event) => event.stopPropagation()}>
              <a href={row.original.mrUrl} rel="noreferrer" target="_blank">
                打开
                <ExternalLink className="h-3.5 w-3.5" />
              </a>
            </Button>
          ) : (
            <Badge variant="outline">无 MR</Badge>
          ),
      },
      {
        accessorKey: 'commitSha',
        header: '提交',
        cell: ({ row }) => (
          <span className="inline-flex items-center gap-1 font-mono text-code">
            <GitCommit className="h-3.5 w-3.5 text-muted-foreground" />
            {shortSha(row.original.commitSha)}
          </span>
        ),
      },
      {
        accessorKey: 'error',
        header: '错误',
        cell: ({ row }) => (
          <span className="line-clamp-2 min-w-[180px] text-caption text-danger">
            {getFailureReason(row.original.error) ?? '--'}
          </span>
        ),
      },
    ],
    [],
  );
  const statusData = useMemo(() => countBy(tasks, (item) => item.status), [tasks]);
  const attemptsData = useMemo(
    () => countBy(tasks, (item) => (item.attempts === undefined ? undefined : `${item.attempts} 次`)),
    [tasks],
  );
  const failureData = useMemo(
    () => countBy(tasks.filter((item) => Boolean(item.error)), (item) => getFailureReason(item.error)).slice(0, 6),
    [tasks],
  );
  const successCount = tasks.filter((item) => getStatusKind(item.status) === 'success').length;
  const failedCount = tasks.filter((item) => getStatusKind(item.status) === 'failed').length;
  const mrCount = tasks.filter((item) => Boolean(item.mrUrl)).length;
  const activeCount = getActiveFilterCount(filters);
  const hasError = taskQuery.isError;

  // 查询前校验日期范围：开始不能晚于结束，非法时提示且不提交。
  const handleSearch = () => {
    if (
      draftFilters.dateStart &&
      draftFilters.dateEnd &&
      draftFilters.dateStart > draftFilters.dateEnd
    ) {
      alert.warning({ title: '日期范围无效', description: '开始日期不能晚于结束日期' });
      return;
    }
    setFilters(draftFilters);
  };

  return (
    <section className="flex flex-col gap-4">
      {hasError ? (
        <Card className="border-danger bg-danger-soft/50 p-4">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-start gap-3">
              <AlertTriangle className="mt-0.5 h-5 w-5 text-danger" />
              <div>
                <p className="text-sm font-semibold text-danger">开发任务数据加载失败</p>
                <p className="text-caption text-danger">数据服务暂不可用，请稍后重试。</p>
              </div>
            </div>
            <Button variant="outline" size="sm" onClick={() => void taskQuery.refetch()}>
              重试
            </Button>
          </div>
        </Card>
      ) : null}

      <FilterBar
        activeCount={activeCount}
        description="按状态、仓库、分支、执行器任务 ID 与 MR 关联筛选，列表默认读取最近 200 条。"
        loading={taskQuery.isFetching}
        title="任务筛选"
        onReset={() => {
          setDraftFilters(DEFAULT_FILTERS);
          setFilters(DEFAULT_FILTERS);
        }}
        onSearch={handleSearch}
      >
        <FilterField label="任务状态">
          <Select
            aria-label="任务状态"
            options={STATUS_OPTIONS}
            value={draftFilters.status}
            onValueChange={(value) => setDraftFilters((prev) => ({ ...prev, status: value }))}
          />
        </FilterField>
        <FilterField label="仓库">
          <input
            className={inputClassName}
            placeholder="仓库"
            value={draftFilters.repo}
            onChange={(event) => setDraftFilters((prev) => ({ ...prev, repo: event.target.value }))}
          />
        </FilterField>
        <FilterField label="分支">
          <input
            className={inputClassName}
            placeholder="分支"
            value={draftFilters.branch}
            onChange={(event) => setDraftFilters((prev) => ({ ...prev, branch: event.target.value }))}
          />
        </FilterField>
        <FilterField label="执行器任务 ID">
          <input
            className={inputClassName}
            placeholder="执行器任务 ID"
            value={draftFilters.executorTaskId}
            onChange={(event) => setDraftFilters((prev) => ({ ...prev, executorTaskId: event.target.value }))}
          />
        </FilterField>
        <FilterField label="MR 关联">
          <Select
            aria-label="MR 关联"
            options={HAS_MR_OPTIONS}
            value={draftFilters.hasMr}
            onValueChange={(value) => setDraftFilters((prev) => ({ ...prev, hasMr: value }))}
          />
        </FilterField>
        <FilterField label="开始日期">
          <DatePicker
            aria-label="开始日期"
            disableFuture
            value={draftFilters.dateStart}
            max={draftFilters.dateEnd || undefined}
            onChange={(value) => setDraftFilters((prev) => ({ ...prev, dateStart: value }))}
          />
        </FilterField>
        <FilterField label="结束日期">
          <DatePicker
            aria-label="结束日期"
            disableFuture
            value={draftFilters.dateEnd}
            min={draftFilters.dateStart || undefined}
            onChange={(value) => setDraftFilters((prev) => ({ ...prev, dateEnd: value }))}
          />
        </FilterField>
      </FilterBar>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-4">
        <MetricCard title="任务总数" value={taskQuery.isLoading ? <Skeleton className="h-8 w-16" /> : tasks.length} />
        <MetricCard title="成功任务" value={successCount} delta={`${tasks.length ? Math.round((successCount / tasks.length) * 100) : 0}%`} trend="up" />
        <MetricCard title="失败任务" value={failedCount} delta={`${tasks.length ? Math.round((failedCount / tasks.length) * 100) : 0}%`} trend={failedCount ? 'down' : 'flat'} />
        <MetricCard title="已关联 MR" value={mrCount} />
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
        <DataTable
          columns={columns}
          data={tasks}
          description="点击行查看任务详情、关联需求与卡片、错误信息与执行时间线。"
          emptyDescription="当前筛选条件下没有开发任务数据"
          emptyTitle="暂无开发任务"
          loading={taskQuery.isLoading}
          title="开发任务列表"
          onRowClick={setSelectedTask}
        />
        <ChartCard title="任务分布概览">
          <div className="flex flex-col gap-6">
            <DistributionSection title="任务状态分布" data={statusData} />
            <div className="border-t border-border" />
            <DistributionSection title="尝试次数分布" data={attemptsData} />
            <div className="border-t border-border" />
            <DistributionSection title="失败原因排行" data={failureData} />
          </div>
        </ChartCard>
      </div>

      <Drawer
        open={Boolean(selectedTask)}
        size="lg"
        title={selectedTask?.taskId ?? '开发任务详情'}
        description={selectedTask?.executorTaskId}
        headerExtra={
          selectedTask ? (
            <StatusBadge label={formatText(selectedTask.status)} status={getStatusKind(selectedTask.status)} />
          ) : null
        }
        onOpenChange={(open) => {
          if (!open) setSelectedTask(undefined);
        }}
      >
        {detailQuery.isLoading ? (
          <div className="space-y-3">
            <Skeleton className="h-20 w-full" />
            <Skeleton className="h-40 w-full" />
            <Skeleton className="h-64 w-full" />
          </div>
        ) : detailQuery.isError ? (
          <EmptyState
            description="数据服务暂不可用，请稍后重试。"
            title="详情加载失败"
            action={<Button size="sm" onClick={() => void detailQuery.refetch()}>重试</Button>}
          />
        ) : (
          <DetailContent detail={detailQuery.data} fallback={selectedTask} />
        )}
      </Drawer>
    </section>
  );
}
