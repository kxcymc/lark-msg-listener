"""kxcymcOpenApiExecutor：通过 kxcymc OpenAPI 提交并等待开发任务事件流。

实现 `dev_task_executor_support.types.DevTaskExecutor` 协议。
"""
from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import re
import time
import uuid
from pathlib import Path
from typing import Any

from loguru import logger as _logger

from ...dev_tasks.prompt import build_api_dev_prompt
from ..types import TaskRequest, TaskSnapshot, TaskStatus
from .models import (
    kxcymcOpenApiConfig,
    RUNNING_STATUSES,
    TERMINAL_STATUSES_FAILED,
    TERMINAL_STATUSES_SUCCESS,
)

logger = logging.getLogger(__name__)


_MR_URL_RE = re.compile(r"https://kxcymc\.com/[^\s\"'<>]+/merge_requests/\d+")


class kxcymcOpenApiError(RuntimeError):
    """kxcymc_openapi 通用错误，error_code 用于分类写入 dev_tasks.error。"""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(f"{error_code}: {message}")
        self.error_code = error_code
        self.message = message


class kxcymcOpenApiExecutor:
    """kxcymc OpenAPI 执行器。"""

    def __init__(self, config: kxcymcOpenApiConfig) -> None:
        self.config = config
        self._repo_id_cache: dict[str, str] = {}

    async def submit(self, request: TaskRequest) -> str:
        repo_id = await self._resolve_repo_id(request.repo_id)
        prompt = build_api_dev_prompt(
            base_branch=request.base_branch,
            work_branch_hint=request.work_branch_hint,
            requirement_summary=request.requirement_summary,
            requirement_detail=request.requirement_detail,
        )
        prompt = await self._append_uploaded_media(prompt, request.media_files)
        payload: dict[str, Any] = {
            "RepoId": repo_id,
            "Branch": request.base_branch,
            "Message": {
                "Id": str(uuid.uuid4()),
                "Role": "user",
                "Parts": [
                    {"Text": {"Text": prompt}},
                ]
            },
        }
        if self.config.agent_name:
            payload["AgentName"] = self.config.agent_name
        if self.config.model_name:
            payload["ModelName"] = self.config.model_name
        data = await self._invoke(
            action="SendCopilotTaskMessage",
            payload=payload,
            timeout=self.config.submit_timeout_seconds,
        )
        task = _extract_task(data)
        task_id = str(task.get("Id") or task.get("TaskId") or "")
        if not task_id:
            raise kxcymcOpenApiError(
                "submit_error", f"SendCopilotTaskMessage 未返回 task_id: {data}"
            )
        return task_id

    async def _resolve_repo_id(self, local_repo_id: str) -> str:
        query = local_repo_id
        cached = self._repo_id_cache.get(query)
        if cached:
            return cached
        try:
            response = await self._invoke(
                action="GetRepository",
                payload={"Path": query},
                timeout=self.config.submit_timeout_seconds,
            )
        except kxcymcOpenApiError:
            response = await self._invoke(
                action="SearchRepositories",
                payload={
                    "Filter": {"Path": query, "Status": "created"},
                    "PageSize": 20,
                },
                timeout=self.config.submit_timeout_seconds,
            )
            result = response.get("Result")
            repositories = (
                result.get("Repositories") if isinstance(result, dict) else None
            )
            if not isinstance(repositories, list) or not repositories:
                raise kxcymcOpenApiError(
                    "submit_error",
                    f"无法在 kxcymc 解析仓库 RepoId: {query}",
                )
            repository = _choose_repository(query, repositories)
        else:
            result = response.get("Result")
            repository = (
                result.get("Repository") if isinstance(result, dict) else None
            )
            if not isinstance(repository, dict) or not repository.get("Id"):
                raise kxcymcOpenApiError(
                    "submit_error",
                    f"无法在 kxcymc 解析仓库 RepoId: {query}",
                )
        repo_id = str(repository.get("Id") or "")
        if not repo_id:
            raise kxcymcOpenApiError(
                "submit_error",
                f"无法在 kxcymc 解析仓库 RepoId: {query}",
            )
        self._repo_id_cache[query] = repo_id
        logger.info("kxcymc OpenAPI 解析仓库 local=%s kxcymc_repo_id=%s", query, repo_id)
        return repo_id

    async def _append_uploaded_media(self, prompt: str, media_files: list[dict]) -> str:
        image_files = [
            item for item in media_files
            if isinstance(item, dict) and str(item.get("kind") or "").lower() == "image"
        ]
        if not image_files:
            return prompt

        uploaded: list[dict[str, str]] = []
        for item in image_files:
            local_path = Path(str(item.get("local_path") or "")).expanduser()
            if not local_path.is_file():
                logger.warning("kxcymc OpenAPI 图片文件不存在，跳过上传: %s", local_path)
                continue
            uploaded.append(await self._upload_file(local_path))

        if not uploaded:
            return prompt

        image_payloads = [
            {
                "prompt": "请结合该图片理解需求中的 UI 文案与页面位置。",
                "raw_mode": False,
                "uri": item["url"],
            }
            for item in uploaded
        ]
        return (
            f"{prompt}\n\n"
            "图片信息：\n"
            f"{json.dumps(image_payloads, ensure_ascii=False, indent=2)}\n"
            "请结合上述图片完成需求，不要忽略图片中的界面文案。"
        )

    async def _upload_file(self, local_path: Path) -> dict[str, str]:
        try:
            import httpx  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - 启动期已校验
            raise kxcymcOpenApiError("network_error", f"httpx 未安装: {exc}") from exc

        pat = self.config.pat_token.strip()
        if not pat:
            raise kxcymcOpenApiError("submit_error", "缺少 kxcymc OpenAPI PAT，无法上传图片")

        mime_type = mimetypes.guess_type(local_path.name)[0] or "application/octet-stream"
        filename = local_path.name.encode("ascii", errors="ignore").decode("ascii") or "image"
        url = self.config.file_endpoint.rstrip("/") + "/UploadFile"
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=self.config.submit_timeout_seconds) as client:
                response = await client.post(
                    url,
                    headers={"Authorization": f"Bearer {pat}"},
                    files={"File": (filename, local_path.read_bytes(), mime_type)},
                )
        except httpx.TimeoutException as exc:
            raise kxcymcOpenApiError("network_error", f"UploadFile 超时: {exc}") from exc
        except httpx.HTTPError as exc:
            raise kxcymcOpenApiError("network_error", f"UploadFile 失败: {exc}") from exc

        elapsed = time.monotonic() - started
        logger.info(
            "kxcymc OpenAPI UploadFile http_status=%s elapsed=%.2fs file=%s",
            response.status_code,
            elapsed,
            local_path.name,
        )
        if response.status_code >= 500:
            raise kxcymcOpenApiError(
                "network_error",
                f"UploadFile 服务端错误 status={response.status_code}: {response.text[:500]}",
            )
        if response.status_code >= 400:
            raise kxcymcOpenApiError(
                "submit_error",
                f"UploadFile 请求失败 status={response.status_code}: {response.text[:500]}",
            )
        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise kxcymcOpenApiError("submit_error", f"UploadFile 响应非 JSON: {response.text[:500]}") from exc

        metadata = data.get("ResponseMetadata")
        if isinstance(metadata, dict) and metadata.get("Error"):
            raise kxcymcOpenApiError(
                "submit_error",
                f"UploadFile API 错误: {json.dumps(metadata['Error'], ensure_ascii=False)}",
            )
        result = data.get("Result")
        if not isinstance(result, dict):
            raise kxcymcOpenApiError("submit_error", f"UploadFile 未返回 Result: {data}")
        file_url = str(result.get("FileURL") or "")
        if not file_url:
            raise kxcymcOpenApiError("submit_error", f"UploadFile 未返回 FileURL: {data}")
        logger.debug("kxcymc OpenAPI 图片已上传 file=%s url=%s", local_path.name, file_url)
        return {
            "url": file_url,
            "id": str(result.get("FileId") or ""),
            "mime_type": str(result.get("MIMEType") or mime_type),
        }

    async def wait(self, task_id: str, timeout_minutes: float) -> TaskSnapshot:
        bound = _logger.bind(task_id=task_id)
        try:
            import httpx  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - 启动期已校验
            raise kxcymcOpenApiError("network_error", f"httpx 未安装: {exc}") from exc

        pat = self.config.pat_token.strip()
        if not pat:
            raise kxcymcOpenApiError(
                "submit_error",
                "缺少 kxcymc OpenAPI PAT：phase3.dev_task.kxcymc_openapi.pat_token 为空",
            )

        url = self.config.endpoint.rstrip("/") + "/?Action=SubscribeCopilotTaskEvents"
        headers = {
            "Authorization": f"Bearer {pat}",
            "Content-Type": "application/json",
        }
        body = json.dumps({"TaskId": task_id}, ensure_ascii=False)
        timeout_seconds = max(1.0, float(timeout_minutes) * 60.0)
        started = time.monotonic()
        deadline = started + timeout_seconds
        # MR URL 往往在终态事件之前的内容事件中流式返回，需跨事件累积，
        # 否则终态事件本身不含 URL 会被误判为 no_mr 并触发重试。
        mr_url_holder: dict[str, str] = {"value": ""}

        async def _stream_once() -> TaskSnapshot | None:
            timeout = httpx.Timeout(connect=self.config.submit_timeout_seconds, read=None, write=None, pool=None)
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    async with client.stream("POST", url, content=body, headers=headers) as response:
                        if response.status_code >= 500:
                            text = (await response.aread()).decode("utf-8", errors="replace")
                            raise kxcymcOpenApiError(
                                "network_error",
                                f"SubscribeCopilotTaskEvents 服务端错误 status={response.status_code}: {text[:500]}",
                            )
                        if response.status_code >= 400:
                            text = (await response.aread()).decode("utf-8", errors="replace")
                            raise kxcymcOpenApiError(
                                "event_error",
                                f"SubscribeCopilotTaskEvents 请求失败 status={response.status_code}: {text[:500]}",
                            )
                        async for raw_line in response.aiter_lines():
                            if not raw_line:
                                continue
                            line = raw_line.strip()
                            if not line.startswith("data:"):
                                continue
                            payload = line[len("data:"):].strip()
                            if not payload:
                                continue
                            try:
                                envelope = json.loads(payload)
                            except json.JSONDecodeError:
                                bound.warning(
                                    "kxcymc OpenAPI SSE 解析失败 executor_task=%s payload=%s",
                                    task_id,
                                    payload[:200],
                                )
                                continue
                            event = envelope.get("Event") if isinstance(envelope, dict) else None
                            if not isinstance(event, dict):
                                continue
                            # 持续累积 MR URL：早于终态事件的内容事件常携带最终 JSON。
                            found_url = _find_mr_url(envelope)
                            if found_url:
                                mr_url_holder["value"] = found_url
                            snapshot = _snapshot_from_event(
                                task_id, event, envelope, mr_url_holder["value"]
                            )
                            if snapshot is not None:
                                return snapshot
            except httpx.TimeoutException as exc:
                raise kxcymcOpenApiError("event_timeout", f"SubscribeCopilotTaskEvents 超时: {exc}") from exc
            except httpx.HTTPError as exc:
                raise kxcymcOpenApiError("network_error", f"SubscribeCopilotTaskEvents 失败: {exc}") from exc

            return None

        snapshot: TaskSnapshot | None = None
        reconnect_count = 0
        while snapshot is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise kxcymcOpenApiError(
                    "event_timeout",
                    f"SubscribeCopilotTaskEvents 等待终态超时 timeout_minutes={timeout_minutes}",
                )
            try:
                snapshot = await asyncio.wait_for(_stream_once(), timeout=remaining)
            except asyncio.TimeoutError as exc:
                raise kxcymcOpenApiError(
                    "event_timeout",
                    f"SubscribeCopilotTaskEvents 等待终态超时 timeout_minutes={timeout_minutes}",
                ) from exc
            if snapshot is None:
                reconnect_count += 1
                bound.debug(
                    "kxcymc OpenAPI SSE 连接结束但未到终态，继续订阅 executor_task=%s reconnect=%d",
                    task_id,
                    reconnect_count,
                )
                await asyncio.sleep(min(2.0, max(0.1, deadline - time.monotonic())))

        elapsed = time.monotonic() - started
        bound.info(
            "kxcymc OpenAPI 任务终态 executor_task=%s normalized=%s mr_url=%s error=%s elapsed=%.2fs",
            task_id,
            snapshot.status,
            snapshot.mr_url or "-",
            snapshot.error or "-",
            elapsed,
        )
        return snapshot

    async def _invoke(
        self,
        *,
        action: str,
        payload: dict[str, Any],
        timeout: float,
    ) -> dict[str, Any]:
        try:
            import httpx  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - 启动期已校验
            raise kxcymcOpenApiError("network_error", f"httpx 未安装: {exc}") from exc

        pat = self.config.pat_token.strip()
        if not pat:
            raise kxcymcOpenApiError(
                "submit_error",
                "缺少 kxcymc OpenAPI PAT：phase3.dev_task.kxcymc_openapi.pat_token 为空",
            )
        url = self.config.endpoint.rstrip("/") + f"/?Action={action}"
        headers = {
            "Authorization": f"Bearer {pat}",
            "Content-Type": "application/json",
        }
        body = json.dumps(payload, ensure_ascii=False)
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(url, content=body, headers=headers)
        except httpx.TimeoutException as exc:
            elapsed = time.monotonic() - started
            logger.warning(
                "kxcymc OpenAPI 超时 action=%s elapsed=%.2fs: %s",
                action,
                elapsed,
                exc,
            )
            raise kxcymcOpenApiError("network_error", f"{action} 超时: {exc}") from exc
        except httpx.HTTPError as exc:
            elapsed = time.monotonic() - started
            logger.warning(
                "kxcymc OpenAPI 网络错误 action=%s elapsed=%.2fs: %s",
                action,
                elapsed,
                exc,
            )
            raise kxcymcOpenApiError("network_error", f"{action} 失败: {exc}") from exc

        elapsed = time.monotonic() - started
        logger.info(
            "kxcymc OpenAPI %s http_status=%s elapsed=%.2fs",
            action,
            response.status_code,
            elapsed,
        )
        if response.status_code >= 500:
            raise kxcymcOpenApiError(
                "network_error",
                f"{action} 服务端错误 status={response.status_code}: {response.text[:500]}",
            )
        if response.status_code >= 400:
            raise kxcymcOpenApiError(
                "submit_error",
                f"{action} 请求失败 status={response.status_code}: {response.text[:500]}",
            )
        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise kxcymcOpenApiError(
                "submit_error",
                f"{action} 响应非 JSON: {response.text[:500]}",
            ) from exc
        if not isinstance(data, dict):
            raise kxcymcOpenApiError(
                "submit_error",
                f"{action} 响应结构异常: {data!r}",
            )
        return data


