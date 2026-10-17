"""M2 证据采集：公告片段、资讯片段、iFinD 概念解析 → evidence_raw.jsonl。

每条证据：evidence_id、公司、来源类型、来源名称、日期、原文片段、链接、call_id。
这一步不经 LLM；片段原样保存，后续 LLM 抽取的引文必须是这里文本的子串。
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import quote

import yaml

from pipeline.ifind import IfindClient, IfindResult
from pipeline.tables import parse_tables

ROOT = Path(__file__).resolve().parent.parent
STAGING = ROOT / "data" / "staging"
UNIVERSE = yaml.safe_load((ROOT / "config" / "universe.yaml").read_text(encoding="utf-8"))
WINDOW = ("2024-10-17", "2026-10-17")  # 近 24 个月
CPO_SLICES = ["总市值大于500亿", "总市值在200亿到500亿之间", "总市值在100亿到200亿之间",
              "总市值在50亿到100亿之间", "总市值小于50亿"]


def evidence_id(*parts: str) -> str:
    return "ev_" + hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:10]


def cninfo_link(title: str) -> str:
    """search_notice 不返回 URL（data_probe 4.7）：用标题拼巨潮全文检索页。"""
    return "https://www.cninfo.com.cn/new/fulltextSearch?notautosubmit=&keyWord=" + quote(title)


_DATE_RE = re.compile(r"(20\d{2})年(\d{1,2})月(\d{1,2})日")
_YEAR_REPORT_RE = re.compile(r"(20\d{2})年(年报|年度报告|半年报|半年度报告|一季报|三季报)")


def concept_source(text: str) -> tuple[str, str | None]:
    """从 iFinD 概念解析文本里识别原始出处和日期（规则，不经 LLM）。"""
    m = _DATE_RE.search(text)
    d = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else None
    if "互动易" in text or "e互动" in text or "互动平台" in text:
        kind = "hdy"
    elif "业绩说明会" in text or "调研" in text or "投资者关系" in text:
        kind = "ir"
    elif _YEAR_REPORT_RE.search(text) or "年报" in text:
        kind = "periodic"
        if not d:
            y = _YEAR_REPORT_RE.search(text)
            d = f"{y.group(1)}-12-31" if y else None
    else:
        kind = "unspecified"
    return kind, d


def notice_kind(title: str) -> str:
    if "澄清" in title:
        return "clarification"
    if re.search(r"年度报告|半年度报告|季度报告", title):
        return "periodic"
    if re.search(r"投资者关系|调研|业绩说明会", title):
        return "ir"
    return "announcement"


def _mentions(company: dict, *texts: str) -> bool:
    name = company["name"].replace("ST", "").replace("*", "")
    code = company["code"].split(".")[0]
    return any(name in (t or "").replace(" ", "") or code in (t or "") for t in texts)


def _json_list(res: IfindResult) -> list[dict]:
    d = res.data
    if isinstance(d, dict) and "data" in d:  # search_news 多包一层
        d = d["data"]
    if isinstance(d, str):
        try:
            d = json.loads(d)
        except json.JSONDecodeError:
            return []
    return [x for x in d if isinstance(x, dict) and "备注" not in x] if isinstance(d, list) else []


def collect(client: IfindClient | None = None) -> dict:
    client = client or IfindClient()
    items: dict[str, dict] = {}
    stats = {"calls": 0, "dropped_other_company": 0}

    def add(ev):
        items.setdefault(ev["evidence_id"], ev)

    # 1. CPO 概念解析：按市值切片绕过 100 行上限（s21）
    cpo_members = {}
    for sl in CPO_SLICES:
        res = client.call("stock", "search_stocks", {"query": f"CPO概念股中{sl}的股票"})
        stats["calls"] += 1
        if res.status != "ok":
            continue
        for tb in parse_tables(res.data.get("answer", "")):
            for row in tb.rows:
                code = row.get("股票代码", "").strip()
                text = row.get("概念解析", "").strip()
                if not code:
                    continue
                cpo_members[code] = {"name": row.get("股票简称"), "call_id": res.call_id}
                if text:
                    kind, d = concept_source(text)
                    add({"evidence_id": evidence_id("concept", code, text), "entity": code,
                         "source_kind": "ifind_concept", "origin_kind": kind,
                         "source_name": f"iFinD 概念解析·{row.get('所属概念', '共封装光学(CPO)')}",
                         "date": d, "text": text, "url": None, "call_id": res.call_id,
                         "query": res.params["query"], "retrieved_at": res.called_at})

    # 2/3. 逐家公告与资讯
    for c in UNIVERSE["cn"]:
        queries = [
            ("news", "search_notice", f"{c['name']} 光模块 光通信 CPO 业务进展 订单 量产 研发", 8),
            ("news", "search_notice", f"{c['name']} 澄清 不涉及 光模块 CPO", 5),
            ("news", "search_news", f"{c['name']} 互动平台 光模块 CPO", 5),
        ]
        for server, tool, q, size in queries:
            res = client.call(server, tool, {"query": q, "time_start": WINDOW[0], "time_end": WINDOW[1], "size": size})
            stats["calls"] += 1
            if res.status != "ok":
                continue
            for it in _json_list(res):
                if tool == "search_notice":
                    title, text = it.get("公告标题", ""), it.get("公告片段内容", "")
                    if not _mentions(c, title):
                        stats["dropped_other_company"] += 1
                        continue
                    add({"evidence_id": evidence_id("notice", c["code"], title, text), "entity": c["code"],
                         "source_kind": "notice", "origin_kind": notice_kind(title), "source_name": title,
                         "date": it.get("日期"), "text": text, "url": cninfo_link(title), "url_kind": "检索页",
                         "call_id": res.call_id, "query": q, "retrieved_at": res.called_at})
                else:
                    title, text = it.get("资讯标题", ""), it.get("资讯内容", "")
                    if not _mentions(c, title, text):
                        stats["dropped_other_company"] += 1
                        continue
                    hdy = any(k in title + text for k in ("互动易", "互动平台", "e互动", "投资者提问"))
                    add({"evidence_id": evidence_id("news", c["code"], title, text), "entity": c["code"],
                         "source_kind": "news", "origin_kind": "hdy_media" if hdy else "media",
                         "source_name": title, "date": it.get("日期"), "text": text, "url": it.get("URL"),
                         "url_kind": "原文", "call_id": res.call_id, "query": q, "retrieved_at": res.called_at})
        print(f"{c['name']}: total evidence {len(items)}", flush=True)

    STAGING.mkdir(parents=True, exist_ok=True)
    with (STAGING / "evidence_raw.jsonl").open("w", encoding="utf-8") as f:
        for ev in items.values():
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    (STAGING / "cpo_members.json").write_text(json.dumps(cpo_members, ensure_ascii=False, indent=1), encoding="utf-8")
    stats["evidence"] = len(items)
    stats["cpo_members"] = len(cpo_members)
    return stats


if __name__ == "__main__":
    print(collect())
