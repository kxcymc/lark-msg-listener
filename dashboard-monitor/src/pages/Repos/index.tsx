import { useQuery } from '@tanstack/react-query';
import type { ColumnDef } from '@tanstack/react-table';
import { AlertTriangle } from 'lucide-react';
import { useMemo, useState } from 'react';
import { ChartCard } from '@/components/ChartCard';
import { DataTable } from '@/components/DataTable';
import { DetailField, DetailGrid, DetailStack } from '@/components/DetailPanel';
import { Drawer } from '@/components/Drawer';
import { EmptyState } from '@/components/EmptyState';
import { FilterBar, FilterField } from '@/components/FilterBar';
import { JsonViewer } from '@/components/JsonViewer';
import { MetricCard } from '@/components/MetricCard';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { getJson } from '@/services';

type UnknownRecord = Record<string, unknown>;

interface RepoListItem {
  repoId: string;
  name?: string;
  localPath?: string;
  remotes: string[];
  branches: string[];
  scannedAt?: string;
  demandCount: number;
  taskCount: number;
  mrCount: number;
  failedCount: number;
  relatedDemands: string[];
  relatedTasks: string[];
  raw: unknown;
}

const inputClassName =
  'h-9 w-full rounded-md border border-input bg-background px-3 text-sm text-foreground shadow-sm outline-none transition-colors placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring';

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
    if (typeof value === 'string' && value.trim() && Number.isFinite(Number(value))) return Number(value);
  }
  return undefined;
}

function getItems(response: unknown): unknown[] {
  if (Array.isArray(response)) return response;
  if (!isRecord(response)) return [];
  const data = response.data;
  if (Array.isArray(response.items)) return response.items;
  if (Array.isArray(response.list)) return response.list;
  if (Array.isArray(response.repos)) return response.repos;
  if (Array.isArray(data)) return data;
  if (isRecord(data)) {
    if (Array.isArray(data.items)) return data.items;
    if (Array.isArray(data.list)) return data.list;
    if (Array.isArray(data.repos)) return data.repos;
  }
  return [];
}

function stringifyListItem(value: unknown) {
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  if (isRecord(value)) {
    return (
      getString(value, [
        'id',
        'name',
        'url',
        'remote',
        'branch',
        'demandId',
        'demand_id',
        'taskId',
        'task_id',
        'summary',
        'title',
      ]) ?? JSON.stringify(value)
    );
  }
  return undefined;
}

function getStringArray(record: UnknownRecord, keys: string[]) {
  for (const key of keys) {
    const value = record[key];
    if (Array.isArray(value)) {
      return value.map(stringifyListItem).filter((item): item is string => Boolean(item?.trim()));
    }
    if (typeof value === 'string' && value.trim()) {
      const parsed = value
        .split(/[\n,]/)
        .map((item) => item.trim())
        .filter(Boolean);
      return parsed.length ? parsed : [value];
    }
    if (isRecord(value)) {
      return Object.entries(value).map(([name, url]) => `${name}: ${stringifyListItem(url) ?? '--'}`);
    }
  }
  return [];
}

function getRelatedIds(record: UnknownRecord, keys: string[], idKeys: string[]) {
  for (const key of keys) {
    const value = record[key];
    if (Array.isArray(value)) {
      return value
        .map((item) => {
          if (isRecord(item)) return getString(item, idKeys) ?? stringifyListItem(item);
          return stringifyListItem(item);
        })
        .filter((item): item is string => Boolean(item?.trim()));
    }
  }
  return [];
}

