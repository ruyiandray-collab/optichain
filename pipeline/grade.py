"""概念可信度分级：最终等级由规则函数裁定；LLM 只提供“已校验引文 + 建议等级 + 理由”。

等级（按产品要求的 A–E 表）：
  A 有收入      财报可拆出相关收入：年报 / 半年报分产品收入有对应项（给金额与占比）
  B 有订单/产能 正式公告的订单、客户认证、量产、扩产，含定增 / 可转债募投项目
  C 有研发/送样 正式文件（年报研发项目、调研纪要 / IR 记录）中的研发、送样、小批量
  D 仅表态      只在互动平台 / 媒体转述中口头提及（互动易、e互动）——非正式来源无论说到哪个阶段，上限都是 D；
                正式文件里只有“关注 / 布局 / 可应用于”也是 D
  E 无证据/否认 检索范围内无证据（记录检索范围与时间），或正式来源明确否认
附加：证据早于截止日 24 个月降一级（A 除外）；
      证据冲突（有正面证据，又有不早于它的正式否认）：保留双方证据，降一级展示，并标待复核；
      LLM 建议等级与规则等级不一致：以规则等级为准，标“待人工复核”。
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
               "客户认证", "通过验证", "通过认证", "扩产", "供货", "募投", "募集资金投资", "产能建设", "扩建", "新建产能",
               "规模化交付", "批量交付", "中标", "投产", "生产建设项目", "出货"]),
    ("rnd", ["样机", "小批量", "送样", "交样", "样品", "研发", "开发中", "验证中", "预研", "在研", "正在开发", "处于开发"]),
    ("talk", ["关注", "布局", "可应用于", "可用于", "有望", "积极", "探索", "储备", "规划"]),
]
DENY_WORDS = ["不涉及", "未涉及", "未开展", "没有相关", "暂无相关", "未形成收入", "无相关业务", "不生产", "未从事"]
NEG_PREFIX = re.compile(r"(尚未|未|没有|暂未|暂无|不)[^，。；,;]{0,4}$")

FORMAL = {"periodic", "ir", "announcement"}
SECONDHAND_CAP = "C"  # 二手转述（iFinD 概念解析引用年报 / 调研）的等级上限
STAGE_CN = {"order": "订单/量产/扩产", "rnd": "研发/送样", "talk": "仅表态", "deny": "否认"}
GRADE_TABLE = {  # (阶段, 来源是否正式) -> 等级；非正式来源（互动平台 / 媒体转述）上限 D
    ("order", True): "B", ("rnd", True): "C", ("talk", True): "D",
    ("order", False): "D", ("rnd", False): "D", ("talk", False): "D",
}


def is_formal(ev: dict) -> bool:
    if ev["source_kind"] == "notice":
        return ev["origin_kind"] in FORMAL
    if ev["source_kind"] == "ifind_concept":  # iFinD 转述的正式文件（定期报告 / 调研 / 公告）：二手，等级上限 C
        return ev["origin_kind"] in ("periodic", "ir", "announcement")
    return False


def mentions_concept(text: str, concept: str) -> bool:
    return any(k in text for k in CONCEPTS[concept]["keywords"])


SUBJECT_SHIFT = re.compile(r"^(而|但|但是|然而|同时|另外|此外|与此同时|另一方面|另一|其他|其余)")
OTHER_SUBJECT = re.compile(r"另一(款|个|类)?产品|其他产品|其余产品|另一业务")  # 交叉核对发现：“另一产品…已量产”被并入 CPO  # “其中”是对前文的展开，不算换主语（审查发现）
OTHER_PRODUCTS = {"cpo": ["LPO", "LRO", "可插拔", "硅光模块", "800G", "1.6T", "电信"], "optical_interconnect": []}
FUNDRAISE_WORDS = {"募投", "募集资金投资", "产能建设", "扩建", "新建产能", "扩产", "生产建设项目"}  # 募投 / 扩产本身就是计划，按 A–E 表算 B
FUTURE = re.compile(r"预计|有望|计划|将于|将在|将会|拟|未来|取决于|争取|力争|目标|规划")
FUTURE_SPAN = (16, 12)  # 将来时看阶段词前 16 字、后 12 字（审查发现：整句匹配会被表格里远处的“按计划进行”误伤）
FUTURE_LEAD = re.compile(r"^(预计|计划|拟|有望|将|未来|争取|力争)")  # 分句以将来时开头：整句都是展望
RND_QUALIFIER = re.compile(r"(样机|样品|小批量|送样|试产|验证)[^，,；;。]{0,6}$")  # “样机开始出货”属于研发 / 送样
TREND = re.compile(r"(适配|顺应|受益于|把握|抓住|契合)[^，,；;。]{0,12}(浪潮|趋势|机遇|红利|热潮)")  # 借势表述只算表态
HEADING = re.compile(r"(关于|情况|方面|进展|布局)$")
_SPLIT = re.compile(r"([，,；;。！!？?：:])")
STAGE_RANK = {"order": 0, "rnd": 1, "talk": 2}


EXPLICIT_DONE = re.compile(r"(已|已经)[^，,；;。]{0,6}(量产|批量|出货|供货|交付|实现销售)")


def _clause_stage(clause: str) -> str | None:
    # 借势表述只算表态；但同一分句里已明确写了“已实现量产 / 已批量供货”，以明确事实为准（交叉核对发现）
    if TREND.search(clause) and not EXPLICIT_DONE.search(clause):
        return "talk"
    for stage, words in STAGE_WORDS:
        for w in words:
            for m in re.finditer(re.escape(w), clause):
                before = clause[max(0, m.start() - 8): m.start()]
                if NEG_PREFIX.search(before):
                    continue
                if w == "量产" and re.search(r"研发[并及和与]$", clause[max(0, m.start() - 3): m.start()]):
                    continue  # “研发并量产……”是研发项目的目标描述，不是量产事实（复核水晶光电时发现）
                if "批量" in w and clause[max(0, m.start() - 1): m.start()] == "小":
                    continue  # “小批量交付/出货”属于 C 档，不算量产
                if stage == "order" and RND_QUALIFIER.search(clause[: m.start()]):
                    return "rnd"
                near = clause[max(0, m.start() - FUTURE_SPAN[0]): m.end() + FUTURE_SPAN[1]]
                if stage in ("order", "rnd") and w not in FUNDRAISE_WORDS and (FUTURE.search(near) or FUTURE_LEAD.match(clause.strip())):
                    return "talk"  # “预计 2027 年有批量订单”“量产进度取决于客户需求”是展望，不是进展
                return stage
    return None


def split_clauses(quote: str) -> list[tuple[str, str]]:
    """切成 (分句, 其后的标点)。冒号也切开（审查发现：小标题“…布局及订单情况：”里的“订单”被当成进展）。"""
    parts = _SPLIT.split(quote)
    out = []
    for i in range(0, len(parts), 2):
        text = parts[i].strip()
        delim = parts[i + 1] if i + 1 < len(parts) else ""
        if text:
            out.append((text, delim))
    return out


def rule_stage(quote: str, concept: str | None = None) -> str | None:
    """返回 order / rnd / talk / deny / None。
    给了 concept 时只看“含概念词的分句 +（逗号相连且主语未换的）下一分句”，避免别的产品的阶段词串到概念上。"""
    clauses_all = split_clauses(quote)
    rnd_words = ("研发", "合作研发", "送样", "开发", "验证")
    # 研发必须发生在“含概念词的分句”里才算（交叉核对发现：“不涉及 CPO，正在研发电源芯片”被误判为研发）
    has_rnd = any(any(w in c for w in rnd_words) and (not concept or mentions_concept(c, concept)) for c, _ in clauses_all)
    for w in DENY_WORDS:
        for m in re.finditer(re.escape(w), quote):
            tail = quote[m.end(): m.end() + 10]
            if re.search(r"影响|约束|限制|风险|处罚", tail):  # “不涉及对公司业务产生影响”不是否认业务
                continue
            # “目前不生产…CPO 部件处于合作研发…尚未产生收入”是研发阶段，不是否认（审查发现：剑桥科技）
            return "rnd" if has_rnd else "deny"
    clauses = split_clauses(quote)
    if not concept:
        windows = [c for c, _ in clauses]
    else:
        windows = []
        for i, (c, d) in enumerate(clauses):
            if not mentions_concept(c, concept):
                continue
            heading = d in ("：", ":") and bool(HEADING.search(c))  # 注意 "" in "：:" 恒为 True，必须用元组
            if not heading:
                windows.append(c)  # 小标题里的阶段词不算进展
            if i + 1 >= len(clauses):
                continue
            nxt = clauses[i + 1][0]
            if mentions_concept(nxt, concept):
                continue  # 下一分句自己含概念词，会单独处理
            # 并入条件：用逗号相连（分号、句号意味着另起一项），或是小标题后的正文；且主语没换
            # 借势表述（“适配 CPO 浪潮，带动订单增长”）后面的结果分句不并入：那些订单不是概念本身的订单
            linked = (d in ("，", ",") or heading) and not (TREND.search(c) and not EXPLICIT_DONE.search(c))
            topics = [t for t in re.split(r"[、及和与]", re.sub(r"^关于", "", c)) if len(t) >= 2
                      and not mentions_concept(t, concept)] if heading else []
            if (linked and not SUBJECT_SHIFT.match(nxt) and not OTHER_SUBJECT.search(nxt)
                    and not any(p in nxt for p in OTHER_PRODUCTS.get(concept, []))
                    and not any(t[:4] in nxt for t in topics)):
                windows.append(nxt)
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


REVENUE_SENT = re.compile(r"(产生|实现|形成|确认|贡献)[^，。；]{0,8}收入|收入[^，。；]{0,6}(人民币|约)?[\d.]+(万|百万|亿)?元")


def revenue_scan_claims(evidences: list[dict]) -> list[dict]:
    """确定性补抽：正式公告中同时含概念词和收入表述的句子（LLM 可能漏抽，审查时发现过）。"""
    out = []
    for ev in evidences:
        if ev.get("source_kind") != "notice" or ev.get("origin_kind") not in FORMAL:
            continue
        for s in _SENT.findall(ev["text"]):
            s = s.strip()
            if not REVENUE_SENT.search(s):
                continue
            for c in CONCEPTS:
                if mentions_concept(s, c):
                    out.append({"evidence_id": ev["evidence_id"], "concept": c, "quote": s, "llm_stage": None,
                                "llm_grade": None, "extractor": "rule_revenue_scan", "verified": True})
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
        eff = st  # 最终等级只由规则决定；LLM 的建议只用于触发复核
        g = GRADE_TABLE[(eff, formal)]
        secondhand = ev["source_kind"] == "ifind_concept" and formal
        if secondhand and GRADES.index(g) < GRADES.index(SECONDHAND_CAP):
            g = SECONDHAND_CAP  # iFinD 概念解析转述的年报 / 调研内容：拿不到一手原文，最高 C（审查发现乾照光电因此得 B）
        stale = is_stale(ev.get("date"))
        if stale:
            g = downgrade(g)
        row = {"evidence_id": ev["evidence_id"], "quote": cl["quote"], "stage": eff, "rule_stage": st,
               "secondhand": secondhand,
               "llm_stage": llm, "llm_grade": cl.get("llm_grade"), "llm_reason": cl.get("llm_reason"), "formal": formal, "source_kind": ev["source_kind"], "origin_kind": ev["origin_kind"],
               "stale": stale, "grade": g, "date": ev.get("date"), "source_name": ev["source_name"]}
        llm_grade, base = cl.get("llm_grade"), GRADE_TABLE[(eff, formal)]  # 比较陈旧降级前的等级
        if llm_grade and llm_grade != base:
            row["review"] = f"LLM 建议等级 {llm_grade} ≠ 规则等级 {base}（以规则为准）"
            review.append(row)
        elif llm and llm != st and not llm_grade:
            row["review"] = f"LLM 建议阶段 {llm} ≠ 规则阶段 {st}（以规则为准）"
            review.append(row)
        elif cl.get("extractor") == "rule_sentence":
            row["review"] = "规则按句回退抽取，未经 LLM 判断主语与产品"
            review.append(row)
        elif cl.get("extractor") == "rule_revenue_scan":
            row["review"] = "规则补抽的收入表述（LLM 漏抽），待复核主语与口径"
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
        src = "二手转述（iFinD 概念解析）" if best.get("secondhand") else ("正式来源" if best["formal"] else "非正式来源")
        result.update(grade=best["grade"], basis=f"{src}·{STAGE_CN[best['stage']]}",
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
            result["grade"] = downgrade(result["grade"])  # 按要求：冲突时保留双方证据并降级展示（A 也不例外）
            result["basis"] += "；存在不早于正面证据的正式否认，保留双方证据、降一级展示，待复核"
    return result
