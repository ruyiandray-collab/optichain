"""M1 采集：按固定问句模板拉取全集数据，规范化为 Metric / Attribute 记录。

每条记录都带 source = {call_id, column, row}，可以回放到 api_calls.jsonl 和缓存里的原始返回。
LLM 不参与本模块。

用法：python -m pipeline.ingest [--only cn_quarterly,...] [--refresh]
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Callable

import yaml

from pipeline.ifind import IfindClient, IfindResult
from pipeline.tables import parse_tables, parse_number, is_null, Table

ROOT = Path(__file__).resolve().parent.parent
STAGING = ROOT / "data" / "staging"
UNIVERSE = yaml.safe_load((ROOT / "config" / "universe.yaml").read_text(encoding="utf-8"))
KEY_COLS = {"证券代码", "证券简称", "日期"}

# 海外公司标签要覆盖到未来财年（财年标签领先自然年，见 fiscal.py）
GLOBAL_LABELS = ["20250331", "20250630", "20250930", "20251231", "20260331",
                 "20260630", "20260930", "20261231", "20270331", "20270630"]


@dataclass
class Record:
    entity: str            # 证券代码
    field: str             # iFinD 列名（去掉单位/排名后缀）
    value: float | str | None
    kind: str              # number | text
    label: str | None      # 报告期 / 交易日标签（iFinD 原样）；None = 返回里没给日期
    rank: int | None = None
    unit: str | None = None
    currency: str | None = None
    dataset: str = ""
    source: dict = field(default_factory=dict)


def _chunks(xs, n):
    return [xs[i:i + n] for i in range(0, len(xs), n)]


def _answer(res: IfindResult) -> str:
    d = res.data
    if isinstance(d, dict):
        return d.get("answer") or ""
    return d if isinstance(d, str) else ""


def table_records(res: IfindResult, dataset: str, currency: str | None,
                  default_label: str | None = None) -> list[Record]:
    """通用抽取：把返回中所有表格的每个单元格转成记录。"""
    out: list[Record] = []
    for ti, tb in enumerate(parse_tables(_answer(res))):
        for ri, row in enumerate(tb.rows):
            code = row.get("证券代码", "").strip()
            if not code:
                continue
            label = row.get("日期", "").strip() or tb.headers[0].date or default_label
            for h in tb.headers:
                if h.name in KEY_COLS:
                    continue
                cell = row.get(h.raw, "")
                if is_null(cell):
                    value, kind = None, "number"
                else:
                    try:
                        value, kind = parse_number(cell), "number"
                    except ValueError:
                        value, kind = cell.strip(), "text"
                out.append(Record(entity=code, field=h.name, value=value, kind=kind,
                                  label=h.date or label, rank=h.rank, unit=h.unit,
                                  currency=currency if kind == "number" and h.unit not in ("%",) else None,
                                  dataset=dataset,
                                  source={"call_id": res.call_id, "column": h.raw, "table": ti, "row": ri}))
    return out


# ---- 模板 ------------------------------------------------------------------
def _d(labels):
    return "、".join(f"{l[:4]}-{l[4:6]}-{l[6:]}" for l in labels)


@dataclass
class Job:
    dataset: str
    server: str
    tool: str
    make: Callable[[list[dict]], str]   # 由公司列表生成问句
    entities: list[dict]
    currency: str | None = None
    default_label: str | None = None
    expect: str | None = None           # 校验：每家公司都必须有该字段的非空值
    need_label: bool = False            # 校验：该字段必须带报告期

    def query(self, ents=None) -> dict:
        return {"query": self.make(ents if ents is not None else self.entities)}


def _names(ents):
    return "、".join(e.get("ifind_name", e["name"]) for e in ents)


def jobs(only: set[str] | None = None) -> list[Job]:
    cn = UNIVERSE["cn"]
    gl = UNIVERSE["global"]
    periods = UNIVERSE["report_periods"]
    cutoff = UNIVERSE["snapshot_cutoff"]
    cut = cutoff.replace("-", "")
    J: list[Job] = []

    for grp in _chunks(cn, 5):
        J.append(Job("cn_profile", "stock", "get_stock_info",
                     lambda e: f"{_names(e)}的公司中文名称、所属申万行业(三级)、主营业务、可比公司", grp, expect="主营业务"))
    # 季度序列：多公司×多期、或带“单季度销售毛利率”时日期不展开（2026-10-17 模板试验），
    # 所以逐家、逐指标查询；毛利率由营收和营业成本确定性计算
    for c in cn:
        for metric, field_ in (("单季度营业收入", "单季度.营业收入"), ("单季度营业成本", "单季度.营业成本"),
                               ("单季度归属于母公司所有者的净利润", "单季度.归属于母公司所有者的净利润")):
            J.append(Job("cn_quarterly", "stock", "get_stock_financials",
                         lambda e, m=metric: f"{_names(e)}在{_d(periods)}的{m}", [c], "CNY",
                         expect=field_, need_label=True))
    for period in ("20251231", "20260630"):
        for grp in _chunks(cn, 4):
            J.append(Job("cn_mainbiz_product", "stock", "get_stock_financials",
                         lambda e, p=period: f"{_names(e)}{_d([p])}主营构成(按产品)项目名称、项目收入、项目成本、项目毛利率",
                         grp, "CNY", period, expect="主营构成-项目收入(按产品)", need_label=True))
            J.append(Job("cn_mainbiz_region", "stock", "get_stock_financials",
                         lambda e, p=period: f"{_names(e)}{_d([p])}主营构成(按地区)项目名称、项目收入",
                         grp, "CNY", period, expect="主营构成-项目收入(按地区)", need_label=True))
    for grp in _chunks(cn, 5):
        J.append(Job("cn_valuation", "stock", "get_stock_financials",
                     lambda e: f"{_names(e)}{cutoff}的市盈率TTM、市净率、总市值", grp, "CNY", cut, expect="总市值"))
        J.append(Job("cn_customers", "stock", "get_stock_financials",
                     lambda e: f"{_names(e)}2025-12-31的前五大客户销售额占比合计、研发费用、营业总收入", grp, "CNY",
                     "20251231", expect="主要客户销售额合计占比"))

    for grp in _chunks(gl, 6):
        J.append(Job("gl_profile", "global_stock", "global_stock_profile",
                     lambda e: f"{_names(e)}的会计年结日、交易币种、上市地点、主营业务", grp, expect="会计年结日"))
        J.append(Job("gl_valuation", "global_stock", "global_stock_financial",
                     lambda e: f"{_names(e)}{cutoff}的市盈率PE(TTM)、总市值、营业收入(TTM)", grp, "USD", cut,
                     expect="营业收入(TTM)"))
    for g in gl:
        if g["role"] == "demand":
            J.append(Job("gl_capex", "global_stock", "global_stock_financial",
                         lambda e: f"{_names(e)}在{_d(GLOBAL_LABELS)}的单季度资本性支出", [g], "USD",
                         expect="单季度-资本性支出", need_label=True))
        J.append(Job("gl_cumulative", "global_stock", "global_stock_financial",
                     lambda e: f"{_names(e)}在{_d(GLOBAL_LABELS)}的营业收入、销售毛利率、净利润", [g], "USD",
                     expect="营业收入", need_label=True))
        J.append(Job("gl_meta", "global_stock", "global_stock_financial",
                     lambda e: f"{_names(e)}最新报告期的报表币种、会计准则类型", [g], expect="会计准则类型"))

    for q in ("平均汇率:中间价:美元兑人民币 月度 202401-202609",
              "光通信设备的激光收发模块(85177950):出口金额:当月值 202301-202609",
              "激光器,激光二极管除外(901320):出口金额:当月值 202301-202609"):
        J.append(Job("edb", "edb", "get_edb_data", lambda e, q=q: q, []))
    return [j for j in J if not only or j.dataset in only]


def missing_entities(job: Job, ents: list[dict], recs: list[Record]) -> list[dict]:
    if not job.expect:
        return []
    got = {r.entity for r in recs if r.field == job.expect and r.value is not None
           and (not job.need_label or r.label)}
    return [e for e in ents if e["code"] not in got]


def edb_records(res: IfindResult) -> list[Record]:
    """EDB 返回无证券代码：第一列是日期，第二列是指标；indicator_description 给数据源和单位。"""
    d = res.data if isinstance(res.data, dict) else {}
    desc = (d.get("indicator_description") or [{}])[0]
    out = []
    for tb in parse_tables(d.get("answer") or ""):
        for ri, row in enumerate(tb.rows):
            for h in tb.headers[1:]:
                cell = row.get(h.raw, "")
                out.append(Record(entity=f"EDB:{desc.get('指标名称', h.name)}", field=h.name,
                                  value=None if is_null(cell) else parse_number(cell), kind="number",
                                  label=row.get("日期", "").replace("-", ""), unit=desc.get("单位") or h.unit,
                                  currency=None, dataset="edb",
                                  source={"call_id": res.call_id, "column": h.raw, "table": 0, "row": ri,
                                          "data_source": desc.get("数据来源"), "freq": desc.get("频率")}))
    return out


def run(only: set[str] | None = None, refresh: bool = False, client: IfindClient | None = None) -> dict:
    client = client or IfindClient()
    records: list[Record] = []
    report = {"calls": 0, "ok": 0, "retried_single": [], "missing": [], "failed": []}

    def do(job: Job, ents: list[dict]):
        params = job.query(ents)
        res = client.call(job.server, job.tool, params, refresh=refresh)
        report["calls"] += 1
        recs = []
        if res.status == "ok":
            report["ok"] += 1
            recs = edb_records(res) if job.dataset == "edb" else table_records(res, job.dataset, job.currency, job.default_label)
            if job.need_label:  # 无报告期的数据不入库（解析漂移的典型症状）
                recs = [r for r in recs if r.label]
        elif res.status != "empty":
            report["failed"].append({"dataset": job.dataset, "call_id": res.call_id, "status": res.status,
                                     "error": res.error, "query": params["query"]})
        print(f"[{res.status:12s}] {job.dataset:20s} {res.ms:5d}ms {'cache' if res.from_cache else '     '} {params['query'][:70]}", flush=True)
        return res, recs

    for job in jobs(only):
        res, recs = do(job, job.entities)
        miss = missing_entities(job, job.entities, recs)
        if miss and len(job.entities) > 1:  # 批量缺失：逐家重试一次
            for e in miss:
                report["retried_single"].append({"dataset": job.dataset, "entity": e["code"]})
                r2, recs2 = do(job, [e])
                recs += recs2
            miss = missing_entities(job, job.entities, recs)
        for e in miss:
            report["missing"].append({"dataset": job.dataset, "entity": e["code"], "name": e["name"],
                                      "expect": job.expect, "query": job.query([e])["query"]})
        records += recs

    STAGING.mkdir(parents=True, exist_ok=True)
    out = STAGING / "records.jsonl"
    keep = []
    if out.exists() and only:  # 增量：只替换本次涉及的 dataset
        keep = [l for l in out.read_text(encoding="utf-8").splitlines() if json.loads(l)["dataset"] not in only]
    with out.open("w", encoding="utf-8") as f:
        for l in keep:
            f.write(l + "\n")
        for r in records:
            f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
    report["records"] = len(records)
    (STAGING / "ingest_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--refresh", action="store_true")
    a = ap.parse_args()
    rep = run(set(filter(None, a.only.split(","))) or None, a.refresh)
    print(json.dumps({k: (v if not isinstance(v, list) else len(v)) for k, v in rep.items()}, ensure_ascii=False))
