"""纯标准库 xlsx 生成器（不依赖 openpyxl 等第三方库）。

为什么自己拼 OOXML：项目约束「最小依赖、三端通用」，xlsx 本质就是
一个 zip 容器 + 一组固定的 OOXML 部件，stdlib 的 zipfile 足以胜任，
新增 openpyxl 会违背依赖约束。

为什么文本统一用 inlineStr：相比 sharedStrings 需要额外维护一张
全局字符串表并在每个单元格里写索引，inlineStr 把文本直接内联到单元格，
实现更简单、单测更易验证，且对本地一次性导出场景没有体积压力。
数值则写成原生数字单元格（省去 t 属性），便于前端/Excel 直接做计算。
"""
from __future__ import annotations

import io
import zipfile
from typing import Any, Iterable, Sequence

# 一个 sheet 用 (名称, 行列表) 表示；每行是一组单元格值（首行约定为表头）。
SheetData = tuple[str, list[list[Any]]]

_XML_DECL = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'


def _xml_escape(text: str) -> str:
    """转义 XML 特殊字符，避免单元格文本破坏 OOXML 结构。"""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _col_letter(idx: int) -> str:
    """1-based 列序号转 Excel 列字母（1->A, 27->AA）。"""
    letters = ""
    while idx > 0:
        idx, rem = divmod(idx - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _cell_xml(ref: str, value: Any) -> str:
    """单个单元格 XML。

    bool 先于 int 判断（Python 中 bool 是 int 子类），避免 True/False 被
    写成 1/0；纯数字写成数值单元格，其余一律 inlineStr 文本。
    """
    if isinstance(value, bool):
        value = "TRUE" if value else "FALSE"
    elif isinstance(value, (int, float)):
        return f'<c r="{ref}"><v>{value}</v></c>'
    if value is None:
        value = ""
    text = _xml_escape(str(value))
    # xml:space="preserve" 保留前后空白，避免被解析器折叠。
    return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'


def _sheet_xml(rows: Sequence[Sequence[Any]]) -> str:
    """把二维行数据渲染为 worksheet 部件 XML。"""
    parts = [
        _XML_DECL,
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
        "<sheetData>",
    ]
    for r_idx, row in enumerate(rows, start=1):
        parts.append(f'<row r="{r_idx}">')
        for c_idx, value in enumerate(row, start=1):
            parts.append(_cell_xml(f"{_col_letter(c_idx)}{r_idx}", value))
        parts.append("</row>")
    parts.append("</sheetData></worksheet>")
    return "".join(parts)


def _content_types_xml(sheet_count: int) -> str:
    """[Content_Types].xml：声明 rels/xml 默认类型与 workbook、各 worksheet 的覆盖类型。"""
    overrides = [
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
    ]
    for i in range(1, sheet_count + 1):
        overrides.append(
            f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        )
    return (
        f"{_XML_DECL}"
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        f"{''.join(overrides)}"
        "</Types>"
    )


def _root_rels_xml() -> str:
    """_rels/.rels：根关系，指向主 workbook 部件。"""
    return (
        f"{_XML_DECL}"
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/>'
        "</Relationships>"
    )


def _workbook_xml(names: Iterable[str]) -> str:
    """xl/workbook.xml：登记各 sheet 名称与 r:id（rId{i} 对应第 i 张表）。"""
    sheets = []
    for i, name in enumerate(names, start=1):
        sheets.append(
            f'<sheet name="{_xml_escape(name)}" sheetId="{i}" r:id="rId{i}"/>'
        )
    return (
        f"{_XML_DECL}"
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f"<sheets>{''.join(sheets)}</sheets>"
        "</workbook>"
    )


def _workbook_rels_xml(sheet_count: int) -> str:
    """xl/_rels/workbook.xml.rels：把 rId{i} 关系映射到对应 worksheet 部件。"""
    rels = []
    for i in range(1, sheet_count + 1):
        rels.append(
            f'<Relationship Id="rId{i}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            f'Target="worksheets/sheet{i}.xml"/>'
        )
    return (
        f"{_XML_DECL}"
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f"{''.join(rels)}"
        "</Relationships>"
    )


def _safe_sheet_name(name: str, used: set[str], fallback_idx: int) -> str:
    """规整 sheet 名以满足 Excel 限制：<=31 字符、禁用 []:*?/\\、不可重名。

    这是防御性处理：当前调用方传入的名称都是安全常量，但保留约束便于复用。
    """
    cleaned = "".join("_" if ch in r"[]:*?/\\" else ch for ch in name).strip()
    cleaned = (cleaned or f"Sheet{fallback_idx}")[:31]
    candidate = cleaned
    suffix = 1
    while candidate.lower() in used:
        tail = f"_{suffix}"
        candidate = cleaned[: 31 - len(tail)] + tail
        suffix += 1
    used.add(candidate.lower())
    return candidate


def build_xlsx(sheets: Sequence[SheetData]) -> bytes:
    """把若干 (sheet 名, 行数据) 组装成 xlsx 字节流。

    全程在内存中完成（io.BytesIO + zipfile），不写任何本地文件，符合
    「导出不落盘、不留历史」的约定。至少保证有一张表，避免空 workbook
    被 Excel 判为损坏。
    """
    normalized: list[SheetData] = list(sheets) or [("Sheet1", [[]])]

    used: set[str] = set()
    names = [
        _safe_sheet_name(name, used, idx)
        for idx, (name, _rows) in enumerate(normalized, start=1)
    ]

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _content_types_xml(len(normalized)))
        zf.writestr("_rels/.rels", _root_rels_xml())
        zf.writestr("xl/workbook.xml", _workbook_xml(names))
        zf.writestr("xl/_rels/workbook.xml.rels", _workbook_rels_xml(len(normalized)))
        for i, (_name, rows) in enumerate(normalized, start=1):
            zf.writestr(f"xl/worksheets/sheet{i}.xml", _sheet_xml(rows))
    return buf.getvalue()
