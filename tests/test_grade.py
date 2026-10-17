"""分级规则单测。引文样例来自 2026-10-17 真实返回（data_probe.md 3.1/3.2）。"""
import pytest

from pipeline.grade import rule_stage, grade_entity, is_stale
from pipeline.evidence import concept_source, notice_kind


@pytest.mark.parametrize("quote,stage", [
    ("目前中瓷电子已有多款1.6T光模块产品处于用户交样阶段，性能已通过客户验证，处于小批量交付阶段", "rnd"),  # “小批量交付”不能被“批量交付”误判为量产
    ("3.2T产能正在准备中，尚未到送样阶段", None),
    ("数通800G硅光模块和共封装光学（CPO）产品正在研发中", "rnd"),
    ("公司持续关注光模块技术的演进趋势", "talk"),
    ("公司目前不涉及CPO相关业务", "deny"),
    ("光模块用陶瓷外壳、陶瓷基板订单大幅放量", "order"),
])
def test_rule_stage(quote, stage):
    assert rule_stage(quote) == stage


def test_concept_source_parsing():
    assert concept_source("2024年1月22日互动易回复：目前中瓷电子已有多款1.6T光模块") == ("hdy", "2024-01-22")
    assert concept_source("根据2025年年报：CPO 共封装-硅透镜研发项目")[0] == "periodic"
    assert concept_source("2024年9月10日上海贝岭2024年半年度业绩说明会内容")[0] == "ir"
    assert notice_kind("中际旭创：投资者关系活动记录表20260712") == "ir"
    assert notice_kind("中际旭创：2025年年度报告") == "periodic"
    assert notice_kind("欧菲光：关于媒体报道的澄清公告") == "clarification"


def test_stale():
    assert is_stale("2023-06-14")
    assert not is_stale("2026-03-24")
    assert not is_stale(None)


def ev(eid, kind, origin, d, text="x"):
    return {"evidence_id": eid, "source_kind": kind, "origin_kind": origin, "date": d, "text": text,
            "source_name": eid}


def claim(eid, quote, concept="cpo", llm_stage=None):
    return {"evidence_id": eid, "concept": concept, "quote": quote, "llm_stage": llm_stage, "verified": True}


def test_hdy_mass_production_capped_at_c():
    evs = [ev("e1", "ifind_concept", "hdy", "2025-08-01")]
    cls = [claim("e1", "800G光芯片产品已实现小批量出货，CPO光引擎量产")]
    r = grade_entity("X", "cpo", evs, cls, [], [])
    assert r["grade"] == "C"


def test_formal_order_is_b_and_stale_downgrades():
    evs = [ev("e1", "notice", "ir", "2026-05-09"), ev("e2", "notice", "ir", "2023-05-09")]
    assert grade_entity("X", "cpo", evs[:1], [claim("e1", "CPO光引擎已批量出货")], [], [])["grade"] == "B"
    assert grade_entity("X", "cpo", evs[1:], [claim("e2", "CPO光引擎已批量出货")], [], [])["grade"] == "C"


def test_unverified_claim_ignored_and_no_evidence_is_e():
    evs = [ev("e1", "notice", "ir", "2026-05-09")]
    c = claim("e1", "CPO光引擎已批量出货")
    c["verified"] = False
    r = grade_entity("X", "cpo", evs, [c], [], [{"query": "q"}])
    assert r["grade"] == "E" and r["basis"] == "无证据" and r["search_scope"] == [{"query": "q"}]


def test_newer_denial_downgrades_and_flags_conflict():
    evs = [ev("e1", "news", "hdy_media", "2025-03-01"), ev("e2", "notice", "clarification", "2026-02-01")]
    cls = [claim("e1", "公司CPO产品已量产"), claim("e2", "公司目前不涉及CPO相关业务")]
    r = grade_entity("X", "cpo", evs, cls, [], [])
    assert r["conflict"] and r["conflict"]["original_grade"] == "C" and r["grade"] == "D"


def test_llm_rule_disagreement_flags_review():
    evs = [ev("e1", "notice", "ir", "2026-05-09")]
    r = grade_entity("X", "cpo", evs, [claim("e1", "CPO产品正在研发中", llm_stage="order")], [], [])
    assert r["grade"] == "C" and r["needs_review"]


def test_grade_a_from_product_revenue():
    recs = [
        {"dataset": "cn_mainbiz_product", "entity": "X", "field": "主营构成-项目名称(按产品)", "label": "20251231",
         "rank": 1, "value": "光通信收发模块", "source": {"call_id": "n"}},
        {"dataset": "cn_mainbiz_product", "entity": "X", "field": "主营构成-项目收入(按产品)", "label": "20251231",
         "rank": 1, "value": 37456518745.9, "source": {"call_id": "v"}},
        {"dataset": "cn_quarterly", "entity": "X", "field": "营业收入", "label": "20251231",
         "value": 38239940000.0, "source": {"call_id": "r"}},
    ]
    r = grade_entity("X", "optical_interconnect", [], [], recs, [])
    assert r["grade"] == "A"
    assert r["a_detail"]["share"] == pytest.approx(37456518745.9 / 38239940000.0)


