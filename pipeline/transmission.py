"""M3 传导图：外部变量 → 环节 → 公司 → 财务指标。

- 变量序列：SEC XBRL（Capex、NVIDIA 营收）与 iFinD EDB（海关出口、汇率），确定性汇总
- 边的支持证据：LLM 从证据池里摘引文（逐字校验，与 M2 同一套防幻觉机制）
- 公司暴露度：境外收入占比（分地区收入）、汇率静态敏感性（代码计算，非预测）
- 置信度：规则计算（confidence_of）
- 数据对照：变量与公司指标的同比增速相关（样本小，只作历史对照，不做推断）
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import date
from pathlib import Path
from statistics import mean

import yaml

from pipeline import sec
from pipeline.checks import sec_singles
from pipeline.extract import api_key, _call_llm, verify_quote, CACHE as LLM_CACHE
from pipeline.metrics import Value

ROOT = Path(__file__).resolve().parent.parent
STAGING = ROOT / "data" / "staging"
CFG = yaml.safe_load((ROOT / "config" / "transmission.yaml").read_text(encoding="utf-8"))
UNIVERSE = yaml.safe_load((ROOT / "config" / "universe.yaml").read_text(encoding="utf-8"))
CLOUD = [g for g in UNIVERSE["global"] if g["role"] == "demand"]
EXPORT_FIELD = "光通信设备的激光收发模块(85177950):出口金额:当月值"
FX_FIELD = "平均汇率:中间价:美元兑人民币"
COPPER_FIELD = "商品价格:铜:当月值"
PROMPT_VERSION = "edge-support-v1"

SYSTEM = """你是卖方研究所的证据摘录员。给你一条“传导机制”假设和若干编号段落，请从段落中原样摘录能【支持】或【反驳】该机制的句子。
要求：quote 必须逐字复制自对应段落（10–150 字，连续文本）；不要输出段落中没有的数字或推测；与机制无关的段落不要选。
输出 JSON：{"quotes":[{"pid":"段落编号","stance":"support|contradict","quote":"..."}]}，最多 4 条；没有就输出 {"quotes":[]}。"""


def _jsonl(name):
    p = STAGING / name
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


def _qkey(end: str) -> str:
    d = date.fromisoformat(end)
    return f"{d.year}Q{(d.month - 1) // 3 + 1}"


# ---- 变量序列 -----------------------------------------------------------------
def capex_series() -> dict:
    per = {}
    for g in CLOUD:
        tag, facts = sec.first_available(g["cik"], sec.CAPEX_TAGS)
        for end, (val, used) in sec_singles(facts).items():
            per.setdefault(_qkey(end), {})[g["name"]] = (val, end, [u["accn"] for u in used])
    points = []
    for q in sorted(per):
        if len(per[q]) == len(CLOUD) and q >= "2023Q1":
            total = sum(v[0] for v in per[q].values())
            points.append({"date": q, "value": total, "ref": asdict(Value(total, "USD", formula="四家单季度资本性支出之和（SEC XBRL，YTD 差分）",
                                                                         inputs=[{"company": k, "value": v[0], "end": v[1], "sec_accn": v[2]}
                                                                                 for k, v in per[q].items()]))})
    return {"name": "北美四家云厂商单季度 Capex 合计", "unit": "USD", "source": "SEC XBRL PaymentsToAcquirePropertyPlantAndEquipment",
            "freq": "Q", "points": points}


def nvidia_series() -> dict:
    nv = next(g for g in UNIVERSE["global"] if g["code"] == "NVDA.O")
    tag, facts = sec.first_available(nv["cik"], sec.REVENUE_TAGS)
    pts = [{"date": end, "value": val, "ref": asdict(Value(val, "USD", formula=f"SEC XBRL {tag} 单季度",
                                                           inputs=[{"sec_accn": u["accn"], "start": u["start"], "end": u["end"]} for u in used]))}
           for end, (val, used) in sorted(sec_singles(facts).items()) if end >= "2023-01-01"]
    return {"name": "NVIDIA 单季度营收", "unit": "USD", "source": f"SEC XBRL {tag}", "freq": "Q", "points": pts}


def edb_series(recs, field_, name, unit, start="202301") -> dict:
    rs = sorted((r for r in recs if r["dataset"] == "edb" and r["field"] == field_ and r["value"] is not None
                 and r["label"][:6] >= start), key=lambda r: r["label"])
    return {"name": name, "unit": unit, "source": f"iFinD EDB（{rs[0]['source'].get('data_source') if rs else ''}）", "freq": "M",
            "points": [{"date": f"{r['label'][:4]}-{r['label'][4:6]}", "value": r["value"],
                        "ref": {"call_id": r["source"]["call_id"], "column": r["source"]["column"], "label": r["label"]}} for r in rs]}


def company_series(metrics, code, key) -> dict:
    m = metrics["companies"][code]
    pts = [{"date": _qkey(q["end"]), "value": q[key]["value"], "ref": {"path": f"companies[{code}].metrics.quarters[{q['fiscal']}].{key}"}}
           for q in m["quarters"] if key in q and q[key]["value"] is not None]
    return {"name": f"{m['name']} 单季度{'营收' if key == 'revenue' else '毛利率'}", "unit": m["currency"] if key == "revenue" else "ratio",
            "source": "iFinD 单季度数据（毛利率 = 1 − 成本/营收）", "freq": "Q", "points": pts}


# ---- 公司暴露度 ---------------------------------------------------------------
OVERSEAS = ("境外", "国外", "海外", "外销")


def exposures(recs, metrics, codes) -> list[dict]:
    out = []
    for code in codes:
        names = {(r["label"], r["rank"]): r for r in recs if r["dataset"] == "cn_mainbiz_region" and r["entity"] == code
                 and r["field"] == "主营构成-项目名称(按地区)" and r["value"]}
        revs = {(r["label"], r["rank"]): r for r in recs if r["dataset"] == "cn_mainbiz_region" and r["entity"] == code
                and r["field"] == "主营构成-项目收入(按地区)" and r["value"] is not None}
        labels = sorted({k[0] for k in revs}, reverse=True)
        for label in labels:
            ov = [(names[k], revs[k]) for k in revs if k[0] == label and k in names and any(w in str(names[k]["value"]) for w in OVERSEAS)]
            tot = next((r for r in recs if r["dataset"] == "cn_quarterly" and r["entity"] == code and r["field"] == "营业收入"
                        and r["label"] == label), None)
            if not ov or not tot:
                continue
            amt = sum(r["value"] for _, r in ov)
            share = amt / tot["value"]
            m = metrics["companies"][code]
            out.append({"code": code, "name": m["name"], "metric": "境外收入占比",
                        "value": asdict(Value(share, "ratio", label=label, formula="境外地区收入 / 同期营业收入",
                                              inputs=[{"item": n["value"], "revenue": r["value"], **r["source"]} for n, r in ov]
                                              + [{"revenue_total": tot["value"], **tot["source"]}])),
                        "fx_sensitivity": fx_sensitivity(m, share, label)})
            break
    return out


def fx_sensitivity(m, share, label) -> dict | None:
    t = m.get("ttm") or {}
    if "revenue" not in t:
        return None
    v = -0.01 * share * t["revenue"]["value"]
    return asdict(Value(v, "CNY", t["window"][0], t["window"][1],
                        formula=f"静态敏感性（非预测）：人民币对美元升值 1% × 境外收入占比（{label}）× TTM 营收；假设境外收入全部以美元计价、其他条件不变",
                        inputs=[{"share_overseas": share}, {"revenue_ttm": t["revenue"]["value"]}]))


# ---- 数据对照 -----------------------------------------------------------------
def yoy(points) -> dict[str, float]:
    by = {p["date"]: p["value"] for p in points}
    out = {}
    for q, v in by.items():
        prev = f"{int(q[:4]) - 1}{q[4:]}"
        if prev in by and by[prev]:
            out[q] = v / by[prev] - 1
    return out


def pearson(xs, ys):
    if len(xs) < 4:
        return None
    mx, my = mean(xs), mean(ys)
    sx = sum((x - mx) ** 2 for x in xs) ** 0.5
    sy = sum((y - my) ** 2 for y in ys) ** 0.5
    return None if not sx or not sy else sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


def _shift(q: str, k: int) -> str:
    y, n = int(q[:4]), int(q[5])
    n += k
    y += (n - 1) // 4
    n = (n - 1) % 4 + 1
    return f"{y}Q{n}"


def lead_lag(x_points, y_points, lags=(0, 1, 2)) -> dict:
    """x 领先 y k 个季度时，两者同比增速的相关系数。只作历史对照。"""
    xy, yy = yoy(x_points), yoy(y_points)
    res = []
    for k in lags:
        pairs = [(xy[q], yy[_shift(q, k)]) for q in xy if _shift(q, k) in yy]
        r = pearson([a for a, _ in pairs], [b for _, b in pairs])
        res.append({"lag_quarters": k, "n": len(pairs), "r": r,
                    "same_direction_share": (sum(1 for a, b in pairs if (a > 0) == (b > 0)) / len(pairs)) if pairs else None})
    return {"method": "x 领先 k 个季度，与 y 的同比增速做 Pearson 相关；样本少，只作历史对照，不构成因果推断或预测",
            "results": res}


# ---- 边的支持证据（LLM 摘录 + 逐字校验） ------------------------------------------
def edge_support(edge, pool, key) -> tuple[list[dict], int]:
    kws = edge["support_keywords"]
    cands = sorted(pool, key=lambda e: -sum(e["text"].count(k) for k in kws))[:10]
    cands = [e for e in cands if any(k in e["text"] for k in kws)]
    if not cands:
        return [], 0
    paras = "\n\n".join(f"[{i}] （{e['source_name'][:40]}，{e.get('date')}）{e['text']}" for i, e in enumerate(cands))
    user = f"机制：{edge['mechanism']}\n\n段落：\n{paras}"
    cid = hashlib.sha1(f"{PROMPT_VERSION}|{SYSTEM}|{user}".encode()).hexdigest()[:12]
    f = LLM_CACHE / f"edge_{cid}.json"
    if f.exists():
        resp = json.loads(f.read_text(encoding="utf-8"))
    else:
        resp = _call_llm([{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}], key)
        LLM_CACHE.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(resp, ensure_ascii=False), encoding="utf-8")
    try:
        qs = json.loads(resp["choices"][0]["message"].get("content") or "{}").get("quotes", [])
    except json.JSONDecodeError:
        qs = []
    ok, rejected = [], 0
    for q in qs:
        try:
            ev = cands[int(str(q.get("pid", "")).strip("[]"))]
        except (ValueError, IndexError):
            rejected += 1
            continue
        must = edge.get("must_keywords") or []
        relevant = not must or any(k in q.get("quote", "") for k in must)  # 引文必须提到该变量本身，泛泛提“光模块”不算
        if q.get("stance") in ("support", "contradict") and verify_quote(q.get("quote", ""), ev["text"]) and relevant:
            ok.append({"evidence_id": ev["evidence_id"], "stance": q["stance"], "quote": q["quote"],
                       "source_kind": ev["source_kind"], "source_name": ev["source_name"], "date": ev.get("date"),
                       "llm_call_id": cid})
        else:
            rejected += 1
    return ok, rejected


def confidence_of(support: list[dict], data_check: dict | None) -> tuple[str, str]:
    sup = [s for s in support if s["stance"] == "support"]
    contra = [s for s in support if s["stance"] == "contradict"]
    sources = {s["source_name"] for s in sup}
    formal = any(s["source_kind"] in ("notice", "sec_filing") for s in sup)
    data_ok = None
    if data_check and data_check.get("results"):
        best = max((r for r in data_check["results"] if r["r"] is not None), key=lambda r: abs(r["r"]), default=None)
        data_ok = best is not None and best["n"] >= 4 and best["r"] > 0.3
    if len(sup) >= 2 and len(sources) >= 2 and formal and data_ok and not contra:
        return "高", "≥2 个独立来源支持（含正式文件）+ 历史数据同向，且无反驳证据"
    if sup and not (contra and len(contra) >= len(sup)):
        return "中", f"有 {len(sup)} 条已校验支持引文" + ("，含正式文件" if formal else "，均为媒体") + \
               ("" if data_ok else "；历史数据未验证或样本不足") + (f"；另有 {len(contra)} 条反驳" if contra else "")
    return "低", "没有已校验的支持引文（仅为机制假设）" if not sup else "支持与反驳证据相当"


def _q_row(metrics, code, quarter):
    m = metrics["companies"][code]
    for i, q in enumerate(m["quarters"]):
        if _qkey(q["end"]) == quarter:
            return i, q
    return None, None


def window_rows(metrics, codes, t):
    """事件季度 t：t−1、t、t+1、t+2 各公司营收 / 毛利率实际值，以及相对 t−1 的变化。"""
    quarters = [_shift(t, k) for k in (-1, 0, 1, 2)]
    comps = []
    for code in codes:
        rows = []
        for qk in quarters:
            i, q = _q_row(metrics, code, qk)
            if q is None:
                rows.append({"quarter": qk, "available": False})
                continue
            rows.append({"quarter": qk, "available": True, "end": q["end"],
                         "revenue": q["revenue"]["value"], "revenue_ref": f"companies[{code}].metrics.quarters[{i}].revenue",
                         "gross_margin": (q.get("gross_margin") or {}).get("value"),
                         "gross_margin_ref": f"companies[{code}].metrics.quarters[{i}].gross_margin" if q.get("gross_margin") else None})
        base = rows[0] if rows[0]["available"] else None
        for r in rows[1:]:
            if base and r["available"] and base["revenue"]:
                r["revenue_vs_t_minus_1"] = r["revenue"] / base["revenue"] - 1
                if r.get("gross_margin") is not None and base.get("gross_margin") is not None:
                    r["gross_margin_pp_vs_t_minus_1"] = (r["gross_margin"] - base["gross_margin"]) * 100
        comps.append({"code": code, "name": metrics["companies"][code]["name"], "rows": rows})
    return quarters, comps


def event_windows(series, metrics, var_ev) -> list[dict]:
    """“上一轮 Capex 加速之后，收入 / 毛利率实际怎么变”：事件季度 t 的前一季到之后两季，逐公司列出实际值。
    只做历史对照（代码计算），不做预测；政策事件晚于最新财报的，如实标注暂无事后财务数据。"""
    cfg = CFG["event_windows"]
    pts = series["capex"]["points"]
    out = []
    for prev, cur in zip(pts, pts[1:]):
        qoq = cur["value"] / prev["value"] - 1
        if qoq < cfg["capex_accel_qoq"]:
            continue
        t = cur["date"]
        quarters, comps = window_rows(metrics, cfg["companies"], t)
        out.append({"id": f"capex_accel_{t}", "variable": "capex", "kind": "data_rule",
                    "title": f"{t} 北美云厂商 Capex 环比 +{qoq * 100:.1f}%",
                    "rule": f"四家 Capex 合计环比增速 ≥ {cfg['capex_accel_qoq'] * 100:.0f}% 视为加速（SEC XBRL）",
                    "trigger": {"quarter": t, "qoq": qoq, "value": cur["value"], "prev_value": prev["value"], "ref": cur["ref"]},
                    "window": quarters, "companies": comps,
                    "note": "t−1 为基准；营收变化为相对 t−1 的比例，毛利率变化单位为百分点。历史对照，不构成因果推断或预测。"})
    latest = cfg["latest_financial_period"]
    exp = {p["date"]: p for p in series["export_control"]["points"]}
    for var in cfg["policy_variables"]:
        evs = sorted((e for e in var_ev if e["entity"] == f"VAR:{var}" and e.get("date")), key=lambda e: e["date"])
        by_date: dict[str, list[dict]] = {}
        for e in evs:  # 同一天的多篇报道合并成一个事件，证据全部保留
            by_date.setdefault(e["date"][:10], []).append(e)
        for d, group in by_date.items():
            e = group[0]
            before = [exp[m] for m in sorted(exp) if m < d[:7]][-3:]
            after = [exp[m] for m in sorted(exp) if m >= d[:7]][:3]
            out.append({"id": f"{var}_{e['evidence_id']}", "variable": var, "kind": "news_event",
                        "title": e["source_name"][:60], "date": d, "evidence_id": e["evidence_id"],
                        "evidence_ids": [x["evidence_id"] for x in group], "reports": len(group),
                        "financial_available": d <= latest,
                        **(dict(zip(("window", "companies"), window_rows(metrics, cfg["companies"], _qkey(d)))) if d <= latest else {}),
                        "financial_note": None if d <= latest else f"事件晚于最新财报期（{latest}），暂无事后财务数据",
                        "export_before": [{"month": p["date"], "value": p["value"]} for p in before],
                        "export_after": [{"month": p["date"], "value": p["value"]} for p in after],
                        "export_note": f"事件前 {len(before)} 个月 / 后 {len(after)} 个月的光模块出口额（海关 HS 85177950）；样本少，只作对照"})
    return out


def build() -> dict:
    recs = _jsonl("records.jsonl")
    metrics = json.loads((STAGING / "metrics.json").read_text(encoding="utf-8"))
    var_ev = _jsonl("evidence_vars.jsonl")
    co_ev = _jsonl("evidence_raw.jsonl")
    members = {m["entity"]: m for m in json.loads((STAGING / "memberships.json").read_text(encoding="utf-8"))}
    key = api_key()

    series = {"capex": capex_series(), "nvidia": nvidia_series(),
              "export_control": edb_series(recs, EXPORT_FIELD, "光模块月度出口额（HS 85177950）", "USD"),
              "fx": edb_series(recs, FX_FIELD, "美元兑人民币月均中间价", "CNY per USD", "202401"),
              "copper": edb_series(recs, COPPER_FIELD, "铜价（世界银行，月度）", "USD/t")}
    named = {"capex_sum": series["capex"], "export_8517": series["export_control"], "fx": series["fx"],
             "copper": series["copper"]}

    variables = []
    for v in CFG["variables"]:
        variables.append({**v, "series": series.get(v["id"]),
                          "evidence": [e["evidence_id"] for e in var_ev if e["entity"] == f"VAR:{v['id']}"]})

    seg_cos = CFG["segment_companies"]
    nodes = [{"id": v["id"], "kind": "variable", "label": v["name"]} for v in CFG["variables"]]
    from pipeline.attribution import SEG_BY_ID
    for seg in seg_cos:
        nodes.append({"id": f"seg:{seg}", "kind": "segment", "label": SEG_BY_ID[seg]["name"]})
        for code in seg_cos[seg]:
            nodes.append({"id": f"co:{code}", "kind": "company", "label": metrics["companies"][code]["name"], "ref": code})
    for mid, lab in (("m:revenue", "营业收入"), ("m:gross_margin", "毛利率"), ("m:overseas", "境外收入")):
        nodes.append({"id": mid, "kind": "metric", "label": lab})

    edges, rejected_total = [], 0
    for e in CFG["edges"]:
        seg = e["to"].split(":")[1]
        pool = [x for x in var_ev if x["entity"] == f"VAR:{e['from']}"] + \
               [x for x in co_ev if x["entity"] in seg_cos.get(seg, [])]
        support, rej = edge_support(e, pool, key) if key else ([], 0)
        rejected_total += rej
        dc = None
        if e["from"] == "capex":
            dc = lead_lag(series["capex"]["points"], company_series(metrics, "300308.SZ", "revenue")["points"])
            dc["y"] = "中际旭创单季营收"
        elif e["from"] == "nvidia":
            nv_pts = [{"date": _qkey(p["date"]), "value": p["value"]} for p in series["nvidia"]["points"]]
            dc = lead_lag(nv_pts, company_series(metrics, "300308.SZ", "revenue")["points"])
            dc["y"] = "中际旭创单季营收（NVIDIA 财季按期末月份归入自然季度）"
        conf, rule = confidence_of(support, dc)
        edges.append({**{k: v for k, v in e.items() if k not in ("support_keywords", "must_keywords")}, "support": support,
                      "data_check": dc, "confidence": conf, "confidence_rule": rule, "mechanism_author": "AI 起草、人工审核"})
        # 环节 → 公司 → 指标
        for code in seg_cos.get(seg, []):
            # 环节 → 公司这一段按真实归属状态标注（审查发现：铜价边指向的沪电股份归属只是“候选”）
            ms = next((x for x in members.get(code, {}).get("memberships", []) if x["segment"] == seg), None)
            verified = bool(ms) and (ms.get("share") is not None or ms.get("verified"))
            share = f"，收入占比 {ms['share'] * 100:.1f}%" if ms and ms.get("share") is not None else ""
            edges.append({"id": f"{e['id']}__{code}", "from": e["to"], "to": f"co:{code}", "sign": "+", "lag": "",
                          "mechanism": f"环节归属：{metrics['companies'][code]['name']} → {SEG_BY_ID[seg]['name']}"
                                       f"（{ms['role'] if ms else '无归属'}，依据：{ms['basis'] if ms else '无'}{share}）",
                          "support": [], "confidence": "确定" if verified else "低",
                          "confidence_rule": "归属已由分产品收入或正式文本验证" if verified else "归属未验证（仅候选），这段传导只是假设",
                          "membership_verified": verified, "derived": True})
    for seg, codes in seg_cos.items():
        for code in codes:
            for mid in ("m:revenue", "m:gross_margin"):
                edges.append({"id": f"{code}->{mid}", "from": f"co:{code}", "to": mid, "sign": "", "lag": "",
                              "mechanism": "公司财务指标（季度序列见 history / 公司页）", "support": [], "confidence": "—",
                              "confidence_rule": "指标节点", "derived": True})

    history = []
    for h in CFG["history"]:
        def s(key):
            if key in named:
                return named[key]
            code, k = key.split(":")
            return company_series(metrics, code, k)
        history.append({"id": h["id"], "title": h["title"], "x": s(h["x"]), "y": s(h["y"]),
                        "note": "历史对照，不构成因果推断或预测"})

    codes = sorted({c for cs in seg_cos.values() for c in cs})
    out = {"variables": variables, "nodes": nodes, "edges": edges, "exposures": exposures(recs, metrics, codes),
           "history": history, "event_windows": event_windows(series, metrics, var_ev),
           "stats": {"support_rejected": rejected_total}}
    (STAGING / "transmission.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


if __name__ == "__main__":
    t = build()
    for e in t["edges"]:
        if not e.get("derived"):
            dc = e.get("data_check") or {}
            print(e["id"], e["confidence"], len(e["support"]), "|", e["confidence_rule"][:60],
                  [(r["lag_quarters"], r["n"], None if r["r"] is None else round(r["r"], 2)) for r in dc.get("results", [])])
    for x in t["exposures"]:
        print(x["name"], round(x["value"]["value"], 4), x["value"]["label"], None if not x["fx_sensitivity"] else round(x["fx_sensitivity"]["value"] / 1e8, 2), "亿元")
    print("capex points", [(p["date"], round(p["value"] / 1e9, 1)) for p in t["variables"][0]["series"]["points"]])
    print(t["stats"])
