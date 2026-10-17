import copy

import pytest

from pipeline import manual_review


def _link(grade="D", review_ids=("ev_1",)):
    return {"entity": "000001.SZ", "concept": "cpo", "grade": grade, "basis": "非正式来源·研发/送样",
            "needs_review": True, "review_items": [{"evidence_id": i} for i in review_ids],
            "trace": [{"evidence_id": "ev_1", "grade": "D", "quote": "进入产品开发阶段", "formal": True}]}


def _reviews(**kw):
    r = {"entity": "000001.SZ", "concept": "cpo", "rule_grade": "D", "final_grade": "D",
         "reviewed_evidence": ["ev_1"], "reason": "核对原文", "reviewer": "Ray", "reviewed_at": "2026-10-18"}
    r.update(kw)
    return {("000001.SZ", "cpo"): r}


def test_keep_grade_clears_review_flag():
    l = manual_review.apply(_link(), _reviews())
    assert l["grade"] == "D" and l["rule_grade"] == "D" and l["needs_review"] is False
    assert l["manual_review"]["status"] == "applied" and "已人工复核" in l["basis"]


def test_change_grade_keeps_rule_grade_and_points_to_evidence():
    l = manual_review.apply(_link(), _reviews(final_grade="C", evidence_for_final="ev_1"))
    assert (l["grade"], l["rule_grade"], l["best_evidence"]) == ("C", "D", "ev_1")
    assert l["best"]["grade"] == "D" and l["manual_review"]["evidence"] == "ev_1"  # 证据行仍显示规则等级


def test_change_grade_requires_evidence_in_trace():
    with pytest.raises(ValueError):
        manual_review.apply(_link(), _reviews(final_grade="C", evidence_for_final="ev_missing"))


def test_stale_when_rule_grade_changed():
    l = manual_review.apply(_link(grade="C"), _reviews(final_grade="C", evidence_for_final="ev_1"))
    assert l["needs_review"] is True and l["grade"] == "C" and "rule_grade" not in l
    assert l["manual_review"]["status"] == "stale" and "规则等级" in l["manual_review"]["stale_reason"]


def test_stale_when_new_review_evidence_appears():
    l = manual_review.apply(_link(review_ids=("ev_1", "ev_2")), _reviews())
    assert l["needs_review"] is True and "ev_2" in l["manual_review"]["stale_reason"]


def test_no_entry_is_untouched():
    l = _link()
    assert manual_review.apply(copy.deepcopy(l), {}) == l


def test_config_loads_without_duplicates():
    reviews = manual_review.load()
    assert len(reviews) == 17
    assert all(r["reviewer"] == "Ray" and r["reviewed_at"] == "2026-10-18" for r in reviews.values())
    changed = {k: r for k, r in reviews.items() if r["final_grade"] != r["rule_grade"]}
    assert changed == {("002273.SZ", "optical_interconnect"): reviews[("002273.SZ", "optical_interconnect")]}
