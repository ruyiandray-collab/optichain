"""报告期标签 → 真实会计区间。

iFinD 对非自然年财年的公司，“日期”列是财年期间标签，不是真实期末日
（data_probe.md 4.2，Coherent 6/6 与 SEC XBRL 一致）：
    标签年份 = 财年（财年截止日所在的自然年）
    0331 → 财年 Q1 累计，0630 → H1 累计，0930 → 9M 累计，1231 → 全年
自然年财年（会计年结日 1231）时，标签就是真实期末日。
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date

LABEL_QUARTERS = {"0331": 1, "0630": 2, "0930": 3, "1231": 4}
PERIOD_NAME = {1: "Q1", 2: "H1", 3: "9M", 4: "FY"}


@dataclass(frozen=True)
class Period:
    label: str          # iFinD 原始标签，如 20250630
    fiscal_year: int
    quarters: int       # 累计季度数 1..4
    start: date         # 财年起始日（累计区间起点）
    end: date           # 真实期末日

    @property
    def name(self) -> str:
        return f"FY{self.fiscal_year} {PERIOD_NAME[self.quarters]}"

    @property
    def is_calendar_aligned(self) -> bool:
        return self.label == self.end.strftime("%Y%m%d")


def _add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    y = d.year + m // 12
    m = m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def _month_end(y: int, m: int) -> date:
    return date(y, m, calendar.monthrange(y, m)[1])


def map_label(label: str, fye: str = "1231") -> Period:
    """label: 'YYYYMMDD'（MMDD ∈ 0331/0630/0930/1231）；fye: 会计年结日 'MMDD'。"""
    label = str(label).strip()
    if len(label) != 8 or label[4:] not in LABEL_QUARTERS:
        raise ValueError(f"unsupported period label: {label!r}")
    fye = str(fye).strip().zfill(4)
    fy = int(label[:4])
    q = LABEL_QUARTERS[label[4:]]
    fye_month = int(fye[:2])
    fy_end = _month_end(fy, fye_month)
    fy_start = _add_months(date(fy_end.year, fy_end.month, 1), -11)
    end_month = _add_months(fy_start, 3 * q - 1)
    end = _month_end(end_month.year, end_month.month)
    return Period(label=label, fiscal_year=fy, quarters=q, start=fy_start, end=end)


def single_quarters(cumulative: dict[str, float | None], fye: str = "1231") -> dict[str, float | None]:
    """由累计值推单季度值：Q1 = 累计Q1，Qn = 累计n − 累计(n−1)。缺任一相邻累计值则为 None。
    返回键为“真实期末日 YYYY-MM-DD”。"""
    by_fy: dict[int, dict[int, float | None]] = {}
    periods = {}
    for label, v in cumulative.items():
        p = map_label(label, fye)
        by_fy.setdefault(p.fiscal_year, {})[p.quarters] = v
        periods[(p.fiscal_year, p.quarters)] = p
    out: dict[str, float | None] = {}
    for (fy, q), p in sorted(periods.items()):
        cur = by_fy[fy].get(q)
        if q == 1:
            val = cur
        else:
            prev = by_fy[fy].get(q - 1)
            val = None if cur is None or prev is None else cur - prev
        out[p.end.isoformat()] = val
    return out


def ttm(singles: dict[str, float | None], as_of_end: str) -> tuple[float | None, list[str]]:
    """截至 as_of_end（含）最近 4 个连续单季度之和；不足 4 个或有缺失返回 None。"""
    ends = sorted(k for k in singles if k <= as_of_end)[-4:]
    if len(ends) < 4 or any(singles[e] is None for e in ends):
        return None, ends
    # 校验连续：相邻期末相差约 3 个月
    ds = [date.fromisoformat(e) for e in ends]
    for a, b in zip(ds, ds[1:]):
        if not 85 <= (b - a).days <= 95:
            return None, ends
    return sum(singles[e] for e in ends), ends
