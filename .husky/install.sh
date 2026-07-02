#!/usr/bin/env sh
# 一次性启用本仓库的 git 钩子（把 core.hooksPath 指向 .husky）。
# 为什么需要它：core.hooksPath 是本地 git 配置，不随 clone 携带，因此每位
# 贡献者克隆后需运行本脚本一次，pre-commit 敏感信息扫描才会生效。
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
if [ -z "$REPO_ROOT" ]; then
  echo "请在 git 仓库内运行本脚本" >&2
  exit 1
fi
git -C "$REPO_ROOT" config core.hooksPath .husky
chmod +x "$REPO_ROOT/.husky/pre-commit" "$REPO_ROOT/.husky/scan-secrets.mjs" 2>/dev/null
echo "已启用 git 钩子：core.hooksPath = .husky"
echo "提交时将自动运行敏感信息扫描（.husky/scan-secrets.mjs）。"
