"""跨平台的 kxcymc CLI 模型发现。"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class kxcymcModelsError(RuntimeError):
    """kxcymc 模型发现过程中的任何不可恢复错误。"""


@dataclass(frozen=True)
class kxcymcModel:
    """单个模型项。`description` 原样来自 kxcymc ACP 响应。"""

    name: str
    description: str = ""
    model_id: str = ""
    source: str = "kxcymc acp"


@dataclass
class kxcymcModelsResult:
    models: list[kxcymcModel] = field(default_factory=list)
    current_model_id: str = ""

    @property
    def names(self) -> list[str]:
        return [m.model_id or m.name for m in self.models]

    def find(self, name: str) -> kxcymcModel | None:
        if not name:
            return None
        for model in self.models:
            if name in {model.name, model.model_id}:
                return model
        return None


def is_kxcymc_available(command: str = "kxcymc") -> bool:
    return shutil.which(command) is not None


async def discover_models(
    *,
    command: str = "kxcymc",
    cwd: Path | None = None,
    timeout: float = 20.0,
) -> kxcymcModelsResult:
    workdir = cwd or Path.cwd()
    try:
        proc = await asyncio.create_subprocess_exec(
            command,
            "acp",
            "serve",
            cwd=str(workdir),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise kxcymcModelsError(f"PATH 中找不到 `{command}` 可执行文件") from exc

    try:
        return await asyncio.wait_for(
            _discover_models_from_acp(proc=proc, cwd=workdir),
            timeout=timeout,
        )
    except asyncio.TimeoutError as exc:
        _terminate_process(proc)
        raise kxcymcModelsError(f"`{command} acp serve` 获取模型列表超时（{timeout}s）") from exc
    finally:
        await _cleanup_process(proc)


async def _discover_models_from_acp(
    *,
    proc: asyncio.subprocess.Process,
    cwd: Path,
) -> kxcymcModelsResult:
    if proc.stdin is None or proc.stdout is None:
        raise kxcymcModelsError("kxcymc ACP 子进程 stdio 初始化失败")

    requests = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": 1,
                "clientCapabilities": {},
                "clientInfo": {"name": "msg-listener-agent", "version": "0"},
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "session/new",
            "params": {"cwd": str(cwd), "mcpServers": []},
        },
    ]
    for request in requests:
        proc.stdin.write((json.dumps(request) + "\n").encode("utf-8"))
        await proc.stdin.drain()

    stderr_task = asyncio.create_task(_drain_stderr(proc))
    try:
        while True:
            line_b = await proc.stdout.readline()
            if not line_b:
                break
            line = line_b.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if message.get("id") != 2:
                continue
            if "error" in message:
                raise kxcymcModelsError(f"kxcymc ACP session/new 失败：{message['error']}")
            return _parse_acp_models(message.get("result") or {})
    finally:
        stderr_task.cancel()
        await asyncio.gather(stderr_task, return_exceptions=True)

    raise kxcymcModelsError("kxcymc ACP 未返回 session/new 结果")


async def _drain_stderr(proc: asyncio.subprocess.Process) -> None:
    if proc.stderr is None:
        return
    async for line_b in proc.stderr:
        text = line_b.decode("utf-8", errors="replace").strip()
        if text:
            logger.debug("kxcymc acp stderr: %s", text)


def _parse_acp_models(result: dict[str, Any]) -> kxcymcModelsResult:
    models_obj = result.get("models") or {}
    raw_models = models_obj.get("availableModels") or []
    if not isinstance(raw_models, list):
        raw_models = []

    models: list[kxcymcModel] = []
    seen: set[str] = set()
    for item in raw_models:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("modelId") or "").strip()
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        name = str(item.get("name") or model_id).strip() or model_id
        models.append(
            kxcymcModel(
                name=name,
                model_id=model_id,
                description=str(item.get("description") or "").strip(),
            )
        )

    return kxcymcModelsResult(
        models=models,
        current_model_id=str(models_obj.get("currentModelId") or "").strip(),
    )


def _terminate_process(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is None:
        try:
            proc.terminate()
        except ProcessLookupError:
            pass


async def _cleanup_process(proc: asyncio.subprocess.Process) -> None:
    if proc.stdin is not None:
        try:
            proc.stdin.close()
        except Exception:  # noqa: BLE001
            pass
    _terminate_process(proc)
    if proc.returncode is not None:
        return
    try:
        await asyncio.wait_for(proc.wait(), timeout=2.0)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
