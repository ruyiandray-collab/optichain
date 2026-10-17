"""M3 确定性计算：海外单季度推导、TTM、汇率折算、同比与领先滞后、置信度规则、可比表备注。"""
import pytest

from pipeline.metrics import gl_quarters, ttm, fx_window_avg
from pipeline.transmission import yoy, pearson, lead_lag, confidence_of, _shift
from pipeline.comparables import net_margin, pe_of


def rec(ds, field, entity, label, value):
    return {"dataset": ds, "field": field, "entity": entity, "label": label, "value": value, "kind": "number",
            "source": {"call_id": "t", "column": field}}


def test_gl_quarters_june_fy_diff_and_gross_margin():
    # Coherent 真实累计值（亿美元×1e8），财年截至 6 月：标签 20260331 = FY26 Q1（2025-07~09）
    recs = [rec("gl_profile", "会计年结日", "C", None, 630.0),
            rec("gl_cumulative", "营业收入", "C", "20260331", 15.8138e8),
            rec("gl_cumulative", "营业收入", "C", "20260630", 32.6701e8),
            rec("gl_cumulative", "销售毛利率", "C", "20260331", 36.0),
            rec("gl_cumulative", "销售毛利率", "C", "20260630", 36.7927)]
    qs = gl_quarters(recs, "C")
    assert [q["end"] for q in qs] == ["2025-09-30", "2025-12-31"]
    assert qs[1]["revenue"]["value"] == pytest.approx(16.8563e8)            # 32.6701 − 15.8138；SEC 2025-10-01→12-31 为 16.8563 亿
    gp2 = 32.6701e8 * 0.367927 - 15.8138e8 * 0.36
    assert qs[1]["gross_margin"]["value"] == pytest.approx(gp2 / 16.8563e8)
    assert len(qs[1]["revenue"]["inputs"]) == 2                             # 两个累计值都可回查


def test_ttm_requires_contiguous_quarters():
    q = lambda end, v: {"end": end, "start": end[:8] + "01", "fiscal": end, "revenue": {"value": v, "unit": "CNY"}}
    qs = [q("2025-09-30", 1), q("2025-12-31", 2), q("2026-03-31", 3), q("2026-06-30", 4)]
    assert ttm(qs)["revenue"]["value"] == 10
    assert ttm(qs[:3]) is None
    assert ttm([q("2025-06-30", 1)] + qs[1:]) is None                       # 缺 2025Q3，不连续


def test_fx_window_avg_needs_all_months():
    fx = {f"2026-{m:02d}": {"value": 7.0 - m * 0.01, "field": "fx", "label": f"2026{m:02d}", "dataset": "edb",
                            "source": {"call_id": "f"}} for m in range(1, 7)}
    fx = {k: {**v, **v["source"]} for k, v in fx.items()}
    r = fx_window_avg(fx, "2026-01-01", "2026-06-30")
    assert r["rate"] == pytest.approx(sum(7.0 - m * 0.01 for m in range(1, 7)) / 6) and len(r["months"]) == 6
    assert fx_window_avg(fx, "2025-12-01", "2026-06-30") is None             # 缺 2025-12，拒绝折算


def test_yoy_and_shift():
    pts = [{"date": "2025Q1", "value": 10}, {"date": "2026Q1", "value": 15}, {"date": "2026Q2", "value": 1}]
    assert yoy(pts) == {"2026Q1": pytest.approx(0.5)}
    assert _shift("2025Q4", 1) == "2026Q1" and _shift("2026Q1", 2) == "2026Q3"


def test_pearson_small_sample_returns_none():
    assert pearson([1, 2, 3], [1, 2, 3]) is None
    assert pearson([1, 2, 3, 4], [2, 4, 6, 8]) == pytest.approx(1.0)


def test_lead_lag_reports_n():
    x = [{"date": f"{y}Q{q}", "value": 10 + i} for i, (y, q) in enumerate((y, q) for y in (2024, 2025, 2026) for q in (1, 2, 3, 4))]
    r = lead_lag(x, x)["results"]
    assert r[0]["lag_quarters"] == 0 and r[0]["n"] == 8


def S(stance, kind, name):
    return {"stance": stance, "source_kind": kind, "source_name": name}


def test_confidence_rules():
    dc_ok = {"results": [{"r": 0.6, "n": 8}]}
    assert confidence_of([S("support", "notice", "a"), S("support", "news", "b")], dc_ok)[0] == "高"
    assert confidence_of([S("support", "news", "a"), S("support", "news", "b")], dc_ok)[0] == "中"   # 无正式文件
    assert confidence_of([S("support", "notice", "a"), S("support", "news", "b")], None)[0] == "中"  # 无数据验证
    assert confidence_of([S("support", "news", "a"), S("contradict", "news", "b")], None)[0] == "低"
    assert confidence_of([], None)[0] == "低"


def test_net_margin_and_negative_pe():
    m = {"ttm": {"window": ["a", "b"], "revenue": {"value": 3014e6}, "net_profit": {"value": -6935.1e6}},
         "valuation": {"市盈率PE(TTM)": {"value": -10.76}}}
    assert net_margin(m)["value"] == pytest.approx(-6935.1 / 3014)
    pe, note = pe_of(m)
    assert note and "不适用" in note


