import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import type { ColumnDef } from '@tanstack/react-table';
import {
  AlertTriangle,
  CalendarClock,
  Copy,
  ExternalLink,
  FileText,
} from 'lucide-react';
import { useAlert } from '@/components/Alert';
import { DataTable } from '@/components/DataTable';
import { DatePicker } from '@/components/DatePicker';
import { DetailField, DetailGrid } from '@/components/DetailPanel';
import { Drawer } from '@/components/Drawer';
import { EmptyState } from '@/components/EmptyState';
import { FilterBar, FilterField } from '@/components/FilterBar';
import { JsonViewer } from '@/components/JsonViewer';
import { StatusBadge } from '@/components/StatusBadge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { getJson } from '@/services/api';
import type { ApiQueryParams } from '@/types';
import type { StatusKind } from '@/components/StatusBadge/StatusBadge';

interface RecordListItem {
  recordId: string;
  chatType?: string;
  chatId?: string;
  chatName?: string;
  senderName?: string;
  anchorTime?: string;
  path?: string;
  filePath?: string;
  rawPath?: string;
  source?: string;
  [key: string]: unknown;
}

interface AnalysisJobItem {
  jobId: string;
  recordId?: string;
  status?: string;
  attempts?: number;
  analyzer?: string;
  error?: string | null;
  createdAt?: string;
  updatedAt?: string;
  [key: string]: unknown;
}

interface RecordFilters {
  dateFrom: string;
  dateTo: string;
  chatType: string;
  chatId: string;
  senderName: string;
  keyword: string;
}

const defaultFilters: RecordFilters = {
  dateFrom: '',
  dateTo: '',
  chatType: '',
  chatId: '',
  senderName: '',
  keyword: '',
};

// 与 Button / Input 一致的原生输入框样式，供 FilterBar 内的文本/日期输入复用。
const inputClassName =
  'h-9 w-full rounded-md border border-input bg-background px-3 text-sm text-foreground shadow-sm outline-none transition-colors placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring';

const statusMap: Record<string, StatusKind> = {
  success: 'success',
  succeeded: 'success',
  done: 'success',
  completed: 'success',
  failed: 'failed',
  error: 'failed',
  running: 'running',
  processing: 'running',
  pending: 'pending',
  queued: 'pending',
  timeout: 'timeout',
};

function getObjectValue(source: unknown, key: string) {
  if (typeof source !== 'object' || source === null) return undefined;
  return (source as Record<string, unknown>)[key];
}

function getString(source: unknown, keys: string[]) {
  for (const key of keys) {
    const value = getObjectValue(source, key);
    if (typeof value === 'string' && value.trim()) return value;
    if (typeof value === 'number') return String(value);
  }
  return undefined;
}

function getNumber(source: unknown, keys: string[]) {
  for (const key of keys) {
    const value = getObjectValue(source, key);
    if (typeof value === 'number') return value;
    if (typeof value === 'string' && value.trim() && !Number.isNaN(Number(value))) {
      return Number(value);
    }
  }
  return undefined;
}

function getArrayFromResponse<T>(payload: unknown, keys: string[]) {
  if (Array.isArray(payload)) return payload as T[];
  for (const key of keys) {
    const value = getObjectValue(payload, key);
    if (Array.isArray(value)) return value as T[];
  }
  return [];
}

function normalizeRecord(payload: unknown): RecordListItem {
  return {
    ...(typeof payload === 'object' && payload !== null
      ? (payload as Record<string, unknown>)
      : {}),
    recordId: getString(payload, ['recordId', 'record_id', 'id']) ?? 'unknown-record',
    chatType: getString(payload, ['chatType', 'chat_type', 'source']),
    chatId: getString(payload, ['chatId', 'chat_id']),
    chatName: getString(payload, ['chatName', 'chat_name']),
    senderName: getString(payload, ['senderName', 'sender_name', 'sender']),
    anchorTime: getString(payload, [
      'anchorTime',
      'anchor_time',
      'createdAt',
      'created_at',
      'time',
    ]),
    path: getString(payload, ['recordPath', 'record_path', 'path']),
    filePath: getString(payload, ['filePath', 'file_path']),
    rawPath: getString(payload, ['rawPath', 'raw_path']),
    source: getString(payload, ['source']),
  };
}