def _extract_task(data: dict[str, Any]) -> dict[str, Any]:
    """从响应中提取 Task 子结构，兼容 Result.Task 包装。"""
    if isinstance(data.get("Task"), dict):
        return dict(data["Task"])
    result = data.get("Result")
    if isinstance(result, dict):
        task = result.get("Task")
        if isinstance(task, dict):
            return dict(task)
    return data


def _choose_repository(query: str, repositories: list[Any]) -> dict[str, Any]:
    candidates = [
        repo for repo in repositories if isinstance(repo, dict) and repo.get("Id")
    ]
    if not candidates:
        raise kxcymcOpenApiError(
            "submit_error", f"无法在 kxcymc 解析仓库 RepoId: {query}"
        )

    def score(repo: dict[str, Any]) -> tuple[int, str]:
        path = str(repo.get("Path") or repo.get("Name") or "")
        name = str(repo.get("Name") or "")
        if path == query:
            return (0, path)
        if path.endswith(f"/{query}"):
            return (1, path)
        if name == query:
            return (2, path)
        if query in path:
            return (3, path)
        return (4, path)

    return sorted(candidates, key=score)[0]


def _task_mr_url(task: dict[str, Any]) -> str:
    return str(
        task.get("MrUrl")
        or task.get("MRUrl")
        or task.get("MergeRequestUrl")
        or task.get("MergeRequestURL")
        or task.get("PullRequestUrl")
        or task.get("WebUrl")
        or task.get("WebURL")
        or ""
    )


