"""采集层：通用抽取、无报告期数据剔除、批量缺失逐家重试、仍缺失时记入 missing。"""
from pipeline import ingest
from pipeline.ifind import IfindResult
from pipeline.ingest import Job, table_records, missing_entities


def res(answer, cid="c1"):
    return IfindResult(cid, "stock", "t", {}, "ok", inner={"code": 1, "data": {"answer": answer}})


DATED = "|证券代码|证券简称|日期|单季度.营业收入（单位：元）|\n|---|---|---|---|\n|A.SZ|甲|20260630|1.5亿|\n|A.SZ|甲|20260331|\t|"
UNDATED = "|证券代码|证券简称|单季度.营业收入（单位：元）|营业收入（单位：元）|\n|---|---|---|---|\n|A.SZ|甲|\t|417.7786亿|"


def test_table_records_values_and_source():
    recs = table_records(res(DATED), "cn_quarterly", "CNY")
    assert [(r.label, r.value) for r in recs] == [("20260630", 1.5e8), ("20260331", None)]
    assert recs[0].source == {"call_id": "c1", "column": "单季度.营业收入（单位：元）", "table": 0, "row": 0}
    assert recs[0].currency == "CNY" and recs[0].unit == "元"


def test_undated_answer_counts_as_missing():
    job = Job("cn_quarterly", "stock", "t", lambda e: "q", [{"code": "A.SZ", "name": "甲"}],
              expect="单季度.营业收入", need_label=True)
    recs = [r for r in table_records(res(UNDATED), "cn_quarterly", "CNY") if r.label]
    assert missing_entities(job, job.entities, recs) == job.entities


class FakeClient:
    def __init__(self, answers):
        self.answers = answers
        self.queries = []

    def call(self, server, tool, params, refresh=False):
        self.queries.append(params["query"])
        return res(self.answers[params["query"]], cid=str(len(self.queries)))


def test_batch_missing_retries_single(monkeypatch, tmp_path):
    ents = [{"code": "A.SZ", "name": "甲"}, {"code": "B.SZ", "name": "乙"}]
    job = Job("cn_mainbiz_product", "stock", "t", lambda e: "+".join(x["name"] for x in e), ents,
              "CNY", "20260630", expect="单季度.营业收入", need_label=True)
    b_row = "|证券代码|证券简称|日期|单季度.营业收入（单位：元）|\n|---|---|---|---|\n|B.SZ|乙|20260630|2亿|"
    fake = FakeClient({"甲+乙": DATED, "乙": b_row})
    monkeypatch.setattr(ingest, "jobs", lambda only=None: [job])
    monkeypatch.setattr(ingest, "STAGING", tmp_path)
    rep = ingest.run(client=fake)
    assert fake.queries == ["甲+乙", "乙"]
    assert rep["retried_single"] == [{"dataset": "cn_mainbiz_product", "entity": "B.SZ"}]
    assert rep["missing"] == []


def test_still_missing_is_reported(monkeypatch, tmp_path):
    ents = [{"code": "A.SZ", "name": "甲"}, {"code": "B.SZ", "name": "乙"}]
    job = Job("cn_mainbiz_product", "stock", "t", lambda e: "+".join(x["name"] for x in e), ents,
              "CNY", "20260630", expect="单季度.营业收入", need_label=True)
    fake = FakeClient({"甲+乙": DATED, "乙": UNDATED})
    monkeypatch.setattr(ingest, "jobs", lambda only=None: [job])
    monkeypatch.setattr(ingest, "STAGING", tmp_path)
    rep = ingest.run(client=fake)
    assert [m["entity"] for m in rep["missing"]] == ["B.SZ"]