function normalizeAnalysisJob(payload: unknown): AnalysisJobItem {
  return {
    ...(typeof payload === 'object' && payload !== null
      ? (payload as Record<string, unknown>)
      : {}),
    jobId: getString(payload, ['jobId', 'job_id', 'id']) ?? 'unknown-job',
    recordId: getString(payload, ['recordId', 'record_id']),
    status: getString(payload, ['status']),
    attempts: getNumber(payload, ['attempts', 'tryCount', 'try_count']),
    analyzer: getString(payload, ['analyzer', 'provider', 'model']),
    error: getString(payload, ['error', 'errorMessage', 'error_message']) ?? null,
    createdAt: getString(payload, ['createdAt', 'created_at']),
    updatedAt: getString(payload, ['updatedAt', 'updated_at']),
  };
}

function buildRecordQuery(filters: RecordFilters): ApiQueryParams {
  // 后端 /api/records 仅支持 chat_type / chat_id 过滤与 page / page_size 分页，
  // 其余筛选项（日期/发送人/关键词）在前端本地过滤。
  return {
    chat_type: filters.chatType || undefined,
    chat_id: filters.chatId || undefined,
    page_size: 200,
  };
}

function applyLocalFilters(records: RecordListItem[], filters: RecordFilters) {
  // 后端不支持这些条件，统一在前端本地过滤，保证筛选 UI 真实生效。
  const senderName = filters.senderName.trim().toLowerCase();
  const keyword = filters.keyword.trim().toLowerCase();
  const fromTime = filters.dateFrom ? new Date(filters.dateFrom).getTime() : undefined;
  // 结束日期取当天 23:59:59.999，保证按日期包含当天数据。
  const toTime = filters.dateTo
    ? new Date(`${filters.dateTo}T23:59:59.999`).getTime()
    : undefined;

  return records.filter((record) => {
    if (fromTime !== undefined || toTime !== undefined) {
      const anchor = record.anchorTime ? new Date(record.anchorTime).getTime() : NaN;
      if (Number.isNaN(anchor)) return false;
      if (fromTime !== undefined && anchor < fromTime) return false;
      if (toTime !== undefined && anchor > toTime) return false;
    }
    if (senderName && !(record.senderName ?? '').toLowerCase().includes(senderName)) {
      return false;
    }
    if (keyword) {
      const haystack = [record.recordId, record.chatName, record.chatId]
        .filter(Boolean)
        .join(' ')
        .toLowerCase();
      if (!haystack.includes(keyword)) return false;
    }
    return true;
  });
}

function formatTime(value?: string) {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date);
}

function getRecordPath(record?: RecordListItem) {
  return record?.filePath ?? record?.rawPath ?? record?.path;
}

function getStatusKind(status?: string): StatusKind {
  if (!status) return 'pending';
  return statusMap[status.toLowerCase()] ?? 'pending';
}

