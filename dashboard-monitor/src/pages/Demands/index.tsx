import { useQuery } from '@tanstack/react-query';
import type { ColumnDef } from '@tanstack/react-table';
import { AlertTriangle, ExternalLink, FileText, GitBranch } from 'lucide-react';
import { useMemo, useState } from 'react';
import type { ReactNode } from 'react';
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
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { getJson } from '@/services';
import type { ApiQueryParams } from '@/types';

type UnknownRecord = Record<string, unknown>;

interface DemandFilters {
  keyword: string;
  status: string;
  dispatchStatus: string;
  repo: string;
  branch: string;
  hasMr: string;
  dateStart: string;
  dateEnd: string;
}

interface DemandListItem {
  demandId: string;
  recordId?: string;
  summary?: string;
  repo?: string;
  branch?: string;
  status?: string;
  dispatchStatus?: string;
  mrUrl?: string;
  updatedAt?: string;
  raw: unknown;
}

const DEFAULT_FILTERS: DemandFilters = {
  keyword: '',
  status: '',
  dispatchStatus: '',
  repo: '',
  branch: '',
  hasMr: '',
  dateStart: '',
  dateEnd: '',
};

const inputClassName =
  'h-9 w-full rounded-md border border-input bg-background px-3 text-sm text-foreground shadow-sm outline-none transition-colors placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring';

// 状态/派发状态/MR 下拉选项：label 用纯中文，value 保持原始枚举与本地过滤逻辑一致。
const STATUS_OPTIONS: SelectOption[] = [
  { value: '', label: '全部状态' },
  { value: 'demand', label: '需求' },
  { value: 'dispatch', label: '派发' },
  { value: 'failed', label: '失败' },
  { value: 'ignored', label: '忽略' },
];

const DISPATCH_STATUS_OPTIONS: SelectOption[] = [
  { value: '', label: '全部派发状态' },
  { value: 'pending', label: '待派发' },
  { value: 'running', label: '派发中' },
  { value: 'success', label: '成功' },
  { value: 'failed', label: '失败' },
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
  return getNestedRecord(response, ['item', 'data', 'detail', 'demand']) ?? response;
}

function normalizeDemand(value: unknown): DemandListItem | null {
  if (!isRecord(value)) return null;
  const demandId = getString(value, ['demandId', 'demand_id', 'id']);
  if (!demandId) return null;
  return {
    demandId,
    recordId: getString(value, ['recordId', 'record_id']),
    summary: getString(value, ['summary', 'title', 'prompt', 'description']),
    repo: getString(value, ['repo', 'repoId', 'repo_id', 'repository']),
    branch: getString(value, ['branch', 'targetBranch', 'target_branch']),
    status: getString(value, ['status', 'demandStatus', 'demand_status']),
    dispatchStatus: getString(value, ['dispatchStatus', 'dispatch_status']),
    mrUrl: getString(value, ['mrUrl', 'mr_url', 'mergeRequestUrl', 'merge_request_url']),
    updatedAt: getString(value, ['updatedAt', 'updated_at', 'createdAt', 'created_at']),
    raw: value,
  };
}

function toQuery(_filters: DemandFilters): ApiQueryParams {
  // 后端 /api/demands 仅支持 page / page_size 分页，其余条件在前端本地过滤。
  return {
    page_size: 200,
  };
}

function getDemandList(filters: DemandFilters) {
  return getJson<unknown>('/demands', toQuery(filters)).then((response) =>
    getItems(response).map(normalizeDemand).filter((item): item is DemandListItem => Boolean(item)),
  );
}

