"""生成带版本号的快照：web/public/data/*.json（前端只读这些文件），并存档到 data/snapshots/<version>/。

数据契约见 docs/snapshot_schema.md。前端只做展示和格式化，不计算新数字；
每个展示的数字都能用 JSON 路径回查到这里（数字溯源测试依赖这一点）。
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path

import yaml

from pipeline import attribution, grade

ROOT = Path(__file__).resolve().parent.parent
STAGING = ROOT / "data" / "staging"
OUT = ROOT / "web" / "public" / "data"
ARCHIVE = ROOT / "data" / "snapshots"
UNIVERSE = yaml.safe_load((ROOT / "config" / "universe.yaml").read_text(encoding="utf-8"))
CST = timezone(timedelta(hours=8))
DISCLAIMER = ("本产品为产业研究工具，不构成任何投资建议；不提供买卖、目标价或仓位建议。数据非实时，均标注截止日。"
              "公告与研报只展示片段和引用链接，不分发全文。")


def _json(name, default=None):
    p = STAGING / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def _jsonl(name):
    p = STAGING / name
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


def _profile(recs, code):
    return {r["field"]: r["value"] for r in recs if r["dataset"] == "cn_profile" and r["entity"] == code}


def _slim_grade(l: dict) -> dict:
    keep = ("grade", "basis", "needs_review", "review_items", "conflict", "a_detail", "best", "search_scope", "extractor", "rule_grade", "manual_review")
    out = {k: l.get(k) for k in keep if l.get(k) is not None}
    out["trace"] = [t for t in l["trace"] if t.get("grade") or t.get("stage") == "deny"]
    return out


def build(version: str | None = None) -> dict:
    recs = _jsonl("records.jsonl")
    links = _json("concept_links.json", [])
    members = {m["entity"]: m for m in _json("memberships.json", [])}
    metrics = _json("metrics.json", {"companies": {}})
    evidence = {e["evidence_id"]: e for e in _jsonl("evidence_raw.jsonl") + _jsonl("evidence_vars.jsonl") + _jsonl("evidence_sec.jsonl")}
    calls = {}
    for c in _jsonl("api_calls.jsonl"):
        calls.setdefault(c["call_id"], c)  # 同一请求以首次真实调用为准

    companies = []
    for c in UNIVERSE["cn"]:
        m = members.get(c["code"], {})
        g = {l["concept"]: _slim_grade(l) for l in links if l["entity"] == c["code"]}
        prof = _profile(recs, c["code"])
        companies.append({"code": c["code"], "name": c["name"], "market": "cn", "role": c["role"],
                          "profile": {k: prof.get(k) for k in ("公司中文名称", "所属申万行业", "主营业务", "可比公司")},
                          "primary_segment": m.get("primary"), "attribution": m, "grades": g,
                          "metrics": metrics["companies"].get(c["code"])})
    for c in UNIVERSE["global"]:
        companies.append({"code": c["code"], "name": c["name"], "market": "global", "role": c["role"],
                          "primary_segment": c["segment_seed"], "attribution": {"basis": "配置（海外分部收入 iFinD 未提供）"},
                          "metrics": metrics["companies"].get(c["code"]), "sec_cik": c["cik"]})

    # 只发布被引用到的证据与调用记录
    used = set()
    for co in companies:
        for g in (co.get("grades") or {}).values():
            used |= {t["evidence_id"] for t in g.get("trace", [])}
            if g.get("conflict"):
                used.add(g["conflict"]["deny_evidence"])
        for mm in (co.get("attribution") or {}).get("memberships", []):
            used |= set(mm.get("evidence", []))
    cpo_all = _json("cpo_all_grades.json", [])
    for x in cpo_all:
        used |= {t["evidence_id"] for t in x.get("trace", []) if t.get("evidence_id")}
    drafts = _json("drafts.json", {"company": {}, "compare": {}, "edge": {}})
    for co in companies:  # AI 初稿：只挂通过校验的，标“AI 初稿”，带所依据的事实
        d = drafts["company"].get(co["code"])
        co["ai_summary"] = d if d and d["status"] == "ok" else None
    comparables = _json("comparables.json", [])
    for g in comparables:
        d = drafts["compare"].get(g["id"])
        g["ai_draft"] = d if d and d["status"] == "ok" else None
    # refs 统一成 JSON Pointer（契约要求）：companies.json#code=X/... → companies.json#/<下标>/...
    pos = {c["code"]: i for i, c in enumerate(companies)}
    for g in comparables:
        for item in g["comparable"] + g["non_comparable"]:
            for r in item.get("refs", []):
                if r["kind"] == "metric" and r["ref"].startswith("companies.json#code="):
                    code, _, rest = r["ref"][len("companies.json#code="):].partition("/")
                    r["ref"] = f"companies.json#/{pos[code]}/{rest}"
    for g in comparables:
        for item in g["comparable"] + g["non_comparable"]:
            used |= {r["ref"] for r in item.get("refs", []) if r["kind"] == "evidence"}
    transmission = _json("transmission.json", {"variables": [], "edges": []})
    # 事件窗口里的路径引用 companies[代码].metrics.quarters[i].x → JSON Pointer（契约要求）
    import re as _re
    def _ptr(path):
        m = _re.fullmatch(r"companies\[(.+?)\]\.metrics\.quarters\[(\d+)\]\.(\w+)", path or "")
        return f"companies.json#/{pos[m.group(1)]}/metrics/quarters/{m.group(2)}/{m.group(3)}/value" if m and m.group(1) in pos else None
    for ew in transmission.get("event_windows", []):
        for c in ew.get("companies", []):
            for r in c["rows"]:
                for k in ("revenue_ref", "gross_margin_ref"):
                    if r.get(k):
                        r[k] = _ptr(r[k])
    for e in transmission.get("edges", []):
        d = drafts["edge"].get(e["id"])
        e["ai_explanation"] = d if d and d["status"] == "ok" else None
    for e in transmission.get("edges", []):
        used |= {s["evidence_id"] for s in e.get("support", [])}
    for v in transmission.get("variables", []):
        used |= set(v.get("evidence", []))
    # 被引用的证据和调用记录一个都不能漏：扫描所有要发布的结构（含子环节、AI 初稿依据、事件对照、检索记录、record 引用）
    checks_pub = {"conflicts": _json("conflicts.json", []), "ingest_missing": (_json("ingest_report.json") or {}).get("missing", [])}
    blob = json.dumps([companies, comparables, transmission, cpo_all, checks_pub], ensure_ascii=False)  # checks 也要扫（审查发现冲突表的调用记录漏发）
    used |= set(re.findall(r"ev_[0-9a-f]{10}", blob)) & set(evidence)
    ev_out = {k: v for k, v in evidence.items() if k in used}
    call_ids = {e["call_id"] for e in ev_out.values() if e.get("call_id")}
    call_ids |= set(re.findall(r"\b[0-9a-f]{12}\b", blob)) & set(calls)
    calls_out = {k: v for k, v in calls.items() if k in call_ids}

    taxonomy = [{"id": l["id"], "name": l["name"],
                 "segments": [{"id": s["id"], "name": s["name"], "desc": s["desc"], "sub_segments": s.get("sub_segments", []),
                               "product_keywords": s["product_keywords"], "generic_items": s["generic_items"]}
                              for s in l["segments"]]}
                for l in yaml.safe_load((ROOT / "config" / "segments.yaml").read_text(encoding="utf-8"))["layers"]]

    files = {
        "companies.json": companies,
        "evidence.json": ev_out,
        "calls.json": calls_out,
        "cpo_universe.json": cpo_all,
        "comparables.json": comparables,
        "transmission.json": transmission,
        "checks.json": {"summary": (_json("checks.json") or {}).get("summary"), "conflicts": _json("conflicts.json", []),
                        "ai_drafts": {"total": sum(len(v) for v in drafts.values()),
                                      "rejected": [{"kind": k, "key": kk, "errors": v["errors"]} for k, vv in drafts.items()
                                                   for kk, v in vv.items() if v["status"] != "ok"]},
                        "ingest_missing": (_json("ingest_report.json") or {}).get("missing", [])},
        "compliance.json": yaml.safe_load((ROOT / "config" / "compliance.yaml").read_text(encoding="utf-8")),
        "taxonomy.json": {"layers": taxonomy, "source_note": attribution.SEG.get("source_note"),
                          "adjustments": attribution.SEG.get("adjustments", []), "evidence_tiers": attribution.SEG.get("evidence_tiers", {}), "item_overrides": attribution.SEG.get("item_overrides", []),
                          "grade_rules": {"grades": grade.GRADES, "stage_words": grade.STAGE_WORDS,
                                          "deny_words": grade.DENY_WORDS, "concepts": grade.CONCEPTS}},
    }
    content_hash = hashlib.sha1(json.dumps(files, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:8]
    now = datetime.now(CST)
    version = version or now.strftime("v%Y%m%d-%H%M%S")  # 精确到秒，避免同一分钟重建时“上一版”是自己
    # 上一版 = 内容哈希不同的最近一次存档（审查发现：diff 自引用、当前为空）
    prev_version = None
    if ARCHIVE.exists():
        for d in sorted((p for p in ARCHIVE.iterdir() if p.is_dir() and p.name != version), key=lambda p: p.name, reverse=True):
            try:
                h = json.loads((d / "meta.json").read_text(encoding="utf-8")).get("content_hash")
            except (OSError, ValueError):
                continue
            if h != content_hash:
                prev_version = d.name
                break
    meta = {"version": version, "content_hash": content_hash, "generated_at": now.isoformat(timespec="seconds"),
            "data_cutoff": UNIVERSE["snapshot_cutoff"], "financial_latest_period": "2026-06-30",
            "realtime": False, "disclaimer": DISCLAIMER, "previous_version": prev_version,
            "sources": ["iFinD MCP（同花顺）", "SEC EDGAR XBRL", "DeepSeek（引文抽取、建议等级、AI 初稿、问答；不参与任何数字计算和等级裁定）"],
            "counts": {k: (len(v) if isinstance(v, (list, dict)) else None) for k, v in files.items()}}
    files["meta.json"] = meta
    files["diff.json"] = diff(prev_version, files) if prev_version else {"previous_version": None, "changes": []}

    OUT.mkdir(parents=True, exist_ok=True)
    for name, obj in files.items():
        (OUT / name).write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    from pipeline import qa_index
    meta["counts"]["qa_index.json"] = qa_index.build()["docs"]
    (OUT / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    arch = ARCHIVE / version
    if arch.exists():
        shutil.rmtree(arch)
    shutil.copytree(OUT, arch)
    return meta


def diff(prev_version: str, files: dict) -> dict:
    """与上一版对比：等级变动、新增证据、数字变化（TTM 营收 / 毛利率 / 市值）。"""
    pdir = ARCHIVE / prev_version
    old_co = {c["code"]: c for c in json.loads((pdir / "companies.json").read_text(encoding="utf-8"))}
    old_ev = set(json.loads((pdir / "evidence.json").read_text(encoding="utf-8")))
    changes = []
    for c in files["companies.json"]:
        o = old_co.get(c["code"])
        if not o:
            changes.append({"type": "new_company", "code": c["code"], "name": c["name"]})
            continue
        for concept, g in (c.get("grades") or {}).items():
            og = (o.get("grades") or {}).get(concept, {}).get("grade")
            if og != g.get("grade"):
                changes.append({"type": "grade", "code": c["code"], "name": c["name"], "concept": concept,
                                "from": og, "to": g.get("grade")})
        # 环节归属变化（主环节、各环节占比）
        if (o.get("primary_segment") != c.get("primary_segment")):
            changes.append({"type": "attribution", "code": c["code"], "name": c["name"], "field": "primary_segment",
                            "from": o.get("primary_segment"), "to": c.get("primary_segment")})
        osh = {m["segment"]: m.get("share") for m in (o.get("attribution") or {}).get("memberships", [])}
        for m in (c.get("attribution") or {}).get("memberships", []):
            if m["segment"] in osh and osh[m["segment"]] != m.get("share"):
                changes.append({"type": "number", "code": c["code"], "name": c["name"], "field": f"share.{m['segment']}",
                                "from": osh[m["segment"]], "to": m.get("share")})
        for path in (("ttm", "revenue"), ("ttm", "gross_margin"), ("market_cap_usd",), ("ps_ttm",)):
            nv, ov = c.get("metrics") or {}, o.get("metrics") or {}
            for k in path:
                nv, ov = (nv or {}).get(k), (ov or {}).get(k)
            nv = nv.get("value") if isinstance(nv, dict) else None
            ov = ov.get("value") if isinstance(ov, dict) else None
            if nv != ov:
                changes.append({"type": "number", "code": c["code"], "name": c["name"], "field": ".".join(path),
                                "from": ov, "to": nv})
    # CPO 概念全量名单的等级变化
    oc = {x["entity"]: x for x in json.loads((pdir / "cpo_universe.json").read_text(encoding="utf-8"))} \
        if (pdir / "cpo_universe.json").exists() else {}
    for x in files["cpo_universe.json"]:
        if x["entity"] in oc and oc[x["entity"]]["grade"] != x["grade"] and not x.get("in_universe"):
            changes.append({"type": "grade", "code": x["entity"], "name": x["name"], "concept": "cpo（全量名单）",
                            "from": oc[x["entity"]]["grade"], "to": x["grade"]})
    new_ev = [k for k in files["evidence.json"] if k not in old_ev]
    if new_ev:
        changes.append({"type": "new_evidence", "count": len(new_ev), "ids": new_ev[:50]})
    return {"previous_version": prev_version, "changes": changes}


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=False, indent=1))
