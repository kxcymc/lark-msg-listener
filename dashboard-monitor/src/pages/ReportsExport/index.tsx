import { Download, Loader2, RotateCcw } from 'lucide-react';
import type { FormEvent } from 'react';
import { useMemo, useState } from 'react';
import { useAlert } from '@/components/Alert';
import { DatePicker } from '@/components/DatePicker';
import { Select, type SelectOption } from '@/components/Select';
import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';

interface ExportFormState {
  reportType: string;
  dateStart: string;
  dateEnd: string;
}

const DEFAULT_FORM: ExportFormState = {
  reportType: 'all',
  dateStart: '',
  dateEnd: '',
};

// 报表类型选项：value 保持原值（REPORT_TYPE_SHEETS 依赖），沿用现有中文标签。
const REPORT_TYPE_OPTIONS: SelectOption[] = [
  { value: 'all', label: '全部（汇总 + 各明细）' },
  { value: 'records', label: '消息记录' },
  { value: 'demands', label: '需求' },
  { value: 'dev-tasks', label: '开发任务' },
  { value: 'repos', label: '仓库' },
  { value: 'weekly-reports', label: '周报' },
];

function getActiveCount(form: ExportFormState) {
  return [
    form.reportType !== DEFAULT_FORM.reportType,
    Boolean(form.dateStart),
    Boolean(form.dateEnd),
  ].filter(Boolean).length;
}

// 报表类型到后端 sheet 名的映射；后端按 body.sheets（小写匹配）选择导出。
const REPORT_TYPE_SHEETS: Record<string, string[]> = {
  all: ['Summary', 'DailyMetrics', 'Records', 'Demands', 'DevTasks', 'Repos', 'WeeklyReports'],
  records: ['Summary', 'Records'],
  demands: ['Summary', 'Demands'],
  'dev-tasks': ['Summary', 'DevTasks'],
  repos: ['Summary', 'Repos'],
  'weekly-reports': ['Summary', 'WeeklyReports'],
};

function buildExportPayload(form: ExportFormState) {
  // 后端 /api/exports/xlsx 仅消费 start / end / sheets，其余字段会被忽略。
  return {
    start: form.dateStart || undefined,
    end: form.dateEnd || undefined,
    sheets: REPORT_TYPE_SHEETS[form.reportType] ?? REPORT_TYPE_SHEETS.all,
  };
}

function getFilenameFromDisposition(disposition: string | null) {
  if (!disposition) return undefined;

  const utf8Match = disposition.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8Match?.[1]) {
    return decodeURIComponent(utf8Match[1]);
  }

  const filenameMatch = disposition.match(/filename="?([^";]+)"?/i);
  return filenameMatch?.[1];
}

function getDefaultFilename(reportType: string) {
  const stamp = new Date().toISOString().slice(0, 19).replace(/[-:T]/g, '');
  return `${reportType || 'report'}-${stamp}.xlsx`;
}

async function downloadXlsx(form: ExportFormState) {
  const response = await fetch('/api/exports/xlsx', {
    method: 'POST',
    headers: {
      Accept: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(buildExportPayload(form)),
  });

  if (!response.ok) {
    const message = await response.text().catch(() => '');
    throw new Error(message || `导出失败：${response.status} ${response.statusText}`);
  }

  const blob = await response.blob();
  const filename =
    getFilenameFromDisposition(response.headers.get('content-disposition')) ??
    getDefaultFilename(form.reportType);
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');

  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);

  return { filename, size: blob.size };
}

export default function ReportsExportPage() {
  const alert = useAlert();
  const [form, setForm] = useState<ExportFormState>(DEFAULT_FORM);
  const [exporting, setExporting] = useState(false);
  const activeCount = useMemo(() => getActiveCount(form), [form]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    // 提交前校验日期范围：开始晚于结束则提示并阻止发起下载。
    if (form.dateStart && form.dateEnd && form.dateStart > form.dateEnd) {
      alert.warning({ title: '日期范围无效', description: '开始日期不能晚于结束日期' });
      return;
    }

    setExporting(true);
    try {
      const result = await downloadXlsx(form);
      alert.success({
        title: '导出完成',
        description: `已生成 ${result.filename}（${Math.max(1, Math.ceil(result.size / 1024))} KB）`,
      });
    } catch (error) {
      alert.error({
        title: '报表导出失败',
        description: error instanceof Error ? error.message : '导出失败，请稍后重试。',
      });
    } finally {
      setExporting(false);
    }
  }

  return (
    <section className="flex flex-col gap-4">
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <CardTitle>导出条件</CardTitle>
              <CardDescription>
                选择报表类型与日期范围，导出对应的 xlsx 报表。
              </CardDescription>
            </div>
            {activeCount ? (
              <span className="rounded-full bg-primary/10 px-2 py-0.5 text-caption font-medium text-primary">
                已选 {activeCount} 项
              </span>
            ) : null}
          </div>
        </CardHeader>
        <CardContent>
          <form className="flex flex-col gap-5" onSubmit={(event) => void handleSubmit(event)}>
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
              <label className="flex flex-col gap-1.5">
                <span className="text-caption font-medium text-muted-foreground">报表类型</span>
                <Select
                  aria-label="报表类型"
                  disabled={exporting}
                  options={REPORT_TYPE_OPTIONS}
                  value={form.reportType}
                  onValueChange={(value) =>
                    setForm((prev) => ({ ...prev, reportType: value }))
                  }
                />
              </label>

              <label className="flex flex-col gap-1.5">
                <span className="text-caption font-medium text-muted-foreground">开始日期</span>
                <DatePicker
                  aria-label="开始日期"
                  disableFuture
                  disabled={exporting}
                  value={form.dateStart}
                  max={form.dateEnd || undefined}
                  onChange={(v) => setForm((prev) => ({ ...prev, dateStart: v }))}
                />
              </label>

              <label className="flex flex-col gap-1.5">
                <span className="text-caption font-medium text-muted-foreground">结束日期</span>
                <DatePicker
                  aria-label="结束日期"
                  disableFuture
                  disabled={exporting}
                  value={form.dateEnd}
                  min={form.dateStart || undefined}
                  onChange={(v) => setForm((prev) => ({ ...prev, dateEnd: v }))}
                />
              </label>
            </div>

            <div className="flex flex-wrap items-center justify-end gap-2">
              <Button
                disabled={exporting}
                type="button"
                variant="outline"
                onClick={() => {
                  setForm(DEFAULT_FORM);
                }}
              >
                <RotateCcw className="h-3.5 w-3.5" />
                重置
              </Button>
              <Button disabled={exporting} type="submit">
                {exporting ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Download className="h-3.5 w-3.5" />
                )}
                {exporting ? '导出中' : '导出 xlsx'}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </section>
  );
}
