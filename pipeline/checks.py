"""M1 确定性校验（不经 LLM）：
1. A 股单季度 vs 累计：同一财年内单季度累加 = 累计值
2. 分产品 / 分地区收入合计 vs 营业收入
3. 海外：iFinD（财年标签映射成真实区间）vs SEC XBRL 同区间
4. 海外 Capex：iFinD 单季度 vs SEC（YTD 差分得单季度）
输出 data/staging/checks.json，每项都给出参与比较的 source（call_id / accn）。
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from pathlib import Path

import yaml

from pipeline.fiscal import map_label
from pipeline import sec

ROOT = Path(__file__).resolve().parent.parent
STAGING = ROOT / "data" / "staging"
UNIVERSE = yaml.safe_load((ROOT / "config" / "universe.yaml").read_text(encoding="utf-8"))
REL_TOL = 1e-4      # iFinD 以“亿”保留 4 位小数，相对误差远小于万分之一
SEG_TOL = 0.005     # 分部合计与营收允许 0.5%（四舍五入 / 内部抵销口径）


def load_records():
    return [json.loads(l) for l in (STAGING / "records.jsonl").read_text(encoding="utf-8").splitlines()]


def _idx(recs, dataset, field):
    out = {}
    for r in recs:
        if r["dataset"] == dataset and r["field"] == field and r["value"] is not None and r["kind"] == "number":
            out[(r["entity"], r["label"])] = r
    return out


def _close(a, b, rel=REL_TOL, abs_=1.0):
    return abs(a - b) <= max(abs_, rel * max(abs(a), abs(b)))


def check_cn_quarterly(recs):
    single = _idx(recs, "cn_quarterly", "单季度.营业收入")
    cum = _idx(recs, "cn_quarterly", "营业收入")
    results = []
    for (code, label), c in cum.items():
        year, mmdd = label[:4], label[4:]
        order = ["0331", "0630", "0930", "1231"]
        if mmdd not in order:
            continue
        need = [f"{year}{m}" for m in order[: order.index(mmdd) + 1]]
        parts = [single.get((code, l)) for l in need]
        if any(p is None for p in parts):
            results.append({"check": "cn_single_sum_eq_cumulative", "entity": code, "label": label,
                            "status": "skipped", "reason": "缺单季度值"})
            continue
        s = sum(p["value"] for p in parts)
        results.append({"check": "cn_single_sum_eq_cumulative", "entity": code, "label": label,
                        "status": "pass" if _close(s, c["value"]) else "fail",
                        "lhs": s, "rhs": c["value"], "diff": s - c["value"],
                        "sources": [p["source"] for p in parts] + [c["source"]]})
    return results


def check_segments(recs, dataset, kind):
    rev = _idx(recs, "cn_quarterly", "营业收入")
    items = defaultdict(list)
    for r in recs:
        if r["dataset"] == dataset and r["field"] == f"主营构成-项目收入(按{kind})" and r["value"] is not None:
            items[(r["entity"], r["label"])].append(r)
    results = []
    for (code, label), rs in items.items():
        total = rev.get((code, label))
        s = sum(r["value"] for r in rs)
        if not total:
            results.append({"check": f"segment_{kind}_sum_eq_revenue", "entity": code, "label": label,
                            "status": "skipped", "reason": "缺营业收入"})
            continue
        dev = (s - total["value"]) / total["value"]
        results.append({"check": f"segment_{kind}_sum_eq_revenue", "entity": code, "label": label,
                        "status": "pass" if abs(dev) <= SEG_TOL else "warn",
                        "lhs": s, "rhs": total["value"], "rel_dev": dev, "n_items": len(rs),
                        "note": "仅前 5 项，若 >5 项则合计偏小" if len(rs) == 5 and dev < -SEG_TOL else "",
                        "sources": [r["source"] for r in rs] + [total["source"]]})
    return results


def fye_map(recs) -> dict[str, str]:
    # “0630” 会被数值解析成 630.0，这里还原成 4 位 MMDD
    return {r["entity"]: f"{int(float(r['value'])):04d}" for r in recs
            if r["dataset"] == "gl_profile" and r["field"] == "会计年结日" and r["value"] is not None}


def _near(d1: str, d2: date, days=8):
    return abs((date.fromisoformat(d1) - d2).days) <= days


def check_global_vs_sec(recs):
    fyes = fye_map(recs)
    cum = _idx(recs, "gl_cumulative", "营业收入")
    results = []
    for g in UNIVERSE["global"]:
        code = g["code"]
        fye = fyes.get(code)
        if not fye:
            results.append({"check": "global_revenue_vs_sec", "entity": code, "status": "skipped", "reason": "缺会计年结日"})
            continue
        tag, facts = sec.first_available(g["cik"], sec.REVENUE_TAGS)
        for (ent, label), r in cum.items():
            if ent != code:
                continue
            p = map_label(label, fye if len(fye) == 4 else "1231")
            match = [f for f in facts if _near(f["end"], p.end) and _near(f["start"], p.start)]
            if not match:
                results.append({"check": "global_revenue_vs_sec", "entity": code, "label": label,
                                "real_period": [p.start.isoformat(), p.end.isoformat()],
                                "status": "skipped", "reason": f"SEC 无对应区间（tag={tag}）"})
                continue
            f = match[0]
            results.append({"check": "global_revenue_vs_sec", "entity": code, "label": label, "fiscal": p.name,
                            "real_period": [f["start"], f["end"]],
                            "status": "pass" if _close(r["value"], f["val"], abs_=1e5) else "fail",
                            "ifind": r["value"], "sec": f["val"], "diff": r["value"] - f["val"],
                            "sources": [r["source"], {"sec_accn": f["accn"], "form": f["form"], "tag": f["tag"], "url": f["url"]}]})
    return results


def sec_singles(facts):
    """SEC 现金流科目在 10-Q 中是 YTD；按相同 start 分组差分得单季度。键：真实期末日。"""
    by_start = defaultdict(list)
    for f in facts:
        by_start[f["start"]].append(f)
    out = {}
    for start, fs in by_start.items():
        fs.sort(key=lambda x: x["end"])
        prev = None
        for f in fs:
            days = (date.fromisoformat(f["end"]) - date.fromisoformat(start)).days
            if prev is None and 80 <= days <= 100:
                out[f["end"]] = (f["val"], [f])
            elif prev is not None:
                pdays = (date.fromisoformat(f["end"]) - date.fromisoformat(prev["end"])).days
                if 80 <= pdays <= 100:
                    out.setdefault(f["end"], (f["val"] - prev["val"], [prev, f]))
            prev = f
    return out


def check_capex_vs_sec(recs):
    capex = _idx(recs, "gl_capex", "单季度-资本性支出")
    fyes = fye_map(recs)
    results = []
    for g in UNIVERSE["global"]:
        if g["role"] != "demand":
            continue
        tag, facts = sec.first_available(g["cik"], sec.CAPEX_TAGS)
        singles = sec_singles(facts)
        fye = fyes.get(g["code"], "1231")
        for (ent, label), r in capex.items():
            if ent != g["code"]:
                continue
            p = map_label(label, fye)
            hit = [k for k in singles if _near(k, p.end)]
            if not hit:
                results.append({"check": "capex_vs_sec", "entity": ent, "label": label, "status": "skipped",
                                "reason": f"SEC 无对应单季度（tag={tag}）"})
                continue
            val, used = singles[hit[0]]
            ifind = abs(r["value"])  # iFinD 为现金流口径负数
            results.append({"check": "capex_vs_sec", "entity": ent, "label": label, "real_end": hit[0],
                            "status": "pass" if _close(ifind, val, rel=1e-3, abs_=1e6) else "fail",
                            "ifind_abs": ifind, "sec": val, "diff": ifind - val,
                            "sources": [r["source"]] + [{"sec_accn": u["accn"], "start": u["start"], "end": u["end"], "tag": tag} for u in used]})
    return results


def run():
    recs = load_records()
    results = (check_cn_quarterly(recs) + check_segments(recs, "cn_mainbiz_product", "产品")
               + check_segments(recs, "cn_mainbiz_region", "地区") + check_global_vs_sec(recs)
               + check_capex_vs_sec(recs))
    summary = defaultdict(lambda: defaultdict(int))
    for r in results:
        summary[r["check"]][r["status"]] += 1
    # 跨源不一致不是代码错误，而是数据冲突：保留双方，按规则选主口径，页面展示
    conflicts = [{**{k: v for k, v in r.items() if k != "status"},
                  "resolution": "以 SEC 原始披露为准（一手文件，可按 accn 回查）；iFinD 值保留展示"}
                 for r in results if r["status"] == "fail" and r["check"] in ("global_revenue_vs_sec", "capex_vs_sec")]
    out = {"summary": {k: dict(v) for k, v in summary.items()}, "results": results}
    (STAGING / "checks.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    (STAGING / "conflicts.json").write_text(json.dumps(conflicts, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


if __name__ == "__main__":
    print(json.dumps(run()["summary"], ensure_ascii=False, indent=1))
