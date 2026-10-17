"""M2 概念可信度分级：规则函数裁定，LLM 只提供“已校验引文 + 建议阶段”。

等级（docs/proposal.md 第 2 节）：
  A 有收入   结构化分产品收入命中产品词表（金额、占比、报告期）
  B 订单/量产 正式文件（定期报告 / IR 记录 / 公告）中的订单、量产、批量出货表述
  C 研发/送样 正式文件中的研发 / 送样 / 小批量；或非正式来源（互动易 / 媒体）中的量产或订单表述
  D 仅表态   只有“关注 / 布局 / 可应用于”，或非正式来源中的研发表述
  E 否认     正式来源明确否认 / 澄清
  E 无证据   检索范围内无相关证据（记录检索范围）
附加：证据早于截止日 24 个月降一级（A 除外）；有否认且否认不早于最强正面证据时，降一级展示并标冲突；
      LLM 建议阶段与规则阶段不一致时标“待人工复核”。
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
STAGING = ROOT / "data" / "staging"
SEG = yaml.safe_load((ROOT / "config" / "segments.yaml").read_text(encoding="utf-8"))
UNIVERSE = yaml.safe_load((ROOT / "config" / "universe.yaml").read_text(encoding="utf-8"))
CUTOFF = date.fromisoformat(UNIVERSE["snapshot_cutoff"])

GRADES = ["A", "B", "C", "D", "E"]
CONCEPTS = {
    "optical_interconnect": {"name": "AI 光互联（光模块产业链）",
                             "keywords": ["光模块", "光通信", "光器件", "光芯片", "光互联", "硅光", "光引擎", "激光器芯片",
                                          "收发模块", "800G", "1.6T", "光通讯", "CPO", "数通"]},
    "cpo": {"name": "共封装光学(CPO)", "keywords": ["CPO", "共封装", "NPO", "光引擎"]},
}
# 规则阶段词表：按强度从高到低匹配
STAGE_WORDS = [
    ("order", ["产生收入", "实现收入", "形成收入", "确认收入", "贡献收入", "销售收入", "批量出货", "批量供货", "批量交付", "大批量", "规模收入", "量产", "放量", "订单", "实现销售", "出货量",
               "客户认证", "通过验证", "通过认证", "扩产", "供货"]),
    ("rnd", ["样机", "小批量", "送样", "交样", "样品", "研发", "开发中", "验证中", "预研", "在研", "正在开发", "处于开发"]),
    ("talk", ["关注", "布局", "可应用于", "可用于", "有望", "积极", "探索", "储备", "规划"]),
]
DENY_WORDS = ["不涉及", "未涉及", "未开展", "没有相关", "暂无相关", "未形成收入", "无相关业务", "不生产", "未从事"]
NEG_PREFIX = re.compile(r"(尚未|未|没有|暂未|暂无|不)[^，。；,;]{0,4}$")

FORMAL = {"periodic", "ir", "announcement"}
GRADE_TABLE = {  # (阶段, 来源是否正式) -> 等级
    ("order", True): "B", ("rnd", True): "C", ("talk", True): "D",
    ("order", False): "C", ("rnd", False): "D", ("talk", False): "D",
}


def is_formal(ev: dict) -> bool:
    if ev["source_kind"] == "notice":
        return ev["origin_kind"] in FORMAL
    if ev["source_kind"] == "ifind_concept":  # iFinD 转述的正式文件，视为正式（标“二手”）
        return ev["origin_kind"] in ("periodic", "ir")
    return False


def mentions_concept(text: str, concept: str) -> bool:
    return any(k in text for k in CONCEPTS[concept]["keywords"])


FUTURE = re.compile(r"预计|有望|计划|将于|将在|将会|拟|未来|取决于|争取|力争|目标|规划")
_CLAUSE = re.compile(r"[^，,；;。！!？?]+")
STAGE_RANK = {"order": 0, "rnd": 1, "talk": 2}


def _clause_stage(clause: str) -> str | None:
    for stage, words in STAGE_WORDS:
        for w in words:
            for m in re.finditer(re.escape(w), clause):
                before = clause[max(0, m.start() - 8): m.start()]
                if NEG_PREFIX.search(before):
                    continue
                if "批量" in w and clause[max(0, m.start() - 1): m.start()] == "小":
                    continue  # “小批量交付/出货”属于 C 档，不算量产
                if stage in ("order", "rnd") and FUTURE.search(clause):
                    return "talk"  # “预计 2027 年有批量订单”“量产进度取决于客户需求”是展望，不是进展
                return stage
    return None


def rule_stage(quote: str, concept: str | None = None) -> str | None:
    """返回 order / rnd / talk / deny / None。
    给了 concept 时只看“含概念词的分句 + 紧随的一个分句”，避免同句里别的产品的阶段词串到概念上。"""
    for w in DENY_WORDS:
        for m in re.finditer(re.escape(w), quote):
            tail = quote[m.end(): m.end() + 10]
            if not re.search(r"影响|约束|限制|风险|处罚", tail):  # “不涉及对公司业务产生影响”不是否认业务
                return "deny"
    clauses = _CLAUSE.findall(quote)
    if concept:
        idx = [i for i, c in enumerate(clauses) if mentions_concept(c, concept)]
        windows = sorted({j for i in idx for j in (i, i + 1) if j < len(clauses)})
        windows = [clauses[j] for j in windows]
    else:
        windows = clauses
    stages = [st for st in (_clause_stage(w) for w in windows) if st]
    return min(stages, key=lambda x: STAGE_RANK[x]) if stages else None


def conservative(rule: str, llm: str | None) -> str:
    """规则与 LLM 不一致时取更保守的一方展示：LLM 只能让结论更保守，不能抬高等级。"""
    if llm in STAGE_RANK and STAGE_RANK[llm] > STAGE_RANK[rule]:
        return llm
    if llm in ("deny", "none"):
        return "talk"
    return rule


def iso(d: str | None) -> str:
    """'20260630' / '2026-06-30' / None -> '2026-06-30' / ''（统一格式后再比较）。"""
    d = (d or "").strip()
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}" if re.fullmatch(r"\d{8}", d) else d[:10]


def is_question(quote: str) -> bool:
    q = quote.strip()
    return "请问" in q or q.endswith(("？", "?")) or q.startswith(("问：", "Q", "投资者提问"))


def downgrade(g: str, n: int = 1) -> str:
    return GRADES[min(GRADES.index(g) + n, len(GRADES) - 1)]


def is_stale(d: str | None) -> bool:
    if not d:
        return False
    try:
        dd = date.fromisoformat(d[:10])
    except ValueError:
        return False
    return (CUTOFF - dd).days > 730


# ---- 回退抽取（无 LLM 时）：按句切分，取含概念词的句子 -------------------------
_SENT = re.compile(r"[^。；;！!？?\n]+[。；;！!？?]?")


def fallback_claims(ev: dict, concept: str) -> list[dict]:
    out = []
    for s in _SENT.findall(ev["text"]):
        s = s.strip()
        if len(s) >= 6 and mentions_concept(s, concept):
            out.append({"evidence_id": ev["evidence_id"], "concept": concept, "quote": s,
                        "llm_stage": None, "extractor": "rule_sentence", "verified": True})
    return out


# ---- A 级：结构化分产品收入 ----------------------------------------------------
def product_items(records: list[dict], entity: str) -> dict[str, list[dict]]:
    """{label: [{name, revenue, source...}]}，按产品口径。"""
    names, revs = {}, {}
    for r in records:
        if r["dataset"] != "cn_mainbiz_product" or r["entity"] != entity:
            continue
        if r["field"] == "主营构成-项目名称(按产品)" and r["value"]:
            names[(r["label"], r["rank"])] = r
        elif r["field"] == "主营构成-项目收入(按产品)" and r["value"] is not None:
            revs[(r["label"], r["rank"])] = r
    out: dict[str, list[dict]] = {}
    for (label, rank), rv in revs.items():
        nm = names.get((label, rank))
        if nm:
            out.setdefault(label, []).append({"name": nm["value"], "revenue": rv["value"], "rank": rank,
                                              "source": rv["source"], "name_source": nm["source"]})
    return out


def revenue_of(records, entity, label):
    for r in records:
        if r["dataset"] == "cn_quarterly" and r["entity"] == entity and r["field"] == "营业收入" and r["label"] == label:
            return r
    return None


def grade_a(records, entity, concept):
    """A 级：分产品项目被确定地归入光互联环节（产品词或单公司映射，不含泛口径）。"""
    from pipeline.attribution import match_item
    items = product_items(records, entity)
    for label in sorted(items, reverse=True):  # 最新报告期优先
        hits = []
        for it in items[label]:
            m = match_item(entity, str(it["name"]))
            ok = m["kind"] in ("specific", "override") and m["segment"] != "cloud"
            if concept == "cpo":
                ok = ok and m["segment"] == "cpo"
            if ok:
                hits.append({**it, "segment": m["segment"], "match": m})
        if hits:
            total = revenue_of(records, entity, label)
            amt = sum(h["revenue"] for h in hits)
            return {"grade": "A", "label": label, "items": hits, "amount": amt,
                    "revenue": total["value"] if total else None,
                    "share": (amt / total["value"]) if total and total["value"] else None,
                    "revenue_source": total["source"] if total else None}
    return None


# ---- 主函数 ----------------------------------------------------------------
def grade_entity(entity: str, concept: str, evidences: list[dict], claims: list[dict], records: list[dict],
                 search_scope: list[dict]) -> dict:
    ev_by_id = {e["evidence_id"]: e for e in evidences}
    trace, review = [], []
    a = grade_a(records, entity, concept)
    best = None
    deny = []
    for cl in claims:
        if not cl.get("verified") or cl["concept"] != concept:
            continue
        ev = ev_by_id[cl["evidence_id"]]
        if not mentions_concept(cl["quote"], concept):
            trace.append({"evidence_id": ev["evidence_id"], "quote": cl["quote"], "result": "丢弃：引文未提及概念"})
            continue
        if is_question(cl["quote"]):
            trace.append({"evidence_id": ev["evidence_id"], "quote": cl["quote"], "result": "丢弃：投资者提问，不是公司表述"})
            continue
        st = rule_stage(cl["quote"], concept)
        formal = is_formal(ev) or ev["origin_kind"] == "clarification"
        if st == "deny":
            if formal:
                deny.append((ev, cl))
            trace.append({"evidence_id": ev["evidence_id"], "quote": cl["quote"], "stage": "deny",
                          "formal": formal, "result": "否认" if formal else "非正式来源的否定表述，不计"})
            continue
        if st is None:
            trace.append({"evidence_id": ev["evidence_id"], "quote": cl["quote"], "result": "无阶段词，不计"})
            continue
        llm = cl.get("llm_stage")
        eff = conservative(st, llm) if llm else st
        g = GRADE_TABLE[(eff, formal)]
        stale = is_stale(ev.get("date"))
        if stale:
            g = downgrade(g)
        row = {"evidence_id": ev["evidence_id"], "quote": cl["quote"], "stage": eff, "rule_stage": st,
               "llm_stage": llm, "formal": formal, "source_kind": ev["source_kind"], "origin_kind": ev["origin_kind"],
               "stale": stale, "grade": g, "date": ev.get("date"), "source_name": ev["source_name"]}
        if llm and llm != st:
            row["review"] = f"LLM 建议阶段 {llm} ≠ 规则阶段 {st}，按较保守的 {eff} 展示"
            review.append(row)
        elif cl.get("extractor") == "rule_sentence":
            row["review"] = "规则按句回退抽取，未经 LLM 判断主语与产品"
            review.append(row)
        trace.append(row)
        if best is None or GRADES.index(g) < GRADES.index(best["grade"]) or (
                g == best["grade"] and (ev.get("date") or "") > (best.get("date") or "")):
            best = row

    result = {"entity": entity, "concept": concept, "trace": trace, "needs_review": bool(review),
              "review_items": review, "conflict": None}
    if a:
        result.update(grade="A", basis="分产品收入", a_detail=a)
    elif best:
        result.update(grade=best["grade"], basis=f"{'正式' if best['formal'] else '非正式'}来源·{best['stage']}",
                      best_evidence=best["evidence_id"], best=best)
    elif deny:
        result.update(grade="E", basis="公司否认", best_evidence=deny[0][0]["evidence_id"])
    else:
        result.update(grade="E", basis="无证据", search_scope=search_scope)

    # 冲突：有正面等级又有正式否认，且否认不早于最强正面证据
    if deny and result["grade"] != "E":
        latest_deny = max(deny, key=lambda x: x[0].get("date") or "")
        pos_date = iso((best or {}).get("date") or (a["label"] if a else ""))
        if iso(latest_deny[0].get("date")) >= pos_date:
            result["conflict"] = {"deny_evidence": latest_deny[0]["evidence_id"], "deny_quote": latest_deny[1]["quote"],
                                  "original_grade": result["grade"]}
            result["needs_review"] = True
            if result["grade"] == "A":  # 结构化收入是硬证据，文本否认不降级，只标冲突待复核
                result["basis"] += "；存在否认表述，已标冲突待复核（收入数据优先）"
            else:
                result["grade"] = downgrade(result["grade"])
                result["basis"] += "；存在更新的否认表述，降一级展示"
    return result
