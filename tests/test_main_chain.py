"""中际旭创主链路回放与异常路径测试，全部依赖本地文件或假 transport。"""
from __future__ import annotations

from collections import Counter
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
from tempfile import TemporaryDirectory

import pytest
import requests

from pipeline import grade, ingest
from pipeline.extract import verify_quote
from pipeline.fiscal import map_label
from pipeline.ifind import IfindClient
from tests.test_number_trace import (
    ROOT, STAGING, PUBLIC, IFIND_CACHE, documents, same_number,
)

CODE = "300308.SZ"
NAME = "中际旭创"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """拦截 Python HTTP 和 socket，避免缓存失效时测试意外访问任何外部服务。"""
    def denied(*args, **kwargs):
        raise AssertionError("离线测试禁止联网")
    monkeypatch.setattr(requests.sessions.Session, "request", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)


def test_pull_quarterly_from_cache():
    """用原调用参数命中只读缓存并重建营收记录，验证拉取到 staging 的主链路可复现。"""
    calls_path, records_path = STAGING / "api_calls.jsonl", STAGING / "records.jsonl"
    with documents(calls_path, records_path) as loaded:
        calls = {c["call_id"]: c for c in loaded[calls_path] if c["tool"] == "get_stock_financials"
                 and NAME in c["params"].get("query", "") and "单季度营业收入" in c["params"].get("query", "")}
        assert calls, "api_calls.jsonl 未找到中际旭创单季度营业收入调用"
        # 调用日志会保留模板试验和旧批次；只回放当前 records 实际采用的调用。
        current_ids = {r["source"]["call_id"] for r in loaded[records_path]
                       if r["entity"] == CODE and r["dataset"] == "cn_quarterly"}
        calls = {cid: c for cid, c in calls.items() if cid in current_ids}
        assert calls, "季度营收调用日志与当前 records.jsonl 没有共同 call_id"
        missing, checked = [], 0
        for cid, call in calls.items():
            if not (IFIND_CACHE / f"{cid}.json").exists():
                missing.append(cid)
                continue
            def denied_transport(*args, **kwargs):
                raise AssertionError(f"call_id={cid}: 未命中缓存，禁止联网")
            client = IfindClient(token="offline-test", transport=denied_transport, cache_dir=IFIND_CACHE,
                                 call_log=None, min_interval=0, sleep=lambda _: None)
            result = client.call(call["server"], call["tool"], call["params"])
            assert result.from_cache and result.status == "ok" and result.call_id == cid
            expected = [r for r in loaded[records_path] if r["source"]["call_id"] == cid]
            assert expected, f"records.jsonl: call_id={cid} 无记录"
            datasets = {r["dataset"] for r in expected}
            assert datasets == {"cn_quarterly"}, f"call_id={cid}: dataset 漂移 {datasets}"
            actual = ingest.table_records(result, "cn_quarterly", "CNY")
            signature = lambda r: (r["entity"], r["field"], r["label"], r["value"])
            a = Counter(signature(vars(r)) for r in actual)
            b = Counter(signature(r) for r in expected)
            assert a == b, f"call_id={cid}: 重解析多出={a - b}; staging 多出={b - a}"
            checked += 1
        print(f"中际旭创缓存回放：调用通过={checked}，缺缓存={len(missing)}")
        if missing:
            pytest.skip("iFinD 私有缓存不在公开仓库；缺少 data/cache/ifind/<call_id>.json：" + ", ".join(missing))


