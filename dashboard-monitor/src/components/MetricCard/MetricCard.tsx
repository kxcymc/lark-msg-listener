import type { ReactNode } from 'react';
import { Card } from '@/components/ui/card';
import { cn } from '@/lib/utils';

interface MetricCardProps {
  /** 指标标题 */
  title: string;
  /** 核心数字 */
  value?: ReactNode;
  /** 同比/环比方向标，如 "+12.4%" */
  delta?: ReactNode;
  /** 方向：上升/下降/持平，决定语义色 */
  trend?: 'up' | 'down' | 'flat';
  className?: string;
}

/**
 * MetricCard 指标卡（骨架）：标题 + 大数字 + 方向标，固定高度 120px。
 * 迷你趋势图等在后续 Task 接入。
 */
export function MetricCard({ title, value, delta, trend = 'flat', className }: MetricCardProps) {
  const deltaColor =
    trend === 'up' ? 'text-success' : trend === 'down' ? 'text-danger' : 'text-muted-foreground';
  return (
    <Card className={cn('flex h-[120px] flex-col justify-between p-4', className)}>
      <span className="text-[13px] text-muted-foreground">{title}</span>
      <span className="text-metric text-foreground">{value ?? '--'}</span>
      {delta ? <span className={cn('text-caption', deltaColor)}>{delta}</span> : null}
    </Card>
  );
}
