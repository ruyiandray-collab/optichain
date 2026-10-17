"""M2 环节归属：公司 → 环节（主 / 次），全部由确定性规则从分产品收入计算。

优先级：分产品收入（产品词表命中 / 单公司映射）＞ 正式文本证据 ＞ 配置里的 segment_seed（只作“待验证假设”，不单独成立）。
- 项目名命中 product_keywords → 该环节，占比 = 项目收入 / 同期营业收入
- 命中 generic_items → 候选环节，需要正式文本证据佐证，占比只能给“上限”（该口径里光互联部分未拆分）
- 多环节命中时按中游 → 上游 → 下游的顺序取第一个（如“高速光组件与光模块”归光模块）
主环节：占比最高的确定环节；次环节：其余占比 ≥10% 的确定环节。
"""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SEG = yaml.safe_load((ROOT / "config" / "segments.yaml").read_text(encoding="utf-8"))
LAYER_ORDER = ["midstream", "upstream", "downstream"]
SECONDARY_MIN = 0.10


def segments():
    out = []
    for lid in LAYER_ORDER:
        layer = next(l for l in SEG["layers"] if l["id"] == lid)
        for s in layer["segments"]:
            out.append({**s, "layer": lid, "layer_name": layer["name"]})
    return out


SEGMENTS = segments()
SEG_BY_ID = {s["id"]: s for s in SEGMENTS}
OVERRIDES = {(o["entity"], o["item"]): o for o in SEG.get("item_overrides", [])}


def match_item(entity: str, item: str) -> dict:
    o = OVERRIDES.get((entity, item))
    if o:
        return {"segment": o["segment"], "kind": "override", "keyword": None, "reason": o["reason"]}
    ex = next((k for k in SEG.get("exclude_item_keywords", []) if k in item), None)
    if ex:
        return {"segment": None, "kind": "excluded", "keyword": ex, "reason": f"项目名含排除词“{ex}”（工业激光等，不属光互联）"}
    for s in SEGMENTS:
        for k in s["product_keywords"]:
            if k in item:
                return {"segment": s["id"], "kind": "specific", "keyword": k}
    for s in SEGMENTS:
        for k in s["generic_items"]:
            if k in item:
                return {"segment": s["id"], "kind": "generic", "keyword": k}
    return {"segment": None, "kind": "none", "keyword": None}


def attribute(entity: str, items_by_label: dict, revenue_lookup, text_support: dict[str, list[str]],
              seed: str | None) -> dict:
    """items_by_label: grade.product_items 的输出；revenue_lookup(label) -> record；
    text_support: {segment_id: [evidence_id, ...]}（正式来源中提及该环节产品的已校验引文）。"""
    if not items_by_label:
        label, items, rev = None, [], None
    else:
        label = max(items_by_label)
        items, rev = items_by_label[label], revenue_lookup(label)
    total = rev["value"] if rev else None
    rows = []
    for it in sorted(items, key=lambda x: x["rank"]):
        m = match_item(entity, str(it["name"]))
        rows.append({**it, **m, "share": (it["revenue"] / total) if total else None})

    shares: dict[str, float] = {}
    for r in rows:
        if r["kind"] in ("specific", "override") and r["share"] is not None:
            shares[r["segment"]] = shares.get(r["segment"], 0.0) + r["share"]
    memberships = []
    for seg_id, sh in sorted(shares.items(), key=lambda x: -x[1]):
        memberships.append({"segment": seg_id, "share": sh, "basis": "分产品收入",
                            "items": [r for r in rows if r["segment"] == seg_id and r["kind"] in ("specific", "override")]})
    for m in memberships:
        m["evidence_tier"] = 1
        m["evidence"] = [x["evidence_id"] for x in text_support.get(m["segment"], [])]
    # 泛口径项目 / 无分产品数据：按证据优先级取最高档（2 公告·IR ＞ 3 研报·资讯；4 官网未采集）
    def text_membership(seg, items, share_upper=None):
        sup = text_support.get(seg, [])
        tier = min((x["tier"] for x in sup), default=None)
        basis = {2: "公告 / IR 文本佐证", 3: "仅研报 / 资讯佐证（较弱）"}.get(tier, "无文本佐证（仅配置假设，待验证）")
        if items:
            basis = "泛口径分部 + " + basis
        return {"segment": seg, "share": None, "share_upper": share_upper, "basis": basis, "evidence_tier": tier,
                "evidence": [x["evidence_id"] for x in sorted(sup, key=lambda x: x["tier"])], "items": items,
                "verified": tier is not None and tier <= 2}
    for r in rows:
        if r["kind"] == "generic" and r["segment"] not in shares:
            memberships.append(text_membership(r["segment"], [r], r["share"]))
            shares.setdefault(r["segment"], -1)  # 同一环节只记一次
    if not memberships and seed:
        memberships.append(text_membership(seed, []))

    confirmed = [m for m in memberships if m.get("share") is not None]
    primary = confirmed[0]["segment"] if confirmed else next(
        (m["segment"] for m in memberships if m.get("verified")), None)
    for m in memberships:
        if m["segment"] == primary:
            m["role"] = "主"
        elif m.get("share") is not None and m["share"] >= SECONDARY_MIN:
            m["role"] = "次"
        elif m.get("share") is not None:
            m["role"] = "minor"
        else:
            m["role"] = "次" if m.get("verified") else "候选"
        exact = m.get("share") is not None  # 分产品收入确定匹配
        m["sub_segments"] = sub_segments(m["segment"], [str(i["name"]) for i in m["items"]],
                                         text_support.get(m["segment"], []), 1 if exact else m.get("evidence_tier"))
    return {"entity": entity, "label": label, "revenue": total,
            "revenue_source": rev["source"] if rev else None, "items": rows,
            "memberships": memberships, "primary": primary}


def sub_segments(seg_id: str, item_names: list[str], support: list[dict], item_tier: int | None = 1) -> list[dict]:
    """子环节：先看分产品项目名，再看已校验引文（第 2 / 3 档）。
    只有确定性匹配的分产品项目（产品词 / 单公司映射）才能给第 1 档；泛口径项目按所属归属的档位
    （审查发现：泛口径的“企业通讯市场板”“电源管理”被标成了第 1 档）。"""
    out = []
    for sub in SEG_BY_ID.get(seg_id, {}).get("sub_segments", []) or []:
        hit = next(((n, k) for n in item_names for k in sub["keywords"] if k in n), None)
        if hit and item_tier is not None:
            out.append({"id": sub["id"], "name": sub["name"], "tier": item_tier,
                        "basis": f"分产品项目“{hit[0]}”含“{hit[1]}”" + ("" if item_tier == 1 else "（泛口径项目，按归属档位）")})
            continue
        q = next(((x, k) for x in sorted(support, key=lambda x: x["tier"]) for k in sub["keywords"] if k in x["quote"]), None)
        if q:
            out.append({"id": sub["id"], "name": sub["name"], "tier": q[0]["tier"], "evidence_id": q[0]["evidence_id"],
                        "basis": f"引文含“{q[1]}”：{q[0]['quote'][:60]}"})
    return out
