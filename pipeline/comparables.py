"""M3 中外可比：为何可比 / 哪些不可比 / 口径调整后的对比表。

文字全部由模板 + 字段 / 证据生成，每条都带 refs；数字全部来自 metrics.json（确定性计算），不经 LLM。
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path

import yaml

from pipeline.attribution import SEG_BY_ID
from pipeline.metrics import Value

ROOT = Path(__file__).resolve().parent.parent
STAGING = ROOT / "data" / "staging"
CFG = yaml.safe_load((ROOT / "config" / "comparables.yaml").read_text(encoding="utf-8"))
FYE_TEXT = {"1231": "12 月 31 日（自然年）", "0630": "6 月底", "1031": "10 月底", "0131": "1 月底", "0430": "4 月底"}


def _load(name, default=None):
    p = STAGING / name
    if not p.exists():
        return default
    if name.endswith(".jsonl"):
        return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()]
    return json.loads(p.read_text(encoding="utf-8"))


def _rec(recs, ds, code, field_):
    for r in recs:
        if r["dataset"] == ds and r["entity"] == code and r["field"] == field_ and r["value"] is not None:
            return r
    return None


TOPIC_MUST = {"customers": "customer", "model": None, "products": None, "datacom": None}


def _quote(quotes, code, topic):
    """LLM 给引文分的主题可能不准：客户类引文必须真的提到 customer（确定性检查）。"""
    must = TOPIC_MUST.get(topic)
    for q in quotes:
        if q["entity"] == code and q["topic"] == topic and (not must or must in q["quote"].lower()):
            return q
    return None


def net_margin(m: dict) -> dict | None:
    t = (m or {}).get("ttm") or {}
    if "net_profit" in t and "revenue" in t and t["revenue"]["value"]:
        return asdict(Value(t["net_profit"]["value"] / t["revenue"]["value"], "ratio", t["window"][0], t["window"][1],
                            formula="TTM 净利润 / TTM 营收（同币种）",
                            inputs=[{"net_profit_ttm": t["net_profit"]["value"]}, {"revenue_ttm": t["revenue"]["value"]}]))
    return None


def pe_of(m: dict) -> tuple[dict | None, str | None]:
    v = (m or {}).get("valuation") or {}
    pe = v.get("市盈率(PE,TTM)") or v.get("市盈率PE(TTM)")
    if pe and pe["value"] is not None and pe["value"] < 0:
        return pe, "TTM 亏损，PE 为负，不适用"
    return pe, None


TECH_NODE = re.compile(r"(?:1\.6T|3\.2T|6\.4T|800G|400G|200G|1\.6\s?Tb?(?:/s|ps)?|800\s?Gb?(?:/s|ps)?)")


def tech_sentence(sec_ev: list[dict], code: str) -> tuple[str, str] | None:
    """10-K 原文里第一句出现速率代际的句子（逐字摘取，不经 LLM）。"""
    for e in sec_ev:
        if e["entity"] != code:
            continue
        for s in re.split(r"(?<=[.;])\s+", e["text"]):
            if TECH_NODE.search(s) and 20 <= len(s) <= 400:
                return e["evidence_id"], s.strip()
    return None


def build() -> list[dict]:
    sec_ev = _load("evidence_sec.jsonl", [])
    recs = _load("records.jsonl", [])
    metrics = _load("metrics.json", {"companies": {}})["companies"]
    members = {m["entity"]: m for m in _load("memberships.json", [])}
    links = {(l["entity"], l["concept"]): l for l in _load("concept_links.json", [])}
    quotes = _load("sec_quotes.json", [])
    out = []
    for g in CFG["groups"]:
        codes = g["cn"] + g["global"]
        seg = SEG_BY_ID[g["segment"]]
        comparable, non_comparable, table = [], [], []

        # ---- 为何可比 --------------------------------------------------
        for code in g["cn"]:
            mem = members.get(code, {})
            ms = next((x for x in mem.get("memberships", []) if x["segment"] == g["segment"]), None)
            items = "、".join(str(i["name"]) for i in (ms or {}).get("items", [])) or "—"
            comparable.append({"dim": "产品线 / 环节位置", "code": code,
                               "text": f"{mem.get('name', code)}：主环节为{SEG_BY_ID.get(mem.get('primary'), {}).get('name', '未定')}，"
                                       f"对应分产品项目“{items}”（{mem.get('label') or '—'}）",
                               "refs": [{"kind": "metric", "ref": f"companies.json#code={code}/attribution"}]})
            oi = links.get((code, "optical_interconnect"), {})
            if oi.get("best"):
                comparable.append({"dim": "数通 / AI 光互联业务", "code": code,
                                   "text": f"{mem.get('name', code)}：{oi['best']['quote'][:80]}",
                                   "refs": [{"kind": "evidence", "ref": oi["best"]["evidence_id"]}]})
            # 客户：前五大客户销售额占比（iFinD 2025 年报）+ 正式来源中提到客户的引文
            cust = _rec(recs, "cn_customers", code, "主要客户销售额合计占比")
            cq = next((t for t in oi.get("trace", []) if t.get("formal") and "客户" in t.get("quote", "")), None)
            if cust or cq:
                comparable.append({"dim": "客户", "code": code,
                                   "text": f"{mem.get('name', code)}：" + (f"前五大客户销售额合计占比 {cust['value']:.2f}%（{cust['label']}）" if cust else "")
                                           + (f"；{cq['quote'][:70]}" if cq else ""),
                                   "refs": ([{"kind": "record", "ref": cust["source"]["call_id"], "column": cust["source"]["column"]}] if cust else [])
                                           + ([{"kind": "evidence", "ref": cq["evidence_id"]}] if cq else [])})
            # 技术节点：已校验引文里出现的速率代际（确定性摘句）
            tq = next((t for t in sorted(oi.get("trace", []), key=lambda t: not t.get("formal"))
                       if TECH_NODE.search(t.get("quote", ""))), None)
            if tq:
                comparable.append({"dim": "技术节点", "code": code,
                                   "text": f"{mem.get('name', code)}：{tq['quote'][:90]}（速率：{'、'.join(sorted(set(TECH_NODE.findall(tq['quote']))))}）",
                                   "refs": [{"kind": "evidence", "ref": tq["evidence_id"]}]})
        for code in g["global"]:
            name = metrics.get(code, {}).get("name", code)
            ts = tech_sentence(sec_ev, code)
            if ts:
                comparable.append({"dim": "技术节点", "code": code, "text": f"{name}（10-K 原文摘句）：{ts[1]}",
                                   "refs": [{"kind": "evidence", "ref": ts[0]}]})
            for topic, dim in (("products", "产品线 / 环节位置"), ("datacom", "数通 / AI 光互联业务"), ("customers", "客户")):
                q = _quote(quotes, code, topic)
                if q:
                    comparable.append({"dim": dim, "code": code, "text": f"{name}（10-K）：{q['quote'][:160]}",
                                       "refs": [{"kind": "evidence", "ref": q["evidence_id"]}]})

        # ---- 哪些不可比 -------------------------------------------------
        fyes = {c: metrics.get(c, {}).get("fye", "1231") for c in codes}
        windows = {c: ((metrics.get(c, {}).get("ttm") or {}).get("window")) for c in codes}
        if len(set(fyes.values())) > 1:
            non_comparable.append({"dim": "财年", "text": "财年截止日不同。iFinD 的财年标签已映射成真实区间，TTM 一律取最近 4 个真实单季度，"
                                                        "但窗口末端仍可能相差 1–2 个月。",
                                   "values": {c: f"{FYE_TEXT.get(f, f)}；TTM {windows[c][0]}~{windows[c][1]}" if windows[c] else FYE_TEXT.get(f, f)
                                              for c, f in fyes.items()}})
        acc = {}
        for c in g["cn"]:
            acc[c] = "中国企业会计准则（CAS）"
        for c in g["global"]:
            r = _rec(recs, "gl_meta", c, "会计准则类型")
            acc[c] = r["value"] if r else "未取得（iFinD 未返回）"
        if g["cn"] and g["global"]:
            non_comparable.append({"dim": "会计准则", "text": "CAS 与 US GAAP 在研发资本化、股份支付、政府补助等处理上有差异，"
                                                          "利润率只做方向性对比。", "values": acc})
            non_comparable.append({"dim": "币种", "text": "A 股以人民币报告，海外以美元报告。流量按 TTM 窗口内月均中间价折算，"
                                                        "市值按最近一个月的月均价折算（近似）。",
                                   "values": {c: metrics.get(c, {}).get("currency", "") for c in codes}})
        biz = {}
        for c in g["cn"]:
            ms = next((x for x in members.get(c, {}).get("memberships", []) if x["segment"] == g["segment"]), None)
            biz[c] = (f"该环节收入占比 {ms['share'] * 100:.1f}%（分产品口径）" if ms and ms.get("share") is not None
                      else "该环节占比未拆分")
        for c in g["global"]:
            q = _quote(quotes, c, "products")
            biz[c] = "10-K 未按可对比口径拆分该环节收入" + (f"；业务描述：{q['quote'][:90]}" if q else "")
        non_comparable.append({"dim": "业务结构", "text": "海外公司多为多元业务，iFinD 不提供分部收入，10-K 也没有按光模块 / 光芯片口径拆分，"
                                                      "所以整体利润率会被其他业务稀释或抬高。", "values": biz})
        models = {}
        for c in g["global"]:
            q = _quote(quotes, c, "model")
            if q:
                models[c] = q["quote"][:140]
        for c in g["cn"]:
            p = _rec(recs, "cn_profile", c, "主营业务")
            if p:
                models[c] = f"主营业务：{p['value']}"
        if models:
            non_comparable.append({"dim": "商业模式", "text": "代工（为 OEM 提供制造服务）、垂直整合、Fabless 的毛利率结构不同，不宜直接比较毛利率。",
                                   "values": models, "refs": [{"kind": "evidence", "ref": q["evidence_id"]}
                                                              for c in g["global"] if (q := _quote(quotes, c, "model"))]})
        if g["cn"] and g["global"]:
            non_comparable.append({"dim": "并表范围", "text": "两边都是合并报表口径，但净利润口径不同：A 股用归属于母公司所有者的净利润，"
                                                          "海外（iFinD 净利润）为合并净利润，含少数股东权益；子公司持股比例不同的公司，"
                                                          "净利率差异里有一部分来自并表口径。",
                                   "values": {**{c: "合并报表；净利润 = 归母净利润（iFinD 单季度归属于母公司所有者的净利润）" for c in g["cn"]},
                                              **{c: "合并报表（10-K consolidated）；净利润 = 合并净利润（iFinD 净利润）" for c in g["global"]}}})
        non_comparable.append({"dim": "上市地估值体系", "text": "A 股与美股的流动性、投资者结构和无风险利率不同，PE / PS 只能并排展示，不能当作“贵或便宜”的判断。",
                               "values": {}})

        # ---- 对比表 ----------------------------------------------------
        for c in codes:
            m = metrics.get(c) or {}
            pe, pe_note = pe_of(m)
            ms = next((x for x in members.get(c, {}).get("memberships", []) if x["segment"] == g["segment"]), None)
            focus = None
            if ms and ms.get("share") is not None:
                focus = asdict(Value(ms["share"], "ratio", label=members[c].get("label"),
                                     formula=f"该环节分产品收入 / 同期营业收入（{members[c].get('label')}）",
                                     inputs=[{"item": i["name"], "revenue": i["revenue"], **i["source"]} for i in ms["items"]]))
            nm = net_margin(m)
            nm_note = ("TTM 含单季大额亏损（数值已与 SEC 10-K 核对一致），利润率不具可比性"
                       if nm and nm["value"] < -0.5 else None)
            fxw = (m.get("ttm_usd") or {}).get("fx")
            fx_basis = ({"kind": "converted", "rate": fxw["rate"], "months": fxw["months"],
                         "text": f"CNY ÷ {fxw['rate']:.4f}（{fxw['months'][0]}～{fxw['months'][-1]} 月均中间价的平均值，中国外汇交易中心，iFinD EDB）",
                         "inputs": fxw["inputs"]} if fxw else
                        {"kind": "native", "text": "美元原币，无需折算"} if m.get("currency") == "USD" else None)
            mc_fx = (m.get("market_cap_usd") or {}).get("formula") if m.get("currency") == "CNY" else None
            table.append({"code": c, "name": m.get("name", c), "market": m.get("market"), "fye": m.get("fye"),
                          "fx_basis": fx_basis, "market_cap_fx": mc_fx,
                          "net_margin_note": nm_note,
                          "accounting": acc.get(c), "currency": m.get("currency"),
                          "ttm_window": (m.get("ttm") or {}).get("window"),
                          "revenue_ttm_usd": (m.get("ttm_usd") or {}).get("revenue"),
                          "gross_margin_ttm": (m.get("ttm") or {}).get("gross_margin"),
                          "net_margin_ttm": net_margin(m), "pe_ttm": pe, "pe_note": pe_note,
                          "ps_ttm": m.get("ps_ttm"), "market_cap_usd": m.get("market_cap_usd"),
                          "focus_share": focus,
                          "focus_share_note": None if focus else ("海外分部未按该环节拆分" if c in g["global"] else "泛口径分部，占比未拆分")})
        adjustments = [
            {"step": "期间对齐", "text": "海外财年标签 → 真实区间（会计年结日），按真实期末日取最近 4 个连续单季度。"},
            {"step": "单季度推导", "text": "海外累计值差分得单季度，毛利 = 累计营收 × 累计毛利率再差分；iFinD 缺失时用 SEC XBRL 一手数据补齐，并在公式里注明。"},
            {"step": "币种", "text": "A 股 TTM 流量 ÷ TTM 窗口 12 个月月均中间价的平均值；市值 ÷ 最近一个月月均中间价（近似）。"},
            {"step": "估值", "text": "PE(TTM) 取 iFinD 原值，负值标为不适用；PS = 总市值(USD) ÷ 本产品计算的 TTM 营收(USD)。"},
        ]
        out.append({"id": g["id"], "name": g["name"], "segment": g["segment"], "segment_name": seg["name"],
                    "cn": g["cn"], "global": g["global"], "note": g.get("note"),
                    "comparable": comparable, "non_comparable": non_comparable, "table": table, "adjustments": adjustments})
    (STAGING / "comparables.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


if __name__ == "__main__":
    for g in build():
        print("==", g["name"], len(g["comparable"]), "comparable,", len(g["non_comparable"]), "non-comparable")
        for r in g["table"]:
            f = lambda v: None if not v else round(v["value"], 4)
            print(f"   {r['name']:10s} rev_usd={f(r['revenue_ttm_usd'])} gm={f(r['gross_margin_ttm'])} nm={f(r['net_margin_ttm'])} "
                  f"pe={f(r['pe_ttm'])} ps={f(r['ps_ttm'])} focus={f(r['focus_share'])} {r['pe_note'] or ''}")
