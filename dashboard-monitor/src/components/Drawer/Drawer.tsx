import type { ReactNode } from 'react';
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';
import { cn } from '@/lib/utils';

export interface DrawerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: ReactNode;
  description?: ReactNode;
  headerExtra?: ReactNode;
  /** 底部操作区 */
  footer?: ReactNode;
  children?: ReactNode;
  side?: 'left' | 'right';
  size?: 'sm' | 'md' | 'lg' | 'xl';
  className?: string;
  contentClassName?: string;
  headerClassName?: string;
  footerClassName?: string;
}

const drawerSizeClassName: Record<NonNullable<DrawerProps['size']>, string> = {
  sm: 'w-[420px]',
  md: 'w-drawer',
  lg: 'w-[720px]',
  xl: 'w-[920px]',
};

/**
 * Drawer 详情抽屉：侧向滑出，标题栏 + 可滚动内容 + 底部操作。
 */
export function Drawer({
  open,
  onOpenChange,
  title,
  description,
  headerExtra,
  footer,
  children,
  side = 'right',
  size = 'md',
  className,
  contentClassName,
  headerClassName,
  footerClassName,
}: DrawerProps) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        className={cn(
          'flex max-w-[calc(100vw-16px)] flex-col gap-0 p-0 sm:max-w-full',
          drawerSizeClassName[size],
          className,
        )}
        side={side}
      >
        <SheetHeader
          className={cn(
            'border-b border-border bg-card/95 px-6 py-5 pr-14 shadow-sm',
            headerClassName,
          )}
        >
          <div className="flex min-w-0 items-start justify-between gap-4">
            <div className="min-w-0">
              <SheetTitle className="break-words pr-1 text-h2">{title}</SheetTitle>
              {description ? (
                <SheetDescription className="mt-1 break-words leading-5">{description}</SheetDescription>
              ) : null}
            </div>
            {headerExtra ? <div className="shrink-0 pt-0.5">{headerExtra}</div> : null}
          </div>
        </SheetHeader>
        <div
          className={cn(
            'min-h-0 min-w-0 flex-1 overflow-y-auto overflow-x-hidden bg-background/60',
            contentClassName,
          )}
        >
          <div className="min-w-0 max-w-full overflow-hidden p-6">{children}</div>
        </div>
        {footer ? (
          <SheetFooter className={cn('border-t border-border bg-card/95 p-4 shadow-sm sm:p-6', footerClassName)}>
            {footer}
          </SheetFooter>
        ) : null}
      </SheetContent>
    </Sheet>
  );
}
