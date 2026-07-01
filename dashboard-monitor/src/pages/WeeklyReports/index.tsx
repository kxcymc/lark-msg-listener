import { useQuery } from '@tanstack/react-query';
import type { ColumnDef } from '@tanstack/react-table';
import { AlertTriangle, ExternalLink, FileText } from 'lucide-react';
import { useMemo, useState } from 'react';
import { useAlert } from '@/components/Alert';
import { DataTable } from '@/components/DataTable';
import { DatePicker } from '@/components/DatePicker';
import { DetailField, DetailGrid, DetailStack } from '@/components/DetailPanel';
import { Drawer } from '@/components/Drawer';
import { EmptyState } from '@/components/EmptyState';
import { FilterBar, FilterField } from '@/components/FilterBar';
import { JsonViewer } from '@/components/JsonViewer';
import { Select, type SelectOption } from '@/components/Select';
import { StatusBadge, type StatusKind } from '@/components/StatusBadge';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { getJson } from '@/services';
import type { ApiQueryParams } from '@/types';

type UnknownRecord = Record<string, unknown>;

interface WeeklyReportFilters {
  keyword: string;
  status: string;
  dateStart: string;
  dateEnd: string;
}

interface WeeklyReportListItem {
  id: string;
  date?: string;
  title?: string;
  link?: string;
  status?: string;
  provider?: string;
  exitCode?: number;
  createdAt?: string;
  raw: unknown;
}

const DEFAULT_FILTERS: WeeklyReportFilters = {
  keyword: '',
  status: '',
  dateStart: '',
  dateEnd: '',
};

// 状态筛选项：value 保持原值（applyLocalFilters 依赖），label 用中文。
const STATUS_OPTIONS: SelectOption[] = [
  { value: '', label: '全部状态' },
  { value: 'generated', label: '已生成' },
  { value: 'success', label: '成功' },
  { value: 'running', label: '生成中' },
  { value: 'failed', label: '失败' },
  { value: 'manual', label: '手动' },
];

function isRecord(value: unknown): value is UnknownRecord {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
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
  if (Array.isArray(response.reports)) return response.reports;
  if (Array.isArray(data)) return data;
  if (isRecord(data)) {
    if (Array.isArray(data.items)) return data.items;
    if (Array.isArray(data.list)) return data.list;
    if (Array.isArray(data.reports)) return data.reports;
  }
  return [];
}

function normalizeWeeklyReport(value: unknown, index: number): WeeklyReportListItem | null {
  if (!isRecord(value)) return null;
  const title = getString(value, ['title', 'name', 'summary']);
  // 后端给的是 date_range（"起 ~ 止"）与 date_start/date_end，优先展示区间字符串。
  const date =
    getString(value, ['date_range', 'dateRange']) ??
    getString(value, ['date_end', 'dateEnd', 'date_start', 'dateStart']);
  const link = getString(value, ['document_url', 'documentUrl', 'link', 'url', 'doc_url', 'docUrl']);
  const id =
    getString(value, ['document_token', 'documentToken', 'file', 'id']) ??
    link ??
    `${date ?? 'weekly-report'}-${title ?? index}`;

  return {
    id,
    date,
    title,
    link,
    status: getString(value, ['status', 'state']),
    provider: getString(value, ['provider_name', 'providerName', 'provider', 'source']),
    exitCode: getNumber(value, ['returncode', 'returnCode', 'exitCode', 'exit_code', 'code']),
    createdAt: getString(value, ['created_at', 'createdAt', 'updated_at', 'updatedAt']),
    raw: value,
  };
}

function toQuery(_filters: WeeklyReportFilters): ApiQueryParams {
  // 后端 /api/weekly-reports 不支持查询参数，全部在前端本地过滤。
  return {};
}

function getWeeklyReports(filters: WeeklyReportFilters) {
  return getJson<unknown>('/weekly-reports', toQuery(filters)).then((response) =>
    getItems(response)
      .map(normalizeWeeklyReport)
      .filter((item): item is WeeklyReportListItem => Boolean(item)),
  );
}

function matchKeyword(item: WeeklyReportListItem, keyword: string) {
  const normalized = keyword.trim().toLowerCase();
  if (!normalized) return true;
  return [item.title, item.link, item.date, item.status, item.provider, item.id]
    .filter(Boolean)
    .some((value) => String(value).toLowerCase().includes(normalized));
}

function matchDateRange(item: WeeklyReportListItem, filters: WeeklyReportFilters) {
  if (!item.date || (!filters.dateStart && !filters.dateEnd)) return true;
  const value = new Date(item.date).getTime();
  if (Number.isNaN(value)) return true;
  if (filters.dateStart && value < new Date(filters.dateStart).getTime()) return false;
  if (filters.dateEnd && value > new Date(`${filters.dateEnd}T23:59:59`).getTime()) return false;
  return true;
}

