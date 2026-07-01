import { useMemo, useState, type ReactNode } from 'react';
import { Check, ChevronDown } from 'lucide-react';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { cn } from '@/lib/utils';

export interface SelectOption {
  value: string;
  /** 主文本 */
  label: ReactNode;
  /** 次文本（如长 recordId、状态原始值），展示在主文本下方并弱化 */
  hint?: ReactNode;
  disabled?: boolean;
}

export interface SelectProps {
  value?: string;
  onValueChange?: (value: string) => void;
  options: SelectOption[];
  placeholder?: string;
  disabled?: boolean;
  /** 触发器额外类名 */
  className?: string;
  /** 下拉面板宽度类名，缺省与触发器同宽 */
  contentClassName?: string;
  /** 无障碍标签 */
  'aria-label'?: string;
  align?: 'start' | 'center' | 'end';
}

/**
 * Select 设计化下拉：基于 Radix Popover，统一替换原生 <select>。
 * 与 Button / Input / Popover 一致的圆角、边框、hover、focus-visible 与暗色模式表现。
 * 支持长选项两行展示（label + hint）。
 */
export function Select({
  value,
  onValueChange,
  options,
  placeholder = '请选择',
  disabled,
  className,
  contentClassName,
  align = 'start',
  ...rest
}: SelectProps) {
  const [open, setOpen] = useState(false);
  const selected = useMemo(
    () => options.find((option) => option.value === value),
    [options, value],
  );

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          aria-label={rest['aria-label']}
          className={cn(
            'flex h-9 w-full items-center justify-between gap-2 rounded-md border border-input bg-background px-3 text-sm text-foreground shadow-sm outline-none transition-colors',
            'hover:bg-accent/40 focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50',
            'data-[state=open]:ring-2 data-[state=open]:ring-ring',
            className,
          )}
          disabled={disabled}
          type="button"
        >
          <span className={cn('truncate text-left', !selected && 'text-muted-foreground')}>
            {selected ? selected.label : placeholder}
          </span>
          <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" />
        </button>
      </PopoverTrigger>
      <PopoverContent
        align={align}
        className={cn('w-[var(--radix-popover-trigger-width)] p-1', contentClassName)}
        sideOffset={6}
      >
        <div className="max-h-[280px] overflow-y-auto overscroll-contain">
          <div className="flex flex-col">
            {options.map((option) => {
              const active = option.value === value;
              return (
                <button
                  key={option.value}
                  className={cn(
                    'flex items-start justify-between gap-2 rounded-md px-2.5 py-2 text-left text-sm outline-none transition-colors',
                    'hover:bg-accent focus-visible:bg-accent disabled:cursor-not-allowed disabled:opacity-50',
                    active && 'bg-accent/60',
                  )}
                  disabled={option.disabled}
                  type="button"
                  onClick={() => {
                    onValueChange?.(option.value);
                    setOpen(false);
                  }}
                >
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-foreground">{option.label}</span>
                    {option.hint ? (
                      <span className="mt-0.5 block truncate font-mono text-caption text-muted-foreground">
                        {option.hint}
                      </span>
                    ) : null}
                  </span>
                  {active ? <Check className="mt-0.5 h-4 w-4 shrink-0 text-primary" /> : null}
                </button>
              );
            })}
          </div>
        </div>
      </PopoverContent>
    </Popover>
  );
}
