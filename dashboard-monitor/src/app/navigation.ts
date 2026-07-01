import type { LucideIcon } from 'lucide-react';
import {
  LayoutDashboard,
  Activity,
  ListTree,
  PlayCircle,
  FileText,
  CheckSquare,
  GitBranch,
  Link2,
  Download,
  Settings,
} from 'lucide-react';

export interface NavItem {
  /** 路由路径（相对于根） */
  path: string;
  /** 导航与页面标题 */
  label: string;
  /** 导航图标 */
  icon: LucideIcon;
  /** 是否只参与路由/面包屑，不显示在左侧导航 */
  hiddenInSidebar?: boolean;
}

export interface NavGroup {
  /** 分组标题 */
  title: string;
  items: NavItem[];
}

/**
 * 左侧导航分组、页面路由元信息与隐藏子页面配置的唯一来源。
 * 同时驱动 AppShell 侧边栏与顶部面包屑，保证两者一致。
 * 分组顺序对齐设计稿 00-app-shell-light.svg。
 */
export const navGroups: NavGroup[] = [
  {
    title: '概览',
    items: [
      { path: '/overview', label: '总览', icon: LayoutDashboard },
      { path: '/realtime', label: '实时监控台', icon: Activity },
    ],
  },
  {
    title: '消息记录',
    items: [
      { path: '/records', label: '记录列表', icon: ListTree },
      {
        path: '/records/replay',
        label: '会话回放',
        icon: PlayCircle,
        hiddenInSidebar: true,
      },
    ],
  },
  {
    title: '研发流程',
    items: [
      { path: '/demands', label: '需求', icon: FileText },
      { path: '/dev-tasks', label: '开发任务', icon: CheckSquare },
      { path: '/repos', label: '仓库', icon: GitBranch },
    ],
  },
  {
    title: '报表与设置',
    items: [
      { path: '/weekly-reports', label: '周报链接', icon: Link2 },
      { path: '/reports-export', label: '报表导出', icon: Download },
      { path: '/settings', label: '设置', icon: Settings },
    ],
  },
];

/** 扁平化的全部导航项，供路由表与面包屑查询使用。 */
export const navItems: NavItem[] = navGroups.flatMap((group) => group.items);
