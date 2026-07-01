import type { ReactNode } from 'react';
import { Card } from '@/components/ui/card';
import { cn } from '@/lib/utils';

interface ChartCardProps {
  /** 图表标题 */
  title: string;
  /** 标题栏右侧操作区：维度切换 / 时间 / 全屏等 */
  actions?: ReactNode;
  /** 图表主体 */
  children?: ReactNode;
  className?: string;
}

/**
 * ChartCard 图表卡（骨架）：标题栏（48px）+ 图表区。
 * 图表渲染（ECharts）在各页面 Task 中接入。
 */
export function ChartCard({ title, actions, children, className }: ChartCardProps) {
  return (
    <Card className={cn('flex flex-col', className)}>
      <div className="flex h-12 items-center justify-between border-b border-border px-4">
        <h2 className="text-h2 text-foreground">{title}</h2>
        {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
      </div>
      <div className="min-h-[240px] p-4">{children}</div>
    </Card>
  );
}
