"""校验逻辑单测（合成数据，不联网）。"""
from pipeline.checks import check_cn_quarterly, check_segments, sec_singles


def rec(dataset, field, entity, label, value):
    return {"dataset": dataset, "field": field, "entity": entity, "label": label, "value": value,
            "kind": "number", "source": {"call_id": "t"}}


def test_cn_quarterly_identity_pass_and_fail():
    # 中际旭创真实值（s17）：26Q1 194.964 亿 + 26Q2 222.8146 亿 = H1 417.7786 亿
    recs = [rec("cn_quarterly", "单季度.营业收入", "A", "20260331", 194.964e8),
            rec("cn_quarterly", "单季度.营业收入", "A", "20260630", 222.8146e8),
            rec("cn_quarterly", "营业收入", "A", "20260331", 194.964e8),
            rec("cn_quarterly", "营业收入", "A", "20260630", 417.7786e8),
            # 人为制造冲突：累计值错 1 亿
            rec("cn_quarterly", "单季度.营业收入", "B", "20260331", 10e8),
            rec("cn_quarterly", "营业收入", "B", "20260331", 11e8)]
    res = {(r["entity"], r["label"]): r["status"] for r in check_cn_quarterly(recs)}
    assert res[("A", "20260630")] == "pass"
    assert res[("A", "20260331")] == "pass"
    assert res[("B", "20260331")] == "fail"


def test_cn_quarterly_missing_single_is_skipped():
    recs = [rec("cn_quarterly", "营业收入", "A", "20260630", 1e8)]
    assert check_cn_quarterly(recs)[0]["status"] == "skipped"


def test_segment_sum_with_internal_offset():
    # 中瓷电子式：含负的“内部抵销”项，合计仍应等于营收
    recs = [rec("cn_mainbiz_product", "主营构成-项目收入(按产品)", "C", "20251231", 20e8),
            rec("cn_mainbiz_product", "主营构成-项目收入(按产品)", "C", "20251231", 13e8),
            rec("cn_mainbiz_product", "主营构成-项目收入(按产品)", "C", "20251231", -4e8),
            rec("cn_quarterly", "营业收入", "C", "20251231", 29e8)]
    r = check_segments(recs, "cn_mainbiz_product", "产品")[0]
    assert r["status"] == "pass" and r["n_items"] == 3


def test_segment_deviation_warns():
    recs = [rec("cn_mainbiz_product", "主营构成-项目收入(按产品)", "C", "20251231", 20e8),
            rec("cn_quarterly", "营业收入", "C", "20251231", 30e8)]
    assert check_segments(recs, "cn_mainbiz_product", "产品")[0]["status"] == "warn"


def test_sec_singles_from_ytd():
    facts = [
        {"start": "2025-01-01", "end": "2025-03-31", "val": 10, "accn": "a"},
        {"start": "2025-01-01", "end": "2025-06-30", "val": 25, "accn": "b"},
        {"start": "2025-01-01", "end": "2025-09-30", "val": 45, "accn": "c"},
        {"start": "2025-01-01", "end": "2025-12-31", "val": 70, "accn": "d"},
    ]
    s = sec_singles(facts)
    assert {k: v[0] for k, v in s.items()} == {"2025-03-31": 10, "2025-06-30": 15, "2025-09-30": 20, "2025-12-31": 25}
