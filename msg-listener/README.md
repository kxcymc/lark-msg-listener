# msg-listener

飞书消息实时监听服务。项目以 user 身份采集群聊 @我消息和私聊消息，聚合上下文后落盘；bot 进程异步分析命中内容、推送交互卡片，并可继续编排开发任务。

## 环境要求

- Node.js `>= 18`，需包含 `npm` 与 `npx`
- 首次运行需要联网下载 `uv`、受管 Python 和 Python 依赖
- 默认不需要预装 Python，启动器会自动准备项目本地运行环境

## 快速开始

首次初始化并启动：

```bash
npm run bootstrap
```

后续启动：

```bash
npm run start
```

初始化指令会自动完成：

- 安装或复用 `uv`
- 下载受管 Python `3.11`
- 创建或复用项目虚拟环境 `.venv`
- 按 `pyproject.toml` 和 `uv.lock` 同步 Python 依赖
- 等待飞书配置、用户授权、bot 权限和事件订阅就绪

## 依赖方式

- Python 依赖统一声明在 `pyproject.toml`，版本锁定在 `uv.lock`
- 飞书 SDK 使用 PyPI 包 `lark-oapi==1.6.5`，代码中通过 `lark_oapi` 导入

## 运行模型

- `npm run bootstrap` / `npm run start` 统一进入 `scripts/launch.mjs`
- 启动器准备环境后拉起 Python supervisor：`src/main.py`
- supervisor 先启动 bot 进程，待 `cache/bot.ready` 写入后再启动 collector 进程
- `/config` 写回配置后会触发原地重启，新的配置自动生效

## 主要能力

- 消息采集：轮询群聊 @我消息和可选私聊消息，按会话补齐前后文
- 需求分析：扫描 pending record，调用配置的分析 CLI 判断是否命中需求
- 交互卡片：向 owner 推送需求、Meego、开发任务等场景卡片
- 开发任务：将命中需求编排给配置的执行器继续处理
- 我的工作总结：每周五 17:30 自动运行 `mywork`，生成飞书云文档后退出

## 运行产物

- `out/`：消息 record、媒体文件和 SQLite 索引
- `out/mywork/`：我的工作总结 JSON 与文档结果
- `cache/`：运行缓存、就绪标记和重启请求
- `logs/`：运行日志
- `.venv/`、`.local/`：项目本地 Python 环境和启动器资源

这些目录均为本地运行产物，不应提交到仓库。

## 常用命令

```bash
npm run bootstrap
npm run start
```

手动运行我的工作总结：

```bash
python -m src.mywork.main
```

## 跨平台约定

- Windows、macOS、Linux 使用同一套 `npm` 入口
- 不引入各平台专用的程序/系统调用
- 不提交 `.venv`、`.local`、`out`、`cache`、`logs`、`config.toml`
- 手动运行 Python 脚本时，请使用项目 `.venv` 中的 Python `3.11+`
