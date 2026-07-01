# AGENTS.md

## 项目概览

- 名称：`msg-listener`
- 定位：飞书消息实时监听服务。以 user 身份轮询「@我」群聊消息与私聊消息，聚合上下文落盘，再由 bot 进程异步分析、推送交互卡片，并可将命中需求编排成开发任务交给执行器执行。
- 进程模型：统一启动入口 [src/main.py](./src/main.py) 作为 supervisor，依次拉起两个子进程：
  - **bot**（[src/bot/main.py](./src/bot/main.py)）：分析循环 + 卡片/命令 WebSocket worker + 开发任务编排。先启动并等待 `cache/bot.ready` 写入。
  - **collector**（[src/collector/main.py](./src/collector/main.py)）：消息轮询与上下文采集。在 bot 就绪后再启动。
  - supervisor 监听 `cache/restart.request` 实现原地重启（`/config` 写回后触发），并在父进程变更或任一子进程退出时整体停服。
- 主要技术栈：
  - Python ≥ 3.11（业务实现，位于 [src/](./src)）
  - Node.js（统一启动器与环境装配，位于 [scripts/](./scripts)）
  - `uv` + 受管 Python 3.11（运行时由启动器自动安装与管理）
  - 核心依赖：`lark-oapi`（飞书 SDK）、`httpx`、`cryptography`、`loguru`

## 平台与运行时声明

1. **跨平台支持**：本项目在 **Windows、macOS、Linux** 三端通用，所有用户入口保持一致，平台差异由 Node 启动器与受管 Python 运行环境内部处理；新增功能与脚本必须保证三端可运行。
2. **Node 版本要求**：**Node.js 版本必须 `>= 18`**（需包含 `npm` 与 `npx`）。低于 18 的环境不在支持范围内，相关 API、ESM 行为均以 Node 18+ 为基线。

## 目录结构