def test_extracted_quotes_match_evidence():
    """逐条验证样本公司引文是原文的连续摘录，防止抽取模型改写或拼接证据。"""
    evpath, clpath = STAGING / "evidence_raw.jsonl", STAGING / "claims.jsonl"
    with documents(evpath, clpath) as loaded:
        evidence = {e["evidence_id"]: e for e in loaded[evpath] if e["entity"] == CODE}
        claims = [c for c in loaded[clpath] if c["evidence_id"] in evidence]
        assert evidence and claims, "样本公司缺少证据或引文，拒绝空循环通过"
        errors = []
        for i, claim in enumerate(claims):
            ev = evidence[claim["evidence_id"]]
            if not verify_quote(claim["quote"], ev["text"]):
                errors.append(f"claims[{i}] evidence_id={ev['evidence_id']} call_id={ev['call_id']} quote={claim['quote']!r}")
        print(f"中际旭创引文：逐字核验={len(claims)}，不一致={len(errors)}（按 verify_quote 容忍空白差异）")
        assert not errors, "\n".join(errors)


@pytest.mark.parametrize("concept", list(grade.CONCEPTS))
def test_regrade_matches_staging(concept):
    """两个概念都用同一版本证据、引文、财务记录重新分级，验证规则输出与存储结果一致。"""
    evpath, clpath, recpath, linkpath = [STAGING / n for n in
                                      ("evidence_raw.jsonl", "claims.jsonl", "records.jsonl", "concept_links.json")]
    with documents(evpath, clpath, recpath, linkpath, m2_pair=True) as loaded:
        evs = [e for e in loaded[evpath] if e["entity"] == CODE]
        ids = {e["evidence_id"] for e in evs}
        claims = [c for c in loaded[clpath] if c["evidence_id"] in ids]
        # 规则补抽的收入表述单独落盘在 claims_rule.jsonl（m2 会把它并入分级），复算时一并读入
        rule_path = STAGING / "claims_rule.jsonl"
        if rule_path.exists():
            claims += [json.loads(l) for l in rule_path.read_text(encoding="utf-8").splitlines()
                       if l.strip() and json.loads(l)["evidence_id"] in ids]
        matches = [l for l in loaded[linkpath] if l["entity"] == CODE and l["concept"] == concept]
        assert len(matches) == 1 and evs
        expected = matches[0]
        if expected.get("extractor") == "rule_sentence":
            claims = [c for ev in evs for co in grade.CONCEPTS for c in grade.fallback_claims(ev, co)]
        actual = grade.grade_entity(CODE, concept, evs, claims, loaded[recpath], expected.get("search_scope", []))
        assert actual["grade"] == expected["grade"], f"{CODE}/{concept}: 复算={actual['grade']}, concept_links={expected['grade']}"
        print(f"中际旭创重新分级：{concept}={actual['grade']}")


def test_snapshot_matches_staging_and_evidence_calls():
    """核对发布的等级、TTM 和最强证据到调用记录的引用，防止快照跨版本或来源链断裂。"""
    paths = [PUBLIC / n for n in ("companies.json", "evidence.json", "calls.json")]
    paths += [STAGING / n for n in ("metrics.json", "concept_links.json")]
    with documents(*paths) as loaded:
        company = next(c for c in loaded[paths[0]] if c["code"] == CODE)
        links = {l["concept"]: l for l in loaded[paths[4]] if l["entity"] == CODE}
        assert set(links) == set(grade.CONCEPTS)
        best_count = 0
        for concept, expected in links.items():
            published = company["grades"][concept]
            assert published["grade"] == expected["grade"], f"companies.json {CODE}/{concept}: {published['grade']} != concept_links {expected['grade']}"
            eid = published.get("best_evidence") or (published.get("best") or {}).get("evidence_id")
            if eid:
                assert eid in loaded[paths[1]], f"companies.json {concept}: evidence_id={eid} 不在 evidence.json"
                ev = loaded[paths[1]][eid]
                assert ev["call_id"] in loaded[paths[2]], f"evidence.json#/{eid}: call_id={ev['call_id']} 不在 calls.json"
                best_count += 1
        assert best_count, "样本公司没有可检查的最强文本证据"
        actual = company["metrics"]["ttm"]["revenue"]["value"]
        expected = loaded[paths[3]]["companies"][CODE]["ttm"]["revenue"]["value"]
        assert same_number(actual, expected), f"companies.json {CODE}/metrics/ttm/revenue: {actual} != metrics.json {expected}"
        print(f"中际旭创快照：两个概念、TTM 与 {best_count} 条最强证据调用链一致")


