import { NavLink } from 'react-router-dom';
import { CheckCircle2, PanelLeftClose, PanelLeftOpen } from 'lucide-react';
import { navGroups } from '@/app/navigation';
import { Button } from '@/components/ui/button';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';

/**
 * 左侧导航：Logo + 名称、分组导航，支持 240px / 72px 展开折叠。
 * 选中态：主色浅背景 + 左侧 3px 指示条。
 */
interface SidebarProps {
  isCollapsed: boolean;
  onToggle: () => void;
}

export function Sidebar({ isCollapsed, onToggle }: SidebarProps) {
  return (
    <aside
      className={cn(
        'flex h-screen flex-col border-r border-border bg-card transition-[width] duration-200',
        isCollapsed ? 'w-[72px]' : 'w-nav',
      )}
    >
      {/* Logo + 名称 */}
      <div
        className={cn(
          'flex items-center gap-3 px-4 py-4',
          isCollapsed ? 'justify-center' : 'justify-start',
        )}
      >
        <div className="flex min-w-0 items-center gap-3">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary">
            <CheckCircle2 className="h-5 w-5 text-primary-foreground" />
          </div>
          <div className={cn('flex min-w-0 flex-col', isCollapsed && 'sr-only')}>
            <span className="truncate text-sm font-semibold text-foreground">工作量后台</span>
            <span className="truncate text-[11px] text-muted-foreground">dashboard-monitor</span>
          </div>
        </div>
      </div>

      {/* 分组导航 */}
      <nav className="flex-1 overflow-y-auto px-2 pb-4">
        {navGroups.map((group) => (
          <div key={group.title} className="mb-2">
            <div
              className={cn(
                'px-3 py-2 text-[11px] font-semibold tracking-wide text-muted-foreground/80',
                isCollapsed && 'sr-only',
              )}
            >
              {group.title}
            </div>
            <ul className="flex flex-col gap-0.5">
              {group.items.filter((item) => !item.hiddenInSidebar).map((item) => {
                const Icon = item.icon;
                const navLink = (
                  <NavLink
                    end
                    to={item.path}
                    className={({ isActive }) =>
                      cn(
                        'relative flex items-center gap-3 rounded-lg px-3 py-2.5 text-body transition-colors',
                        isCollapsed && 'justify-center px-0',
                        isActive
                          ? 'bg-primary/10 font-semibold text-primary'
                          : 'text-foreground hover:bg-accent',
                      )
                    }
                  >
                    {({ isActive }) => (
                      <>
                        {isActive ? (
                          <span className="absolute left-0 top-1/2 h-7 w-[3px] -translate-y-1/2 rounded-full bg-primary" />
                        ) : null}
                        <Icon className="h-5 w-5 shrink-0" />
                        <span className={cn(isCollapsed && 'sr-only')}>{item.label}</span>
                      </>
                    )}
                  </NavLink>
                );

                return (
                  <li key={item.path}>
                    {isCollapsed ? (
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <span className="block">{navLink}</span>
                        </TooltipTrigger>
                        <TooltipContent side="right" sideOffset={12}>
                          {item.label}
                        </TooltipContent>
                      </Tooltip>
                    ) : (
                      navLink
                    )}
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </nav>

      <div className="border-t border-border p-3">
        <Button
          aria-label={isCollapsed ? '展开侧边栏' : '折叠侧边栏'}
          className="w-full justify-center"
          size={isCollapsed ? 'icon' : 'sm'}
          type="button"
          variant="ghost"
          onClick={onToggle}
        >
          {isCollapsed ? (
            <PanelLeftOpen className="h-4 w-4" />
          ) : (
            <>
              <PanelLeftClose className="h-4 w-4" />
              收起侧边栏
            </>
          )}
        </Button>
      </div>
    </aside>
  );
}