function normalizeRepo(value: unknown): RepoListItem | null {
  if (!isRecord(value)) return null;
  const repoId = getString(value, ['repoId', 'repo_id', 'id', 'repo', 'repository']);
  if (!repoId) return null;
  const relatedDemands = getRelatedIds(
    value,
    ['relatedDemands', 'related_demands', 'demands', 'demandItems', 'demand_items'],
    ['demandId', 'demand_id', 'id', 'summary', 'title'],
  );
  const relatedTasks = getRelatedIds(
    value,
    ['relatedTasks', 'related_tasks', 'tasks', 'devTasks', 'dev_tasks'],
    ['taskId', 'task_id', 'id', 'executorTaskId', 'executor_task_id'],
  );

  return {
    repoId,
    name: getString(value, ['label', 'name', 'repoName', 'repo_name', 'displayName', 'display_name']),
    localPath: getString(value, ['localPath', 'local_path', 'path', 'root', 'worktree']),
    remotes: getStringArray(value, ['remotes_json', 'remotes', 'remoteUrls', 'remote_urls', 'remote', 'origin']),
    branches: getStringArray(value, ['branches_json', 'branches', 'branchNames', 'branch_names', 'localBranches', 'local_branches']),
    scannedAt: getString(value, ['scannedAt', 'scanned_at', 'scanTime', 'scan_time', 'updatedAt', 'updated_at']),
    demandCount: getNumber(value, ['demandCount', 'demand_count', 'demandsCount', 'demands_count']) ?? relatedDemands.length,
    taskCount: getNumber(value, ['taskCount', 'task_count', 'tasksCount', 'tasks_count']) ?? relatedTasks.length,
    mrCount: getNumber(value, ['mrCount', 'mr_count', 'mergeRequestCount', 'merge_request_count']) ?? 0,
    failedCount: getNumber(value, ['failedCount', 'failed_count', 'failureCount', 'failure_count', 'failCount', 'fail_count']) ?? 0,
    relatedDemands,
    relatedTasks,
    raw: value,
  };
}

