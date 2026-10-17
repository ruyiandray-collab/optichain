"""M2 汇总：证据 + 引文 → 概念可信度（concept_links.json）与环节归属（memberships.json）。

用法：python -m pipeline.m2 [--llm]
  --llm   先调用 DeepSeek 抽取（需要 DEEPSEEK_API_KEY）；否则读已有 claims.jsonl，没有就用规则按句回退抽取
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from pipeline import grade, attribution, extract, manual_review
from pipeline.evidence import WINDOW, CPO_SLICES

ROOT = Path(__file__).resolve().parent.parent
STAGING = ROOT / "data" / "staging"
UNIVERSE = yaml.safe_load((ROOT / "config" / "universe.yaml").read_text(encoding="utf-8"))


def _jsonl(name):
    p = STAGING / name
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


_CALL_TIMES: dict | None = None


def _called_at(tool: str, query: str) -> str | None:
    global _CALL_TIMES
    if _CALL_TIMES is None:
        _CALL_TIMES = {}
        for c in _jsonl("api_calls.jsonl"):
            if c["status"] in ("ok", "empty"):
                _CALL_TIMES.setdefault((c["tool"], c["params"].get("query")), (c["called_at"], c["call_id"]))
    return _CALL_TIMES.get((tool, query))


def search_scope(c: dict) -> list[dict]:
    """E 级“无证据”必须给出检索范围与检索时间（与 evidence.collect 的模板一致）。"""
    qs = [("search_stocks", f"CPO概念股中{s}的股票", None) for s in CPO_SLICES]
    qs += [(tool, q, list(WINDOW)) for tool, q in (
        ("search_notice", f"{c['name']} 光模块 光通信 CPO 业务进展 订单 量产 研发"),
        ("search_notice", f"{c['name']} 澄清 不涉及 光模块 CPO"),
        ("search_news", f"{c['name']} 互动平台 光模块 CPO"),
        ("search_notice", f"{c['name']} 募集资金投资项目 定向增发 可转债 扩产 产能建设"))]
    out = []
    for tool, q, window in qs:
        hit = _called_at(tool, q)
        out.append({"tool": tool, "query": q, "window": window, "called_at": hit[0] if hit else None,
                    "call_id": hit[1] if hit else None})
    return out


def run(use_llm: bool = False) -> dict:
    evidences = _jsonl("evidence_raw.jsonl")
    records = _jsonl("records.jsonl")
    names = {k: v["name"] for k, v in json.loads((STAGING / "cpo_members.json").read_text(encoding="utf-8")).items()}
    names.update({c["code"]: c["name"] for c in UNIVERSE["cn"]})
    stats = {}
    if use_llm:
        stats["extract"] = extract.run(evidences, names)
    claims = _jsonl("claims.jsonl")
    extractor = "llm" if claims else "rule_sentence"
    if not claims:  # 回退：规则按句抽取（页面上会标注“未经 LLM 抽取”）
        claims = [cl for ev in evidences for c in grade.CONCEPTS for cl in grade.fallback_claims(ev, c)]
    # 收入表述是 B 档最关键的证据，召回不能只靠 LLM：正式公告里“概念词 + 产生/实现收入”的句子由规则补抽
    seen = {(c["evidence_id"], c["concept"], c["quote"]) for c in claims}
    extra = [c for c in grade.revenue_scan_claims(evidences) if (c["evidence_id"], c["concept"], c["quote"]) not in seen]
    claims = claims + extra
    stats["revenue_scan_added"] = len(extra)
    # 落盘（与 LLM 的 claims.jsonl 分开），保证分级可以只用落盘文件复现
    with (STAGING / "claims_rule.jsonl").open("w", encoding="utf-8") as f:
        for c in extra:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    reviews = manual_review.load()
    links, members = [], []
    for c in UNIVERSE["cn"]:
        evs = [e for e in evidences if e["entity"] == c["code"]]
        ids = {e["evidence_id"] for e in evs}
        cls = [x for x in claims if x["evidence_id"] in ids]
        for concept in grade.CONCEPTS:
            r = grade.grade_entity(c["code"], concept, evs, cls, records, search_scope(c))
            r.update(name=c["name"], role=c["role"], extractor=extractor)
            manual_review.apply(r, reviews)
            links.append(r)
        # 泛口径分部的文本佐证：正式来源里“光互联 + 量产/订单”的已校验引文
        oi = next(l for l in links if l["entity"] == c["code"] and l["concept"] == "optical_interconnect")
        # 只有引文里出现该环节自己的产品词，才算给这个环节背书
        # 证据档位：正式来源（公告 / IR / 定期报告）= 2；研报 / 资讯 / 互动平台转述 = 3（官网 = 4，本版未采集）
        cpo_l = next(l for l in links if l["entity"] == c["code"] and l["concept"] == "cpo")
        order_rows = [t for t in oi["trace"] + cpo_l["trace"] if t.get("rule_stage") == "order" and t.get("quote")]
        items = grade.product_items(records, c["code"])
        support = {}
        for s in attribution.SEGMENTS:
            kws = s.get("text_keywords", []) + [k for sub in s.get("sub_segments", []) or [] for k in sub["keywords"]]
            hits = {}
            for t in order_rows:
                if any(k in t["quote"] for k in kws):
                    tier = 2 if t.get("formal") else 3
                    if t["evidence_id"] not in hits or tier < hits[t["evidence_id"]]["tier"]:
                        hits[t["evidence_id"]] = {"evidence_id": t["evidence_id"], "tier": tier, "quote": t["quote"]}
            if hits:
                support[s["id"]] = list(hits.values())
        m = attribution.attribute(c["code"], items, lambda lb, code=c["code"]: grade.revenue_of(records, code, lb),
                                  support, c.get("segment_seed"))
        m.update(name=c["name"], role=c["role"])
        members.append(m)

    # CPO 概念全部成分股：全集外的公司只有 iFinD 概念解析一个来源，标“单一来源”，没有分产品数据所以不可能给 A
    universe_codes = {c["code"] for c in UNIVERSE["cn"]}
    cpo_members = json.loads((STAGING / "cpo_members.json").read_text(encoding="utf-8"))
    cpo_all = []
    for code, info in cpo_members.items():
        if code in universe_codes:
            r = next(l for l in links if l["entity"] == code and l["concept"] == "cpo")
            cpo_all.append({"entity": code, "name": info["name"], "grade": r["grade"], "basis": r["basis"],
                            "in_universe": True, "single_source": False, "needs_review": r["needs_review"],
                            "manual_review": r.get("manual_review"), "search_scope": r.get("search_scope")})
            continue
        evs = [e for e in evidences if e["entity"] == code]
        ids = {e["evidence_id"] for e in evs}
        # 全集外公司的检索范围：CPO 概念 5 个市值切片（search_stocks），附每次检索的时间与 call_id
        scope = []
        for sl in CPO_SLICES:
            q = f"CPO概念股中{sl}的股票"
            hit = _called_at("search_stocks", q)
            scope.append({"tool": "search_stocks", "query": q, "window": None,
                          "called_at": hit[0] if hit else None, "call_id": hit[1] if hit else None,
                          "note": "仅 iFinD 概念解析单一来源；未对全集外公司检索公告与资讯"})
        r = grade.grade_entity(code, "cpo", evs, [x for x in claims if x["evidence_id"] in ids], [], scope)
        manual_review.apply(r, reviews)
        cpo_all.append({"entity": code, "name": info["name"], "grade": r["grade"], "basis": r["basis"],
                        "in_universe": False, "single_source": True, "needs_review": r["needs_review"],
                        "manual_review": r.get("manual_review"),
                        "trace": r["trace"], "concept_call_id": info["call_id"],
                        "search_scope": r.get("search_scope")})
    (STAGING / "cpo_all_grades.json").write_text(json.dumps(cpo_all, ensure_ascii=False, indent=1), encoding="utf-8")
    stats["cpo_all_distribution"] = {g: sum(1 for x in cpo_all if x["grade"] == g) for g in grade.GRADES}

    (STAGING / "concept_links.json").write_text(json.dumps(links, ensure_ascii=False, indent=1), encoding="utf-8")
    (STAGING / "memberships.json").write_text(json.dumps(members, ensure_ascii=False, indent=1), encoding="utf-8")
    dist = {}
    for l in links:
        dist.setdefault(l["concept"], {}).setdefault(l["grade"], 0)
        dist[l["concept"]][l["grade"]] += 1
    stats.update(extractor=extractor, claims=len(claims), grade_distribution=dist,
                 needs_review=sum(l["needs_review"] for l in links),
                 manual_reviewed=sum((l.get("manual_review") or {}).get("status") == "applied" for l in links),
                 manual_review_stale=sum((l.get("manual_review") or {}).get("status") == "stale" for l in links), conflicts=sum(bool(l["conflict"]) for l in links))
    return stats


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", action="store_true")
    print(json.dumps(run(ap.parse_args().llm), ensure_ascii=False, indent=1))
