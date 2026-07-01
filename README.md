# lark-msg-listener 工作区

本目录是一个双项目工作区，包含飞书消息监听与分析服务 `msg-listener`，以及读取该服务本地只读 API 的前端仪表盘 `dashboard-monitor`。

两个子项目彼此独立运行，但数据链路是配套的：

1. `msg-listener` 以 user / bot 身份采集、分析飞书消息，并把 record、分析任务、需求、开发任务、仓库扫描等运行数据写入本地 `out/` 与 `out/index.sqlite`。
2. `msg-listener` 可按配置额外启动 `dashboard_api`，在本地 `127.0.0.1:4317` 暴露只读 HTTP API。
3. `dashboard-monitor` 是 React 前端，开发服务默认运行在 `127.0.0.1:4318`，通过 `/api` 代理访问 `msg-listener` 的本地 API。

## 项目结构

| 路径 | 定位 | 技术栈 | 主要入口 |
| --- | --- | --- | --- |
| [`msg-listener`](./msg-listener) | 飞书消息实时监听、上下文聚合、需求分析、卡片交互、开发任务编排、本地 dashboard API | Python 3.11、Node.js 启动器、`uv`、`lark-oapi`、`httpx`、`loguru` | `npm run bootstrap`、`npm run start` |
| [`dashboard-monitor`](./dashboard-monitor) | `msg-listener` 的本地 Web 仪表盘 | React 18、TypeScript、Vite 5、pnpm、Tailwind CSS、shadcn/ui、TanStack Query/Table、ECharts | `pnpm dev` |

## 数据与进程关系

```text
飞书消息 / 卡片回调 / Bot 单聊命令
        │
        ▼
msg-listener/scripts/launch.mjs
        │  准备 uv、受管 Python、.venv、依赖与 TLS CA
        ▼
msg-listener/scripts/setup.py
        │  bootstrap/run 前置检查，按需启动 dashboard_api
        ▼
msg-listener/src/main.py
        │  supervisor
        ├─ bot:       src/bot/main.py
        │             分析循环、卡片回调 worker、命令服务、开发任务编排
        ├─ collector: src/collector/main.py
        │             轮询 @我群聊 / 可选私聊、聚合上下文、下载媒体、写 record
        └─ mywork:    src/mywork/main.py
                      周五 17:30 一次性工作总结任务

msg-listener/out/index.sqlite + out/**/record.json
        │
        ▼
msg-listener/src/dashboard_api/main.py
        │  只读 API，默认端口 4317
        ▼
dashboard-monitor
        │  Vite dev server，默认端口 4318，/api 代理到 4317
        ▼
浏览器仪表盘
```

## 环境要求

- Node.js `>= 18`，并包含 `npm` 与 `npx`。
- `msg-listener` 首次启动会自动准备 `uv`、受管 Python 3.11、项目 `.venv` 与 Python 依赖，默认不要求预装 Python。
- `dashboard-monitor` 使用 `pnpm` 管理依赖。
- 飞书相关能力依赖 `lark-cli`、飞书应用配置、用户授权、bot 权限、事件订阅与必要的人工确认项；这些由 `msg-listener` 的 bootstrap 流程引导。

## 启动方式

### 1. 启动消息监听服务

```bash
cd msg-listener
npm run bootstrap
```

后续快速启动：

```bash
cd msg-listener
npm run start
```

`bootstrap` 会创建 `config.toml`、检查飞书 CLI / 权限 / 事件订阅、同步 Python 依赖，并启动 supervisor。`start` 只做快速校验后启动服务。

### 2. 开启 dashboard API

前端仪表盘需要 `msg-listener` 暴露本地只读 API。确认 `msg-listener/config.toml` 中：

```toml
[dashboard_api]
enabled = true
host = "127.0.0.1"
port = 4317
```

该 API 由 `scripts/setup.py` 在 bot 写入 `cache/bot.ready` 后按配置拉起；bot 重启或未就绪时，dashboard API 会被停止或等待重新拉起。

### 3. 启动前端仪表盘

```bash
cd dashboard-monitor
pnpm install --registry=https://registry.npmmirror.com
pnpm dev
```

默认访问地址为 `http://127.0.0.1:4318`。前端的 `/api` 会在 Vite 开发服务中代理到 `http://127.0.0.1:4317`。

## msg-listener 概览

`msg-listener` 的详细说明见 [`msg-listener/README.md`](./msg-listener/README.md) 与 [`msg-listener/AGENTS.md`](./msg-listener/AGENTS.md)。

