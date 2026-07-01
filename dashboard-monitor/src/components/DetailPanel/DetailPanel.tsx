import type { HTMLAttributes, ReactNode } from 'react';
import { Card } from '@/components/ui/card';
import { cn } from '@/lib/utils';

export function DetailStack({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('flex min-w-0 max-w-full flex-col gap-4', className)} {...props} />;
}

export function DetailGrid({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('grid min-w-0 max-w-full grid-cols-1 gap-3 sm:grid-cols-2', className)} {...props} />;
}

interface DetailFieldProps extends HTMLAttributes<HTMLDivElement> {
  label: ReactNode;
  value?: ReactNode;
  emptyText?: ReactNode;
  mono?: boolean;
  valueClassName?: string;
}

function isEmptyValue(value: ReactNode) {
  return value === undefined || value === null || value === '';
}

export function DetailField({
  label,
  value,
  emptyText = '--',
  mono = false,
  className,
  valueClassName,
  ...props
}: DetailFieldProps) {
  return (
    <Card className={cn('min-w-0 max-w-full p-3', className)} {...props}>
      <p className="text-caption font-medium text-muted-foreground">{label}</p>
      <div
        className={cn(
          'mt-1 min-w-0 whitespace-pre-wrap break-words text-sm leading-5 text-foreground',
          mono && 'font-mono text-code',
          valueClassName,
        )}
      >
        {isEmptyValue(value) ? emptyText : value}
      </div>
    </Card>
  );
}