```
msg-listener/
├── package.json              # 统一入口脚本 bootstrap / start
├── pyproject.toml            # Python 项目元数据与依赖（setuptools 构建）
├── uv.lock                   # uv 锁定文件
├── config.toml.default       # 配置模板；首启复制为 config.toml（不入库）
├── README.md
├── AGENTS.md
├── .gitignore                # 忽略 out/ cache/ .local/ logs/ .venv/ config.toml 等
├── scripts/
│   ├── launch.mjs            # Node 启动器：安装 uv、受管 Python、创建 .venv 与依赖、编排授权
│   └── setup.py              # Python 端初始化脚本
└── src/
    ├── __init__.py
    ├── main.py               # supervisor：编排 bot+collector，处理重启/退出/进程清理
    ├── bot/                  # Bot 核心逻辑（分析、卡片、命令、开发任务编排）
    │   ├── __init__.py
    │   ├── main.py           # bot 进程入口：装配分析循环、卡片 worker、命令服务、开发任务编排
    │   ├── notifier.py       # BotNotifier：以 bot 身份向 owner 发送消息/卡片
    │   ├── trae_side_chat.py # 唤起 Trae 侧聊投递 prompt
    │   ├── analysis_cli_registry.py  # 需求判断 CLI 注册表（解析/预检/预热/构建所选分析器）
    │   ├── analysis_cli_types.py     # 分析 CLI 的类型定义
    │   ├── analysis/         # 分析循环与产物
    │   │   ├── analyzer_loop.py      # AnalyzerLoop：扫描 pending record、并发分析、推送卡片
    │   │   ├── card_builder.py       # 分析结果 → 卡片内容构建
    │   │   ├── record_repository.py  # record 状态读写（基于 index.sqlite）
    │   │   └── analyzers/            # 分析器接口与类型
    │   ├── analysis_cli_support/     # 各需求判断 CLI 的接入实现
    │   │   ├── claude_code_cli/      # Claude Code CLI 分析器
    │   │   └── kxcymc_cli/             # kxcymc CLI 分析器
    │   ├── cards/            # 可复用交互卡片基础设施
    │   │   ├── base.py / registry.py / router.py / service.py
    │   │   ├── state_store.py        # 卡片状态持久化
    │   │   ├── worker.py             # CardActionWorker：卡片回调 WebSocket worker
    │   │   ├── lark_config.py
    │   │   └── scenes/              # 卡片场景：需求 / Meego / 开发任务
    │   ├── commands/         # 机器人单聊命令服务
    │   │   ├── command_server.py     # CommandServer：命令分发、启动欢迎语
    │   │   ├── config_card_scene.py  # /config 配置卡片场景
    │   │   ├── config_schema.py / config_store.py  # 配置 schema 与读写
    │   │   └── out_reset.py          # /del-out 等 out 目录维护
    │   ├── dev_tasks/        # 开发任务编排子包
    │   │   ├── models.py             # DevTask / DevTaskStatus / DispatchStatus
    │   │   ├── orchestrator.py       # DevTaskOrchestrator：并发、重试、恢复、补偿
    │   │   ├── prompt.py             # build_dev_prompt
    │   │   └── repository.py         # DevTaskRepository / BubbleRepository
    │   ├── dev_task_executor_support/  # 开发任务执行器注册表与实现
    │   │   ├── types.py              # DevTaskExecutor / CardSupport 接口
    │   │   ├── kxcymc_openapi/         # kxcymc OpenAPI 执行器（默认）
    │   │   ├── claude_code_cli/      # Claude Code CLI 执行器
    │   │   └── trae_side_chat/       # Trae 侧聊执行器
    │   ├── git_inventory/    # 本地 git 仓库清单
    │   │   ├── scanner.py            # RepoScanner：扫描本地仓库
    │   │   ├── service.py            # GitInventoryService：带 TTL 缓存的清单服务
    │   │   └── repository.py         # RepoInventoryRepository
    │   ├── meego/            # Meego 需求收集
    │   │   ├── collectors.py         # 从飞书群 / Meego URL 收集需求
    │   │   ├── cli.py
    │   │   └── figma_finder.py
    │   └── notifications/    # BubbleNotifier：智能体私聊气泡消息通知
    ├── collector/            # 消息采集器
    │   ├── main.py           # collector 进程入口：poller→filter→context→media→sink 管道
    │   ├── poller.py         # AtMePoller：轮询 @我群聊 / 私聊消息，持久化游标 + overlap
    │   ├── command_filter.py # 命令消息识别（collector 端短路）
    │   ├── context.py        # 上下文聚合（前 N 条 + 后 N 条）
    │   ├── media.py          # 图片/视频下载
    │   ├── permissions.py    # 启动前权限预检
    │   └── sink.py           # RecordSink：写 record + 登记 seen + 写 index
    ├── mywork/               # 我的工作总结一次性任务
    │   ├── main.py           # python -m src.mywork.main：收集本人消息、落盘、调用总结 CLI
    │   ├── collector.py      # 按 owner 消息锚点收集同会话上下文
    │   ├── concluder.py      # 读取 mywork JSON 并编排总结 provider 创建云文档
    │   └── conclude_cli_support/  # 总结 CLI provider 实现目录
    └── common/               # 通用工具与配置
        ├── cli.py            # lark-cli 子进程调用封装
        ├── command_parser.py # 命令文本解析
        ├── filter.py         # HitFilter / OwnerCache（命中判断、owner 缓存）
        ├── index.py          # IndexDB：SQLite 索引（seen_messages、record 状态等）
        ├── lark_app_config.py# 飞书应用配置与子进程环境注入
        ├── logging_setup.py  # configure_logging：loguru 配置入口
        ├── paths.py          # record / media / index 路径规则
        ├── process_lifecycle.py  # 信号处理、父进程监控、优雅退出
        ├── tls.py
        └── utils.py
```

