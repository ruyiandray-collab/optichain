"""离线回放页面数字：快照 → staging → iFinD/SEC 原始缓存。"""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from datetime import date
import json
import math
import os
from pathlib import Path
import random

import pytest

from pipeline.ifind import classify
from pipeline.tables import parse_number, parse_tables

ROOT = Path(__file__).resolve().parents[1]
STAGING = ROOT / "data/staging"
PUBLIC = ROOT / "web/public/data"
IFIND_CACHE = ROOT / "data/cache/ifind"
SEC_CACHE = ROOT / "data/cache/sec"


def _stamp(path):
    s = path.stat()
    return s.st_mtime_ns, s.st_size


@contextmanager
def documents(*paths, m2_pair=False):
    """读取同一组文件并检查读前读后版本，避免后台写入造成假失败。"""
    before = {p: _stamp(p) for p in paths}
    if m2_pair:
        # 分级结果由 claims 生成：只有 claims 比 concept_links 新时，分级才过期（不调 LLM 的重分级只改写 concept_links，属正常）
        a, b = STAGING / "claims.jsonl", STAGING / "concept_links.json"
        if before[a][0] > before[b][0]:
            gap = (before[a][0] - before[b][0]) / 1e9
            pytest.skip(f"claims.jsonl 比 concept_links.json 新 {gap:.1f}s：分级结果尚未按最新引文重算（后台 M2 可能正在重跑）")
    try:
        loaded = {}
        for p in paths:
            text = p.read_text(encoding="utf-8")
            loaded[p] = ([json.loads(line) for line in text.splitlines() if line.strip()]
                         if p.suffix == ".jsonl" else json.loads(text))
        yield loaded
    finally:
        changed = [str(p.relative_to(ROOT)) for p in paths if _stamp(p) != before[p]]
        if changed:
            pytest.skip("读取/校验期间文件被后台改写，无法比较同一版本：" + ", ".join(changed))


def walk_dicts(obj, pointer="", code=None):
    """保留 RFC 6901 JSON Pointer 和所属公司，方便失败时定位页面数据。"""
    if isinstance(obj, dict):
        code = obj.get("code", code)
        yield pointer, obj, code
        for key, value in obj.items():
            escaped = str(key).replace("~", "~0").replace("/", "~1")
            yield from walk_dicts(value, f"{pointer}/{escaped}", code)
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            yield from walk_dicts(value, f"{pointer}/{i}", code)


def same_number(a, b):
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(a, b, rel_tol=1e-9, abs_tol=0)
    return a == b


def require_number(actual, expected, where, errors):
    if not same_number(actual, expected):
        errors.append(f"{where}: value={actual!r}, expected={expected!r}")


@pytest.fixture
def trace_data():
    paths = [PUBLIC / f"{name}.json" for name in ("companies", "comparables", "transmission")]
    paths += [STAGING / "records.jsonl", STAGING / "metrics.json"]
    with documents(*paths) as loaded:
        candidates = [(f"{p.name}#{ptr}", v, code) for p in paths[:3]
                      for ptr, v, code in walk_dicts(loaded[p]) if "value" in v and "inputs" in v]
        assert candidates, "三个页面快照中未找到 Value，拒绝空抽样通过"
        n = int(os.environ.get("NUM_TRACE_N", "60"))
        assert n > 0, "NUM_TRACE_N 必须为正整数"
        sample = random.Random(20261017).sample(candidates, min(n, len(candidates)))
        records = loaded[STAGING / "records.jsonl"]
        metrics = loaded[STAGING / "metrics.json"]["companies"]
        yield sample, records, metrics


def expanded_values(sample, metrics):
    """TTM 的季度输入继续展开到季度 Value，汇率转换继续展开到人民币与月汇率。"""
    seen = set()

    def visit(path, value, code):
        key = (code, json.dumps(value, sort_keys=True, ensure_ascii=False))
        if key in seen:
            return
        seen.add(key)
        yield path, value, code
        for ptr, child, _ in walk_dicts(value.get("inputs", [])):
            if "value" in child and "inputs" in child:
                yield from visit(path + "/inputs" + ptr, child, code)
        m = metrics.get(code, {})
        formula = value.get("formula", "")
        if formula.startswith("Σ 最近 4 个单季度"):
            metric = formula.removeprefix("Σ 最近 4 个单季度")
            window = (m.get("ttm") or {}).get("window", ["", ""])
            for i, inp in enumerate(value["inputs"]):
                matches = [q for q in m.get("quarters", []) if q["fiscal"] == inp["quarter"]
                           and window[0] <= q["end"] <= window[1] and metric in q]
                assert len(matches) == 1, f"{path}/inputs/{i}: 无法唯一定位季度 {inp!r}"
                assert same_number(inp["value"], matches[0][metric]["value"]), f"{path}: TTM 季度输入与 metrics 不同"
                yield from visit(f"{path} -> metrics[{code}]/{matches[0]['end']}/{metric}", matches[0][metric], code)
        if formula.startswith("CNY ÷"):
            cny = value["inputs"][0]["cny"]
            for metric, v in (m.get("ttm") or {}).items():
                if isinstance(v, dict) and same_number(v.get("value"), cny):
                    yield from visit(f"{path} -> metrics[{code}]/ttm/{metric}", v, code)
            fx = (m.get("ttm_usd") or {}).get("fx")
            if fx:
                yield path + " -> fx", {"value": fx["rate"], "inputs": fx["inputs"], "formula": "月汇率平均"}, code

    for path, value, code in sample:
        yield from visit(path, value, code)


