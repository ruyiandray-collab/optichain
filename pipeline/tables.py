"""iFinD 返回的 Markdown 表格解析与数值规范化。

已知格式问题（data_probe.md 第 4.3 节）：
- 一个 answer 里可能有多张表，中间夹着说明文字
- 单元格里用 \\| 转义竖线（如 “光芯片\\|\\|激光器芯片”）
- 数值写法有 “417.7786亿” “1.5693万亿” “7.904245614275E11” “-30116000000.0” 几种
- 空值写法有 \\t、空串、“null”、“--”
- 表头带单位或排名，如 “营业收入（单位：元）” “主营构成-项目收入(按产品)（排名：第1名）”
  “主要客户占比（单位：%；排名：第1名）” “总市值[20261016]”
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

NULLS = {"", "null", "none", "nan", "--", "-", "—"}
SCALE = {"万亿": Decimal("1e12"), "亿": Decimal("1e8"), "万": Decimal("1e4")}
_NUM_RE = re.compile(r"^([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)(万亿|亿|万)?$")
_HEADER_SUFFIX_RE = re.compile(r"（([^（）]*)）$")
_BRACKET_DATE_RE = re.compile(r"\[(\d{8})\]$")


@dataclass
class Header:
    raw: str
    name: str
    unit: str | None = None
    rank: int | None = None
    date: str | None = None


@dataclass
class Table:
    headers: list[Header]
    rows: list[dict[str, str]]  # 以 header.raw 为键的原始单元格

    def names(self) -> list[str]:
        return [h.name for h in self.headers]

    def header(self, name: str, rank: int | None = None) -> Header | None:
        for h in self.headers:
            if h.name == name and (rank is None or h.rank == rank):
                return h
        return None


def parse_header(raw: str) -> Header:
    s = raw.strip()
    h = Header(raw=s, name=s)
    m = _BRACKET_DATE_RE.search(s)
    if m:
        h.date = m.group(1)
        s = s[: m.start()].strip()
    m = _HEADER_SUFFIX_RE.search(s)
    if m and ("单位" in m.group(1) or "排名" in m.group(1)):
        for part in re.split(r"[；;]", m.group(1)):
            part = part.strip()
            if part.startswith("单位"):
                h.unit = part.split("：", 1)[-1].strip() or None
            elif part.startswith("排名"):
                rm = re.search(r"第(\d+)名", part)
                h.rank = int(rm.group(1)) if rm else None
        s = s[: m.start()].strip()
    h.name = s
    return h


def _split_row(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|") and not line.endswith("\\|"):
        line = line[:-1]
    cells, buf, i = [], [], 0
    while i < len(line):
        if line[i] == "\\" and i + 1 < len(line) and line[i + 1] == "|":
            buf.append("|")
            i += 2
            continue
        if line[i] == "|":
            cells.append("".join(buf))
            buf = []
        else:
            buf.append(line[i])
        i += 1
    cells.append("".join(buf))
    return [c.strip(" ") for c in cells]


def _is_separator(line: str) -> bool:
    cells = _split_row(line)
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", c.strip()) for c in cells if c.strip())


def parse_tables(answer: str) -> list[Table]:
    """从 answer 文本中解析出所有 Markdown 表格。"""
    if not isinstance(answer, str):
        return []
    lines = answer.replace("\r\n", "\n").split("\n")
    tables: list[Table] = []
    i = 0
    while i < len(lines) - 1:
        if lines[i].strip().startswith("|") and _is_separator(lines[i + 1]):
            headers = [parse_header(c) for c in _split_row(lines[i])]
            rows = []
            j = i + 2
            while j < len(lines) and lines[j].strip().startswith("|"):
                cells = _split_row(lines[j])
                cells += [""] * (len(headers) - len(cells))
                rows.append({h.raw: cells[k] for k, h in enumerate(headers)})
                j += 1
            tables.append(Table(headers, rows))
            i = j
        else:
            i += 1
    return tables


def parse_number(cell: str | None) -> float | None:
    """'417.7786亿' -> 41777860000.0；空值返回 None；非数值抛 ValueError。"""
    if cell is None:
        return None
    s = str(cell).strip().replace(",", "")
    if s.lower() in NULLS:
        return None
    m = _NUM_RE.match(s)
    if not m:
        raise ValueError(f"not a number: {cell!r}")
    try:
        d = Decimal(m.group(1))
    except InvalidOperation as e:  # pragma: no cover
        raise ValueError(f"not a number: {cell!r}") from e
    if m.group(2):
        d *= SCALE[m.group(2)]
    return float(d)


def is_null(cell: str | None) -> bool:
    return cell is None or str(cell).strip().lower() in NULLS


def parse_share_list(cell: str) -> list[tuple[str, float]]:
    """'境外:90.5768%;境内:9.4232%' -> [('境外', 90.5768), ('境内', 9.4232)]"""
    out = []
    for part in re.split(r"[;；]", cell or ""):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^(.*?)[:：]\s*([+-]?\d+(?:\.\d+)?)%$", part)
        if not m:
            raise ValueError(f"bad share item: {part!r}")
        out.append((m.group(1).strip(), float(m.group(2))))
    return out
