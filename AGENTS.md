# AGENTS.md

本文件是根目录级协作约定，适用于整个 `lark-msg-listener` 工作区。进入具体子项目后，还必须继续遵守对应子项目的约定：

- [`msg-listener/AGENTS.md`](./msg-listener/AGENTS.md)
- [`dashboard-monitor/AGENTS.md`](./dashboard-monitor/AGENTS.md)

## 工作区定位

本工作区包含两个独立但配套的项目：

| 路径 | 角色 | 技术栈 |
| --- | --- | --- |
| `msg-listener/` | 飞书消息实时监听、上下文聚合、需求分析、卡片交互、开发任务编排、本地只读 dashboard API | Python 3.11、Node.js 启动器、`uv`、`lark-oapi`、`httpx`、`loguru` |
| `dashboard-monitor/` | `msg-listener` 本地 API 的前端仪表盘 | React 18、TypeScript、Vite 5、pnpm、Tailwind CSS、shadcn/ui、TanStack Query/Table、ECharts |

跨项目关系：

- `msg-listener` 负责采集和写入 `out/**/record.json`、`out/index.sqlite`、`logs/` 等本地运行数据。
- `msg-listener/src/dashboard_api/main.py` 在 `[dashboard_api].enabled = true` 时暴露本地只读 API，默认监听 `127.0.0.1:4317`。
- `dashboard-monitor` 默认通过 Vite dev server 运行在 `127.0.0.1:4318`，并将 `/api` 代理到 `127.0.0.1:4317`。

## 通用协作规则

1. 先确认当前任务涉及哪个子项目和技术栈。
2. 修改前先读相关入口、配置和本级 / 子项目 `AGENTS.md`，以现有分层和命名为准。
3. 默认用中文沟通。
4. 可以按任务需要执行读取、搜索、lint、typecheck 等非 build 指令；如果用户只要求文档或分析，优先避免不必要命令。
5. 在 `./msg-listener/config.toml.default` 新增配置时，同步修改 `./msg-listener/config.toml`。
8. 保持改动范围小而明确。修复bug时，必须有注释写清楚为什么要按这版代码改。
9. 编辑 Markdown、代码和配置时使用项目已有格式。
10. 项目本地git工作区存在的改动对你的任务无影响，如果需要修改已有改动，直接修改，然后在回答中向用户说明。

## msg-listener 约定

进入 `msg-listener/` 后，以 [`msg-listener/AGENTS.md`](./msg-listener/AGENTS.md) 为细则。根目录补充以下跨项目关注点：

- 统一入口是 `package.json` 中的 `npm run bootstrap` 与 `npm run start`，实际进入 `scripts/launch.mjs`，再进入 `scripts/setup.py`。
- `scripts/launch.mjs` 负责 TLS CA、`uv`、受管 Python 3.11、`.venv` 与依赖同步；不要绕过它新增平台专用启动脚本。
- `src/main.py` 是 supervisor，负责按顺序启动 `src.bot.main` 和 `src.collector.main`，并处理 `cache/restart.request` 原地重启。
- `src/bot/main.py` 负责分析循环、卡片回调 worker、命令服务、开发任务编排和 `cache/bot.ready` 写入。
- `src/collector/main.py` 负责消息轮询、命中过滤、上下文聚合、媒体下载和 record 落盘。
- `src/dashboard_api/main.py` 是 dashboard API 的端点登记处；新增 / 删除 / 改名端点时必须同步前端类型、服务调用和文档。
- `src/dashboard_api` 是本地只读 API，不应引入写入业务状态的端点；导出接口允许即时生成 xlsx 字节流，但不应落盘。
- 运行配置以 `config.toml.default` 为模板，`config.toml` 是本地敏感配置，不应作为文档或代码变更来源。
- Python 依赖只在 `pyproject.toml` 中声明，版本锁由 `uv.lock` 管理。

## dashboard-monitor 约定

进入 `dashboard-monitor/` 后，以 [`dashboard-monitor/AGENTS.md`](./dashboard-monitor/AGENTS.md) 为细则。根目录补充以下跨项目关注点：

- 技术栈固定为 React 18 + TypeScript + Vite 5 + pnpm。
- UI 体系使用 shadcn/ui + Radix + Tailwind。必须保持UI体系的统一。
- 设计 token 在 `tailwind.config.ts` 与 `src/app/styles/globals.css` 维护，优先使用语义类，不硬编码色值。
- 路由元信息与侧边栏来源是 `src/app/navigation.ts`；页面路由表是 `src/app/router.tsx`。新增页面要同步这两处。
- API 客户端统一走 `src/services/api.ts`，共享响应类型放在 `src/types/`。
- 页面消费后端接口时优先用 TanStack Query；query key 复用或扩展 `src/services/queryKeys.ts`。
- 公共业务组件位于 `src/components/<Name>/` 并通过 `index.ts` 桶导出；不要在多个页面重复实现同类表格、抽屉、状态徽标、日期选择、JSON 预览等能力。

## API 契约同步

后端 API 与前端页面强绑定，变更时按以下顺序检查：

1. `msg-listener/src/dashboard_api/main.py`：端点登记是否变化。
2. `msg-listener/src/dashboard_api/handlers/`：响应结构、分页、错误码、二进制响应是否变化。
3. `dashboard-monitor/src/types/`：前端消费类型是否同步。
4. `dashboard-monitor/src/services/`：API path、query 参数、导出下载逻辑是否同步。
5. `dashboard-monitor/src/pages/`：页面筛选、表格列、详情抽屉和空态是否同步。
6. `README.md`、子项目 README / AGENTS：端点列表、页面能力、启动方式是否需要更新。

当前后端主端点包括：

- 基础：`GET /api/health`、`GET /api/config`
- 概览：`GET /api/overview`、`GET /api/timeseries`
- 业务：`GET /api/records`、`GET /api/records/:id`、`GET /api/records/:id/replay`、`GET /api/records/:id/media/*`、`GET /api/analysis-jobs`、`GET /api/demands`、`GET /api/demands/:id`、`GET /api/dev-tasks`、`GET /api/dev-tasks/:id`、`GET /api/repos`
- 监控：`GET /api/monitor/processes`、`GET /api/monitor/queues`、`GET /api/monitor/system`、`GET /api/logs`
- 报表：`GET /api/weekly-reports`、`POST /api/exports/xlsx`

## 文档维护

- 根目录 [`README.md`](./README.md) 只维护工作区级说明：两个子项目关系、启动顺序、跨项目数据流、常用命令与 API 总览。
- 子项目 README 维护各自项目的详细运行方式、目录结构和能力说明。
- 子项目 AGENTS 维护各自项目的具体工程约定。
- 修改启动链路、API 契约、目录职责、页面数量、运行产物或配置项时，必须同步检查相关文档。
- 不要把运行日志、真实 record、token 或本地绝对隐私路径写入文档示例。

## 排查优先级

遇到问题时按依赖顺序排查：

1. `msg-listener` 是否能完成 bootstrap / start 前置校验。
2. `cache/bot.ready` 是否存在，bot 是否完成卡片 worker、分析循环和欢迎语启动。
3. collector 是否在写入 `out/index.sqlite` 与 `out/**/record.json`。
4. `[dashboard_api].enabled` 是否为 `true`，`dashboard-api` 是否监听 `127.0.0.1:4317`。
5. `dashboard-monitor/vite.config.ts` 的 `/api` 代理是否指向同一端口。
6. 前端页面是否与后端返回结构保持一致。