def test_claim_without_concept_word_is_dropped():
    evs = [ev("e1", "notice", "periodic", "2026-03-31")]
    r = grade_entity("X", "cpo", evs, [claim("e1", "公司持续推进研发投入")], [], [])
    assert r["grade"] == "E"


def test_quote_verification_rejects_paraphrase():
    from pipeline.extract import verify_quote
    text = "公司1.6T产品投产顺利，预计每个季度的出货量都将环比提升。3.2T产能正在准备中，尚未到送样阶段。"
    assert verify_quote("3.2T产能正在准备中，尚未到送样阶段", text)
    assert verify_quote("1.6T产品投产 顺利", text)          # 空白差异容忍
    assert not verify_quote("3.2T产品已送样", text)          # 改写 → 拒绝
    assert not verify_quote("投产", text)                    # 过短 → 拒绝


def test_investor_question_not_counted():
    evs = [ev("e1", "notice", "ir", "2026-05-15")]
    r = grade_entity("X", "cpo", evs, [claim("e1", "（2）请问公司CPO相关产品目前小批量送样及量产进度如何？")], [], [])
    assert r["grade"] == "E"


def test_rule_fallback_always_needs_review():
    evs = [ev("e1", "notice", "ir", "2026-05-15")]
    c = claim("e1", "CPO光引擎已批量出货")
    c["extractor"] = "rule_sentence"
    assert grade_entity("X", "cpo", evs, [c], [], [])["needs_review"]


def test_policy_impact_is_not_denial():
    assert rule_stage("目前发布的相关政策未对光器件产品进行约束，不涉及对公司业务产生影响") is None


def test_text_denial_does_not_downgrade_revenue_grade():
    recs = [
        {"dataset": "cn_mainbiz_product", "entity": "X", "field": "主营构成-项目名称(按产品)", "label": "20260630",
         "rank": 1, "value": "光无源器件", "source": {}},
        {"dataset": "cn_mainbiz_product", "entity": "X", "field": "主营构成-项目收入(按产品)", "label": "20260630",
         "rank": 1, "value": 10.0, "source": {}},
    ]
    evs = [ev("e1", "notice", "clarification", "2026-08-01")]
    r = grade_entity("X", "optical_interconnect", evs, [claim("e1", "公司不涉及光模块业务", concept="optical_interconnect")], recs, [])
    assert r["grade"] == "A" and r["conflict"] and r["needs_review"]


def test_text_revenue_statement_is_order_stage():
    q = "截至最后实际可行日期，我们自XPO、NPO及CPO解决方案产生收入人民币2.0百万元"
    assert rule_stage(q) == "order"
    evs = [ev("e1", "notice", "announcement", "2026-07-17")]
    assert grade_entity("X", "cpo", evs, [claim("e1", q)], [], [])["grade"] == "B"


@pytest.mark.parametrize("quote,concept,stage", [
    ("针对NPO产品，公司已有相关团队持续跟进研发及客户合作，预计2027年有批量订单交付需求", "cpo", "rnd"),
    ("公司积极投入共封装光学（CPO）相关光器件及连接方案研发，具体量产释放进度取决于客户需求", "cpo", "rnd"),
    ("800G及以上高速交换机出货量同比增长 1.6倍，环比增长46%，CPO全光交换机样机开始出货", "cpo", "rnd"),
    ("公司为CPO产品配套的FAU、ELS等产品已具备较好的基础，目前相关产品已实现稳定量产交付", "cpo", "order"),
])
def test_clause_scoped_stage(quote, concept, stage):
    # 天孚例：概念词与“量产”在相邻分句（顿号不分句），属于同一窗口
    assert rule_stage(quote, concept) == stage


def test_llm_can_only_make_it_more_conservative():
    evs = [ev("e1", "notice", "ir", "2026-05-09")]
    up = grade_entity("X", "cpo", evs, [claim("e1", "CPO产品正在研发中", llm_stage="order")], [], [])
    down = grade_entity("X", "cpo", evs, [claim("e1", "CPO光引擎已批量出货", llm_stage="rnd")], [], [])
    assert up["grade"] == "C"      # LLM 说量产，规则说研发 → 不抬高
    assert down["grade"] == "C"    # 规则说量产，LLM 说研发 → 取保守
    assert up["needs_review"] and down["needs_review"]