def raw_inputs(values):
    for path, value, code in values:
        for ptr, inp, _ in walk_dicts(value.get("inputs", [])):
            if "call_id" in inp and "column" in inp:
                yield path + "/inputs" + ptr, inp, code


def input_number(inp):
    return next((inp[k] for k in ("value", "revenue", "revenue_total") if k in inp), None)


def matching_records(inp, records):
    matches = [r for r in records if r["source"].get("call_id") == inp["call_id"]
               and r["source"].get("column") == inp["column"]]
    if inp.get("label"):
        matches = [r for r in matches if r["label"] == inp["label"]]
    else:
        matches = [r for r in matches if r["source"].get("row") == inp.get("row")]
    if "table" in inp:
        matches = [r for r in matches if r["source"].get("table") == inp["table"]]
    if "row" in inp:
        matches = [r for r in matches if r["source"].get("row") == inp["row"]]
    return matches


def recalculate(value):
    """只解释已知公式，不使用 eval；未知公式仍校验全部可追溯输入。"""
    f, ins = value.get("formula", ""), value["inputs"]
    nums = [i.get("value") for i in ins]
    if not ins or value["value"] is None:
        return None
    flat = {k: v for i in ins for k, v in i.items()}
    if f == "月汇率平均":
        return sum(nums) / len(nums)
    if f.startswith("Σ 最近 4 个单季度"):
        assert len(ins) == 4, "TTM 必须有四个季度输入"
        return sum(nums)
    if f.startswith("四家单季度资本性支出之和"):
        return sum(nums)
    if f in ("iFinD 原始值", "iFinD 单季度营业收入", "iFinD 单季度归母净利润", "财年 Q1 累计营收即单季度"):
        return nums[0]
    if f.startswith("累计营收[") or f == "单季度营业收入 − 单季度营业成本":
        return nums[0] - nums[1]
    if f == "累计净利润差分":
        return nums[0] - (nums[1] if len(nums) > 1 else 0)
    if f == "1 − 单季度营业成本 / 单季度营业收入":
        return 1 - nums[1] / nums[0]
    if f in ("累计营收 × 累计毛利率 差分", "单季度毛利 / 单季度营收"):
        gp = nums[0] * nums[1] / 100 - (nums[2] * nums[3] / 100 if len(nums) == 4 else 0)
        return gp if "差分" in f else gp / (nums[0] - (nums[2] if len(nums) == 4 else 0))
    for numerator, denominator in (("gross_profit_ttm", "revenue_ttm"), ("net_profit_ttm", "revenue_ttm"),
                                   ("market_cap_usd", "revenue_ttm_usd"), ("cny", "fx")):
        if numerator in flat and denominator in flat:
            return flat[numerator] / flat[denominator]
    if f.startswith("总市值 CNY ÷"):
        return nums[0] / nums[1]
    if f == "境外地区收入 / 同期营业收入":
        return sum(i["revenue"] for i in ins if "revenue" in i) / flat["revenue_total"]
    if f.startswith("静态敏感性"):
        return -0.01 * flat["share_overseas"] * flat["revenue_ttm"]
    return None


def test_sampled_snapshot_to_records_and_formulas(trace_data):
    """固定种子抽页面 Value 并回查 staging、复算可解释公式，防止数字或派生计算失真。"""
    sample, records, metrics = trace_data
    values = list(expanded_values(sample, metrics))
    errors, counts = [], Counter()
    for path, inp, _ in raw_inputs(values):
        matches = matching_records(inp, records)
        where = f"{path} call_id={inp['call_id']} column={inp['column']} label={inp.get('label')} row={inp.get('row')}"
        if not matches:
            errors.append(where + ": records.jsonl 无匹配记录")
            continue
        expected = input_number(inp)
        if expected is None:
            errors.append(where + ": 输入缺少可核对的数值")
            continue
        for rec in matches:
            require_number(expected, rec["value"], where, errors)
        counts["staging"] += 1
    for path, value, _ in values:
        expected = recalculate(value)
        if expected is not None:
            require_number(value["value"], expected, path + " formula=" + value.get("formula", ""), errors)
            counts["formula"] += 1
        else:
            counts["formula_unavailable"] += 1
    print(f"数字溯源：抽样={len(sample)}，展开 Value={len(values)}，staging通过={counts['staging']}，"
          f"复算通过={counts['formula']}，仅检查输入/不可直接复算={counts['formula_unavailable']}，不一致={len(errors)}")
    assert not errors, "\n" + "\n".join(errors)


