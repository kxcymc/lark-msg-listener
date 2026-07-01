import type { ReactNode } from 'react';

interface PagePlaceholderProps {
  title: string;
  description?: string;
  children?: ReactNode;
}

/**
 * 页面占位骨架：仅用于项目初始化阶段，展示页面标题与说明。
 * 各页面的具体模块（指标卡、表格、图表、抽屉等）在后续 Task 中实现。
 */
export function PagePlaceholder({ title, description, children }: PagePlaceholderProps) {
  return (
    <section className="flex flex-col gap-4">
      <header className="flex flex-col gap-1">
        <h1 className="text-h1 text-foreground">{title}</h1>
        {description ? (
          <p className="text-body text-muted-foreground">{description}</p>
        ) : null}
      </header>
      <div className="flex min-h-[320px] items-center justify-center rounded-xl border border-dashed border-border bg-card text-caption text-muted-foreground">
        页面骨架占位 · 内容待实现
      </div>
      {children}
    </section>
  );
}
