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
    # 泛口径项目：有正式文本佐证才归属，占比给上限
    for r in rows:
        if r["kind"] == "generic" and r["segment"] not in shares:
            sup = text_support.get(r["segment"], [])
            memberships.append({"segment": r["segment"], "share": None, "share_upper": r["share"],
                                "basis": "泛口径分部 + 文本佐证" if sup else "泛口径分部（无文本佐证，待验证）",
                                "evidence": sup, "items": [r], "verified": bool(sup)})
            shares.setdefault(r["segment"], -1)  # 同一环节只记一次
    if not memberships and seed:
        sup = text_support.get(seed, [])
        memberships.append({"segment": seed, "share": None, "basis": "文本证据" if sup else "仅配置假设（无证据）",
                            "evidence": sup, "items": [], "verified": bool(sup)})

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
            m["role"] = "候选" if not m.get("verified") else "次"
    return {"entity": entity, "label": label, "revenue": total,
            "revenue_source": rev["source"] if rev else None, "items": rows,
            "memberships": memberships, "primary": primary}