def test_sampled_records_to_ifind_cache(trace_data):
    """按 table/row/column 重解析 iFinD 原始缓存，防止 staging 和快照共同带着错误通过。"""
    sample, records, metrics = trace_data
    errors, missing, tables, passed = [], set(), {}, 0
    for path, inp, _ in raw_inputs(expanded_values(sample, metrics)):
        cid = inp["call_id"]
        cache = IFIND_CACHE / f"{cid}.json"
        if not cache.exists():
            missing.add(str(cache.relative_to(ROOT)))
            continue
        if cid not in tables:
            raw = json.loads(cache.read_text(encoding="utf-8"))["raw"]
            status, _, inner, error = classify(raw)
            assert status == "ok", f"{cache}: {status} {error}"
            data = inner.get("data")
            answer = data.get("answer", "") if isinstance(data, dict) else data
            tables[cid] = parse_tables(answer)
        where = f"{path} call_id={cid} table={inp.get('table')} row={inp.get('row')} column={inp['column']}"
        # 快照的 table/row 优先；旧引用缺定位信息时从匹配的 staging source 补齐。
        sources = [inp] if all(k in inp for k in ("table", "row")) else [r["source"] for r in matching_records(inp, records)]
        assert sources, where + ": 缺少原始单元格定位信息"
        for source in sources:
            try:
                cell = tables[cid][source["table"]].rows[source["row"]][inp["column"]]
                actual = parse_number(cell)
            except (IndexError, KeyError, ValueError) as exc:
                errors.append(f"{where}: 原始单元格无法解析 {exc}")
                continue
            require_number(input_number(inp), actual, where, errors)
            passed += 1
    print(f"iFinD 原始层：通过={passed}，缺失缓存={len(missing)}，不一致={len(errors)}")
    assert not errors, "\n" + "\n".join(errors)
    if missing:
        pytest.skip("iFinD 私有原始缓存不在公开仓库，已检查现存缓存；缺失：" + ", ".join(sorted(missing)))
    if not passed:
        pytest.skip("本次抽样没有 iFinD 原始引用，此层无适用项")


def sec_facts():
    """直接读 companyconcept 缓存，绝不调用可能联网的 sec._get。"""
    facts = []
    for path in sorted(SEC_CACHE.glob("*.json")):
        if path.name == "company_tickers.json":
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            continue
        for fact in data.get("units", {}).get("USD", []):
            facts.append({**fact, "tag": data.get("tag", path.stem.split("_", 1)[-1]),
                          "cik": str(data.get("cik", path.stem.split("_")[0])).zfill(10)})
    return facts


def test_sampled_sec_references(trace_data):
    """将 SEC 原始事实按 accn/start/end/val 对回缓存，并复算 Capex 的累计差分。"""
    from pipeline.grade import UNIVERSE
    sample, _, metrics = trace_data
    facts, errors, missing, passed = sec_facts(), [], set(), 0
    by_name = {c["name"]: str(c["cik"]).zfill(10) for c in UNIVERSE["global"]}
    by_code = {c["code"]: str(c["cik"]).zfill(10) for c in UNIVERSE["global"]}
    for path, value, code in expanded_values(sample, metrics):
        for ptr, inp, _ in walk_dicts(value["inputs"]):
            if "sec_accn" not in inp:
                continue
            accns = inp["sec_accn"] if isinstance(inp["sec_accn"], list) else [inp["sec_accn"]]
            cik = by_name.get(inp.get("company"), by_code.get(code))
            tag = inp.get("tag")
            if not tag and isinstance(inp["sec_accn"], list):
                tags = {"PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"}
            elif not tag and value.get("formula", "").startswith("SEC XBRL "):
                tags = {value["formula"].split()[2]} if " 单季度" in value["formula"] and "Revenues" in value["formula"] else None
            else:
                tags = {tag} if tag else None
            available = [f for f in facts if (not cik or f["cik"] == cik) and (not tags or f["tag"] in tags)]
            where = f"{path}/inputs{ptr} sec_accn={accns} start={inp.get('start')} end={inp.get('end')}"
            if not available:
                missing.add(f"CIK={cik} tag={tags or tag} {accns}")
                continue
            matches = [f for f in available if f["accn"] in accns
                       and (not inp.get("start") or f.get("start") == inp["start"])
                       and (not inp.get("end") or isinstance(inp["sec_accn"], list) or f.get("end") == inp["end"])]
            if isinstance(inp["sec_accn"], list):
                current = [f for f in matches if f.get("end") == inp["end"] and f.get("start")]
                valid = []
                for cur in current:
                    days = (date.fromisoformat(cur["end"]) - date.fromisoformat(cur["start"])).days
                    if len(accns) == 1 and 80 <= days <= 100:
                        valid.append(cur["val"])
                    for prev in matches:
                        if prev.get("start") == cur["start"] and prev.get("end", "") < cur["end"]:
                            gap = (date.fromisoformat(cur["end"]) - date.fromisoformat(prev["end"])).days
                            if 80 <= gap <= 100 and {prev["accn"], cur["accn"]} == set(accns):
                                valid.append(cur["val"] - prev["val"])
                if not any(same_number(inp["value"], v) for v in valid):
                    errors.append(f"{where}: SEC 季度值={inp['value']}，原始事实差分候选={sorted(set(valid))}")
            elif not matches or ("val" in inp and not any(same_number(inp["val"], f["val"]) for f in matches)):
                errors.append(f"{where}: val={inp.get('val')}，缓存匹配值={sorted({f['val'] for f in matches})}")
            passed += 1
    print(f"SEC 原始层：通过={passed}，缺失公司/标签缓存={len(missing)}，不一致={len(errors)}")
    assert not errors, "\n" + "\n".join(errors)
    if missing or not passed:
        pytest.skip("SEC companyconcept 私有缓存缺失或本次抽样没有 SEC 引用：" + "; ".join(sorted(missing)))


