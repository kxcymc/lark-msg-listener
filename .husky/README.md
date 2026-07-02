# .husky — 提交前敏感信息扫描

本目录提供 git `pre-commit` 钩子，在**每次提交时**扫描本次暂存（staged）的全部文件，命中疑似敏感信息即**阻止提交**。作为 `.gitignore` 之外的第二道防线，防止敏感数据被 `git add -f` 强制入库或被复制粘贴进普通文件。

> 本目录会提交到远程仓库，**不包含任何真实敏感信息**：所有规则只描述敏感串的“结构形状”，用正则通用匹配。

## 目录内容

| 文件 | 作用 |
| --- | --- |
| `pre-commit` | git 钩子入口（POSIX sh）：定位仓库根与 node，执行扫描脚本 |
| `scan-secrets.mjs` | 扫描器（Node ≥18）：读取暂存区内容，按路径规则 + 通用正则检测 |
| `install.sh` | 一次性启用脚本：将 `core.hooksPath` 指向本目录 |

## 启用方式（clone 后执行一次）

`core.hooksPath` 属于本地 git 配置，不随 clone 携带，需手动启用一次：

```sh
sh .husky/install.sh
# 或手动：git config core.hooksPath .husky
```

启用后，`git commit` 会自动触发扫描；命中则退出码非 0，提交被拦截。

## 检测规则

**1. 敏感路径**（文件一旦落在这些位置即拦截，无论内容）

- `msg-listener/.local`、`msg-listener/.venv`、`msg-listener/cache`、`msg-listener/logs`
- `msg-listener/msg_listener_agent.egg-info`、`msg-listener/config.toml`
- 通用：任意层级的 `.local`、`.venv`、`*.egg-info` 目录段，以及 `config.toml` 文件名

**2. 通用敏感内容**（任意文件正文命中即拦截）

- 暴露主机用户名的绝对家目录路径（如 `/Users/<name>/`、`/home/<name>/`、Windows 反斜杠路径）
- 飞书身份/会话 ID（`ou_`/`oc_`/`on_` 等 + 长十六进制）、应用 ID（`cli_...`）
- kxcymc OpenAPI PAT（`code_pat_...`）、AWS Access Key、Slack/GitHub Token、JWT
- 私钥文件头（`-----BEGIN ... PRIVATE KEY-----`）
- 凭证键值对（`app_secret`/`api_key`/`password`/`access_token` 等被赋值为带引号的长字符串）

命中信息在终端以**脱敏**形式打印（只显示头尾几位），不完整回显敏感值。

## 应急跳过

确认为误报或需紧急提交时（请谨慎）：

```sh
git commit --no-verify
# 或临时关闭本次扫描：
HUSKY_SKIP_SECRET_SCAN=1 git commit
```

## 扩展规则

在 `scan-secrets.mjs` 中：

- 新增敏感路径：追加到 `SENSITIVE_PATH_PREFIXES` / `SENSITIVE_PATH_SEGMENTS` / `SENSITIVE_BASENAMES`。
- 新增敏感内容：向 `CONTENT_RULES` 追加 `{ name, re }`；正则务必带 `g` 标志，且**不得写入真实敏感值**（只描述结构）。
