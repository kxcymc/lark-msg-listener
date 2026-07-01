---
name: configure-and-run
description: 当用户说“配置并运行项目”、“启动项目”、“初始化并运行 msg-listener”、“跑起来”、“bootstrap and run” 时触发。
---

### 基本规则

- 用于初次启动 msg-listener 项目，并按需引导用户一步步完成 config.toml 中缺失的关键配置。读取项目根目录下的 config.toml，根据 `phase3.dev_task.kxcymc_openapi.pat_token` 与 `lark_app` 下的各项人工确认状态自动选择 bootstrap 或 run 模式启动。
- 启动入口仅可使用 `npm run bootstrap` 或 `npm run start`。
- 严禁猜测或编造任何 token、回调状态等敏感配置；缺失配置时必须停下来，向用户提供可操作的引导。
- 输出与互动语言与用户当前对话语言一致。

---

### 执行步骤

**Step 1: 校验项目结构**

- 读取 `package.json`，确认存在 `bootstrap`、`start` 两个 npm 脚本。
- 读取 `config.toml`；若文件不存在，复制 `config.toml.default` 到 `config.toml`。

**Step 2: 解析关键配置**

从 `config.toml` 中提取以下项（注意 TOML 段落层级）：

- `phase3.dev_task.kxcymc_openapi.pat_token`
- `lark_app.card_callback_event_confirmed`
- `lark_app.bot_menu_config_confirmed`
- `lark_app.bot_menu_event_confirmed`

判定规则：

- `pat_token` 视为“非空”当且仅当字符串存在且去除首尾空白后长度 > 0，且不是占位符（如 `""`、`"YOUR_PAT"`、`"code_pat_xxx"` 这类明显占位）。
- `lark_app` 下的 `card_callback_event_confirmed`、`bot_menu_config_confirmed`、`bot_menu_event_confirmed` 三者必须严格为布尔值 `true`。
- 上述所有条件均满足 → `npm run start`；否则 → `npm run bootstrap`。

**Step 3: 执行启动命令**

根据判定结果执行：

- run 模式：在项目根目录运行 `npm run start`。
- bootstrap 模式：在项目根目录运行 `npm run bootstrap`。

执行约束：

- 必须使用 `RunCommand`，`cwd` 设为项目根目录绝对路径。
- 启动属于 long_running_process，应以 `blocking: false` 启动；启动后给用户简短状态汇报（当前模式、原因），并告知用户可继续在 IDE 中观察启动器日志。
- 启动失败或报错时，使用 `CheckCommandStatus` 取回最新输出，定位问题并向用户简明汇报，避免反复重试同一指令。

**Step 4: 引导用户完成配置**

- 持续观察终端输出，根据终端日志，引导用户完成必要配置。
- 引导阶段必须使用 `AskUserQuestion`，给出明确选项（例如：现在填写 / 先 bootstrap 启动 / 取消）。

---

### 用户交互策略

- 任何需要用户拍板的分支（缺失配置如何处理、是否立即启动、是否回写人工确认状态）一律使用 `AskUserQuestion`，提供 2–4 个互斥选项；自由文本通过“Other”兜底。
- 当用户未明确指示时，默认推荐选项放在第一位，并在 label 末尾标注 “(Recommended)”。
- 解析 `config.toml` 出现歧义（如自定义注释、字段位置异常）时，先把读到的原文片段反馈给用户确认，再决定后续动作。

---

### 关键约束

- 不修改 `config.toml` 中除明确得到用户确认以外的字段；尤其不得自动把 `lark_app` 下的状态改为 `true`。
- 启动命令固定为 `npm run bootstrap` 或 `npm run start`。

---

### 提示信息

- 解析 TOML 时只关心目标字段，不需要完整 TOML 解析器；按段落标记 `[phase3.dev_task.kxcymc_openapi]`、`[lark_app]` 定位即可。
- `pat_token` 的常见占位形式：空串、`"code_pat_"`、`"YOUR_PAT"`、`"xxx"`、`"changeme"`，遇到这类字符串都视作未配置。
- 启动器自身会处理 `uv`、Python 3.11、`.venv` 装配，skill 不需要额外干预环境。