def _find_mr_url(value: Any) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        text = str(value)
    match = _MR_URL_RE.search(text)
    return match.group(0) if match else ""


def _snapshot_from_event(
    task_id: str,
    event: dict[str, Any],
    envelope: dict[str, Any],
    accumulated_mr_url: str = "",
) -> TaskSnapshot | None:
    detail = event.get("Detail") if isinstance(event.get("Detail"), dict) else {}
    event_type = str(event.get("Type") or "").lower()

    status_update = detail.get("TaskStatusUpdate") if isinstance(detail, dict) else None
    if event_type == "task_status_update" and isinstance(status_update, dict):
        raw_status = str(status_update.get("Status") or "").lower()
        is_final = bool(status_update.get("Final"))
        if not is_final and raw_status not in TERMINAL_STATUSES_SUCCESS and raw_status not in TERMINAL_STATUSES_FAILED:
            return None
        task_like = _task_like_from_event(
            detail, envelope, status=raw_status, accumulated_mr_url=accumulated_mr_url
        )
        return _snapshot_from_task(task_id=task_id, task=task_like)
    return None


def _task_like_from_event(
    detail: dict[str, Any],
    envelope: dict[str, Any],
    *,
    status: str,
    accumulated_mr_url: str = "",
) -> dict[str, Any]:
    state_update = detail.get("TaskStateUpdate") if isinstance(detail.get("TaskStateUpdate"), dict) else {}
    json_updates = state_update.get("JsonUpdates") if isinstance(state_update, dict) else None
    summary = ""
    if isinstance(json_updates, list):
        for chunk in json_updates:
            if isinstance(chunk, str) and chunk:
                summary = chunk
                break

    # 优先用终态事件自带的 URL，否则回退到流式累积的 URL。
    mr_url = _find_mr_url(envelope) or accumulated_mr_url
    return {
        "Status": status,
        "PullRequestUrl": mr_url,
        "Summary": summary,
    }