def test_draft_validation_rejects_new_numbers_and_advice():
    from pipeline.drafts import validate
    facts = [{"id": "F1", "text": "光模块收入占比 99.0%"}, {"id": "F2", "text": "CPO 处于研发及样品交付阶段"}]
    assert validate("company", "公司以光模块为主，占比 99.0% [F1]，CPO 仍在研发 [F2]。", [], facts) == []
    assert any("数字" in e for e in validate("company", "光模块占比约 98% [F1]。", [], facts))          # 改写过的数字
    assert any("不存在" in e for e in validate("company", "CPO 已量产 [F9]。", [], facts))              # 编造的依据编号
    assert any("投资建议" in e for e in validate("company", "建议买入 [F1]。", [], facts))
    assert any("依据" in e for e in validate("company", "公司做光模块。", [], facts))                    # 没有标依据


def test_draft_validation_exact_numbers_and_brackets():
    from pipeline.drafts import validate
    facts = [{"id": "F1", "text": "光模块收入占比 99.0%"}]
    assert any("9%" in e for e in validate("company", "净利率 9% [F1]。", [], facts))                 # 子串“9”不能算命中“99.0”
    assert any("652" in e for e in validate("company", "营收 [652 亿元] [F1]。", [], facts))           # 非 F 编号的方括号照常校验
    assert validate("company", "光模块占比 99% [F1]，有 2 条依据。", [], facts) == []                   # 99 == 99.0；计数个位数豁免


def test_quarter_start_month_end_and_52_53_week():
    from datetime import date
    from pipeline.metrics import quarter_start
    assert quarter_start(date(2026, 6, 30)) == date(2026, 4, 1)
    assert quarter_start(date(2025, 11, 2)) == date(2025, 8, 3)     # 博通 52/53 周财年的季度末
    assert quarter_start(date(2026, 2, 28)) == date(2025, 12, 1)


def test_all_ttm_windows_are_about_one_year():
    """审查发现博通 TTM 窗口被标成 334 天：所有公司的 TTM 窗口都应接近一年（360–372 天）。"""
    import json
    from datetime import date
    from pathlib import Path
    p = Path(__file__).resolve().parent.parent / "data" / "staging" / "metrics.json"
    if not p.exists():
        import pytest; pytest.skip("metrics.json 未生成")
    bad = []
    for code, c in json.loads(p.read_text(encoding="utf-8"))["companies"].items():
        t = c.get("ttm")
        if t:
            days = (date.fromisoformat(t["window"][1]) - date.fromisoformat(t["window"][0])).days + 1
            if not 360 <= days <= 372:
                bad.append((code, t["window"], days))
    assert not bad, bad


def test_draft_validation_per_sentence_and_units():
    """独立审查发现的初稿绕过：整体比对（99.0% 出自 F2 却标 F1）、只靠 used、全角 / 中文数字、单位错配。"""
    from pipeline.drafts import validate
    facts = [{"id": "F1", "text": "毛利率 45.4%"}, {"id": "F2", "text": "光模块收入占比 99.0%；营收 652.29 亿元"}]
    assert any("F1" in e for e in validate("company", "毛利率 99.0% [F1]。", [], facts))            # 数字出自 F2，不能标 F1
    assert any("标注依据" in e for e in validate("company", "光模块占比 99.0%。", ["F2"], facts))     # 只在 used 里列编号不算
    assert any("中文数字" in e for e in validate("company", "毛利率约百分之四十五 [F1]。", [], facts))
    assert any(("不在" in e or "不符" in e) for e in validate("company", "营收 652.29% [F2]。", [], facts))  # 单位错配
    assert any(("不在" in e or "不符" in e) for e in validate("company", "毛利率 ９% [F1]。", [], facts))    # 全角数字
    assert validate("company", "光模块占比 99% [F2]，营收 652.29 亿元 [F2]，毛利率 45.4% [F1]。", [], facts) == []


def test_draft_validation_crosscheck_counterexamples():
    """交叉核对（astra）给出的初稿反例：指标错配、无数字断言无依据、长句只在句尾挂无关 [F]、币种 / 符号 / 修饰词 / 倍数。"""
    from pipeline.drafts import validate
    facts = [{"id": "F1", "text": "营收同比 99.0%；营收 652.29 亿元"}, {"id": "F2", "text": "Capex 环比 -27.2%"},
             {"id": "F3", "text": "研发持续推进"}]
    bad = ["净利率 99.0% [F1]。",                                   # 指标错配
           "公司是全球行业龙头，地位非常稳固。",                       # 无依据断言
           "营收 652.29 亿元，研发继续推进 [F3]。",                    # 数字不在该句引用的事实里
           "营收 652.29 亿美元 [F1]。", "Capex 环比 +27.2% [F2]。", "营收超 652.29 亿元 [F1]。", "营收翻了三番 [F1]。"]
    for d in bad:
        assert validate("company", d, [], facts), d
    assert validate("company", "营收同比 99.0%，营收 652.29 亿元 [F1]；Capex 环比 -27.2% [F2]。", [], facts) == []


def test_sec_form_numbers_are_not_facts():
    from pipeline.drafts import validate
    f = [{"id": "F1", "text": "Coherent 10-K 原文：for use in the data center"}]
    assert validate("company", "Coherent 的数据中心业务仅10-K有定性描述 [F1]。", [], f) == []
