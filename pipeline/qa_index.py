"""问答检索索引：web/public/data/qa_index.json。

文档分两类：
- evidence：证据原文片段（公告 / 资讯 / 概念解析 / 10-K）
- card：由代码从快照数字生成的摘要卡片（公司、可比组、传导边）。卡片里的数字都是快照值的格式化结果，
  所以问答接口可以做“答案中的每个数字都必须出现在检索上下文里”的校验。
LLM 不参与生成索引。
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "web" / "public" / "data"
GRADE_TEXT = {"A": "A 有收入", "B": "B 订单/量产", "C": "C 研发/送样", "D": "D 仅表态", "E": "E 否认/无证据"}


def fmt_money(v: float | None, cur: str) -> str:
    if v is None:
        return "—"
    if cur == "CNY":
        return f"{v / 1e8:.2f} 亿元"
    return f"{v / 1e9:.2f} 十亿美元" if abs(v) >= 1e9 else f"{v / 1e6:.1f} 百万美元"


def pct(v: float | None) -> str:
    return "—" if v is None else f"{v * 100:.1f}%"


def company_card(c: dict, seg_names: dict) -> dict:
    m = c.get("metrics") or {}
    t = m.get("ttm") or {}
    parts = [f"{c['name']}（{c['code']}，{'A 股' if c['market'] == 'cn' else '海外'}）"]
    if c.get("primary_segment"):
        parts.append(f"主环节：{seg_names.get(c['primary_segment'], c['primary_segment'])}")
    for ms in (c.get("attribution") or {}).get("memberships", []):
        if ms.get("share") is not None:
            parts.append(f"{seg_names.get(ms['segment'], ms['segment'])}收入占比 {pct(ms['share'])}（{c['attribution'].get('label')} 分产品口径）")
    if t.get("revenue"):
        parts.append(f"TTM 营收 {fmt_money(t['revenue']['value'], m.get('currency'))}（{t['window'][0]} 至 {t['window'][1]}）")
    if t.get("gross_margin"):
        parts.append(f"TTM 毛利率 {pct(t['gross_margin']['value'])}")
    for concept, g in (c.get("grades") or {}).items():
        label = "光互联" if concept == "optical_interconnect" else "CPO"
        q = (g.get("best") or {}).get("quote")
        parts.append(f"{label}可信度 {GRADE_TEXT.get(g['grade'], g['grade'])}，依据：{g.get('basis')}" + (f"，引文：{q[:80]}" if q else ""))
    return {"id": f"card:company:{c['code']}", "kind": "card", "title": f"{c['name']} 概况", "text": "；".join(parts),
            "entity": c["code"], "ref": f"#/company/{c['code']}"}


def build() -> dict:
    companies = json.loads((DATA / "companies.json").read_text(encoding="utf-8"))
    evidence = json.loads((DATA / "evidence.json").read_text(encoding="utf-8"))
    tax = json.loads((DATA / "taxonomy.json").read_text(encoding="utf-8"))
    comps = json.loads((DATA / "comparables.json").read_text(encoding="utf-8"))
    trans = json.loads((DATA / "transmission.json").read_text(encoding="utf-8"))
    meta = json.loads((DATA / "meta.json").read_text(encoding="utf-8"))
    seg_names = {s["id"]: s["name"] for l in tax["layers"] for s in l["segments"]}
    names = {c["code"]: c["name"] for c in companies}

    docs = [company_card(c, seg_names) for c in companies]
    # 概念汇总卡：回答“概念股里谁真有业务”这类横向问题
    cpo_all = json.loads((DATA / "cpo_universe.json").read_text(encoding="utf-8"))
    by = {g: [x["name"] for x in cpo_all if x["grade"] == g] for g in "ABCDE"}
    docs.append({"id": "card:cpo_universe", "kind": "card", "title": "CPO 概念股里谁真有 CPO 业务：成分股可信度分布",
                 "text": f"CPO（共封装光学）概念成分股共 {len(cpo_all)} 只。按证据分级："
                         + "；".join(f"{GRADE_TEXT[g]} {len(by[g])} 只" for g in "ABCDE")
                         + f"。B 级（正式文件中有订单 / 量产 / 收入表述）：{'、'.join(by['B']) or '无'}。"
                         + f"C 级（研发 / 送样，或非正式来源的量产表述）：{'、'.join(by['C']) or '无'}。"
                         + "全集外的公司只有 iFinD 概念解析一个来源，不可能给 A 级。", "ref": "#/concepts"})
    for concept, label in (("cpo", "CPO"), ("optical_interconnect", "AI 光互联")):
        rows = {g: [c["name"] for c in companies if (c.get("grades") or {}).get(concept, {}).get("grade") == g] for g in "ABCDE"}
        docs.append({"id": f"card:concept:{concept}", "kind": "card", "title": f"{label}概念 26 家公司分级",
                     "text": f"{label}概念下 26 家 A 股公司的可信度分级：" + "；".join(
                         f"{GRADE_TEXT[g]}：{'、'.join(rows[g]) or '无'}" for g in "ABCDE"), "ref": "#/concepts"})
    for g in comps:
        rows = [f"{r['name']}：TTM 营收 {fmt_money((r.get('revenue_ttm_usd') or {}).get('value'), 'USD')}，"
                f"TTM 毛利率 {pct((r.get('gross_margin_ttm') or {}).get('value'))}" for r in g["table"]]
        nc = "；".join(f"{x['dim']}：{x['text']}" for x in g["non_comparable"])
        docs.append({"id": f"card:compare:{g['id']}", "kind": "card", "title": f"可比组·{g['name']}",
                     "text": f"{g['name']}可比组。{g.get('note') or ''}。" + "；".join(rows) + f"。不可比之处：{nc}", "ref": "#/compare"})
    var_names = {v["id"]: v["name"] for v in trans.get("variables", [])}
    for e in trans.get("edges", []):
        if e.get("derived"):
            continue
        sup = "；".join(s["quote"][:60] for s in e["support"][:2])
        docs.append({"id": f"card:edge:{e['id']}", "kind": "card", "title": f"传导·{var_names.get(e['from'], e['from'])}",
                     "text": f"{var_names.get(e['from'], e['from'])} → {seg_names.get(e['to'].split(':')[-1], e['to'])}（方向 {e['sign']}，时滞 {e['lag']}）："
                             f"{e['mechanism']}。置信度{e['confidence']}：{e['confidence_rule']}。" + (f"证据：{sup}" if sup else ""),
                     "ref": "#/transmission"})
    for x in trans.get("exposures", []):
        fx = x.get("fx_sensitivity")
        docs.append({"id": f"card:exposure:{x['code']}", "kind": "card", "title": f"{x['name']} 境外收入暴露",
                     "text": f"{x['name']}境外收入占比 {pct(x['value']['value'])}（{x['value']['label']}）"
                             + (f"；人民币对美元升值 1% 的静态敏感性约 {fx['value'] / 1e8:.2f} 亿元营收（非预测）" if fx else ""),
                     "entity": x["code"], "ref": f"#/company/{x['code']}"})
    for eid, ev in evidence.items():
        docs.append({"id": eid, "kind": "evidence", "title": ev["source_name"], "text": ev["text"], "date": ev.get("date"),
                     "entity": ev["entity"], "entity_name": names.get(ev["entity"], ev["entity"]), "url": ev.get("url")})
    out = {"version": meta["version"], "data_cutoff": meta["data_cutoff"], "docs": docs}
    (DATA / "qa_index.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return {"docs": len(docs), "cards": sum(1 for d in docs if d["kind"] == "card")}


if __name__ == "__main__":
    print(build())
