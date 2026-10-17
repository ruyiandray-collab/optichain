"""解析层单测：夹具来自 2026-10-17 真实 iFinD 返回的节选（tests/fixtures/answers.json）。"""
import json
from datetime import date
from pathlib import Path

import pytest

from pipeline.tables import parse_tables, parse_number, parse_header, parse_share_list, is_null
from pipeline.fiscal import map_label, single_quarters, ttm

FX = json.loads((Path(__file__).parent / "fixtures" / "answers.json").read_text(encoding="utf-8"))


# ---- 数值 ----------------------------------------------------------------
@pytest.mark.parametrize("cell,expected", [
    ("417.7786亿", 41777860000.0),
    ("1.5693万亿", 1569300000000.0),
    ("7.904245614275E11", 790424561427.5),
    ("-30116000000.0", -30116000000.0),
    ("37456518745.9000", 37456518745.9),
    ("46.2522", 46.2522),
    ("1,234.5", 1234.5),
])
def test_parse_number(cell, expected):
    assert parse_number(cell) == pytest.approx(expected, rel=0, abs=1e-6)


@pytest.mark.parametrize("cell", ["\t", "", "null", "--", None, " "])
def test_parse_number_nulls(cell):
    assert parse_number(cell) is None
    assert is_null(cell)


def test_parse_number_rejects_text():
    with pytest.raises(ValueError):
        parse_number("光通信收发模块")


# ---- 表头 ----------------------------------------------------------------
def test_header_unit_and_rank():
    h = parse_header("主要客户占比（单位：%；排名：第1名）")
    assert (h.name, h.unit, h.rank) == ("主要客户占比", "%", 1)
    h = parse_header("主营构成-项目收入(按产品)（排名：第2名）")
    assert (h.name, h.unit, h.rank) == ("主营构成-项目收入(按产品)", None, 2)
    h = parse_header("总市值[20261016]")
    assert (h.name, h.date) == ("总市值", "20261016")
    h = parse_header("营业收入（单位：元）")
    assert (h.name, h.unit) == ("营业收入", "元")


# ---- 表格 ----------------------------------------------------------------
def test_mainbiz_two_tables():
    tables = parse_tables(FX["s03_mainbiz"])
    assert len(tables) == 2
    names, vals = tables
    assert names.rows[0][names.header("主营构成-项目名称(按产品)", 1).raw] == "光通信收发模块"
    row = vals.rows[0]
    rev1 = parse_number(row[vals.header("主营构成-项目收入(按产品)", 1).raw])
    rev2 = parse_number(row[vals.header("主营构成-项目收入(按产品)", 2).raw])
    assert rev1 == pytest.approx(37456518745.90)
    assert rev2 == pytest.approx(783416894.77)
    # 第 3 名起为空（\t）
    assert parse_number(row[vals.header("主营构成-项目收入(按产品)", 3).raw]) is None


def test_escaped_pipes_in_cells():
    t = parse_tables(FX["s13_pick_biz"])[0]
    first = t.rows[0]
    assert first["股票简称"] == "源杰科技"
    assert first["相关产品名称"] == "光芯片||激光器芯片||探测器芯片"


def test_quarter_series_rows():
    t = parse_tables(FX["s17_q_series"])[0]
    dates = [r["日期"] for r in t.rows]
    assert dates == ["20260630", "20260331", "20251231", "20250930", "20250630", "20250331"]


def test_share_list():
    t = parse_tables(FX["s05_mainbiz_region"])[0]
    shares = parse_share_list(t.rows[0]["主营业务构成"])
    assert shares == [("境外", 90.5768), ("境内", 9.4232)]


def test_no_table_returns_empty():
    assert parse_tables("查询结果为空") == []
    assert parse_tables(None) == []


# ---- 财年映射（期望值来自 SEC XBRL，见 data_probe.md 4.2） ------------------------
@pytest.mark.parametrize("label,start,end,name", [
    ("20250630", date(2024, 7, 1), date(2024, 12, 31), "FY2025 H1"),
    ("20250930", date(2024, 7, 1), date(2025, 3, 31), "FY2025 9M"),
    ("20251231", date(2024, 7, 1), date(2025, 6, 30), "FY2025 FY"),
    ("20260331", date(2025, 7, 1), date(2025, 9, 30), "FY2026 Q1"),
    ("20260630", date(2025, 7, 1), date(2025, 12, 31), "FY2026 H1"),
    ("20261231", date(2025, 7, 1), date(2026, 6, 30), "FY2026 FY"),
])
def test_june_fiscal_year_mapping(label, start, end, name):
    p = map_label(label, "0630")
    assert (p.start, p.end, p.name) == (start, end, name)
    assert not p.is_calendar_aligned


def test_calendar_year_identity():
    p = map_label("20260630", "1231")
    assert (p.start, p.end) == (date(2026, 1, 1), date(2026, 6, 30))
    assert p.is_calendar_aligned


def test_bad_label():
    with pytest.raises(ValueError):
        map_label("20260615")


def test_single_quarters_and_ttm_coherent():
    # Coherent 累计营收（iFinD 标签，单位亿美元），推单季度后与 SEC 10-Q 单季度值对照
    cum = {"20250630": 27.828, "20250930": 42.8068, "20251231": 58.1011,
           "20260331": 15.8138, "20260630": 32.6701}
    s = single_quarters(cum, "0630")
    assert s["2025-03-31"] == pytest.approx(14.9788, abs=1e-4)   # 42.8068 − 27.828；SEC 2025-01-01→03-31 为 14.9788 亿
    assert s["2025-06-30"] == pytest.approx(15.2943, abs=1e-4)   # 58.1011 − 42.8068
    assert s["2025-12-31"] == pytest.approx(16.8563, abs=1e-4)   # SEC 2025-10-01→12-31
    assert "2024-12-31" in s and s["2024-12-31"] is None          # 缺 Q1 累计，不能推
    total, ends = ttm(s, "2025-12-31")
    assert ends == ["2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31"]
    assert total == pytest.approx(14.9788 + 15.2943 + 15.8138 + 16.8563, abs=1e-3)


def test_ttm_requires_four_contiguous():
    total, _ = ttm({"2025-06-30": 1.0, "2025-09-30": 1.0, "2025-12-31": 1.0}, "2025-12-31")
    assert total is None
    total, _ = ttm({"2024-12-31": 1.0, "2025-06-30": 1.0, "2025-09-30": 1.0, "2025-12-31": 1.0}, "2025-12-31")
    assert total is None  # 不连续