function applyLocalFilters(items: DemandListItem[], filters: DemandFilters): DemandListItem[] {
  // 后端不支持按条件过滤，统一在前端本地过滤，保证筛选 UI 生效。
  const keyword = filters.keyword.trim().toLowerCase();
  const repo = filters.repo.trim().toLowerCase();
  const branch = filters.branch.trim().toLowerCase();
  // 日期范围按 updatedAt 落在 [dateStart 00:00, dateEnd 23:59:59] 过滤。
  const startTime = filters.dateStart ? new Date(`${filters.dateStart}T00:00:00`).getTime() : undefined;
  const endTime = filters.dateEnd ? new Date(`${filters.dateEnd}T23:59:59.999`).getTime() : undefined;
  return items.filter((item) => {
    if (keyword) {
      const haystack = [item.demandId, item.summary, item.recordId]
        .filter(Boolean)
        .join(' ')
        .toLowerCase();
      if (!haystack.includes(keyword)) return false;
    }
    if (repo && !(item.repo ?? '').toLowerCase().includes(repo)) return false;
    if (branch && !(item.branch ?? '').toLowerCase().includes(branch)) return false;
    if (filters.status && (item.status ?? '').toLowerCase() !== filters.status.toLowerCase()) {
      return false;
    }
    if (
      filters.dispatchStatus &&
      (item.dispatchStatus ?? '').toLowerCase() !== filters.dispatchStatus.toLowerCase()
    ) {
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

function getDemandDetail(demandId: string) {
  return getJson<unknown>(`/demands/${encodeURIComponent(demandId)}`).then(normalizeDetail);
}

function getActiveFilterCount(filters: DemandFilters) {
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

function getStatusKind(status?: string): StatusKind {
  const normalized = status?.toLowerCase() ?? '';
  if (['success', 'succeeded', 'done', 'completed', 'merged'].some((key) => normalized.includes(key))) {
    return 'success';
  }
  if (['failed', 'error', 'reject'].some((key) => normalized.includes(key))) {
    return 'failed';
  }
  if (['dispatch', 'running', 'processing'].some((key) => normalized.includes(key))) {
    return 'running';
  }
  if (['ignore', 'timeout', 'skip'].some((key) => normalized.includes(key))) {
    return 'timeout';
  }
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
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <section className="flex flex-col gap-3">
      <div>
        <h3 className="text-sm font-medium text-foreground">{title}</h3>
      </div>
      {children}
    </section>
  );
}

function countBy(items: DemandListItem[], getKey: (item: DemandListItem) => string | undefined) {
  const counts = new Map<string, number>();
  items.forEach((item) => {
    const key = getKey(item) || '未填写';
    counts.set(key, (counts.get(key) ?? 0) + 1);
  });
  return Array.from(counts, ([label, value]) => ({ label, value })).sort((a, b) => b.value - a.value);
}

function extractStringArray(value: unknown) {
  if (Array.isArray(value)) {
    return value.map((item) => (typeof item === 'string' ? item : JSON.stringify(item))).slice(0, 8);
  }
  if (typeof value === 'string' && value.trim()) return [value];
  return [];
}

function DetailContent({ detail, fallback }: { detail?: unknown; fallback?: DemandListItem }) {
  const detailDemand = normalizeDemand(detail) ?? fallback;
  const record = isRecord(detail) ? detail : isRecord(fallback?.raw) ? fallback.raw : {};
  const evidence = extractStringArray(record.evidence_json ?? record.evidence);
  const media = extractStringArray(record.media_json ?? record.media);
  const taskId = getString(record, ['current_task_id', 'currentTaskId', 'taskId', 'task_id']);
  const prompt = getString(record, ['prompt', 'originalPrompt', 'original_prompt']);

  return (
    <DetailStack>
      <DetailGrid>
        <DetailField label="关联记录" value={detailDemand?.recordId} />
        <DetailField label="关联任务" value={taskId} />
        <DetailField label="仓库" value={detailDemand?.repo} />
        <DetailField label="分支" value={detailDemand?.branch} />
      </DetailGrid>
      <Card className="p-4">
        <div className="mb-2 flex items-center gap-2 text-sm font-medium text-foreground">
          <FileText className="h-4 w-4 text-primary" />
          需求摘要
        </div>
        <p className="whitespace-pre-wrap text-sm leading-6 text-muted-foreground">
          {formatText(detailDemand?.summary)}
        </p>
      </Card>
      <Card className="p-4">
        <div className="mb-2 flex items-center gap-2 text-sm font-medium text-foreground">
          <GitBranch className="h-4 w-4 text-primary" />
          Prompt / Evidence / Media
        </div>
        <div className="space-y-3 text-sm text-muted-foreground">
          <p className="whitespace-pre-wrap">{formatText(prompt)}</p>
          <div>
            <p className="mb-1 text-caption text-foreground">Evidence</p>
            {evidence.length ? (
              <ul className="space-y-1">
                {evidence.map((item) => (
                  <li key={item} className="rounded-md bg-muted px-3 py-2 font-mono text-code">
                    {item}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-caption">暂无 evidence</p>
            )}
          </div>
          <div>
            <p className="mb-1 text-caption text-foreground">Media</p>
            {media.length ? (
              <ul className="space-y-1">
                {media.map((item) => (
                  <li key={item} className="rounded-md bg-muted px-3 py-2 font-mono text-code">
                    {item}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-caption">暂无 media</p>
            )}
          </div>
        </div>
      </Card>
      <JsonViewer data={detail ?? fallback?.raw} title="原始数据" maxHeight={420} />
    </DetailStack>
  );
}

export default function DemandsPage() {
  const alert = useAlert();
  const [draftFilters, setDraftFilters] = useState(DEFAULT_FILTERS);
  const [filters, setFilters] = useState(DEFAULT_FILTERS);
  const [selectedDemand, setSelectedDemand] = useState<DemandListItem | undefined>();
  const demandQuery = useQuery({
    queryKey: ['demands', filters],
    queryFn: () => getDemandList(filters),
  });
  const detailQuery = useQuery({
    queryKey: ['demands', selectedDemand?.demandId],
    queryFn: () => getDemandDetail(selectedDemand?.demandId ?? ''),
    enabled: Boolean(selectedDemand?.demandId),
  });
  const demands = useMemo(
    () => applyLocalFilters(demandQuery.data ?? [], filters),
    [demandQuery.data, filters],
  );
  const columns = useMemo<ColumnDef<DemandListItem>[]>(
    () => [
      {
        accessorKey: 'demandId',
        header: '需求 ID',
        cell: ({ row }) => (
          <span className="font-mono text-code text-primary">{row.original.demandId}</span>
        ),
      },
      {
        accessorKey: 'summary',
        header: '摘要',
        cell: ({ row }) => (
          <span className="line-clamp-2 min-w-[220px] text-sm">{formatText(row.original.summary)}</span>
        ),
      },
      {
        accessorKey: 'repo',
        header: '仓库',
        cell: ({ row }) => <span className="font-mono text-code">{formatText(row.original.repo)}</span>,
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
        accessorKey: 'dispatchStatus',
        header: '派发状态',
        cell: ({ row }) => (
          <StatusBadge
            label={formatText(row.original.dispatchStatus)}
            status={getStatusKind(row.original.dispatchStatus)}
          />
        ),
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
        accessorKey: 'updatedAt',
        header: '更新时间',
        cell: ({ row }) => <span className="whitespace-nowrap">{formatDateTime(row.original.updatedAt)}</span>,
      },
    ],
    [],
  );
  const statusData = useMemo(() => countBy(demands, (item) => item.status), [demands]);
  const repoData = useMemo(() => countBy(demands, (item) => item.repo), [demands]);
  const mrCount = demands.filter((item) => Boolean(item.mrUrl)).length;
  const dispatchedCount = demands.filter((item) => {
    const value = item.dispatchStatus?.toLowerCase() ?? '';
    return Boolean(value) && !['pending', 'ignored', 'none'].includes(value);
  }).length;
  const activeCount = getActiveFilterCount(filters);
  const hasError = demandQuery.isError;

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
                <p className="text-sm font-semibold text-danger">需求数据加载失败</p>
                <p className="text-caption text-danger">数据服务暂不可用，请稍后重试。</p>
              </div>
            </div>
            <Button variant="outline" size="sm" onClick={() => void demandQuery.refetch()}>
              重试
            </Button>
          </div>
        </Card>
      ) : null}

      <FilterBar
        activeCount={activeCount}
        description="筛选条件随查询提交生效，列表默认读取最近 200 条。"
        loading={demandQuery.isFetching}
        title="需求筛选"
        onReset={() => {
          setDraftFilters(DEFAULT_FILTERS);
          setFilters(DEFAULT_FILTERS);
        }}
        onSearch={handleSearch}
      >
        <FilterField label="关键词">
          <input
            className={inputClassName}
            placeholder="关键词 / 摘要"
            value={draftFilters.keyword}
            onChange={(event) => setDraftFilters((prev) => ({ ...prev, keyword: event.target.value }))}
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
        <FilterField label="需求状态">
          <Select
            aria-label="需求状态"
            options={STATUS_OPTIONS}
            value={draftFilters.status}
            onValueChange={(value) => setDraftFilters((prev) => ({ ...prev, status: value }))}
          />
        </FilterField>
        <FilterField label="派发状态">
          <Select
            aria-label="派发状态"
            options={DISPATCH_STATUS_OPTIONS}
            value={draftFilters.dispatchStatus}
            onValueChange={(value) => setDraftFilters((prev) => ({ ...prev, dispatchStatus: value }))}
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



      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
        <DataTable
          columns={columns}
          data={demands}
          description="点击行查看需求详情、提示词、证据与媒体信息。"
          emptyDescription="当前筛选条件下没有需求数据"
          emptyTitle="暂无需求"
          loading={demandQuery.isLoading}
          title="需求列表"
          onRowClick={setSelectedDemand}
        />
        <div className="grid grid-cols-1 gap-4 md:grid-cols-4">
          <MetricCard title="需求总数" value={demandQuery.isLoading ? <Skeleton className="h-8 w-16" /> : demands.length} />
          <MetricCard title="已派发" value={dispatchedCount} delta={`${demands.length ? Math.round((dispatchedCount / demands.length) * 100) : 0}%`} trend="up" />
          <MetricCard title="已关联 MR" value={mrCount} delta={`${demands.length ? Math.round((mrCount / demands.length) * 100) : 0}%`} trend="flat" />
          <MetricCard title="涉及仓库" value={new Set(demands.map((item) => item.repo).filter(Boolean)).size} />
        </div>
        <ChartCard title="需求分布概览">
          <div className="flex flex-col gap-6">
            <DistributionSection title="需求状态分布">
              <ChartBars data={statusData} />
            </DistributionSection>
            <div className="border-t border-border" />
            <DistributionSection title="仓库需求分布">
              <ChartBars data={repoData.slice(0, 8)} />
            </DistributionSection>
            <div className="border-t border-border" />
            <DistributionSection title="需求到任务转化">
              <div className="flex flex-col gap-3">
                <div>
                  <div className="mb-1.5 flex items-center justify-between text-caption">
                    <span className="text-muted-foreground">派发转化率</span>
                    <span className="font-medium text-foreground">
                      {demands.length ? Math.round((dispatchedCount / demands.length) * 100) : 0}%
                    </span>
                  </div>
                  <div className="h-3 overflow-hidden rounded-full bg-muted">
                    <div
                      className="h-full rounded-full bg-success"
                      style={{ width: `${demands.length ? (dispatchedCount / demands.length) * 100 : 0}%` }}
                    />
                  </div>
                </div>
                <p className="text-caption text-muted-foreground">
                  以派发状态非空且非 pending/ignored/none 的需求估算转化。
                </p>
              </div>
            </DistributionSection>
          </div>
        </ChartCard>
      </div>


      <Drawer
        open={Boolean(selectedDemand)}
        size="lg"
        title={selectedDemand?.demandId ?? '需求详情'}
        description={selectedDemand?.summary}
        headerExtra={
          selectedDemand ? (
            <StatusBadge label={formatText(selectedDemand.status)} status={getStatusKind(selectedDemand.status)} />
          ) : null
        }
        onOpenChange={(open) => {
          if (!open) setSelectedDemand(undefined);
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
          <DetailContent detail={detailQuery.data} fallback={selectedDemand} />
        )}
      </Drawer>
    </section>
  );
}
