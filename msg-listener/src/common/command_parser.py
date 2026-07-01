"""命令解析：把消息文本解析成结构化命令。

- 仅接受以 `/` 开头的消息
- 支持 `/help`、`/show`、`/config`、`/del-out`、`/del-out confirm`
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

@dataclass
class ParsedCommand:
    name: str  # help / show / config / del-out / unknown
    args: list[str]
    kvs: dict[str, str]
    raw_text: str
    raw_after_strip: str  # 兼容保留字段；当前等于原始命令文本


def parse_command(text: str, *, prefix: str = "/") -> Optional[ParsedCommand]:
    """解析命令；非命令返回 None。"""
    if text is None:
        return None
    raw_text = text
    if not raw_text.startswith(prefix):
        return None
    body = raw_text[len(prefix):].strip()
    if not body:
        return None

    parts = body.split(maxsplit=1)
    head = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""

    args: list[str] = []
    kvs: dict[str, str] = {}
    if rest:
        for tok in rest.split():
            if "=" in tok:
                k, v = tok.split("=", 1)
                k = k.strip()
                v = v.strip()
                if k:
                    kvs[k] = v
            else:
                args.append(tok)

    name = {
        "help": "help",
        "h": "help",
        "?": "help",
        "show": "show",
        "config": "config",
        "devtask": "devtask",
        "dev-task": "devtask",
        "del-out": "del-out",
        "delout": "del-out",
    }.get(head, head)

    return ParsedCommand(
        name=name,
        args=args,
        kvs=kvs,
        raw_text=raw_text,
        raw_after_strip=raw_text,
    )