def test_page_ssr_vitest():
    """执行已有 Vitest SSR 覆盖页面渲染和 data-ref，避免 Python 溯源正确但页面引用错误。"""
    web = ROOT / "web"
    if not shutil.which("node") or not (web / "node_modules").is_dir():
        pytest.skip("系统没有 node 或 web/node_modules，无法运行已有 Vitest SSR")
    npx = shutil.which("npx.cmd" if os.name == "nt" else "npx")
    assert npx, "node 和 node_modules 存在，但未找到 npx"
    assert (web / "node_modules/vitest/vitest.mjs").is_file(), "node_modules 缺少本地 vitest，禁止 npx 下载"
    env = {**os.environ, "npm_config_offline": "true", "npm_config_update_notifier": "false"}
    try:
        # 禁用持久缓存，遵守测试不得改写 web/ 的边界；单 worker 避免争抢后台任务资源。
        result = subprocess.run([npx, "vitest", "run", "src/app.test.ts", "--no-cache", "--maxWorkers=1", "--minWorkers=1"], cwd=web,
                                capture_output=True, text=True, encoding="utf-8", errors="replace",
                                timeout=180, env=env)
    except subprocess.TimeoutExpired:
        pytest.fail("Vitest SSR 超过 180 秒未完成")
    print(result.stdout)
    assert result.returncode == 0, result.stdout + "\n" + result.stderr


def test_template_drift_without_date_is_missing(monkeypatch):
    """构造日期列丢失的返回，验证 ingest.run 报 missing 而不把无期数值混入正式记录。"""
    answer = "|证券代码|证券简称|单季度.营业收入（单位：元）|\n|---|---|---|\n|300308.SZ|中际旭创|1亿|"
    raw = {"result": {"content": [{"type": "text", "text": json.dumps({"code": 1, "data": {"answer": answer}})}]}}

    def transport(server, payload, timeout):
        if payload["method"] == "initialize":
            return 200, {"Mcp-Session-Id": "offline"}, {}
        if payload["method"] == "notifications/initialized":
            return 202, {}, None
        return 200, {}, raw

    job = ingest.Job("cn_quarterly", "stock", "get_stock_financials", lambda _: "模板漂移离线夹具",
                     [{"code": CODE, "name": NAME}], "CNY", expect="单季度.营业收入", need_label=True)
    monkeypatch.setattr(ingest, "jobs", lambda only=None: [job])
    # 独立临时目录在退出时自动清理，也避开其他进程占有的 pytest 共享临时目录。
    with TemporaryDirectory(prefix="optichain-main-chain-") as directory:
        tmp_path = Path(directory)
        monkeypatch.setattr(ingest, "STAGING", tmp_path / "staging")
        client = IfindClient(token="offline-test", transport=transport, cache_dir=tmp_path / "cache",
                             call_log=None, min_interval=0, sleep=lambda _: None)
        report = ingest.run(client=client)
        assert [m["entity"] for m in report["missing"]] == [CODE]
        assert report["records"] == 0 and report["failed"] == []
        assert (ingest.STAGING / "records.jsonl").read_text(encoding="utf-8") == ""