function applyLocalFilters(items: WeeklyReportListItem[], filters: WeeklyReportFilters) {
  return items.filter((item) => {
    const normalizedStatus = item.status?.toLowerCase() ?? '';
    const statusMatched = filters.status
      ? normalizedStatus === filters.status ||
        getStatusKind(item.status) === filters.status ||
        (filters.status === 'generated' && getStatusKind(item.status) === 'success') ||
        (filters.status === 'manual' && normalizedStatus.includes('manual')) ||
        (filters.status === 'manual' && normalizedStatus.includes('手动')) ||
        (filters.status === 'manual' && item.provider?.toLowerCase() === 'manual')
      : true;
    return statusMatched && matchKeyword(item, filters.keyword) && matchDateRange(item, filters);
  });
}

function getActiveFilterCount(filters: WeeklyReportFilters) {
  return Object.values(filters).filter(Boolean).length;
}

function formatText(value?: string) {
  return value?.trim() ? value : '--';
}

function formatDate(value?: string) {
  if (!value) return '--';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' });
}

function formatDateTime(value?: string) {
  if (!value) return '--';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN', { hour12: false });
}

function getStatusKind(status?: string): StatusKind {
  const normalized = status?.toLowerCase() ?? '';
  if (
    ['success', 'succeeded', 'done', 'completed', 'generated', 'created', '已生成'].some((key) =>
      normalized.includes(key),
    )
  ) {
    return 'success';
  }
  if (['failed', 'error', '失败'].some((key) => normalized.includes(key))) {
    return 'failed';
  }
  if (['running', 'processing', 'generating', 'pending', '生成中'].some((key) => normalized.includes(key))) {
    return 'running';
  }
  if (['timeout'].some((key) => normalized.includes(key))) {
    return 'timeout';
  }
  return 'pending';
}

function DetailContent({ report }: { report: WeeklyReportListItem }) {
  return (
    <DetailStack>
      <DetailGrid>
        <DetailField label="日期" value={report.date} />
        <DetailField label="标题" value={report.title} />
        <DetailField label="Provider" value={report.provider} />
        <DetailField label="返回码" value={report.exitCode === undefined ? undefined : String(report.exitCode)} />
        <DetailField label="创建时间" value={formatDateTime(report.createdAt)} />
        <DetailField label="记录 ID" value={report.id} />
      </DetailGrid>
      {report.link ? (
        <Card className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="min-w-0">
            <p className="text-caption text-muted-foreground">周报链接</p>
            <p className="mt-1 truncate font-mono text-code text-primary">{report.link}</p>
          </div>
          <Button asChild size="sm" variant="outline">
            <a href={report.link} rel="noreferrer" target="_blank">
              打开链接
              <ExternalLink className="h-3.5 w-3.5" />
            </a>
          </Button>
        </Card>
      ) : null}
      <JsonViewer data={report.raw} title="Raw snapshot" maxHeight={420} />
    </DetailStack>
  );
}

