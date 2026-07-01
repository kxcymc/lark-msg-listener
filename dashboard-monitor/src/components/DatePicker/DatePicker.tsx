import { useMemo, useState } from 'react';
import { CalendarDays, ChevronLeft, ChevronRight, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { cn } from '@/lib/utils';

export interface DatePickerProps {
  /** 受控值，格式 YYYY-MM-DD，空字符串表示未选择 */
  value?: string;
  onChange?: (value: string) => void;
  placeholder?: string;
  disabled?: boolean;
  /** 禁止选择今天之后的日期（历史数据筛选场景） */
  disableFuture?: boolean;
  /** 可选最小日期 YYYY-MM-DD（含） */
  min?: string;
  /** 可选最大日期 YYYY-MM-DD（含） */
  max?: string;
  className?: string;
  'aria-label'?: string;
}

const WEEK_LABELS = ['日', '一', '二', '三', '四', '五', '六'];

function pad(value: number) {
  return `${value}`.padStart(2, '0');
}

function toKey(year: number, month: number, day: number) {
  return `${year}-${pad(month + 1)}-${pad(day)}`;
}

function parseKey(value?: string) {
  if (!value) return undefined;
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) return undefined;
  const year = Number(match[1]);
  const month = Number(match[2]) - 1;
  const day = Number(match[3]);
  const date = new Date(year, month, day);
  if (Number.isNaN(date.getTime())) return undefined;
  return { year, month, day };
}

function todayKey() {
  const now = new Date();
  return toKey(now.getFullYear(), now.getMonth(), now.getDate());
}

/**
 * DatePicker 设计化日期选择：Popover + 日历网格，纯 Tailwind 语义类。
 * 通过 disableFuture / min / max 直接禁用不可选日期（按钮 disabled，无法点选）。
 */
export function DatePicker({
  value,
  onChange,
  placeholder = '选择日期',
  disabled,
  disableFuture,
  min,
  max,
  className,
  ...rest
}: DatePickerProps) {
  const [open, setOpen] = useState(false);
  const parsed = useMemo(() => parseKey(value), [value]);
  const today = useMemo(() => todayKey(), []);
  const effectiveMax = disableFuture ? (max && max < today ? max : today) : max;

  const [view, setView] = useState(() => {
    const base = parsed ?? parseKey(today)!;
    return { year: base.year, month: base.month };
  });

  const cells = useMemo(() => {
    const firstDay = new Date(view.year, view.month, 1).getDay();
    const daysInMonth = new Date(view.year, view.month + 1, 0).getDate();
    const items: Array<{ key: string; day: number } | null> = [];
    for (let i = 0; i < firstDay; i += 1) items.push(null);
    for (let day = 1; day <= daysInMonth; day += 1) {
      items.push({ key: toKey(view.year, view.month, day), day });
    }
    return items;
  }, [view]);

  const isDisabled = (key: string) => {
    if (min && key < min) return true;
    if (effectiveMax && key > effectiveMax) return true;
    return false;
  };

  const changeMonth = (delta: number) => {
    setView((prev) => {
      const next = new Date(prev.year, prev.month + delta, 1);
      return { year: next.getFullYear(), month: next.getMonth() };
    });
  };

  const handleSelect = (key: string) => {
    if (isDisabled(key)) return;
    onChange?.(key);
    setOpen(false);
  };

  const handleOpenChange = (next: boolean) => {
    if (next) {
      const base = parsed ?? parseKey(today)!;
      setView({ year: base.year, month: base.month });
    }
    setOpen(next);
  };

  return (
    <Popover open={open} onOpenChange={handleOpenChange}>
      <PopoverTrigger asChild>
        <button
          aria-label={rest['aria-label']}
          className={cn(
            'flex h-9 w-full items-center justify-between gap-2 rounded-md border border-input bg-background px-3 text-sm shadow-sm outline-none transition-colors',
            'hover:bg-accent/40 focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50',
            'data-[state=open]:ring-2 data-[state=open]:ring-ring',
            className,
          )}
          disabled={disabled}
          type="button"
        >
          <span className={cn('truncate text-left', !value && 'text-muted-foreground')}>
            {value || placeholder}
          </span>
          <span className="flex shrink-0 items-center gap-1">
            {value ? (
              <span
                aria-label="清除日期"
                className="rounded-sm text-muted-foreground hover:text-foreground"
                role="button"
                tabIndex={-1}
                onClick={(event) => {
                  event.stopPropagation();
                  onChange?.('');
                }}
              >
                <X className="h-3.5 w-3.5" />
              </span>
            ) : null}
            <CalendarDays className="h-4 w-4 text-muted-foreground" />
          </span>
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-[280px] p-3" sideOffset={6}>
        <div className="mb-2 flex items-center justify-between">
          <Button
            aria-label="上个月"
            className="h-7 w-7"
            size="icon"
            type="button"
            variant="ghost"
            onClick={() => changeMonth(-1)}
          >
            <ChevronLeft className="h-4 w-4" />
          </Button>
          <span className="text-sm font-medium text-foreground tabular-nums">
            {view.year} 年 {pad(view.month + 1)} 月
          </span>
          <Button
            aria-label="下个月"
            className="h-7 w-7"
            size="icon"
            type="button"
            variant="ghost"
            onClick={() => changeMonth(1)}
          >
            <ChevronRight className="h-4 w-4" />
          </Button>
        </div>
        <div className="grid grid-cols-7 gap-1 text-center">
          {WEEK_LABELS.map((label) => (
            <span key={label} className="py-1 text-caption text-muted-foreground">
              {label}
            </span>
          ))}
          {cells.map((cell, index) => {
            if (!cell) return <span key={`empty-${index}`} />;
            const selected = cell.key === value;
            const isToday = cell.key === today;
            const cellDisabled = isDisabled(cell.key);
            return (
              <button
                key={cell.key}
                className={cn(
                  'flex h-8 items-center justify-center rounded-md text-sm tabular-nums outline-none transition-colors',
                  'hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring',
                  'disabled:cursor-not-allowed disabled:text-muted-foreground/40 disabled:hover:bg-transparent',
                  selected && 'bg-primary text-primary-foreground hover:bg-primary/90',
                  !selected && isToday && 'border border-primary/40 text-primary',
                )}
                disabled={cellDisabled}
                type="button"
                onClick={() => handleSelect(cell.key)}
              >
                {cell.day}
              </button>
            );
          })}
        </div>
      </PopoverContent>
    </Popover>
  );
}