def test_conflicts_resolve_capex_to_sec():
    """每条两源冲突保留双方与裁决，并检查使用的 Capex 是 SEC 数值且保留 accn。"""
    conflicts, transmission = STAGING / "conflicts.json", PUBLIC / "transmission.json"
    with documents(conflicts, transmission) as loaded:
        from pipeline.grade import UNIVERSE
        names = {c["code"]: c["name"] for c in UNIVERSE["global"]}
        rows = loaded[conflicts]
        assert rows, "conflicts.json 应至少保留一条真实两源冲突"
        points = next(v for v in loaded[transmission]["variables"] if v["id"] == "capex")["series"]["points"]
        errors, checked = [], 0
        for i, conflict in enumerate(rows):
            assert ("ifind" in conflict or "ifind_abs" in conflict) and "sec" in conflict and conflict.get("resolution"), f"conflicts.json#/{i}: 缺少双方值或 resolution"
            if conflict["check"] != "capex_vs_sec":
                continue
            refs = [inp for point in points for inp in point["ref"]["inputs"]
                    if inp.get("company") == names[conflict["entity"]] and inp.get("end") == conflict["real_end"]]
            where = f"conflicts.json#/{i} {conflict['entity']} {conflict['real_end']} call_id={conflict['sources'][0].get('call_id')}"
            if not refs:
                errors.append(where + ": transmission 缺少对应 Capex")
            for ref in refs:
                if not same_number(ref["value"], conflict["sec"]):
                    errors.append(f"{where}: transmission={ref['value']}, SEC={conflict['sec']}, iFinD={conflict.get('ifind_abs', conflict.get('ifind'))}")
                expected_accns = {s["sec_accn"] for s in conflict["sources"] if "sec_accn" in s}
                if not ref.get("sec_accn") or set(ref["sec_accn"]) != expected_accns:
                    errors.append(where + f": SEC accn 来源不一致 {ref}")
            checked += 1
        print(f"两源冲突：保留双方={len(rows)}，Capex SEC 裁决通过={checked}，不一致={len(errors)}")
        assert checked and not errors, "\n".join(errors)


def test_currency_and_noncalendar_fiscal_periods():
    """海外非自然财年必须把标签映射成真实期末，A 股流量美元折算必须覆盖完整十二个月。"""
    p = STAGING / "metrics.json"
    with documents(p) as loaded:
        foreign, chinese, errors = 0, 0, []
        for code, m in loaded[p]["companies"].items():
            if m["market"] == "global" and m["fye"] != "1231":
                for i, q in enumerate(m["quarters"]):
                    if q.get("label") is None:  # SEC 补齐季度本来就没有 iFinD 标签。
                        assert q.get("sec_end") == q["end"], f"{code}/quarters/{i}: 无 label 也无真实 SEC end"
                        continue
                    expected = map_label(q["label"], m["fye"]).end.isoformat()
                    # 有 SEC 对应时，期末改用 SEC 真实日期（52/53 周财年），推算的月末保存在 end_mapped（交叉核对后的口径）
                    mapped = q.get("end_mapped", q["end"])
                    real_ok = q["end"] == q.get("sec_end") if q.get("end_mapped") else True
                    if mapped != expected or not real_ok or mapped.replace("-", "") == q["label"].replace("-", ""):
                        errors.append(f"metrics.json#/companies/{code}/quarters/{i}: end={q['end']} mapped={mapped} label={q['label']} fye={m['fye']} expected={expected}")
                    foreign += 1
            if m["market"] == "cn":
                fx = m.get("ttm_usd", {}).get("fx")
                if not fx:
                    errors.append(f"metrics.json#/companies/{code}/ttm_usd: 缺少 fx")
                    continue
                months = fx["months"]
                window = m["ttm"]["window"]
                start = int(window[0][:4]) * 12 + int(window[0][5:7]) - 1
                expected = [f"{(start + j) // 12:04d}-{(start + j) % 12 + 1:02d}" for j in range(12)]
                if months != expected or months[-1] != window[1][:7]:
                    errors.append(f"metrics.json#/companies/{code}/ttm_usd/fx/months: {months}, TTM={window}")
                chinese += 1
        print(f"财年/币种：海外映射季度={foreign}，A 股十二月汇率窗口={chinese}，不一致={len(errors)}")
        assert foreign and chinese and not errors, "\n".join(errors)