function getRepoList() {
  return getJson<unknown>('/repos').then((response) =>
    getItems(response).map(normalizeRepo).filter((item): item is RepoListItem => Boolean(item)),
  );
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

function formatLatestScan(repos: RepoListItem[]) {
  const timestamps = repos
    .map((repo) => (repo.scannedAt ? new Date(repo.scannedAt).getTime() : Number.NaN))
    .filter(Number.isFinite);
  if (!timestamps.length) return '--';
  return formatDateTime(new Date(Math.max(...timestamps)).toISOString());
}

function InlineList({ items, emptyText }: { items: string[]; emptyText: string }) {
  if (!items.length) return <span className="text-muted-foreground">{emptyText}</span>;
  return (
    <div className="flex max-w-[260px] flex-wrap gap-1.5">
      {items.slice(0, 3).map((item) => (
        <Badge key={item} className="max-w-full truncate" variant="outline" title={item}>
          {item}
        </Badge>
      ))}
      {items.length > 3 ? <Badge variant="secondary">+{items.length - 3}</Badge> : null}
    </div>
  );
}

function DetailList({ title, items, emptyText }: { title: string; items: string[]; emptyText: string }) {
  return (
    <Card className="p-4">
      <p className="mb-3 text-sm font-medium text-foreground">{title}</p>
      {items.length ? (
        <div className="flex flex-wrap gap-2">
          {items.map((item) => (
            <Badge key={item} className="max-w-full truncate" variant="outline" title={item}>
              {item}
            </Badge>
          ))}
        </div>
      ) : (
        <p className="text-caption text-muted-foreground">{emptyText}</p>
      )}
    </Card>
  );
}

function RepoWorkloadBars({ repos }: { repos: RepoListItem[] }) {
  const data = repos
    .filter((repo) => repo.demandCount || repo.taskCount || repo.mrCount || repo.failedCount)
    .slice()
    .sort((a, b) => b.demandCount + b.taskCount - (a.demandCount + a.taskCount))
    .slice(0, 8);
  const max = Math.max(...data.map((repo) => repo.demandCount + repo.taskCount + repo.mrCount + repo.failedCount), 1);

  if (!data.length) {
    return <EmptyState className="min-h-[180px]" title="暂无统计数据" description="当前仓库没有可展示的工作量聚合" />;
  }

  return (
    <div className="flex flex-col gap-3">
      {data.map((repo) => {
        const total = repo.demandCount + repo.taskCount + repo.mrCount + repo.failedCount;
        return (
          <div key={repo.repoId} className="grid grid-cols-[112px_minmax(0,1fr)_40px] items-center gap-3">
            <span className="truncate text-caption text-muted-foreground" title={repo.name ?? repo.repoId}>
              {repo.name ?? repo.repoId}
            </span>
            <div className="h-2.5 overflow-hidden rounded-full bg-muted">
              <div
                className="h-full rounded-full bg-primary"
                style={{ width: `${Math.max((total / max) * 100, 6)}%` }}
              />
            </div>
            <span className="text-right text-caption font-medium text-foreground">{total}</span>
          </div>
        );
      })}
    </div>
  );
}

function RepoDetail({ repo }: { repo: RepoListItem }) {
  return (
    <DetailStack>
      <DetailGrid>
        <DetailField label="仓库 ID" value={repo.repoId} />
        <DetailField label="名称" value={repo.name} />
        <DetailField label="本地路径" value={repo.localPath} />
        <DetailField label="扫描时间" value={formatDateTime(repo.scannedAt)} />
      </DetailGrid>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <MetricCard title="需求数" value={repo.demandCount} />
        <MetricCard title="任务数" value={repo.taskCount} />
        <MetricCard title="MR 数" value={repo.mrCount} />
        <MetricCard title="失败数" value={repo.failedCount} trend={repo.failedCount ? 'down' : 'flat'} />
      </div>
      <DetailList title="远端" items={repo.remotes} emptyText="暂无 remote 信息" />
      <DetailList title="分支" items={repo.branches} emptyText="暂无分支信息" />
      <DetailList title="关联需求" items={repo.relatedDemands} emptyText="暂无关联需求" />
      <DetailList title="关联任务" items={repo.relatedTasks} emptyText="暂无关联任务" />
      <JsonViewer data={repo.raw} title="原始数据" maxHeight={420} />
    </DetailStack>
  );
}

export default function ReposPage() {
  const [keyword, setKeyword] = useState('');
  const [selectedRepo, setSelectedRepo] = useState<RepoListItem | undefined>();
  const repoQuery = useQuery({
    queryKey: ['repos'],
    queryFn: getRepoList,
  });
  const repos = useMemo(() => repoQuery.data ?? [], [repoQuery.data]);
  const filteredRepos = useMemo(() => {
    const normalizedKeyword = keyword.trim().toLowerCase();
    if (!normalizedKeyword) return repos;
    return repos.filter((repo) =>
      [repo.repoId, repo.name, repo.localPath, ...repo.remotes, ...repo.branches]
        .filter((value): value is string => Boolean(value))
        .some((value) => value.toLowerCase().includes(normalizedKeyword)),
    );
  }, [keyword, repos]);
  const columns = useMemo<ColumnDef<RepoListItem>[]>(
    () => [
      {
        accessorKey: 'repoId',
        header: '仓库 ID',
        cell: ({ row }) => (
          <span className="font-mono text-code text-primary">{row.original.repoId}</span>
        ),
      },
      {
        accessorKey: 'name',
        header: '名称',
        cell: ({ row }) => <span className="font-medium">{formatText(row.original.name)}</span>,
      },
      {
        accessorKey: 'localPath',
        header: '本地路径',
        cell: ({ row }) => (
          <span className="line-clamp-2 min-w-[220px] font-mono text-code text-muted-foreground">
            {formatText(row.original.localPath)}
          </span>
        ),
      },
      {
        accessorKey: 'remotes',
        header: '远端',
        enableSorting: false,
        cell: ({ row }) => <InlineList items={row.original.remotes} emptyText="无 remote" />,
      },
      {
        accessorKey: 'branches',
        header: '分支',
        enableSorting: false,
        cell: ({ row }) => <InlineList items={row.original.branches} emptyText="无分支" />,
      },
      {
        accessorKey: 'demandCount',
        header: '需求',
        cell: ({ row }) => <span>{row.original.demandCount}</span>,
      },
      {
        accessorKey: 'taskCount',
        header: '任务',
        cell: ({ row }) => <span>{row.original.taskCount}</span>,
      },
      {
        accessorKey: 'mrCount',
        header: 'MR',
        cell: ({ row }) => <span>{row.original.mrCount}</span>,
      },
      {
        accessorKey: 'failedCount',
        header: '失败',
        cell: ({ row }) => (
          <span className={row.original.failedCount ? 'font-medium text-danger' : 'text-muted-foreground'}>
            {row.original.failedCount}
          </span>
        ),
      },
      {
        accessorKey: 'scannedAt',
        header: '扫描时间',
        cell: ({ row }) => <span className="whitespace-nowrap">{formatDateTime(row.original.scannedAt)}</span>,
      },
    ],
    [],
  );
  const totals = useMemo(
    () =>
      repos.reduce(
        (acc, repo) => ({
          demands: acc.demands + repo.demandCount,
          tasks: acc.tasks + repo.taskCount,
          failures: acc.failures + repo.failedCount,
        }),
        { demands: 0, tasks: 0, failures: 0 },
      ),
    [repos],
  );
  const hasError = repoQuery.isError;

  return (
    <section className="flex flex-col gap-4">
      {hasError ? (
        <Card className="border-danger bg-danger-soft/50 p-4">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-start gap-3">
              <AlertTriangle className="mt-0.5 h-5 w-5 text-danger" />
              <div>
                <p className="text-sm font-semibold text-danger">仓库数据加载失败</p>
                <p className="text-caption text-danger">数据服务暂不可用，请稍后重试。</p>
              </div>
            </div>
            <Button variant="outline" size="sm" onClick={() => void repoQuery.refetch()}>
              重试
            </Button>
          </div>
        </Card>
      ) : null}

      <div className="grid grid-cols-1 gap-4 md:grid-cols-4">
        <MetricCard title="仓库总数" value={repoQuery.isLoading ? <Skeleton className="h-8 w-16" /> : repos.length} />
        <MetricCard title="需求数" value={totals.demands} />
        <MetricCard title="任务数" value={totals.tasks} />
        <MetricCard title="失败数" value={totals.failures} trend={totals.failures ? 'down' : 'flat'} />
      </div>

      <FilterBar
        activeCount={keyword ? 1 : 0}
        description="按仓库、路径或分支关键词实时过滤列表。"
        title="仓库筛选"
        onReset={() => setKeyword('')}
        onSearch={() => {
          // 关键词过滤为实时生效，无需额外提交动作。
        }}
      >
        <FilterField label="关键词">
          <input
            aria-label="搜索仓库"
            className={inputClassName}
            placeholder="搜索仓库 / 路径 / 分支"
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
          />
        </FilterField>
      </FilterBar>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
        <DataTable
          columns={columns}
          data={filteredRepos}
          description={`仓库清单来自本地扫描，共 ${repos.length} 个仓库，最近扫描 ${formatLatestScan(repos)}。点击行查看仓库详情。`}
          emptyDescription={keyword ? '当前关键词没有匹配仓库' : '暂无仓库库存数据'}
          emptyTitle={keyword ? '无匹配仓库' : '暂无仓库'}
          loading={repoQuery.isLoading}
          tableClassName="min-w-[1120px]"
          title="仓库列表"
          onRowClick={setSelectedRepo}
        />

        <div className="flex flex-col gap-4">
          <ChartCard title="仓库工作量排行">
            <RepoWorkloadBars repos={repos} />
          </ChartCard>
        </div>
      </div>

      <Drawer
        open={Boolean(selectedRepo)}
        size="lg"
        title={selectedRepo?.name ?? selectedRepo?.repoId ?? '仓库详情'}
        description={selectedRepo?.localPath}
        headerExtra={selectedRepo ? <Badge variant="secondary">{selectedRepo.branches.length} branches</Badge> : null}
        onOpenChange={(open) => {
          if (!open) setSelectedRepo(undefined);
        }}
      >
        {selectedRepo ? <RepoDetail repo={selectedRepo} /> : null}
      </Drawer>
    </section>
  );
}
