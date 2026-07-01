import { useEffect, useMemo, useState } from 'react';
import { Calendar, Check, ChevronDown } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { cn } from '@/lib/utils';

export interface DateRange {
  start: string;
  end: string;
}

export interface DateRangePreset {
  label: string;
  range: DateRange;
}

export interface DateRangePickerProps {
  value?: DateRange;
  onChange?: (range: DateRange) => void;
  presets?: DateRangePreset[];
  disabled?: boolean;
  className?: string;
}

function formatDate(date: Date) {
  const year = date.getFullYear();
  const month = `${date.getMonth() + 1}`.padStart(2, '0');
  const day = `${date.getDate()}`.padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function shiftDate(days: number) {
  const date = new Date();
  date.setDate(date.getDate() + days);
  return formatDate(date);
}

function createDefaultRange(): DateRange {
  return {
    start: shiftDate(-6),
    end: shiftDate(0),
  };
}

function createDefaultPresets(): DateRangePreset[] {
  return [
    { label: '近 7 天', range: { start: shiftDate(-6), end: shiftDate(0) } },
    { label: '近 14 天', range: { start: shiftDate(-13), end: shiftDate(0) } },
    { label: '近 30 天', range: { start: shiftDate(-29), end: shiftDate(0) } },
  ];
}

/**
 * DateRangePicker 日期范围：快捷项 + 双日期输入，适合作为全局时间窗口筛选。
 */
export function DateRangePicker({
  value,
  onChange,
  presets,
  disabled,
  className,
}: DateRangePickerProps) {
  const fallbackRange = useMemo(() => createDefaultRange(), []);
  const presetOptions = useMemo(() => presets ?? createDefaultPresets(), [presets]);
  const [range, setRange] = useState<DateRange>(value ?? fallbackRange);

  useEffect(() => {
    if (value) {
      setRange(value);
    }
  }, [value]);

  const activePreset = presetOptions.find(
    (preset) => preset.range.start === range.start && preset.range.end === range.end,
  );

  const updateRange = (nextRange: DateRange) => {
    setRange(nextRange);
    onChange?.(nextRange);
  };

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button
          className={cn('gap-2 bg-card font-normal shadow-sm', className)}
          disabled={disabled}
          size="sm"
          variant="outline"
        >
          <Calendar className="h-3.5 w-3.5 text-muted-foreground" />
          <span className="tabular-nums">
            {range.start} ~ {range.end}
          </span>
          <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="center" className="w-[360px] p-0">
        <div className="border-b border-border px-4 py-3">
          <div className="text-body font-medium text-foreground">选择时间范围</div>
          <div className="text-caption text-muted-foreground">
            {activePreset?.label ?? '自定义日期范围'}
          </div>
        </div>
        <div className="grid gap-4 p-4">
          <div className="grid grid-cols-3 gap-2">
            {presetOptions.map((preset) => {
              const active =
                preset.range.start === range.start && preset.range.end === range.end;
              return (
                <Button
                  key={preset.label}
                  className={cn(active && 'border-primary bg-primary/10 text-primary')}
                  size="sm"
                  type="button"
                  variant="outline"
                  onClick={() => updateRange(preset.range)}
                >
                  {active ? <Check className="h-3.5 w-3.5" /> : null}
                  {preset.label}
                </Button>
              );
            })}
          </div>
          <div className="grid grid-cols-2 gap-3">
            <label className="grid gap-1.5 text-caption font-medium text-muted-foreground">
              开始日期
              <input
                className="h-9 rounded-md border border-input bg-background px-3 text-body text-foreground outline-none transition-colors focus:border-ring focus:ring-2 focus:ring-ring/20"
                max={range.end}
                type="date"
                value={range.start}
                onChange={(event) =>
                  updateRange({ start: event.target.value, end: range.end })
                }
              />
            </label>
            <label className="grid gap-1.5 text-caption font-medium text-muted-foreground">
              结束日期
              <input
                className="h-9 rounded-md border border-input bg-background px-3 text-body text-foreground outline-none transition-colors focus:border-ring focus:ring-2 focus:ring-ring/20"
                min={range.start}
                type="date"
                value={range.end}
                onChange={(event) =>
                  updateRange({ start: range.start, end: event.target.value })
                }
              />
            </label>
          </div>
        </div>
      </PopoverContent>
    </Popover>
  );
}
