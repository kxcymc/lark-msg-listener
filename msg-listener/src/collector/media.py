"""图片 / 视频下载到本地。

调用 `lark-cli im +messages-resources-download`：
- image: --type image
- video / media: --type file
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from ..common import cli

logger = logging.getLogger(__name__)


_IMAGE_KEY_RE = re.compile(r"\[Image:\s*([^\]\s]+)\]")
_VIDEO_KEY_RE = re.compile(r'<video\s+[^>]*?key="([^"]+)"')
_VIDEO_NAME_RE = re.compile(r'<video\s+[^>]*?name="([^"]+)"')
_FILE_KEY_RE = re.compile(r'<file\s+[^>]*?key="([^"]+)"')


def extract_image_key(content: str) -> str | None:
    if not content:
        return None
    m = _IMAGE_KEY_RE.search(content)
    return m.group(1) if m else None


def extract_image_keys(content: str) -> list[str]:
    if not content:
        return []
    keys: list[str] = []
    for match in _IMAGE_KEY_RE.finditer(content):
        key = match.group(1)
        if key and key not in keys:
            keys.append(key)
    return keys


def extract_video_key_and_name(content: str) -> tuple[str | None, str | None]:
    if not content:
        return None, None
    key_match = _VIDEO_KEY_RE.search(content)
    name_match = _VIDEO_NAME_RE.search(content)
    return (
        key_match.group(1) if key_match else None,
        name_match.group(1) if name_match else None,
    )


async def download_resource(
    *,
    message_id: str,
    file_key: str,
    resource_type: str,
    output_path: Path,
    record_dir: Path,
) -> str | None:
    """调用 lark-cli 下载并返回相对 record_dir 的路径。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # `--output` 必须是相对路径（lark-cli 拒绝绝对路径），且相对于 lark-cli 的 cwd。
    # 优先把 cwd 切到 record_dir，然后传入 output 在 record_dir 内的相对路径，
    # 这样无论 record_dir 与 Path.cwd() 是否同盘（Windows 下 C: vs D:）都能成立。
    cli_cwd: str | None = None
    record_dir_resolved = record_dir.resolve()
    output_resolved = output_path.resolve()
    try:
        rel_path = output_resolved.relative_to(record_dir_resolved)
        cli_cwd = str(record_dir_resolved)
    except ValueError:
        # output 不在 record_dir 内时回退：尝试相对当前 cwd。
        try:
            rel_path = output_resolved.relative_to(Path.cwd().resolve())
        except ValueError:
            # Windows 跨盘等情况：把 cwd 切到 output 所在目录，传文件名。
            cli_cwd = str(output_resolved.parent)
            rel_path = Path(output_resolved.name)

    args = [
        "im",
        "+messages-resources-download",
        "--message-id",
        message_id,
        "--file-key",
        file_key,
        "--type",
        resource_type,
        "--output",
        rel_path.as_posix(),
    ]
    try:
        data = await cli.run(
            args, identity="user", auto_format=False, timeout=120, cwd=cli_cwd
        )
    except cli.CLIError as exc:
        logger.warning(
            "下载失败 message=%s file_key=%s type=%s: %s",
            message_id,
            file_key,
            resource_type,
            exc,
        )
        return None

    saved = (data or {}).get("saved_path")
    if not saved:
        return None
    saved_abs = Path(saved).resolve()
    try:
        rel = saved_abs.relative_to(record_dir.resolve())
        return f"./{rel.as_posix()}"
    except ValueError:
        return str(saved_abs)


async def download_for_message(
    msg: dict,
    *,
    record_dir: Path,
    image_dir: Path,
    video_dir: Path,
) -> dict:
    """根据 normalize_message 输出结构下载媒体并写入 local_path / media 字段。"""
    msg_type = msg.get("type") or ""
    text = msg.get("text") or ""
    message_id = msg.get("message_id") or ""
    if not message_id:
        return msg

    if msg_type == "image":
        key = extract_image_key(text)
        if not key:
            logger.debug("无法从 content 中识别 image_key: %s", text)
            return msg
        local = await _download_image(
            message_id=message_id,
            key=key,
            image_dir=image_dir,
            record_dir=record_dir,
        )
        if local:
            msg["local_path"] = local
            _append_media_item(msg, kind="image", local_path=local)
    elif msg_type == "post":
        for key in extract_image_keys(text):
            local = await _download_image(
                message_id=message_id,
                key=key,
                image_dir=image_dir,
                record_dir=record_dir,
            )
            if local:
                if not msg.get("local_path"):
                    msg["local_path"] = local
                _append_media_item(msg, kind="image", local_path=local)
    elif msg_type in {"video", "media"}:
        key, name = extract_video_key_and_name(text)
        if not key:
            return msg
        filename = name or key
        target = video_dir / filename
        local = await download_resource(
            message_id=message_id,
            file_key=key,
            resource_type="file",
            output_path=target,
            record_dir=record_dir,
        )
        if local:
            msg["local_path"] = local
            _append_media_item(msg, kind="video", local_path=local)
    # 文档要求只下载图片/视频；其他媒体（file、audio）跳过
    return msg


async def _download_image(
    *,
    message_id: str,
    key: str,
    image_dir: Path,
    record_dir: Path,
) -> str | None:
    return await download_resource(
        message_id=message_id,
        file_key=key,
        resource_type="image",
        output_path=image_dir / key,
        record_dir=record_dir,
    )


def _append_media_item(msg: dict, *, kind: str, local_path: str) -> None:
    media = msg.setdefault("media", [])
    if not isinstance(media, list):
        media = []
        msg["media"] = media
    if any(isinstance(item, dict) and item.get("local_path") == local_path for item in media):
        return
    media.append(
        {
            "kind": kind,
            "local_path": local_path,
            "name": Path(local_path).name,
        }
    )
