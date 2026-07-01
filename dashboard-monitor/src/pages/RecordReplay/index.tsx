import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  AlertTriangle,
  ArrowDownToLine,
  Braces,
  ImageOff,
  Link as LinkIcon,
  Video,
} from 'lucide-react';
import { EmptyState } from '@/components/EmptyState';
import { JsonViewer } from '@/components/JsonViewer';
import { StatusBadge } from '@/components/StatusBadge';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { cn } from '@/lib/utils';
import { getJson } from '@/services/api';
import type { StatusKind } from '@/components/StatusBadge/StatusBadge';

interface AnalysisJobItem {
  jobId: string;
  recordId?: string;
  status?: string;
  attempts?: number;
  analyzer?: string;
  error?: string | null;
  [key: string]: unknown;
}

interface ReplayMedia {
  name?: string;
  path?: string;
  url?: string;
  type?: string;
  mimeType?: string;
  exists?: boolean;
  [key: string]: unknown;
}

interface ReplayMessage {
  index: number;
  position?: string;
  isSelf: boolean;
  senderName?: string;
  time?: string;
  text?: string;
  links: string[];
  media: ReplayMedia[];
  raw: unknown;
}

interface ReplayData {
  recordId?: string;
  chatName?: string;
  chatType?: string;
  chatId?: string;
  anchorTime?: string;
  anchorIndex?: number;
  source?: string;
  messages: ReplayMessage[];
  raw: unknown;
}

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

