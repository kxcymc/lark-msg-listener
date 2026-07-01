"""claude-code-router(CCR) 网关共享支持：保活 + 环境变量注入。

分析侧、执行器侧以及执行器的独立 worker 子进程都要把 `claude` 请求路由到 CCR，
因此把「构造注入 env」与「保活网关」统一收敛到 common，避免各处重复实现且行为漂移。
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_CCR_BASE_URL = "http://127.0.0.1:3456"
DEFAULT_CCR_COMMAND = "ccr"
DEFAULT_STARTUP_TIMEOUT_SECONDS = 30.0
# 占位 key：真正鉴权由 CCR 服务端按 ~/.claude-code-router/config.json 代办，
# 但 claude CLI 要求 ANTHROPIC_API_KEY 必须存在，否则会提前报错。
_PLACEHOLDER_API_KEY = "ccr-proxy"
# Node 18 兼容垫片：与本模块同目录的 .cjs；详见 _ccr_subprocess_env。
_NODE18_FILE_POLYFILL = Path(__file__).resolve().with_name(
    "ccr_node18_file_polyfill.cjs"
)


def _ccr_subprocess_env(base_env: dict | None = None) -> dict:
    """为 ccr 子进程注入 Node 18 的 File 垫片（经 NODE_OPTIONS=--require）。

    claude-code-router 打包的 undici@7 期望全局 File，但该对象 Node 20 才全局可用，
    Node 18（项目支持下限）下 ccr 启动即抛 `ReferenceError: File is not defined`。
    预加载垫片即可修复；NODE_OPTIONS 会被 `ccr start` 派生的后台服务进程继承，
    因此 status 与 start 两条路径都被覆盖。Node 20+ 上垫片为无副作用空操作。
    """
    env = dict(base_env if base_env is not None else os.environ)
    if not _NODE18_FILE_POLYFILL.is_file():
        return env
    # 路径含空格时用双引号包裹，Node 的 NODE_OPTIONS 解析支持双引号。
    path = str(_NODE18_FILE_POLYFILL)
    token = f'--require "{path}"' if " " in path else f"--require {path}"
    # 保留调用方已有的 NODE_OPTIONS，把垫片前置而非覆盖。
    existing = env.get("NODE_OPTIONS", "").strip()
    env["NODE_OPTIONS"] = f"{token} {existing}".strip() if existing else token
    return env


@dataclass(frozen=True)
class CcrGatewayConfig:
    enabled: bool = True          # 关掉则退化为直连，便于排障/回滚
    auto_start: bool = True       # 是否由本服务自动 ccr start
    command: str = DEFAULT_CCR_COMMAND
    base_url: str = DEFAULT_CCR_BASE_URL
    startup_timeout_seconds: float = DEFAULT_STARTUP_TIMEOUT_SECONDS


def build_ccr_env(
    config: CcrGatewayConfig, base_env: dict | None = None
) -> dict:
    """在给定 env 基础上注入 ANTHROPIC_BASE_URL / 占位 ANTHROPIC_API_KEY。

    enabled=false 时返回不含 CCR 变量的 env 副本（等价直连），便于回滚。
    """
    env = dict(base_env if base_env is not None else os.environ)
    if not config.enabled:
        return env
    env["ANTHROPIC_BASE_URL"] = config.base_url
    # setdefault：若调用方已显式提供真实 key 则不覆盖，仅在缺失时补占位值。
    env.setdefault("ANTHROPIC_API_KEY", _PLACEHOLDER_API_KEY)
    return env


def is_gateway_running(config: CcrGatewayConfig) -> bool:
    """ccr status 探测；返回是否 running。命令缺失/异常一律视为未运行。"""
    try:
        res = subprocess.run(
            [config.command, "status"],
            capture_output=True,
            text=True,
            timeout=10,
            env=_ccr_subprocess_env(),
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return False
    # ccr status 打印 "Status: Running" 或 "Status: Not Running"。
    # 裸 "running" 子串会被 "not running" 命中而误判为已就绪，故需排除否定形式。
    text = (res.stdout + res.stderr).lower()
    return "running" in text and "not running" not in text


def ensure_gateway_ready(config: CcrGatewayConfig) -> None:
    """幂等保活：未 running 且 auto_start 时 ccr start 并轮询确认。

    失败抛 RuntimeError，由各侧 ensure_ready 决定 sys.exit(1) 还是仅告警。
    """
    if not config.enabled:
        return
    if shutil.which(config.command) is None:
        raise RuntimeError(
            f"未找到 CCR 命令 `{config.command}`。"
            "请先安装：npm i -g @musistudio/claude-code-router"
        )
    if is_gateway_running(config):
        return
    if not config.auto_start:
        raise RuntimeError(
            f"CCR 网关未运行且 auto_start=false。请手动执行 `{config.command} start`。"
        )
    logger.info("CCR 网关未运行，执行 %s start", config.command)
    # 后台 detached 启动：start_new_session 让 ccr 脱离本进程会话组，
    # 避免 bot 退出时连带杀掉网关；Windows 无 setsid，沿用 executor.py 的判断方式。
    # env 注入 Node 18 File 垫片，且会被 ccr 派生的后台服务进程继承。
    subprocess.Popen(
        [config.command, "start"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=(os.name != "nt"),
        env=_ccr_subprocess_env(),
    )
    deadline = time.monotonic() + config.startup_timeout_seconds
    while time.monotonic() < deadline:
        if is_gateway_running(config):
            logger.info("CCR 网关已就绪：%s", config.base_url)
            return
        time.sleep(1.0)
    raise RuntimeError(
        f"等待 CCR 网关就绪超时（> {config.startup_timeout_seconds:.0f}s）。"
        f"请检查 `{config.command} status` 与 ~/.claude-code-router/config.json。"
    )


def load_ccr_config(cfg: dict) -> CcrGatewayConfig:
    """从全局 config.toml 的顶层 [ccr] 读取；缺省回落到内置默认值。"""
    raw = cfg.get("ccr") or {}
    return CcrGatewayConfig(
        enabled=bool(raw.get("enabled", True)),
        auto_start=bool(raw.get("auto_start", True)),
        command=str(raw.get("command", DEFAULT_CCR_COMMAND)) or DEFAULT_CCR_COMMAND,
        base_url=str(raw.get("base_url", DEFAULT_CCR_BASE_URL)) or DEFAULT_CCR_BASE_URL,
        startup_timeout_seconds=float(
            raw.get("startup_timeout_seconds", DEFAULT_STARTUP_TIMEOUT_SECONDS)
        ),
    )