### 运行期生成目录（均不入库）

- `out/`：record 落盘根目录，按 `日期/会话类型/chat_id/message_id/` 组织，含 `record.json`、`media/`；以及 `index.sqlite` 索引库。
- `out/mywork/`：mywork 一次性任务产物，包含 `{YYYYMMDD}_mywork.json` 和 `{YYYYMMDD}_mywork_doc.json`。
- `cache/`：owner 信息缓存、`bot.ready` 就绪标志、`restart.request` 重启请求等。
- `logs/`：运行期日志（`loguru` 文件 sink，按服务名分文件，每日轮转、保留 14 天）。可用 `MSG_LISTENER_LOG_DIR` 覆盖目录、`MSG_LISTENER_LOG_DIAGNOSE=1` 开启诊断回溯。
- `.venv/` / `.local/`：受管 Python 环境与启动器本地资源。

## 入口与脚本

仅通过统一入口启动，禁止引入新的平台相关脚本：

```bash
# 首次初始化并启动
npm run bootstrap

# 后续快速启动
npm run start
```

bot 进程支持调试参数（一般无需手动使用）：

- `--once`：跑一轮 `AnalyzerLoop` 后退出，不启动 WebSocket worker。

## 配置约定

- 运行配置来自项目根的 `config.toml`（首启由 [config.toml.default](./config.toml.default) 复制，含敏感信息时不入库）。
- 主要分段：
  - 顶层：上下文聚合、轮询、过滤开关、日志级别。
  - `[phase2]` / `[phase2.kxcymc]` / `[phase2.claude]` / `[phase2.card]`：分析循环、需求判断 CLI、卡片推送。
  - `[phase3.git]`：本地仓库扫描。
  - `[phase3.dev_task]` 及其子表：开发任务编排与执行器（`kxcymc_openapi` / `trae_side_chat` 等）。
  - `[commands]`：机器人单聊命令前缀与行为。
  - `[lark_app]`：飞书卡片回调人工确认标志。
- 部分配置可通过 `/config` 卡片在飞书内修改并写回，写回后 supervisor 经 `restart.request` 原地重启生效。

## 依赖管理约定

- Python 依赖统一在 [pyproject.toml](./pyproject.toml) 中维护，通过 `uv` 安装到项目本地 `.venv`。
- 不要将 `.venv`、`.local`、`config.toml`（含敏感信息时）提交到仓库。
- 日志库统一使用 `loguru`，由 [src/common/logging_setup.py](./src/common/logging_setup.py) 的 `configure_logging` 在各进程入口配置；业务侧仍可继续使用 `logging.getLogger(__name__)`，stdlib 日志会经 InterceptHandler 透传到 loguru。
- 如需手动运行 Python 脚本，请确保使用受管 Python 3.11+ 解释器。

## Agent 协作准则

- 改动前先确认项目技术栈与受影响模块。
- 仅完成被要求的改动，并加上注释，注释风格与现有的代码保持一致。
- 注释要描述“为什么采取这种做法”。
- 编辑现有文件优先于新建文件；不要主动新建 README 或文档类文件。
- 沟通语言与用户语言一致。

## 常见操作

- 编辑配置：修改[config.toml.default](./config.toml.default)时，需要同步更新[config.toml](./config.toml)。
- 新增 Python 依赖：在 [pyproject.toml](./pyproject.toml) 的 `dependencies` 中追加，并通过 `npm run bootstrap` 重新装配环境。
- 调整启动行为：修改 [scripts/launch.mjs](./scripts/launch.mjs) 时务必同时验证三端行为。
- 调整业务逻辑：定位到 [src/](./src) 下对应子模块，保持现有分层结构。
- 新增分析 CLI / 开发任务执行器：分别在 `src/bot/analysis_cli_support/` 与 `src/bot/dev_task_executor_support/` 下按 provider 约定新增目录，并在对应注册表登记。