export default function RecordsPage() {
  const alert = useAlert();
  const [draftFilters, setDraftFilters] = useState<RecordFilters>(defaultFilters);
  const [filters, setFilters] = useState<RecordFilters>(defaultFilters);
  const [selectedRecord, setSelectedRecord] = useState<RecordListItem | null>(null);

  const recordsQuery = useQuery({
    queryKey: ['records', filters],
    queryFn: async () => {
      const payload = await getJson<unknown>('/records', buildRecordQuery(filters));
      return getArrayFromResponse<unknown>(payload, ['records', 'items', 'data']).map(
        normalizeRecord,
      );
    },
  });

  const analysisJobsQuery = useQuery({
    queryKey: ['analysis-jobs'],
    queryFn: async () => {
      const payload = await getJson<unknown>('/analysis-jobs');
      return getArrayFromResponse<unknown>(payload, [
        'analysisJobs',
        'analysis_jobs',
        'items',
        'data',
      ]).map(normalizeAnalysisJob);
    },
  });

  const recordDetailQuery = useQuery({
    queryKey: ['record-detail', selectedRecord?.recordId],
    queryFn: () =>
      getJson<unknown>(`/records/${encodeURIComponent(selectedRecord?.recordId ?? '')}`),
    enabled: Boolean(selectedRecord?.recordId),
  });

  const analysisJobsByRecord = useMemo(() => {
    const result = new Map<string, AnalysisJobItem[]>();
    (analysisJobsQuery.data ?? []).forEach((job) => {
      if (!job.recordId) return;
      const jobs = result.get(job.recordId) ?? [];
      jobs.push(job);
      result.set(job.recordId, jobs);
    });
    return result;
  }, [analysisJobsQuery.data]);

  // 列表数据在前端本地过滤（日期/发送人/关键词/媒体/链接），chat_type/chat_id 已随后端 query 生效。
  const records = useMemo(
    () => applyLocalFilters(recordsQuery.data ?? [], filters),
    [recordsQuery.data, filters],
  );
  const activeCount = useMemo(
    () => Object.values(filters).filter(Boolean).length,
    [filters],
  );

  const columns = useMemo<ColumnDef<RecordListItem>[]>(
    () => [
      {
        accessorKey: 'chatName',
        header: '会话',
        cell: ({ row }) => (
          <span className="block min-w-[160px] font-medium text-foreground">
            {row.original.chatName ?? '-'}
          </span>
        ),
      },
      {
        accessorKey: 'anchorTime',
        header: '时间',
        cell: ({ row }) => (
          <span className="inline-flex items-center gap-1.5 text-muted-foreground">
            <CalendarClock className="h-3.5 w-3.5" />
            {formatTime(row.original.anchorTime)}
          </span>
        ),
      },
      {
        id: 'actions',
        header: '操作',
        enableSorting: false,
        cell: ({ row }) => (
          <Button
            size="sm"
            type="button"
            variant="outline"
            onClick={(event) => {
              event.stopPropagation();
              setSelectedRecord(row.original);
            }}
          >
            <FileText className="h-3.5 w-3.5" />
            详情
          </Button>
        ),
      },
      {
        id: 'jobs',
        header: '分析',
        cell: ({ row }) => {
          const jobs = analysisJobsByRecord.get(row.original.recordId) ?? [];
          const latestJob = jobs[0];
          return latestJob ? (
            <StatusBadge
              label={`${latestJob.status ?? 'pending'} · ${jobs.length}`}
              status={getStatusKind(latestJob.status)}
            />
          ) : (
            <span className="text-caption text-muted-foreground">未关联</span>
          );
        },
      },
    ],
    [analysisJobsByRecord],
  );

  const selectedJobs = selectedRecord
    ? (analysisJobsByRecord.get(selectedRecord.recordId) ?? [])
    : [];
  const selectedPath = getRecordPath(selectedRecord ?? undefined);

  return (
    <section className="flex flex-col gap-4">
      <FilterBar
        activeCount={activeCount}
        description="按会话类型、会话 ID、关键词等条件筛选记录。"
        loading={recordsQuery.isFetching}
        title="记录筛选"
        onReset={() => {
          setDraftFilters(defaultFilters);
          setFilters(defaultFilters);
        }}
        onSearch={() => {
          // 起始日期不得晚于结束日期，非法时提示且不更新 filters。
          if (
            draftFilters.dateFrom &&
            draftFilters.dateTo &&
            draftFilters.dateFrom > draftFilters.dateTo
          ) {
            alert.warning({
              title: '日期范围无效',
              description: '开始日期不能晚于结束日期',
            });
            return;
          }
          setFilters(draftFilters);
        }}
      >
        <FilterField label="起始日期">
          <DatePicker
            aria-label="起始日期"
            disableFuture
            max={draftFilters.dateTo || undefined}
            value={draftFilters.dateFrom}
            onChange={(v) => setDraftFilters((prev) => ({ ...prev, dateFrom: v }))}
          />
        </FilterField>
        <FilterField label="结束日期">
          <DatePicker
            aria-label="结束日期"
            disableFuture
            min={draftFilters.dateFrom || undefined}
            value={draftFilters.dateTo}
            onChange={(v) => setDraftFilters((prev) => ({ ...prev, dateTo: v }))}
          />
        </FilterField>
        <FilterField label="会话类型">
          <input
            aria-label="会话类型"
            className={inputClassName}
            placeholder="group / p2p / im"
            value={draftFilters.chatType}
            onChange={(event) =>
              setDraftFilters((prev) => ({ ...prev, chatType: event.target.value }))
            }
          />
        </FilterField>
        <FilterField label="会话 ID">
          <input
            aria-label="会话 ID"
            className={inputClassName}
            placeholder="oc_xxx"
            value={draftFilters.chatId}
            onChange={(event) =>
              setDraftFilters((prev) => ({ ...prev, chatId: event.target.value }))
            }
          />
        </FilterField>
        <FilterField label="发送人">
          <input
            aria-label="发送人"
            className={inputClassName}
            placeholder="姓名或 open_id"
            value={draftFilters.senderName}
            onChange={(event) =>
              setDraftFilters((prev) => ({ ...prev, senderName: event.target.value }))
            }
          />
        </FilterField>
        <FilterField label="关键词">
          <input
            aria-label="关键词"
            className={inputClassName}
            placeholder="recordId / 会话"
            value={draftFilters.keyword}
            onChange={(event) =>
              setDraftFilters((prev) => ({ ...prev, keyword: event.target.value }))
            }
          />
        </FilterField>
      </FilterBar>

      {recordsQuery.isError ? (
        <Card className="border-danger bg-danger-soft/50 p-4">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-start gap-3">
              <AlertTriangle className="mt-0.5 h-5 w-5 text-danger" />
              <div>
                <p className="text-sm font-semibold text-danger">记录数据加载失败</p>
                <p className="text-caption text-danger">数据服务暂不可用，请稍后重试。</p>
              </div>
            </div>
            <Button
              size="sm"
              variant="outline"
              onClick={() => void recordsQuery.refetch()}
            >
              重试
            </Button>
          </div>
        </Card>
      ) : (
        <DataTable
          columns={columns}
          data={records}
          description="点击“详情”按钮可查看记录详情；详情抽屉内可复制路径、打开 IM 回放。"
          emptyDescription="当前筛选条件下没有记录"
          emptyTitle="暂无记录"
          loading={recordsQuery.isLoading || analysisJobsQuery.isLoading}
          skeletonRows={8}
          title="记录明细"
        />
      )}

      <Drawer
        description={selectedRecord?.recordId}
        footer={
          <div className="flex w-full flex-wrap items-center justify-between gap-2">
            <Button
              disabled={!selectedPath}
              type="button"
              variant="outline"
              onClick={async () => {
                if (!selectedPath) return;
                try {
                  await navigator.clipboard.writeText(selectedPath);
                  alert.success({ title: '已复制路径' });
                } catch {
                  alert.error({ title: '复制失败' });
                }
              }}
            >
              <Copy className="h-4 w-4" />
              复制路径
            </Button>
            {selectedRecord ? (
              <Button asChild>
                <Link
                  to={`/records/replay?recordId=${encodeURIComponent(selectedRecord.recordId)}`}
                >
                  <ExternalLink className="h-4 w-4" />
                  打开 IM 回放
                </Link>
              </Button>
            ) : null}
          </div>
        }
        open={Boolean(selectedRecord)}
        size="xl"
        title="记录详情"
        onOpenChange={(open) => {
          if (!open) setSelectedRecord(null);
        }}
      >
        <Tabs defaultValue="json">
          <TabsList>
            <TabsTrigger value="json">原始 JSON</TabsTrigger>
            <TabsTrigger value="jobs">关联分析</TabsTrigger>
            <TabsTrigger value="index">索引字段</TabsTrigger>
          </TabsList>
          <TabsContent value="json">
            <JsonViewer
              data={recordDetailQuery.data ?? selectedRecord}
              filePath={selectedPath}
              maxHeight={620}
              title={recordDetailQuery.isLoading ? '正在加载记录详情' : '记录 JSON'}
            />
          </TabsContent>
          <TabsContent value="jobs">
            <div className="flex flex-col gap-3">
              {selectedJobs.length ? (
                selectedJobs.map((job) => (
                  <Card key={job.jobId}>
                    <CardHeader className="pb-2">
                      <div className="flex items-start justify-between gap-3">
                        <div>
                          <CardTitle className="font-mono text-body">
                            {job.jobId}
                          </CardTitle>
                          <p className="mt-1 text-caption text-muted-foreground">
                            {job.analyzer ?? 'unknown analyzer'} · 尝试{' '}
                            {job.attempts ?? 0} 次
                          </p>
                        </div>
                        <StatusBadge
                          label={job.status ?? 'pending'}
                          status={getStatusKind(job.status)}
                        />
                      </div>
                    </CardHeader>
                    <CardContent>
                      {job.error ? (
                        <p className="rounded-md bg-danger-soft px-3 py-2 text-caption text-danger">
                          {job.error}
                        </p>
                      ) : (
                        <p className="text-caption text-muted-foreground">暂无错误信息</p>
                      )}
                    </CardContent>
                  </Card>
                ))
              ) : (
                <EmptyState
                  compact
                  description="未找到与该记录关联的分析任务"
                  icon={<FileText className="h-6 w-6" />}
                  title="未关联分析任务"
                />
              )}
            </div>
          </TabsContent>
          <TabsContent value="index">
            <DetailGrid>
              {[
                ['recordId', selectedRecord?.recordId],
                ['chatType', selectedRecord?.chatType],
                ['chatId', selectedRecord?.chatId],
                ['chatName', selectedRecord?.chatName],
                ['senderName', selectedRecord?.senderName],
                ['anchorTime', selectedRecord?.anchorTime],
                ['path', selectedPath],
              ].map(([label, value]) => (
                <DetailField
                  key={String(label)}
                  mono
                  emptyText="-"
                  label={label}
                  value={value === undefined ? undefined : String(value)}
                  valueClassName="break-all"
                />
              ))}
            </DetailGrid>
          </TabsContent>
        </Tabs>
      </Drawer>
    </section>
  );
}
