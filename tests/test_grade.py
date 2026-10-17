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


def test_hdy_mass_production_capped_at_d():
    # 按 A–E 表：只在互动平台口头提及 → D，无论说到哪个阶段
    evs = [ev("e1", "ifind_concept", "hdy", "2025-08-01")]
    cls = [claim("e1", "800G光芯片产品已实现小批量出货，CPO光引擎量产")]
    assert grade_entity("X", "cpo", evs, cls, [], [])["grade"] == "D"


def test_fundraising_project_is_order_stage():
    assert rule_stage("公司拟使用募集资金投资建设高速光模块产能建设项目", "optical_interconnect") == "order"


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
    evs = [ev("e1", "notice", "ir", "2025-03-01"), ev("e2", "notice", "clarification", "2026-02-01")]
    cls = [claim("e1", "公司CPO产品已量产"), claim("e2", "公司目前不涉及CPO相关业务")]
    r = grade_entity("X", "cpo", evs, cls, [], [])
    assert r["conflict"] and r["conflict"]["original_grade"] == "B" and r["grade"] == "C" and r["needs_review"]


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


def test_conflict_downgrades_even_revenue_grade():
    # 按要求：证据冲突时保留双方证据并降级展示（A 也降），标待复核
    recs = [
        {"dataset": "cn_mainbiz_product", "entity": "X", "field": "主营构成-项目名称(按产品)", "label": "20260630",
         "rank": 1, "value": "光无源器件", "source": {}},
        {"dataset": "cn_mainbiz_product", "entity": "X", "field": "主营构成-项目收入(按产品)", "label": "20260630",
         "rank": 1, "value": 10.0, "source": {}},
    ]
    evs = [ev("e1", "notice", "clarification", "2026-08-01")]
    r = grade_entity("X", "optical_interconnect", evs, [claim("e1", "公司不涉及光模块业务", concept="optical_interconnect")], recs, [])
    assert r["grade"] == "B" and r["conflict"]["original_grade"] == "A" and r["needs_review"]


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


def test_rule_decides_and_llm_mismatch_flags_review():
    evs = [ev("e1", "notice", "ir", "2026-05-09")]
    c1 = claim("e1", "CPO光引擎已批量出货"); c1["llm_grade"] = "C"
    c2 = claim("e1", "CPO产品正在研发中"); c2["llm_grade"] = "C"
    r1 = grade_entity("X", "cpo", evs, [c1], [], [])
    r2 = grade_entity("X", "cpo", evs, [c2], [], [])
    assert r1["grade"] == "B" and r1["needs_review"]        # 规则 B，LLM 建议 C → 以规则为准，待复核
    assert r2["grade"] == "C" and not r2["needs_review"]    # 一致 → 不需要复核

def test_rnd_goal_phrase_is_not_mass_production():
    # 复核水晶光电时发现：研发项目目标描述“研发并量产”不能判为量产
    q = "CPO 共封装-硅透镜研发项目研发并量产满足 CPO 光引擎厂商需求的高品质硅透镜器件"
    assert rule_stage(q, "cpo") == "rnd"
    assert rule_stage("CPO光引擎已实现量产", "cpo") == "order"


@pytest.mark.parametrize("quote,stage", [
    # 审查发现的真实误判：下一分句换了主语（LPO/LRO），不能把“量产”套到 CPO
    ("我们的XPO、NPO及CPO解决方案目前处于研发及样品交付阶段，而LPO及LRO解决方案已进入量产阶段。", "rnd"),
    # 主语没换（“相关产品”指代 CPO 配套产品），仍然并入
    ("公司为CPO产品配套的FAU、ELS等产品已具备较好的基础，目前相关产品已实现稳定量产交付", "order"),
])
def test_next_clause_requires_same_subject(quote, stage):
    assert rule_stage(quote, "cpo") == stage


# ---- 独立审查（Claude 子代理）点名的真实误判，逐条回归 ----
@pytest.mark.parametrize("quote,concept,stage", [
    # 帝奥微：分号另起一项，“量产”说的是采样开关，不能并到光模块上
    ("完美适配1.6T光模块、CPO共封装光学、硅光集成等下一代高速光传输场景； 持续迭代量产业界首款超低漏电流2GHz超高速微弱信号采样开关", "optical_interconnect", None),
    # 太辰光：借 CPO 浪潮的表述，订单不是 CPO 订单
    ("在数据中心及超算中心的规模化应用，有效适配CPO技术升级浪潮，带动订单稳步增长。", "cpo", "talk"),
    # 乾照光电：小标题里的“订单”不算；标题后的正文讲的是 MicroLED
    ("关于MicroLED、CPO及光互连领域布局及订单情况：公司在MicroLED领域已实现技术突破，完成国内外大客户的小批量验证和交付。", "cpo", None),
    # 剑桥科技：“不生产…处于合作研发…尚未产生收入”是研发阶段，不是否认
    ("公司目前不生产含CPO技术的芯片，CPO相关核心部件光引擎与外置光源ELSFP分别处于合作研发阶段，尚未产生收入", "cpo", "rnd"),
    # 工业富联：样机出货属研发 / 送样
    ("上半年，公司CPO全光交换机样机开始出货", "cpo", "rnd"),
    # 将来时只看阶段词附近：远处的“按计划进行”不影响
    ("CPO光引擎已实现批量交付，客户反馈良好，产品线整体按计划进行", "cpo", "order"),
    # “其中”不是换主语
    ("公司光模块业务快速增长，其中800G产品实现规模化交付", "optical_interconnect", "order"),
])
def test_reviewer_cases(quote, concept, stage):
    assert rule_stage(quote, concept) == stage


def test_ifind_paraphrase_capped_at_c():
    evs = [ev("e1", "ifind_concept", "ir", "2026-03-01")]
    r = grade_entity("X", "cpo", evs, [claim("e1", "公司CPO光引擎已批量出货")], [], [])
    assert r["grade"] == "C" and "二手转述" in r["basis"]


@pytest.mark.parametrize("quote,concept,stage", [
    # 修审查问题时引入、又被自查发现的两个回归
    ("公司已有相关团队持续跟进NPO产品的研发，预计2027年下半年有订单交付需求，2028年出货量级将逐步提升。", "cpo", "rnd"),
    ("公司围绕电源及电能转换领域，在CPO交换机电源、大功率AI服务器电源、HVDC、Power Shelf及SST等方向均有布局", "cpo", "talk"),
])
def test_self_found_regressions(quote, concept, stage):
    assert rule_stage(quote, concept) == stage


@pytest.mark.parametrize("quote,concept,stage", [
    # 交叉核对（astra）给出的分级反例
    ("公司不涉及CPO业务，目前正在研发电源芯片", "cpo", "deny"),
    ("公司CPO光引擎处于研发阶段，另一产品电源芯片已实现量产", "cpo", "rnd"),
    ("公司CPO光引擎受益于AI算力浪潮已实现量产出货", "cpo", "order"),
])
def test_crosscheck_grade_cases(quote, concept, stage):
    assert rule_stage(quote, concept) == stage


def test_concept_note_with_announcement_is_secondhand():
    assert concept_source("据2025年8月29日公告，公司CPO相关产品已送样")[0] == "announcement"
