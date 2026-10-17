"""人工复核：把 config/manual_review.yaml 里落定的结论应用到分级结果上。

规则函数给出等级；LLM 建议与规则不一致（或规则补抽）时标“待人工复核”。人核对原文后在配置里写下结论，
这里只在“复核时看到的东西没变”时才应用：
  - 当前规则等级必须等于复核时的 rule_grade；
  - 当前触发复核的证据必须都在 reviewed_evidence 里（出现新的待复核证据，说明情况变了）。
任一条件不满足，复核作废：仍标待复核，并注明“复核已过期”。规则等级始终保留在 rule_grade 里。
"""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "manual_review.yaml"


def load(path: Path = CONFIG) -> dict:
    if not path.exists():
        return {}
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out = {}
    for r in cfg.get("reviews", []):
        key = (str(r["entity"]), r["concept"])
        if key in out:
            raise ValueError(f"manual_review.yaml 重复条目：{key}")
        out[key] = {**r, "reviewer": r.get("reviewer", cfg.get("reviewer")),
                    "reviewed_at": str(r.get("reviewed_at", cfg.get("reviewed_at")))}
    return out


def apply(link: dict, reviews: dict) -> dict:
    """就地修改并返回 link。没有对应复核条目时原样返回。"""
    r = reviews.get((link["entity"], link["concept"]))
    if not r:
        return link
    current = {i["evidence_id"] for i in link.get("review_items", [])}
    reviewed = set(r.get("reviewed_evidence", []))
    record = {"reviewer": r["reviewer"], "reviewed_at": r["reviewed_at"], "rule_grade": r["rule_grade"],
              "final_grade": r["final_grade"], "reason": r["reason"]}
    if link["grade"] != r["rule_grade"] or not current <= reviewed:
        why = (f"规则等级已从 {r['rule_grade']} 变为 {link['grade']}" if link["grade"] != r["rule_grade"]
               else f"出现复核时没有的待复核证据：{', '.join(sorted(current - reviewed))}")
        link["manual_review"] = {**record, "status": "stale", "stale_reason": why}
        link["needs_review"] = True
        return link
    link["rule_grade"] = link["grade"]
    link["manual_review"] = {**record, "status": "applied"}
    link["needs_review"] = False
    if r["final_grade"] != link["grade"]:
        ev_id = r.get("evidence_for_final")
        row = next((t for t in link["trace"] if t.get("evidence_id") == ev_id and t.get("grade")), None)
        if row is None:
            raise ValueError(f"{link['entity']} {link['concept']}：改等级必须给出在 trace 里的 evidence_for_final")
        link["grade"] = r["final_grade"]
        link["best_evidence"] = ev_id
        link["best"] = row  # 证据行保留规则等级；复核等级只体现在 link["grade"] 与 manual_review 上
        link["manual_review"]["evidence"] = ev_id
        link["basis"] = f"人工复核改定（规则等级 {link['rule_grade']}）"
    else:
        link["basis"] = f"{link['basis']}；已人工复核，维持"
    return link