核心能力：

- 消息采集：轮询群聊 @我消息和可选私聊消息，按会话补齐前后文。
- 去重与索引：通过 `out/index.sqlite` 记录 seen messages、records、analysis jobs、demands、dev tasks、repo inventory 等数据。
- 媒体处理：下载图片 / 视频等素材到对应 record 目录。
- 异步分析：bot 进程扫描 pending record，调用配置的分析 CLI 判断是否命中需求。
- 卡片与命令：通过飞书 bot 推送交互卡片，并支持 `/config`、`/del-out` 等单聊命令。
- 开发任务编排：把命中需求派发给配置的执行器，例如 `kxcymc_openapi` 或 `trae_side_chat`。
- 我的工作总结：周五 17:30 自动运行 `mywork`，生成本地 JSON 与飞书云文档结果。
- 本地仪表盘 API：在 `[dashboard_api].enabled = true` 时提供只读查询、日志、监控、回放、导出接口。

主要运行产物：

| 路径 | 内容 |
| --- | --- |
| `msg-listener/out/` | record、媒体、`index.sqlite`、mywork 产物 |
| `msg-listener/cache/` | owner 缓存、`bot.ready`、`restart.request` 等运行标记 |
| `msg-listener/logs/` | main / launcher / bot / collector / dashboard-api 等日志 |
| `msg-listener/.venv/` | 项目本地 Python 运行环境 |
| `msg-listener/.local/` | 启动器、uv、lark-cli 缓存和本地证书资源 |
| `msg-listener/config.toml` | 本地运行配置，可能包含 token，不应提交或外传 |

## dashboard-monitor 概览

`dashboard-monitor` 的详细说明见 [`dashboard-monitor/README.md`](./dashboard-monitor/README.md) 与 [`dashboard-monitor/AGENTS.md`](./dashboard-monitor/AGENTS.md)。

前端当前包含 4 个导航分组和 9 个侧边栏入口：

| 分组 | 页面 |
| --- | --- |
| 概览 | 总览、实时监控台 |
| 消息记录 | 记录列表；会话回放为隐藏子页面 |
| 研发流程 | 需求、开发任务、仓库 |
| 报表与设置 | 周报链接、报表导出、设置 |

前端消费的主要后端路径包括：

- `GET /api/health`
- `GET /api/config`
- `GET /api/overview`
- `GET /api/timeseries`
- `GET /api/records`
- `GET /api/records/:id`
- `GET /api/records/:id/replay`
- `GET /api/records/:id/media/*`
- `GET /api/analysis-jobs`
- `GET /api/demands`
- `GET /api/demands/:id`
- `GET /api/dev-tasks`
- `GET /api/dev-tasks/:id`
- `GET /api/repos`
- `GET /api/monitor/processes`
- `GET /api/monitor/queues`
- `GET /api/monitor/system`
- `GET /api/logs`
- `GET /api/weekly-reports`
- `POST /api/exports/xlsx`

## 常用命令

| 目录 | 命令 | 用途 |
| --- | --- | --- |
| `msg-listener` | `npm run bootstrap` | 首次初始化并启动服务 |
| `msg-listener` | `npm run start` | 后续快速启动服务 |
| `msg-listener` | `python -m src.mywork.main` | 手动运行一次 mywork 总结任务 |
| `dashboard-monitor` | `pnpm install --registry=https://registry.npmmirror.com` | 安装前端依赖 |
| `dashboard-monitor` | `pnpm dev` | 启动 Vite 开发服务 |
| `dashboard-monitor` | `pnpm typecheck` | TypeScript 类型检查 |
| `dashboard-monitor` | `pnpm lint` | ESLint 检查 |
| `dashboard-monitor` | `pnpm lint:style` | Stylelint 检查 |


## 文档与维护约定

- 根目录 README 只描述工作区级关系、启动顺序、跨项目数据流与协作入口。
- 子项目内部细节以各自的 README / AGENTS 为准。
- 修改 API 契约时，需要同步更新 `msg-listener/src/dashboard_api/main.py`、`dashboard-monitor/src/types/`、`dashboard-monitor/src/services/` 和相关页面说明。
- 修改前端页面或导航时，需要同步检查 `dashboard-monitor/src/app/navigation.ts`、`dashboard-monitor/src/app/router.tsx` 与文档。
- 修改运行目录、配置项或启动链路时，需要同步检查 `msg-listener/config.toml.default`、`msg-listener/README.md`、`msg-listener/AGENTS.md` 与本文档。
