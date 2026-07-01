"""一些通用工具：时间转换、链接抽取等。"""
from __future__ import annotations

import re
from datetime import datetime, timezone


# 抽取裸 URL（不含包装）
_BARE_URL_RE = re.compile(r"https?://[^\s<>\[\]\)\(]+")
# 抽取 Markdown 链接 [text](url)
_MD_LINK_RE = re.compile(r"\[([^\[\]]+)\]\((https?://[^\s()]+)\)")


def ms_to_iso_local(ms: str | int | None) -> str:
    """把毫秒/秒级时间戳字符串转为 ISO8601 本地时区字符串。"""
    if ms is None:
        return ""
    try:
        n = int(ms)
    except (TypeError, ValueError):
        return ""
    if n <= 0:
        return ""
    if n > 10_000_000_000:  # 13 位毫秒
        n //= 1000
    dt = datetime.fromtimestamp(n, tz=timezone.utc).astimezone()
    # 输出形如 2026-05-15T14:32:11+08:00
    return dt.isoformat(timespec="seconds")


def local_time_string_to_ms(s: str | int | None) -> int:
    """把 lark-cli 返回的本地时间/ISO 时间转为毫秒时间戳。"""
    if s is None:
        return 0
    if isinstance(s, int):
        return s if s > 10_000_000_000 else s * 1000

    text = str(s).strip()
    if not text:
        return 0
    try:
        n = int(text)
    except ValueError:
        pass
    else:
        return n if n > 10_000_000_000 else n * 1000

    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    for candidate in (normalized, normalized.replace(" ", "T", 1)):
        try:
            dt = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
        return int(dt.timestamp() * 1000)

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            dt = datetime.strptime(text, fmt)
        except ValueError:
            continue
        dt = dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
        return int(dt.timestamp() * 1000)
    return 0


def normalize_local_time_string(s: str) -> str:
    """把 lark-cli 的 "YYYY-MM-DD HH:MM:SS" 本地时间转为 ISO8601 带时区字符串。

    若已是 ISO8601 或无法识别，原样返回。
    """
    if not s:
        return ""
    s = s.strip()
    # 已经像 ISO8601（含 T 或 时区符号）就直接返回
    if "T" in s or s.endswith("Z") or "+" in s[10:] or "-" in s[10:]:
        return s
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            dt = datetime.strptime(s, fmt)
        except ValueError:
            continue
        local_tz = datetime.now().astimezone().tzinfo
        return dt.replace(tzinfo=local_tz).isoformat(timespec="seconds")
    return s


def to_local_date(ms: str | int | None) -> str:
    """把毫秒级时间戳转为本地日期 YYYY-MM-DD。"""
    if ms is None:
        return ""
    try:
        n = int(ms)
    except (TypeError, ValueError):
        return ""
    if n > 10_000_000_000:
        n //= 1000
    dt = datetime.fromtimestamp(n, tz=timezone.utc).astimezone()
    return dt.strftime("%Y-%m-%d")


def extract_links(text: str) -> list[dict]:
    """从规范化的 content 中提取链接。

    - 优先匹配 Markdown 链接 [text](url)
    - 其余裸 URL 单独成项
    返回 [{url, text}, ...]，按出现顺序、去重。
    """
    if not text:
        return []
    seen: set[str] = set()
    result: list[dict] = []

    # Markdown 链接（替换占位以便后续避免重复抽取）
    placeholders: list[tuple[str, str]] = []
    def _take_md(m: re.Match) -> str:
        link_text = m.group(1).strip()
        url = m.group(2).strip()
        token = f"\x00MDLINK{len(placeholders)}\x00"
        placeholders.append((url, link_text))
        if url not in seen:
            seen.add(url)
            result.append({"url": url, "text": link_text or url})
        return token

    consumed = _MD_LINK_RE.sub(_take_md, text)

    # 裸 URL
    for m in _BARE_URL_RE.finditer(consumed):
        url = m.group(0).rstrip(".,;:!?")
        if url not in seen:
            seen.add(url)
            result.append({"url": url, "text": url})

    return result