def test_metrics_ttm_inputs_sum():
    """逐公司复算 metrics 的 TTM 四季度输入，防止随机抽样漏掉聚合错误。"""
    p = STAGING / "metrics.json"
    with documents(p) as loaded:
        errors, count = [], 0
        for code, m in loaded[p]["companies"].items():
            for key, v in (m.get("ttm") or {}).items():
                if isinstance(v, dict) and v.get("inputs") and all("quarter" in i for i in v["inputs"]):
                    assert len(v["inputs"]) == 4, f"metrics.json#/companies/{code}/ttm/{key}: 季度数不是 4"
                    require_number(v["value"], sum(i["value"] for i in v["inputs"]), f"metrics.json#/companies/{code}/ttm/{key}", errors)
                    # 同时核对输入季度对应的真实季度字段。
                    list(expanded_values([(f"metrics.json#/companies/{code}/ttm/{key}", v, code)], loaded[p]["companies"]))
                    count += 1
        print(f"metrics TTM：四季度复算通过={count}，不一致={len(errors)}")
        assert count and not errors, "\n".join(errors)


def test_coherent_fy2026_revenue():
    """以 SEC FY2026 10-K 的 7,118,181,000 美元作独立锚点，允许 iFinD 万美元舍入。"""
    p = STAGING / "metrics.json"
    with documents(p) as loaded:
        value = loaded[p]["companies"]["COHR.N"]["ttm"]["revenue"]["value"]
        assert value == pytest.approx(7_118_181_000, rel=0, abs=10_000), f"metrics.json#/companies/COHR.N/ttm/revenue/value={value}"
        print(f"Coherent FY2026 TTM={value:,.0f} USD，SEC=7,118,181,000 USD，差额={value - 7_118_181_000:,.0f} USD")


def test_coherent_sec_accession_trace():
    """缓存存在时检查 metrics 的 SEC 引用可追到指定年报或 TTM 对应季度披露，防止金额正确但来源链断裂。"""
    p = STAGING / "metrics.json"
    with documents(p) as loaded:
        m = loaded[p]["companies"]["COHR.N"]
        facts = [f for f in sec_facts() if f["cik"] == "0000820318"]
        if not facts:
            pytest.skip("缺少 data/cache/sec/0000820318_<tag>.json companyconcept 缓存，无法核对 Coherent SEC accn")
        annual = [f for f in facts if f["accn"] == "0000820318-26-000020" and f["val"] == 7_118_181_000]
        assert annual, "Coherent SEC 缓存缺少指定年报 accn=0000820318-26-000020 的营收事实"
        window = m["ttm"]["window"]
        quarters = [q for q in m["quarters"] if window[0] <= q["end"] <= window[1]]
        allowed = {f["accn"] for f in facts if window[0] <= f.get("end", "") <= window[1]}
        refs = [v for q in quarters for _, v, _ in walk_dicts(q) if "sec_accn" in v]
        assert refs, "metrics.json#/companies/COHR.N/quarters: TTM 对应季度缺少 SEC accn 引用"
        for ref in refs:
            assert ref["sec_accn"] in allowed, f"COHR.N TTM 季度引用不属于对应披露：{ref}"
        print(f"Coherent SEC：TTM 季度可追溯引用={len(refs)}，accn={sorted({r['sec_accn'] for r in refs})}")
