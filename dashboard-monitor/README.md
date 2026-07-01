# dashboard-monitor

本项目是[消息监听服务](../msg-listener)的数据仪表盘。独立于 `msg-listener` 的本地 Web 控制台，通过 `./src/services/api.ts` 与[消息监听服务](../msg-listener) API 交互。

## 技术栈

| 分类 | 选型 |
| --- | --- |
| 框架 | React 18 + TypeScript |
| 构建 | Vite 5 |
| 包管理 | pnpm |
| UI 组件库 | shadcn/ui（基于 Radix UI + Tailwind，组件源码内置于 `src/components/ui`） |
| 样式 | Tailwind CSS 3，语义 token 写入 `tailwind.config.ts` |
| 路由 | React Router v6 |
| 数据请求 | TanStack Query |
| 表格 | TanStack Table |
| 状态 | Zustand |
| 图表 | ECharts |

## 目录结构

```text
dashboard-monitor/
├── index.html
├── package.json
├── vite.config.ts
├── tailwind.config.ts          # 设计规范语义 token 唯一来源
├── postcss.config.cjs
├── components.json             # shadcn/ui CLI 配置
├── tsconfig.json
├── .eslintrc.cjs / .stylelintrc.cjs / .prettierrc.json
└── src/
    ├── main.tsx
    ├── app/
    │   ├── navigation.ts       # 4 个分组 + 路由元信息/侧边栏可见性的唯一来源
    │   ├── router.tsx          # 路由表（懒加载页面，含隐藏子页面）
    │   ├── providers.tsx       # Query / Theme / Tooltip / Alert 全局 Provider
    │   ├── ThemeProvider.tsx   # 浅/深色主题 Provider
    │   ├── theme.ts            # 主题 context 与系统主题探测
    │   ├── GlobalError.tsx     # 全局错误页
    │   └── styles/globals.css  # Tailwind + shadcn CSS 变量
    ├── components/
    │   ├── ui/                 # shadcn/ui 原子组件
    │   ├── AppShell/           # 全局框架：Sidebar + Topbar
    │   └── ...                 # 公共业务组件
    ├── lib/utils.ts            # cn()
    ├── services/               # API 客户端、query key 与 Settings API hooks
    ├── types/                  # API 响应与共享消费类型
    └── pages/                  # 页面入口，均已接入对应 API 与页面交互
```

## 本地开发

```bash
# 安装依赖（仅影响本项目，使用项目级 registry）
pnpm install --registry=https://registry.npmmirror.com

# 启动前端 dev server（默认 http://127.0.0.1:4318）
pnpm dev
```

> 前端通过 `/api` 代理到本地后台服务（默认 `http://127.0.0.1:4317`，见 `vite.config.ts`）。后台服务为独立项目，本仓库只包含 dashboard-monitor 前端应用。

## API 基础层

`src/services/api.ts` 提供 `/api` 前缀、查询参数、JSON 响应读取和错误处理封装；`src/services/settings.ts` 封装 `/health` 与 `/config` 的 TanStack Query hooks。页面直接基于 TanStack Query 消费 `msg-listener` 本地 API 返回的数据，响应共享类型集中在 `src/types/`。

当前页面接入的主要后端路径包括：`/overview`、`/timeseries`、`/monitor/processes`、`/logs`、`/records`、`/records/:recordId`、`/records/:recordId/media/:path`、`/analysis-jobs`、`/demands`、`/demands/:demandId`、`/dev-tasks`、`/dev-tasks/:taskId`、`/repos`、`/weekly-reports`、`/health`、`/config`、`/exports/xlsx`；前端请求统一经 `/api` 代理，导出页直接请求 `POST /api/exports/xlsx` 下载文件。

## 公共组件

公共业务组件位于 `src/components/<Name>/`，均通过目录 `index.ts` 桶导出。当前已沉淀 `MetricCard`、`ChartCard`、`DataTable`、`StatusBadge`、`DatePicker`、`DateRangePicker`、`JsonViewer`、`Timeline`、`Drawer`、`FilterBar`、`EmptyState`、`DetailPanel`、`Select`、`Alert` 等组件；shadcn/ui 原子组件位于 `src/components/ui/`。

## 脚本

| 命令 | 用途 |
| --- | --- |
| `pnpm dev` | 启动 Vite 开发服务 |
| `pnpm typecheck` | TypeScript 类型检查 |
| `pnpm lint` | ESLint 检查 |
| `pnpm lint:fix` | ESLint 自动修复 |
| `pnpm lint:style` | Stylelint 检查样式 |
| `pnpm format` | Prettier 格式化 |

## 新增 shadcn/ui 组件

```bash
pnpm dlx shadcn@latest add <component>
```

组件会写入 `src/components/ui`，颜色自动复用 `globals.css` 中的 CSS 变量，无需额外配置。

## 页面与路由

4 个导航分组共 9 个侧边栏入口，路由表见 [src/app/navigation.ts](src/app/navigation.ts)：概览（总览 / 实时监控台）、消息记录（记录列表）、研发流程（需求 / 开发任务 / 仓库）、报表与设置（周报链接 / 报表导出 / 设置）。会话回放是 `/records/replay` 隐藏子页面，仅从记录详情的“打开 IM 回放”进入，并要求携带合法 `recordId` 查询参数。

| 页面 | 路由 | 能力 |
| --- | --- | --- |
| 总览 | `/overview` | 核心指标、需求分布、趋势图、近期任务与近期消息记录 |
| 实时监控台 | `/realtime` | 进程状态、日志服务切换、关键字过滤和手动刷新 |
| 记录列表 | `/records` | 会话/时间/发送人/关键词筛选、记录表格、详情抽屉、关联分析与 IM 回放入口 |
| 会话回放 | `/records/replay?recordId=...` | 消息流回放、媒体预览、链接/原始 JSON 查看 |
| 需求 | `/demands` | 需求筛选、状态分布、明细抽屉与原始 JSON 查看 |
| 开发任务 | `/dev-tasks` | 任务筛选、状态统计、MR/提交信息、执行时间线与明细抽屉 |
| 仓库 | `/repos` | 仓库清单、工作量排行、远端/分支/关联需求任务详情 |
| 周报链接 | `/weekly-reports` | 周报筛选、链接打开、生成状态和明细抽屉 |
| 报表导出 | `/reports-export` | 按报表类型与日期范围导出 xlsx |
| 设置 | `/settings` | `/api/health` 检查项与 `/api/config` 只读配置展示 |