export default function WeeklyReportsPage() {
  const alert = useAlert();
  const [draftFilters, setDraftFilters] = useState(DEFAULT_FILTERS);
  const [filters, setFilters] = useState(DEFAULT_FILTERS);
  const [selectedReport, setSelectedReport] = useState<WeeklyReportListItem | undefined>();
  const weeklyReportsQuery = useQuery({
    queryKey: ['weekly-reports', filters],
    queryFn: () => getWeeklyReports(filters),
  });
  const reports = useMemo(
    () => applyLocalFilters(weeklyReportsQuery.data ?? [], filters),
    [weeklyReportsQuery.data, filters],
  );
  const columns = useMemo<ColumnDef<WeeklyReportListItem>[]>(
    () => [
      {
        accessorKey: 'date',
        header: '日期',
        cell: ({ row }) => <span>{formatDate(row.original.date)}</span>,
      },
      {
        accessorKey: 'title',
        header: '标题',
        cell: ({ row }) => (
          <span className="line-clamp-2 min-w-[180px] text-sm font-medium text-foreground">
            {formatText(row.original.title)}
          </span>
        ),
      },
      {
        accessorKey: 'link',
        header: '链接',
        enableSorting: false,
        cell: ({ row }) =>
          row.original.link ? (
            <Button asChild size="sm" variant="outline" onClick={(event) => event.stopPropagation()}>
              <a href={row.original.link} rel="noreferrer" target="_blank">
                打开文档
                <ExternalLink className="h-3.5 w-3.5" />
              </a>
            </Button>
          ) : (
            <Badge variant="outline">无链接</Badge>
          ),
      },
      {
        accessorKey: 'status',
        header: '生成状态',
        cell: ({ row }) => (
          <StatusBadge label={formatText(row.original.status)} status={getStatusKind(row.original.status)} />
        ),
      },
      {
        accessorKey: 'provider',
        header: '生成方',
        cell: ({ row }) => <span className="font-mono text-code">{formatText(row.original.provider)}</span>,
      },
      {
        accessorKey: 'exitCode',
        header: '返回码',
        cell: ({ row }) => (
          <span className={row.original.exitCode && row.original.exitCode !== 0 ? 'font-mono text-code text-danger' : 'font-mono text-code'}>
            {row.original.exitCode ?? '--'}
          </span>
        ),
      },
      {
        accessorKey: 'createdAt',
        header: '创建时间',
        cell: ({ row }) => <span className="text-caption">{formatDateTime(row.original.createdAt)}</span>,
      },
    ],
    [],
  );
  const activeCount = getActiveFilterCount(filters);
  const hasError = weeklyReportsQuery.isError;

  // 查询前校验日期范围：开始晚于结束则提示并阻止应用筛选。
  function handleQuery() {
    if (
      draftFilters.dateStart &&
      draftFilters.dateEnd &&
      draftFilters.dateStart > draftFilters.dateEnd
    ) {
      alert.warning({ title: '日期范围无效', description: '开始日期不能晚于结束日期' });
      return;
    }
    setFilters(draftFilters);
  }

  return (
    <section className="flex flex-col gap-4">
      {hasError ? (
        <Card className="border-danger bg-danger-soft/50 p-4">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-start gap-3">
              <AlertTriangle className="mt-0.5 h-5 w-5 text-danger" />
              <div>
                <p className="text-sm font-semibold text-danger">周报数据加载失败</p>
                <p className="text-caption text-danger">数据服务暂不可用，请稍后重试。</p>
              </div>
            </div>
            <Button variant="outline" size="sm" onClick={() => void weeklyReportsQuery.refetch()}>
              重试
            </Button>
          </div>
        </Card>
      ) : null}

      <FilterBar
        activeCount={activeCount}
        description="按标题、链接、日期范围与状态筛选周报。"
        loading={weeklyReportsQuery.isFetching}
        title="列表搜索"
        onReset={() => {
          setDraftFilters(DEFAULT_FILTERS);
          setFilters(DEFAULT_FILTERS);
        }}
        onSearch={handleQuery}
      >
        <FilterField label="关键词">
          <input
            className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm text-foreground shadow-sm outline-none transition-colors placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring"
            placeholder="搜索标题 / 链接"
            value={draftFilters.keyword}
            onChange={(event) => setDraftFilters((prev) => ({ ...prev, keyword: event.target.value }))}
          />
        </FilterField>
        <FilterField label="生成状态">
          <Select
            aria-label="生成状态筛选"
            options={STATUS_OPTIONS}
            placeholder="全部状态"
            value={draftFilters.status}
            onValueChange={(value) => setDraftFilters((prev) => ({ ...prev, status: value }))}
          />
        </FilterField>
        <FilterField label="开始日期">
          <DatePicker
            aria-label="开始日期"
            disableFuture
            value={draftFilters.dateStart}
            max={draftFilters.dateEnd || undefined}
            onChange={(v) => setDraftFilters((prev) => ({ ...prev, dateStart: v }))}
          />
        </FilterField>
        <FilterField label="结束日期">
          <DatePicker
            aria-label="结束日期"
            disableFuture
            value={draftFilters.dateEnd}
            min={draftFilters.dateStart || undefined}
            onChange={(v) => setDraftFilters((prev) => ({ ...prev, dateEnd: v }))}
          />
        </FilterField>
      </FilterBar>

      <DataTable
        columns={columns}
        data={reports}
        description="点击行查看周报日期、provider、返回码与原始快照。"
        emptyDescription="当前筛选条件下没有周报链接数据"
        emptyTitle="暂无周报链接"
        loading={weeklyReportsQuery.isLoading}
        title="周报链接列表"
        toolbar={<Badge variant="outline">共 {reports.length} 条</Badge>}
        onRowClick={setSelectedReport}
      />

      <Drawer
        open={Boolean(selectedReport)}
        size="lg"
        title={selectedReport?.title ?? '周报详情'}
        description={selectedReport ? `${formatDate(selectedReport.date)} · ${formatText(selectedReport.provider)}` : undefined}
        headerExtra={
          selectedReport ? (
            <StatusBadge label={formatText(selectedReport.status)} status={getStatusKind(selectedReport.status)} />
          ) : null
        }
        onOpenChange={(open) => {
          if (!open) setSelectedReport(undefined);
        }}
      >
        {selectedReport ? (
          <DetailContent report={selectedReport} />
        ) : (
          <EmptyState
            icon={<FileText className="h-10 w-10" />}
            title="未选择周报"
            description="从列表中选择一条记录后查看详情"
          />
        )}
      </Drawer>
    </section>
  );
}
