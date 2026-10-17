"""M2 汇总：证据 + 引文 → 概念可信度（concept_links.json）与环节归属（memberships.json）。

用法：python -m pipeline.m2 [--llm]
  --llm   先调用 DeepSeek 抽取（需要 DEEPSEEK_API_KEY）；否则读已有 claims.jsonl，没有就用规则按句回退抽取
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from pipeline import grade, attribution, extract
from pipeline.evidence import WINDOW, CPO_SLICES

ROOT = Path(__file__).resolve().parent.parent
STAGING = ROOT / "data" / "staging"
UNIVERSE = yaml.safe_load((ROOT / "config" / "universe.yaml").read_text(encoding="utf-8"))


def _jsonl(name):
    p = STAGING / name
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


def search_scope(c: dict) -> list[dict]:
    """E 级“无证据”必须给出检索范围（与 evidence.collect 的模板一致）。"""
    scope = [{"tool": "search_stocks", "query": f"CPO概念股中{s}的股票"} for s in CPO_SLICES]
    for tool, q in (("search_notice", f"{c['name']} 光模块 光通信 CPO 业务进展 订单 量产 研发"),
                    ("search_notice", f"{c['name']} 澄清 不涉及 光模块 CPO"),
                    ("search_news", f"{c['name']} 互动平台 光模块 CPO")):
        scope.append({"tool": tool, "query": q, "window": list(WINDOW)})
    return scope


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

    links, members = [], []
    for c in UNIVERSE["cn"]:
        evs = [e for e in evidences if e["entity"] == c["code"]]
        ids = {e["evidence_id"] for e in evs}
        cls = [x for x in claims if x["evidence_id"] in ids]
        for concept in grade.CONCEPTS:
            r = grade.grade_entity(c["code"], concept, evs, cls, records, search_scope(c))
            r.update(name=c["name"], role=c["role"], extractor=extractor)
            links.append(r)
        # 泛口径分部的文本佐证：正式来源里“光互联 + 量产/订单”的已校验引文
        oi = next(l for l in links if l["entity"] == c["code"] and l["concept"] == "optical_interconnect")
        # 只有引文里出现该环节自己的产品词，才算给这个环节背书
        formal_order = [t for t in oi["trace"] if t.get("formal") and t.get("stage") == "order"]
        items = grade.product_items(records, c["code"])
        support = {}
        for s in attribution.SEGMENTS:
            hits = [t["evidence_id"] for t in formal_order if any(k in t["quote"] for k in s.get("text_keywords", []))]
            if hits:
                support[s["id"]] = hits
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
                            "in_universe": True, "single_source": False, "needs_review": r["needs_review"]})
            continue
        evs = [e for e in evidences if e["entity"] == code]
        ids = {e["evidence_id"] for e in evs}
        r = grade.grade_entity(code, "cpo", evs, [x for x in claims if x["evidence_id"] in ids], [],
                               [{"tool": "search_stocks", "query": f"CPO概念股中{s}的股票"} for s in CPO_SLICES])
        cpo_all.append({"entity": code, "name": info["name"], "grade": r["grade"], "basis": r["basis"],
                        "in_universe": False, "single_source": True, "needs_review": r["needs_review"],
                        "trace": r["trace"], "concept_call_id": info["call_id"]})
    (STAGING / "cpo_all_grades.json").write_text(json.dumps(cpo_all, ensure_ascii=False, indent=1), encoding="utf-8")
    stats["cpo_all_distribution"] = {g: sum(1 for x in cpo_all if x["grade"] == g) for g in grade.GRADES}

    (STAGING / "concept_links.json").write_text(json.dumps(links, ensure_ascii=False, indent=1), encoding="utf-8")
    (STAGING / "memberships.json").write_text(json.dumps(members, ensure_ascii=False, indent=1), encoding="utf-8")
    dist = {}
    for l in links:
        dist.setdefault(l["concept"], {}).setdefault(l["grade"], 0)
        dist[l["concept"]][l["grade"]] += 1
    stats.update(extractor=extractor, claims=len(claims), grade_distribution=dist,
                 needs_review=sum(l["needs_review"] for l in links), conflicts=sum(bool(l["conflict"]) for l in links))
    return stats


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", action="store_true")
    print(json.dumps(run(ap.parse_args().llm), ensure_ascii=False, indent=1))