def _snapshot_from_task(*, task_id: str, task: dict[str, Any]) -> TaskSnapshot:
    raw_status = _task_raw_status(task)
    status: TaskStatus
    error = ""
    if raw_status in TERMINAL_STATUSES_SUCCESS:
        status = "succeeded"
    elif raw_status in TERMINAL_STATUSES_FAILED:
        status = "failed"
        error = "kxcymc_canceled" if raw_status in {"cancelled", "canceled"} else "kxcymc_failed"
    elif raw_status in RUNNING_STATUSES:
        status = "running"
    else:
        # 未知状态保守视为 running，由调用方靠 max_wait_minutes 终止。
        status = "running"

    mr_url = _task_mr_url(task)
    work_branch = str(
        task.get("WorkBranch")
        or task.get("HeadBranch")
        or task.get("SourceBranch")
        or task.get("Branch")
        or ""
    )
    base_branch = str(task.get("BaseBranch") or task.get("TargetBranch") or "")
    commit_sha = str(task.get("CommitSha") or task.get("Commit") or "")
    summary = str(task.get("Summary") or task.get("Title") or "")
    err_msg = str(task.get("Error") or task.get("ErrorMessage") or "")
    if status == "failed" and err_msg:
        error = err_msg

    if status == "succeeded" and not mr_url:
        # 业务约定：成功但无 MR 视为失败。
        status = "failed"
        error = "no_mr"

    return TaskSnapshot(
        task_id=task_id,
        status=status,
        base_branch=base_branch,
        work_branch=work_branch,
        mr_url=mr_url,
        commit_sha=commit_sha,
        summary=summary,
        error=error,
    )


def _task_raw_status(task: dict[str, Any]) -> str:
    return str(task.get("Status") or task.get("State") or "").lower()
