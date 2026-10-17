"""M3 规范化指标：真实期间单季度序列 → TTM → 统一币种（确定性计算，不经 LLM）。

每个派生值都是 Value：{value, unit, period, formula, inputs[]}，inputs 指回 records 的 source（call_id/列名）
或 SEC accn，页面上可以逐层展开。

口径：
- A 股：单季度营收 / 营业成本 / 归母净利直接取 iFinD 单季度值；毛利率 = 1 − 成本/营收
- 海外：iFinD 累计值按会计年结日映射到真实区间后差分得单季度；毛利 = 累计营收 × 累计毛利率，再差分
- TTM：最近 4 个连续单季度求和；毛利率 TTM = Σ毛利 / Σ营收
- 汇率：TTM 流量按窗口内 12 个月的月均中间价的算术平均折算；市值按截止日前最近一个月的月均价折算（近似，标注）
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict, field
from datetime import date
from pathlib import Path
from statistics import mean

import yaml

from pipeline.fiscal import map_label, _add_months

ROOT = Path(__file__).resolve().parent.parent
STAGING = ROOT / "data" / "staging"
UNIVERSE = yaml.safe_load((ROOT / "config" / "universe.yaml").read_text(encoding="utf-8"))
FX_FIELD = "平均汇率:中间价:美元兑人民币"


@dataclass
class Value:
    value: float | None
    unit: str
    period_start: str | None = None
    period_end: str | None = None
    fiscal: str | None = None
    label: str | None = None
    formula: str = "原始值"
    inputs: list = field(default_factory=list)


def _ref(r: dict) -> dict:
    return {"value": r["value"], "field": r["field"], "label": r["label"], "dataset": r["dataset"], **r["source"]}


def _idx(recs, dataset, field_, entity):
    return {r["label"]: r for r in recs if r["dataset"] == dataset and r["field"] == field_
            and r["entity"] == entity and r["value"] is not None and r["label"]}


def quarter_start(end: date) -> date:
    """单季度起始日。月末期末：两个月前的月初；52/53 周财年的非月末期末（如 SEC 的 2025-11-02）：往前 3 个月加 1 天。
    （审查发现：之前一律按月初算，博通 TTM 窗口被标成 334 天）"""
    import calendar
    from datetime import timedelta
    if end.day == calendar.monthrange(end.year, end.month)[1]:
        return _add_months(date(end.year, end.month, 1), -2)
    return _add_months(end, -3) + timedelta(days=1)


# ---- A 股 ------------------------------------------------------------------
def cn_quarters(recs, code) -> list[dict]:
    rev = _idx(recs, "cn_quarterly", "单季度.营业收入", code)
    cost = _idx(recs, "cn_quarterly", "单季度.营业成本", code)
    np_ = _idx(recs, "cn_quarterly", "单季度.归属于母公司所有者的净利润", code)
    out = []
    for label in sorted(rev):
        p = map_label(label, "1231")
        q = {"end": p.end.isoformat(), "start": quarter_start(p.end).isoformat(),
             "fiscal": f"{p.end.year}Q{p.quarters}", "label": label,
             "revenue": asdict(Value(rev[label]["value"], "CNY", formula="iFinD 单季度营业收入", inputs=[_ref(rev[label])]))}
        if label in cost:
            gp = rev[label]["value"] - cost[label]["value"]
            q["gross_profit"] = asdict(Value(gp, "CNY", formula="单季度营业收入 − 单季度营业成本",
                                             inputs=[_ref(rev[label]), _ref(cost[label])]))
            q["gross_margin"] = asdict(Value(gp / rev[label]["value"] if rev[label]["value"] else None, "ratio",
                                             formula="1 − 单季度营业成本 / 单季度营业收入",
                                             inputs=[_ref(rev[label]), _ref(cost[label])]))
        if label in np_:
            q["net_profit"] = asdict(Value(np_[label]["value"], "CNY", formula="iFinD 单季度归母净利润", inputs=[_ref(np_[label])]))
        out.append(q)
    return out


# ---- 海外 ------------------------------------------------------------------
def fye_of(recs, code) -> str:
    for r in recs:
        if r["dataset"] == "gl_profile" and r["entity"] == code and r["field"] == "会计年结日" and r["value"] is not None:
            return f"{int(float(r['value'])):04d}"
    return "1231"


def gl_quarters(recs, code) -> list[dict]:
    fye = fye_of(recs, code)
    rev = _idx(recs, "gl_cumulative", "营业收入", code)
    gm = _idx(recs, "gl_cumulative", "销售毛利率", code)
    np_ = _idx(recs, "gl_cumulative", "净利润", code)
    periods = {l: map_label(l, fye) for l in rev}
    by_key = {(p.fiscal_year, p.quarters): l for l, p in periods.items()}
    out = []
    for label, p in sorted(periods.items(), key=lambda x: x[1].end):
        prev = by_key.get((p.fiscal_year, p.quarters - 1)) if p.quarters > 1 else None
        if p.quarters > 1 and not prev:
            continue  # 缺上一累计期，无法差分
        start = quarter_start(p.end).isoformat()
        q = {"end": p.end.isoformat(), "start": start, "fiscal": p.name.replace(" H1", " Q2").replace(" 9M", " Q3").replace(" FY", " Q4"),
             "label": label, "fye": fye}
        if prev:
            v = rev[label]["value"] - rev[prev]["value"]
            q["revenue"] = asdict(Value(v, "USD", formula=f"累计营收[{label}] − 累计营收[{prev}]（财年标签已映射为真实区间）",
                                        inputs=[_ref(rev[label]), _ref(rev[prev])]))
        else:
            v = rev[label]["value"]
            q["revenue"] = asdict(Value(v, "USD", formula="财年 Q1 累计营收即单季度", inputs=[_ref(rev[label])]))
        if label in gm and (not prev or prev in gm):
            gp_cum = rev[label]["value"] * gm[label]["value"] / 100
            gp = gp_cum - (rev[prev]["value"] * gm[prev]["value"] / 100 if prev else 0)
            ins = [_ref(rev[label]), _ref(gm[label])] + ([_ref(rev[prev]), _ref(gm[prev])] if prev else [])
            q["gross_profit"] = asdict(Value(gp, "USD", formula="累计营收 × 累计毛利率 差分", inputs=ins))
            q["gross_margin"] = asdict(Value(gp / v if v else None, "ratio", formula="单季度毛利 / 单季度营收", inputs=ins))
        if label in np_ and (not prev or prev in np_):
            nv = np_[label]["value"] - (np_[prev]["value"] if prev else 0)
            q["net_profit"] = asdict(Value(nv, "USD", formula="累计净利润差分",
                                           inputs=[_ref(np_[label])] + ([_ref(np_[prev])] if prev else [])))
        out.append(q)
    return out


def sec_quarters(cik: str) -> dict[str, dict]:
    """SEC 一手披露的单季度营收 / 毛利（YTD 差分）。键：真实期末日。"""
    from pipeline import sec
    from pipeline.checks import sec_singles
    out: dict[str, dict] = {}
    rtag, rfacts = sec.first_available(cik, sec.REVENUE_TAGS)
    gp_facts = sec.concept_facts(cik, "GrossProfit")
    cost_tag, cost_facts = sec.first_available(cik, ["CostOfGoodsAndServicesSold", "CostOfRevenue"])
    rev = sec_singles(rfacts)
    gp = sec_singles(gp_facts) if gp_facts else {}
    cost = sec_singles(cost_facts) if not gp_facts and cost_facts else {}
    for end, (val, used) in rev.items():
        e = {"revenue": (val, [{"sec_accn": u["accn"], "form": u["form"], "start": u["start"], "end": u["end"],
                                "val": u["val"], "tag": rtag} for u in used])}
        if end in gp:
            g, gu = gp[end]
            e["gross_profit"] = (g, [{"sec_accn": u["accn"], "start": u["start"], "end": u["end"], "val": u["val"],
                                      "tag": "GrossProfit"} for u in gu])
        elif end in cost:
            c, cu = cost[end]
            e["gross_profit"] = (val - c, e["revenue"][1] + [{"sec_accn": u["accn"], "start": u["start"], "end": u["end"],
                                                               "val": u["val"], "tag": cost_tag} for u in cu])
        out[end] = e
    return out


def _near_end(ends, target: str, days=8):
    t = date.fromisoformat(target)
    for e in ends:
        if abs((date.fromisoformat(e) - t).days) <= days:
            return e
    return None


def fill_from_sec(quarters: list[dict], cik: str, fye: str) -> list[dict]:
    """iFinD 推不出的季度（缺上一累计期 / 缺毛利率）用 SEC 补齐；SEC 有而 iFinD 没有的季度也补进来。"""
    secq = sec_quarters(cik)
    by_end = {q["end"]: q for q in quarters}
    for q in quarters:
        e = _near_end(secq, q["end"])
        if not e:
            continue
        s = secq[e]
        q["sec_end"] = e
        # 用 SEC 披露的真实期末日（52/53 周财年），替换按会计年结日推出的近似月末（交叉核对发现 Marvell 窗口偏差）
        if q["end"] != e:
            q["end_mapped"] = q["end"]
            q["end"] = e
            q["start"] = quarter_start(date.fromisoformat(e)).isoformat()
        if "gross_profit" not in q and "gross_profit" in s and "revenue" in q:
            gpv, refs = s["gross_profit"]
            q["gross_profit"] = asdict(Value(gpv, "USD", formula="SEC XBRL 单季度毛利（iFinD 缺毛利率，以 SEC 补）", inputs=refs))
            q["gross_margin"] = asdict(Value(gpv / q["revenue"]["value"], "ratio", formula="SEC 毛利 / 单季度营收",
                                             inputs=refs + q["revenue"]["inputs"]))
    for e, s in secq.items():
        if _near_end(by_end, e) or e < "2024-12-01":
            continue
        rv, refs = s["revenue"]
        p_end = date.fromisoformat(e)
        q = {"end": e, "start": quarter_start(p_end).isoformat(),
             "fiscal": f"截至 {e}（SEC）", "label": None, "fye": fye, "sec_end": e,
             "revenue": asdict(Value(rv, "USD", formula="SEC XBRL 单季度营收（iFinD 缺该期累计值，以 SEC 补）", inputs=refs))}
        if "gross_profit" in s:
            gpv, grefs = s["gross_profit"]
            q["gross_profit"] = asdict(Value(gpv, "USD", formula="SEC XBRL 单季度毛利", inputs=grefs))
            q["gross_margin"] = asdict(Value(gpv / rv, "ratio", formula="SEC 毛利 / SEC 营收", inputs=grefs + refs))
        quarters.append(q)
    for q in quarters:
        if not q.get("sec_end") and q.get("fye", "1231") != "1231":
            q["end_approx"] = True  # 非自然年财年且无 SEC 对应：期末为按会计年结日推算的近似月末
    quarters.sort(key=lambda x: x["end"])
    return quarters


# ---- TTM 与汇率 --------------------------------------------------------------
def ttm(quarters: list[dict], as_of: str | None = None) -> dict | None:
    qs = [q for q in quarters if not as_of or q["end"] <= as_of][-4:]
    if len(qs) < 4:
        return None
    ends = [date.fromisoformat(q["end"]) for q in qs]
    if any(not 85 <= (b - a).days <= 95 for a, b in zip(ends, ends[1:])):
        return None
    out = {"window": [qs[0]["start"], qs[-1]["end"]], "quarters": [q["fiscal"] for q in qs]}
    for k in ("revenue", "gross_profit", "net_profit"):
        if all(k in q and q[k]["value"] is not None for q in qs):
            out[k] = asdict(Value(sum(q[k]["value"] for q in qs), qs[0][k]["unit"], qs[0]["start"], qs[-1]["end"],
                                  formula=f"Σ 最近 4 个单季度{k}", inputs=[{"quarter": q["fiscal"], "value": q[k]["value"]} for q in qs]))
    if "revenue" in out and "gross_profit" in out:
        out["gross_margin"] = asdict(Value(out["gross_profit"]["value"] / out["revenue"]["value"], "ratio",
                                           out["window"][0], out["window"][1], formula="Σ毛利 / Σ营收（TTM）",
                                           inputs=[{"gross_profit_ttm": out["gross_profit"]["value"], "revenue_ttm": out["revenue"]["value"]}]))
    return out


def fx_series(recs) -> dict[str, dict]:
    """{YYYY-MM: record}，CNY per USD 月均中间价。"""
    return {f"{r['label'][:4]}-{r['label'][4:6]}": r for r in recs
            if r["dataset"] == "edb" and r["field"] == FX_FIELD and r["value"] is not None}


def fx_window_avg(fx, start: str, end: str) -> dict | None:
    months, d = [], date.fromisoformat(start[:8] + "01")
    while d.isoformat()[:7] <= end[:7]:
        months.append(d.isoformat()[:7])
        d = _add_months(d, 1)
    if any(m not in fx for m in months):
        return None
    vals = [fx[m]["value"] for m in months]
    return {"rate": mean(vals), "months": months, "formula": f"{months[0]}…{months[-1]} 共 {len(months)} 个月月均中间价的算术平均",
            "inputs": [{"month": m, **_ref(fx[m])} for m in months]}


def to_usd(v: dict, fx_info: dict) -> dict:
    return asdict(Value(v["value"] / fx_info["rate"], "USD", v.get("period_start"), v.get("period_end"),
                        formula=f"CNY ÷ {fx_info['rate']:.4f}（{fx_info['formula']}）",
                        inputs=[{"cny": v["value"]}, {"fx": fx_info["rate"], "months": fx_info["months"]}]))


def valuation(recs, code, market) -> dict:
    ds = "cn_valuation" if market == "cn" else "gl_valuation"
    out = {}
    for r in recs:
        if r["dataset"] == ds and r["entity"] == code and r["value"] is not None and r["kind"] == "number":
            f_ = r["field"]
            unit = "x" if f_.startswith(("市盈率", "市净率", "市销率")) else ("%" if "率" in f_ else (r.get("currency") or ""))
            out[f_] = asdict(Value(r["value"], unit, label=r["label"], formula="iFinD 原始值", inputs=[_ref(r)]))
    return out


def build() -> dict:
    recs = [json.loads(l) for l in (STAGING / "records.jsonl").read_text(encoding="utf-8").splitlines()]
    fx = fx_series(recs)
    latest_fx_month = max(m for m in fx if m <= UNIVERSE["snapshot_cutoff"][:7])
    out = {"fx_latest": {"month": latest_fx_month, **_ref(fx[latest_fx_month])}, "companies": {}}
    for market, lst in (("cn", UNIVERSE["cn"]), ("global", UNIVERSE["global"])):
        for c in lst:
            qs = cn_quarters(recs, c["code"]) if market == "cn" else gl_quarters(recs, c["code"])
            if market == "global":
                qs = fill_from_sec(qs, c["cik"], fye_of(recs, c["code"]))
            t = ttm(qs)
            entry = {"code": c["code"], "name": c["name"], "market": market, "currency": "CNY" if market == "cn" else "USD",
                     "fye": "1231" if market == "cn" else fye_of(recs, c["code"]), "quarters": qs, "ttm": t,
                     "valuation": valuation(recs, c["code"], market)}
            if market == "cn" and t:
                fxw = fx_window_avg(fx, t["window"][0], t["window"][1])
                if fxw:
                    entry["ttm_usd"] = {k: to_usd(t[k], fxw) for k in ("revenue", "gross_profit", "net_profit") if k in t}
                    entry["ttm_usd"]["fx"] = fxw
                mc = entry["valuation"].get("总市值")
                if mc:
                    rate = fx[latest_fx_month]["value"]
                    entry["market_cap_usd"] = asdict(Value(mc["value"] / rate, "USD", label=mc["label"],
                                                           formula=f"总市值 CNY ÷ {rate}（{latest_fx_month} 月均中间价，近似）",
                                                           inputs=mc["inputs"] + [_ref(fx[latest_fx_month])]))
            elif market == "global":
                entry["ttm_usd"] = {k: t[k] for k in ("revenue", "gross_profit", "net_profit") if t and k in t}
                mc = next((v for k, v in entry["valuation"].items() if k.startswith("总市值")), None)
                if mc:
                    entry["market_cap_usd"] = mc
            # PS 用本币计算：市值与 TTM 营收同币种，避免“市值用月末汇率、营收用平均汇率”的口径混用（审查发现偏差 3.5%）
            mc_local = entry["valuation"].get("总市值") if market == "cn" else entry.get("market_cap_usd")
            rev_local = (t or {}).get("revenue")
            if mc_local and rev_local and rev_local["value"]:
                ps = mc_local["value"] / rev_local["value"]
                entry["ps_ttm"] = asdict(Value(ps, "x", formula=f"总市值 ÷ TTM 营收（同为 {entry['currency']}，不经汇率折算）",
                                               inputs=[{"market_cap": mc_local["value"], "currency": entry["currency"]},
                                                       {"revenue_ttm": rev_local["value"], "currency": entry["currency"]}]))
            out["companies"][c["code"]] = entry
    (STAGING / "metrics.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


if __name__ == "__main__":
    m = build()
    for code, e in m["companies"].items():
        t = e["ttm"]
        print(f"{e['name']:10s} {e['fye']} q={len(e['quarters'])} TTM={t['window'] if t else None} "
              f"rev_usd={(e.get('ttm_usd') or {}).get('revenue', {}).get('value')} "
              f"gm={t['gross_margin']['value'] if t and 'gross_margin' in t else None} ps={(e.get('ps_ttm') or {}).get('value')}")
