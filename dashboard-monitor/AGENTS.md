# AGENTS.md

本文件为 AI 代理与协作者提供 `dashboard-monitor` 的工程约定。

## 项目说明

本项目是[消息监听服务](../msg-listener)的数据仪表盘。独立于 `msg-listener` 的本地 Web 控制台，通过 `./src/services/api.ts` 与[消息监听服务](../msg-listener) API 交互。

## 技术栈与约束

- React 18 + TypeScript + Vite 5 + pnpm。
- UI 组件库：**shadcn/ui**（Radix + Tailwind），组件源码位于 `src/components/ui`，直接编辑即可。
- 样式**只用 Tailwind 一套来源**，禁止引入第二套样式系统（如 antd/CSS-in-JS 主题）。
- 设计规范语义 token（颜色/间距/圆角/字号）统一在 `tailwind.config.ts` + `src/app/styles/globals.css` 维护，**不要硬编码色值**，优先用语义类（`text-foreground`、`bg-card`、`text-success`、`bg-danger-soft` 等）。
- 依赖安装使用命令级 registry：`pnpm install --registry=https://registry.npmmirror.com`，不修改全局 npm/pnpm 配置。

## 目录约定

| 路径 | 职责 |
| --- | --- |
| `src/app/navigation.ts` | 导航分组、侧边栏可见性与页面路由元信息的**唯一来源**，新增/调整页面从这里改 |
| `src/app/router.tsx` | 路由表，与 navigation 一一对应 |
| `src/app/providers.tsx` | 全局 Provider（Query / Theme / Tooltip / Alert） |
| `src/components/ui/` | shadcn/ui 原子组件 |
| `src/components/AppShell/` | 全局框架：Sidebar（240px）+ Topbar（56px） |
| `src/components/<Name>/` | 公共业务组件，每个目录含实现文件 + `index.ts` 桶导出 |
| `src/pages/<Page>/index.tsx` | 页面入口，默认导出 React 组件，供路由懒加载 |
| `src/services/` | API 客户端、query key 与 Settings API hooks；页面级 Query 可在页面内就近维护 |
| `src/types/` | 与 `msg-listener` API 对齐的前端消费类型 |
| `src/lib/utils.ts` | `cn()` 等工具 |

## 公共组件能力

`MetricCard`、`ChartCard`、`DataTable`、`StatusBadge`、`DatePicker`、`DateRangePicker`、`JsonViewer`、`Timeline`、`Drawer`、`FilterBar`、`EmptyState`、`DetailPanel`、`Select`、`Alert`。

公共能力已覆盖指标展示、图表容器、表格排序/分页/加载/空态、状态徽标、日期与日期范围选择、JSON 格式化高亮、执行时间线、详情抽屉、筛选表单、全局反馈提示和详情字段布局。多页面复用，禁止各页面重复造同类组件。

## 编码约定

- 路径别名 `@/` 指向 `src/`。
- 组件使用具名导出 + 目录 `index.ts` 桶导出；页面文件用 `export default`（配合懒加载）。
- 导入类型使用 `import type`（ESLint 已开启 `consistent-type-imports`）。
- 新增/调整页面时，先更新 `src/app/navigation.ts`，再同步 `src/app/router.tsx`，并保持侧边栏可见性与面包屑一致。
- 新增 shadcn 组件：`pnpm dlx shadcn@latest add <component>`。
- 每次改动代码后，检查 `AGENTS.md` 与 `README.md` 是否因本次改动过时（如页面数量、路由、目录结构、技术栈、约定等），如已过时必须同步更新，保持文档与代码一致。

## 提交前自检

```bash
pnpm typecheck
pnpm lint
pnpm lint:style
```
