"""快照完整性：发布文件里引用到的每个 evidence_id / call_id 都必须能在 evidence.json / calls.json 里找到。
（前端 Codex worker 发现过“引用了但没发布”的问题，这里兜底。）"""
import json
import re
from pathlib import Path

import pytest

DATA = Path(__file__).resolve().parent.parent / "web" / "public" / "data"


def load(name):
    p = DATA / name
    if not p.exists():
        pytest.skip(f"{name} 尚未生成")
    return json.loads(p.read_text(encoding="utf-8"))


def test_every_referenced_evidence_and_call_is_published():
    evidence, calls = load("evidence.json"), load("calls.json")
    blob = json.dumps([load(n) for n in ("companies.json", "comparables.json", "transmission.json", "cpo_universe.json",
                                         "checks.json")], ensure_ascii=False)  # checks.json 也要扫（审查发现漏扫）
    ev_refs = set(re.findall(r"ev_[0-9a-f]{10}", blob))
    missing_ev = sorted(ev_refs - set(evidence))
    assert not missing_ev, f"引用了但未发布的证据：{missing_ev[:10]}（共 {len(missing_ev)}）"
    call_refs = set(re.findall(r'"(?:call_id|concept_call_id|llm_call_id_never)":\s*"([0-9a-f]{12})"', blob))
    call_refs |= {e["call_id"] for e in evidence.values() if e.get("call_id")}
    missing_calls = sorted(call_refs - set(calls))
    assert not missing_calls, f"引用了但未发布的调用记录：{missing_calls[:10]}（共 {len(missing_calls)}）"


def test_record_refs_in_comparables_point_to_calls():
    calls = load("calls.json")
    for g in load("comparables.json"):
        for item in g["comparable"]:
            for r in item.get("refs", []):
                if r["kind"] == "record":
                    assert r["ref"] in calls, f"{g['id']} {item['dim']} 引用的调用记录 {r['ref']} 未发布"


def test_every_no_evidence_grade_records_search_scope_and_time():
    """要求：E（无证据）必须记录检索范围与时间——26 家全集和 225 只 CPO 成分股都要满足（审查发现过 193 条缺失）。"""
    bad = []
    for c in load("companies.json"):
        for concept, g in (c.get("grades") or {}).items():
            if g["grade"] == "E" and g["basis"] == "无证据":
                sc = g.get("search_scope") or []
                if not sc or not all(x.get("called_at") for x in sc):
                    bad.append((c["code"], concept))
    for x in load("cpo_universe.json"):
        if x["grade"] == "E" and x["basis"] == "无证据":
            sc = x.get("search_scope") or []
            if not sc or not all(s.get("called_at") for s in sc):
                bad.append((x["entity"], "cpo_universe"))
    assert not bad, f"E 级缺检索范围或时间：{bad[:10]}（共 {len(bad)}）"


def test_diff_previous_version_is_a_different_snapshot():
    """审查发现：previous_version 指向自己，diff 为空。上一版必须是另一个版本，且内容哈希不同。"""
    meta, diff = load("meta.json"), load("diff.json")
    prev = meta.get("previous_version")
    if not prev:
        pytest.skip("还没有更早的存档")
    assert prev != meta["version"]
    arch = DATA.parent.parent.parent / "data" / "snapshots" / prev / "meta.json"
    assert json.loads(arch.read_text(encoding="utf-8"))["content_hash"] != meta["content_hash"]
    assert diff["previous_version"] == prev