function getBoolean(source: unknown, keys: string[]) {
  for (const key of keys) {
    const value = getObjectValue(source, key);
    if (typeof value === 'boolean') return value;
    if (typeof value === 'string') {
      if (value.toLowerCase() === 'true') return true;
      if (value.toLowerCase() === 'false') return false;
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

function getNestedArray<T>(payload: unknown, paths: string[][]) {
  for (const path of paths) {
    let cursor = payload;
    for (const segment of path) {
      cursor = getObjectValue(cursor, segment);
    }
    if (Array.isArray(cursor)) return cursor as T[];
  }
  return [];
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
  };
}

function normalizeMedia(payload: unknown): ReplayMedia {
  if (typeof payload === 'string') {
    return { path: payload, name: payload.split('/').pop(), exists: true };
  }
  return {
    ...(typeof payload === 'object' && payload !== null
      ? (payload as Record<string, unknown>)
      : {}),
    name: getString(payload, ['name', 'filename', 'fileName']),
    path: getString(payload, [
      'path',
      'relativePath',
      'relative_path',
      'filePath',
      'file_path',
    ]),
    url: getString(payload, ['url', 'src']),
    type: getString(payload, ['kind', 'type', 'mediaType', 'media_type']),
    mimeType: getString(payload, [
      'mimeType',
      'mime_type',
      'contentType',
      'content_type',
    ]),
    exists: getBoolean(payload, ['exists', 'available', 'readable']) ?? true,
  };
}

function getLinks(payload: unknown) {
  const direct = getObjectValue(payload, 'links');
  if (Array.isArray(direct)) {
    return direct
      .map((item) => (typeof item === 'string' ? item : getString(item, ['url', 'href'])))
      .filter((item): item is string => Boolean(item));
  }
  const text = getString(payload, ['text', 'content', 'message']);
  return text?.match(/https?:\/\/[^\s)]+/g) ?? [];
}

function normalizeReplayMessage(payload: unknown, fallbackIndex: number): ReplayMessage {
  const mediaValue =
    getObjectValue(payload, 'media') ??
    getObjectValue(payload, 'images') ??
    getObjectValue(payload, 'attachments');
  const media = Array.isArray(mediaValue) ? mediaValue.map(normalizeMedia) : [];
  const index =
    getNumber(payload, ['index', 'messageIndex', 'message_index']) ?? fallbackIndex;

  return {
    index,
    position: getString(payload, ['position']),
    isSelf: getBoolean(payload, ['isSelf', 'is_self', 'fromSelf', 'from_self']) ?? false,
    senderName: getString(payload, ['senderName', 'sender_name', 'sender', 'name']),
    time: getString(payload, [
      'create_time',
      'createTime',
      'time',
      'timestamp',
      'createdAt',
      'created_at',
      'sendTime',
      'send_time',
    ]),
    text: getString(payload, ['text', 'content', 'message', 'summary']),
    links: getLinks(payload),
    media,
    raw: payload,
  };
}

function normalizeReplay(payload: unknown): ReplayData {
  const messages = getNestedArray<unknown>(payload, [
    ['messages'],
    ['items'],
    ['data'],
    ['replay', 'messages'],
    ['record', 'messages'],
  ]).map(normalizeReplayMessage);

  return {
    recordId: getString(payload, ['recordId', 'record_id', 'id']),
    chatName: getString(payload, ['chatName', 'chat_name']),
    chatType: getString(payload, ['chatType', 'chat_type', 'source']),
    chatId: getString(payload, ['chatId', 'chat_id']),
    anchorTime: getString(payload, [
      'anchorTime',
      'anchor_time',
      'createdAt',
      'created_at',
    ]),
    anchorIndex: getNumber(payload, ['anchorIndex', 'anchor_index']),
    source: getString(payload, ['source']),
    messages,
    raw: payload,
  };
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

function truncate(value?: string, length = 80) {
  if (!value) return '-';
  return value.length > length ? `${value.slice(0, length)}...` : value;
}

function getStatusKind(status?: string): StatusKind {
  if (!status) return 'pending';
  return statusMap[status.toLowerCase()] ?? 'pending';
}

function getMediaLabel(media: ReplayMedia) {
  return media.name ?? media.path?.split('/').pop() ?? media.url ?? '未命名媒体';
}

function getMediaKind(media: ReplayMedia) {
  const signature = `${media.type ?? ''} ${media.mimeType ?? ''} ${media.name ?? ''} ${media.path ?? ''}`;
  if (/video|\.mp4|\.mov|\.webm/i.test(signature)) return 'video';
  if (/image|\.png|\.jpe?g|\.gif|\.webp|\.svg/i.test(signature)) return 'image';
  return 'file';
}

function buildMediaUrl(recordId: string | undefined, media: ReplayMedia) {
  if (media.url) return media.url;
  if (!recordId || !media.path) return undefined;
  const encodedPath = media.path.split('/').map(encodeURIComponent).join('/');
  return `/api/records/${encodeURIComponent(recordId)}/media/${encodedPath}`;
}

export default function RecordReplayPage() {
  const [searchParams] = useSearchParams();
  const recordId = searchParams.get('recordId')?.trim() ?? '';
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null);

  const replayQuery = useQuery({
    queryKey: ['record-replay', recordId],
    queryFn: async () => {
      const payload = await getJson<unknown>(
        `/records/${encodeURIComponent(recordId)}/replay`,
      );
      return normalizeReplay(payload);
    },
    enabled: Boolean(recordId),
  });

  const analysisJobsQuery = useQuery({
    queryKey: ['analysis-jobs', 'replay'],
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

  const replay = replayQuery.data;
  const activeRecordId = recordId;
  const selectedMessage =
    replay?.messages.find((message) => message.index === selectedIndex) ??
    replay?.messages.find((message) => message.index === replay.anchorIndex) ??
    replay?.messages[0];
  const selectedJobs = useMemo(
    () =>
      (analysisJobsQuery.data ?? []).filter(
        (job) => job.recordId === activeRecordId,
      ),
    [activeRecordId, analysisJobsQuery.data],
  );

  useEffect(() => {
    if (!replay?.messages.length) {
      setSelectedIndex(null);
      return;
    }
    const anchorMessage = replay.messages.find(
      (message) => message.index === replay.anchorIndex,
    );
    setSelectedIndex((prev) => {
      if (prev !== null && replay.messages.some((message) => message.index === prev))
        return prev;
      return anchorMessage?.index ?? replay.messages[0]?.index ?? null;
    });
  }, [replay]);

  const isLoading = replayQuery.isLoading;
  const hasRemoteError = replayQuery.isError;

  return (
    <section className="grid min-h-[calc(100vh-104px)] gap-4 xl:grid-cols-[360px_minmax(0,1fr)_420px]">
      <aside className="flex min-h-0 flex-col gap-4">
        <Card className="min-h-0 flex-1">
          <CardHeader className="pb-2">
            <CardTitle className="text-h2">关联分析</CardTitle>
          </CardHeader>
          <CardContent>
            {selectedJobs.length ? (
              <div className="flex flex-col gap-2">
                {selectedJobs.map((job) => (
                  <div
                    key={job.jobId}
                    className="rounded-lg border border-border bg-muted/20 p-3"
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <div className="truncate font-mono text-code text-foreground">
                          {job.jobId}
                        </div>
                        <div className="mt-1 text-caption text-muted-foreground">
                          {job.analyzer ?? 'unknown'} · 尝试 {job.attempts ?? 0} 次
                        </div>
                      </div>
                      <StatusBadge
                        label={job.status ?? 'pending'}
                        status={getStatusKind(job.status)}
                      />
                    </div>
                    {job.error ? (
                      <div className="mt-2 rounded-md bg-danger-soft px-2 py-1 text-caption text-danger">
                        {job.error}
                      </div>
                    ) : null}
                  </div>
                ))}
              </div>
            ) : (
              <EmptyState
                compact
                description="按当前 recordId 匹配 analysis-jobs"
                title="暂无关联任务"
              />
            )}
          </CardContent>
        </Card>
      </aside>

      <main className="min-h-0 overflow-hidden rounded-xl border border-border bg-card shadow-sm">
        <div className="border-b border-border bg-card px-5 py-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="flex min-w-0 max-w-xl flex-col gap-1">
              <span className="text-caption text-muted-foreground">当前 record</span>
              <span className="truncate text-h2 text-foreground">
                {replay?.chatName ?? replay?.chatId ?? '正在加载会话信息'}
              </span>
              <span className="truncate font-mono text-code text-muted-foreground">
                {recordId}
              </span>
            </div>
            <div className="grid grid-cols-3 gap-2 text-center">
              <div className="rounded-lg bg-muted/50 px-3 py-2">
                <div className="text-h2 text-foreground">
                  {replay?.messages.length ?? 0}
                </div>
                <div className="text-caption text-muted-foreground">消息</div>
              </div>
              <div className="rounded-lg bg-muted/50 px-3 py-2">
                <div className="text-h2 text-foreground">
                  {replay?.messages.reduce(
                    (sum, message) => sum + message.media.length,
                    0,
                  ) ?? 0}
                </div>
                <div className="text-caption text-muted-foreground">媒体</div>
              </div>
              <div className="rounded-lg bg-muted/50 px-3 py-2">
                <div className="text-h2 text-foreground">
                  {replay?.anchorIndex ?? '-'}
                </div>
                <div className="text-caption text-muted-foreground">锚点</div>
              </div>
            </div>
          </div>
        </div>

        {hasRemoteError ? (
          <div className="p-5">
            <Card className="border-danger bg-danger-soft/50 p-4">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <div className="flex items-start gap-3">
                  <AlertTriangle className="mt-0.5 h-5 w-5 text-danger" />
                  <div>
                    <p className="text-sm font-semibold text-danger">会话回放数据加载失败</p>
                    <p className="text-caption text-danger">数据服务暂不可用，请稍后重试。</p>
                  </div>
                </div>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => void replayQuery.refetch()}
                >
                  重试
                </Button>
              </div>
            </Card>
          </div>
        ) : !replay && !isLoading ? (
          <EmptyState
            description="当前 record 没有可渲染的回放数据。"
            title="暂无回放数据"
          />
        ) : (
          <ScrollArea className="h-[calc(100vh-232px)]">
            <div className="mx-auto flex max-w-4xl flex-col gap-3 px-5 py-5">
              {isLoading
                ? Array.from({ length: 6 }).map((_, index) => (
                    <div
                      key={index}
                      className={cn(
                        'h-24 max-w-[72%] animate-pulse rounded-2xl bg-muted',
                        index % 2 ? 'ml-auto' : 'mr-auto',
                      )}
                    />
                  ))
                : replay?.messages.map((message) => {
                    const isAnchor = message.index === replay.anchorIndex;
                    const isSelected = message.index === selectedMessage?.index;
                    return (
                      <button
                        key={`${message.index}-${message.time ?? ''}`}
                        aria-label={`选择第 ${message.index} 条消息，${formatTime(
                          message.time,
                        )}，${message.senderName ?? (message.isSelf ? '我' : '未知发送人')}`}
                        className={cn(
                          'flex w-full flex-col text-left outline-none',
                          message.isSelf ? 'items-end' : 'items-start',
                        )}
                        type="button"
                        onClick={() => setSelectedIndex(message.index)}
                      >
                        <div
                          className={cn(
                            'max-w-[76%] rounded-2xl border px-4 py-3 shadow-sm transition-all',
                            message.isSelf
                              ? 'rounded-br-sm border-primary/20 bg-primary text-primary-foreground'
                              : 'rounded-bl-sm border-border bg-background',
                            isAnchor &&
                              'ring-2 ring-warning ring-offset-2 ring-offset-background',
                            isSelected && 'scale-[1.01]',
                          )}
                        >
                          <div
                            className={cn(
                              'mb-1 flex flex-wrap items-center gap-2 text-caption',
                              message.isSelf
                                ? 'text-primary-foreground/80'
                                : 'text-muted-foreground',
                            )}
                          >
                            <span>
                              {message.senderName ??
                                (message.isSelf ? '我' : '未知发送人')}
                            </span>
                            <span>#{message.index}</span>
                            <span>{formatTime(message.time)}</span>
                            {isAnchor ? (
                              <Badge
                                className="bg-warning-soft text-warning"
                                variant="warning"
                              >
                                锚点
                              </Badge>
                            ) : null}
                          </div>
                          <p className="whitespace-pre-wrap text-body leading-6">
                            {message.text ?? '[非文本消息]'}
                          </p>
                          {message.links.length ? (
                            <div className="mt-3 flex flex-col gap-1">
                              {message.links.map((link) => (
                                <a
                                  key={link}
                                  className={cn(
                                    'inline-flex items-center gap-1 break-all text-caption underline underline-offset-4',
                                    message.isSelf
                                      ? 'text-primary-foreground'
                                      : 'text-primary',
                                  )}
                                  href={link}
                                  rel="noreferrer"
                                  target="_blank"
                                  onClick={(event) => event.stopPropagation()}
                                >
                                  <LinkIcon className="h-3.5 w-3.5 shrink-0" />
                                  {truncate(link, 96)}
                                </a>
                              ))}
                            </div>
                          ) : null}
                          {message.media.length ? (
                            <div className="mt-3 grid gap-2 sm:grid-cols-2">
                              {message.media.map((media, index) => (
                                <MediaPreview
                                  key={`${getMediaLabel(media)}-${index}`}
                                  media={media}
                                  recordId={activeRecordId}
                                  self={message.isSelf}
                                />
                              ))}
                            </div>
                          ) : null}
                        </div>
                      </button>
                    );
                  })}
            </div>
          </ScrollArea>
        )}
      </main>

      <aside className="min-h-0 overflow-hidden rounded-xl border border-border bg-card shadow-sm">
        <Tabs className="flex h-full flex-col" defaultValue="message">
          <div className="border-b border-border px-4 py-3">
            <TabsList>
              <TabsTrigger value="message">消息 JSON</TabsTrigger>
              <TabsTrigger value="record">回放 JSON</TabsTrigger>
            </TabsList>
          </div>
          <TabsContent className="min-h-0 flex-1 p-4" value="message">
            <JsonViewer
              data={selectedMessage?.raw}
              description={
                selectedMessage ? `message #${selectedMessage.index}` : '尚未选择消息'
              }
              maxHeight="calc(100vh - 220px)"
              title={
                <span className="inline-flex items-center gap-2">
                  <Braces className="h-4 w-4" />
                  选中消息
                </span>
              }
            />
          </TabsContent>
          <TabsContent className="min-h-0 flex-1 p-4" value="record">
            <JsonViewer
              data={replay?.raw}
              description={activeRecordId || '暂无回放 JSON'}
              maxHeight="calc(100vh - 220px)"
              title="Replay JSON"
            />
          </TabsContent>
        </Tabs>
      </aside>
    </section>
  );
}

function MediaPreview({
  media,
  recordId,
  self,
}: {
  media: ReplayMedia;
  recordId?: string;
  self?: boolean;
}) {
  const url = buildMediaUrl(recordId, media);
  const kind = getMediaKind(media);
  const label = getMediaLabel(media);
  const missing = media.exists === false || !url;

  if (missing) {
    return (
      <div
        className={cn(
          'flex min-h-24 flex-col items-center justify-center gap-2 rounded-xl border border-dashed p-3 text-center',
          self
            ? 'border-primary-foreground/40 text-primary-foreground/80'
            : 'border-border text-muted-foreground',
        )}
      >
        <ImageOff className="h-5 w-5" />
        <span className="text-caption">媒体文件不可用</span>
        <span className="break-all font-mono text-caption">{label}</span>
      </div>
    );
  }

  if (kind === 'image') {
    return (
      <a
        className="group relative block overflow-hidden rounded-xl border border-border bg-muted"
        href={url}
        rel="noreferrer"
        target="_blank"
      >
        <img
          alt={label}
          className="h-36 w-full object-cover transition-transform group-hover:scale-105"
          src={url}
        />
        <span className="absolute inset-x-0 bottom-0 truncate bg-background/80 px-2 py-1 text-caption text-foreground backdrop-blur">
          {label}
        </span>
      </a>
    );
  }

  if (kind === 'video') {
    return (
      <a
        className={cn(
          'flex min-h-24 items-center gap-3 rounded-xl border p-3 transition-colors hover:bg-accent',
          self
            ? 'border-primary-foreground/30 text-primary-foreground'
            : 'border-border text-foreground',
        )}
        href={url}
        rel="noreferrer"
        target="_blank"
      >
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-info/10 text-info">
          <Video className="h-5 w-5" />
        </span>
        <span className="min-w-0">
          <span className="block truncate text-body font-medium">视频文件</span>
          <span className="block truncate text-caption opacity-75">{label}</span>
        </span>
      </a>
    );
  }

  return (
    <a
      className={cn(
        'flex min-h-20 items-center gap-3 rounded-xl border p-3 transition-colors hover:bg-accent',
        self
          ? 'border-primary-foreground/30 text-primary-foreground'
          : 'border-border text-foreground',
      )}
      href={url}
      rel="noreferrer"
      target="_blank"
    >
      <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground">
        <ArrowDownToLine className="h-5 w-5" />
      </span>
      <span className="min-w-0">
        <span className="block truncate text-body font-medium">媒体文件</span>
        <span className="block truncate text-caption opacity-75">{label}</span>
      </span>
    </a>
  );
}
