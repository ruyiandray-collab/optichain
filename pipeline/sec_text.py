"""海外可比公司的业务描述证据：SEC 最新 10-K 的 Item 1（Business）原文 → 关键段落 → LLM 摘录（逐字校验）。

为什么需要：iFinD 港美股的“主营业务”字段过于简略，甚至有错（Fabrinet 与 Coherent 的描述一字不差），
不足以支撑“为何可比 / 哪些不可比”。10-K 是一手文件，可按 accn 回查。
"""
from __future__ import annotations

import hashlib
import html
import json
import re
from pathlib import Path

import yaml

from pipeline import sec
from pipeline.extract import api_key, _call_llm, verify_quote

ROOT = Path(__file__).resolve().parent.parent
STAGING = ROOT / "data" / "staging"
UNIVERSE = yaml.safe_load((ROOT / "config" / "universe.yaml").read_text(encoding="utf-8"))
TARGETS = ["COHR.N", "LITE.O", "FN.N", "AAOI.O", "AVGO.O", "MRVL.O", "CRDO.O"]
KEYWORDS = re.compile(r"transceiver|optical|datacom|data center|manufactur|foundry|fabless|customer|NVIDIA|laser|"
                      r"AI |artificial intelligence|DSP|SerDes|segment", re.I)
TOPICS = {"products": "主要产品与业务", "model": "商业模式（自有品牌/代工/fabless/垂直整合）",
          "customers": "客户与集中度", "datacom": "数据中心/AI 光互联相关业务"}

SYSTEM = """You extract verbatim quotes from a company's SEC 10-K business section. No numbers of your own, no judgement.
For each topic, copy 1-2 sentences EXACTLY as written (10-300 characters, contiguous text, no edits).
Topics: products (main products/business lines), model (business model: own brand vs contract manufacturing vs fabless vs vertically integrated),
customers (key customers / concentration), datacom (data center / AI / optical interconnect exposure).
Output JSON: {"quotes":[{"topic":"products|model|customers|datacom","quote":"..."}]}. Omit a topic if the text has nothing on it."""


def latest_10k(cik: str) -> dict | None:
    d = sec._get(f"https://data.sec.gov/submissions/CIK{cik}.json", f"submissions_{cik}.json")
    rec = d["filings"]["recent"]
    for i, form in enumerate(rec["form"]):
        if form in ("10-K", "20-F"):
            accn = rec["accessionNumber"][i]
            doc = rec["primaryDocument"][i]
            url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accn.replace('-', '')}/{doc}"
            return {"accn": accn, "form": form, "filed": rec["filingDate"][i], "url": url, "doc": doc}
    return None


def _strip(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", html.unescape(raw)).strip()


def business_section(text: str, limit: int = 20000) -> str:
    """取 Item 1 Business 到 Item 1A 之间（跳过目录里的第一次出现）。"""
    starts = [m.start() for m in re.finditer(r"Item\s*1\.?\s*Business", text, re.I)]
    for s in starts:
        end = re.search(r"Item\s*1A\.?\s*Risk\s*Factors", text[s + 200:], re.I)
        body = text[s: s + 200 + end.start()] if end else text[s: s + limit]
        if len(body) > 3000:  # 目录里的那次很短
            return body[:limit]
    return text[:limit]


def chunks(body: str, size: int = 900) -> list[str]:
    sents = re.split(r"(?<=[.;])\s+", body)
    out, buf = [], ""
    for s in sents:
        if len(buf) + len(s) > size and buf:
            out.append(buf.strip())
            buf = ""
        buf += s + " "
    if buf.strip():
        out.append(buf.strip())
    return [c for c in out if KEYWORDS.search(c)]


def run(max_chunks: int = 10) -> dict:
    key = api_key()
    names = {g["code"]: g for g in UNIVERSE["global"]}
    evidences, quotes, stats = [], [], {"companies": 0, "rejected": 0}
    for code in TARGETS:
        g = names[code]
        f = latest_10k(g["cik"])
        if not f:
            continue
        cache = sec.CACHE / f"doc_{g['cik']}_{f['accn']}.txt"
        if cache.exists():
            text = cache.read_text(encoding="utf-8")
        else:
            import requests
            r = requests.get(f["url"], headers={"User-Agent": sec.UA}, timeout=60)
            r.raise_for_status()
            text = _strip(r.text)
            cache.write_text(text, encoding="utf-8")
        body = business_section(text)
        stats["companies"] += 1
        for i, ch in enumerate(chunks(body)[:max_chunks]):
            eid = "ev_" + hashlib.sha1(f"sec|{code}|{f['accn']}|{i}|{ch}".encode()).hexdigest()[:10]
            ev = {"evidence_id": eid, "entity": code, "source_kind": "sec_filing", "origin_kind": "periodic",
                  "source_name": f"{g['name']} {f['form']}（{f['filed']} 提交，Item 1 Business）", "date": f["filed"],
                  "text": ch, "url": f["url"], "url_kind": "原文", "call_id": None, "sec_accn": f["accn"],
                  "query": "SEC submissions → latest 10-K primary document → Item 1", "retrieved_at": None}
            evidences.append(ev)
            resp = _call_llm([{"role": "system", "content": SYSTEM},
                              {"role": "user", "content": f"Company: {g['name']}\nText:\n{ch}"}], key)
            content = resp["choices"][0]["message"].get("content") or "{}"
            try:
                qs = json.loads(content).get("quotes", [])
            except json.JSONDecodeError:
                qs = []
            for q in qs:
                if q.get("topic") in TOPICS and verify_quote(q.get("quote", ""), ch):
                    quotes.append({"entity": code, "evidence_id": eid, "topic": q["topic"], "quote": q["quote"],
                                   "model": resp.get("model")})
                else:
                    stats["rejected"] += 1
        print(code, f["form"], f["filed"], "chunks", len([e for e in evidences if e["entity"] == code]), flush=True)
    with (STAGING / "evidence_sec.jsonl").open("w", encoding="utf-8") as fh:
        for e in evidences:
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")
    (STAGING / "sec_quotes.json").write_text(json.dumps(quotes, ensure_ascii=False, indent=1), encoding="utf-8")
    stats.update(evidence=len(evidences), quotes=len(quotes))
    return stats


if __name__ == "__main__":
    print(run())
