"""SEC EDGAR XBRL：海外公司数字的第二来源（真实起止日、表单、accn 可追溯）。

- CIK 用 company_tickers.json 校验，不信任手写配置
- companyconcept 按标签拉取；同一 (start, end) 取最早提交的值（原始披露，不取后续重述）
"""
from __future__ import annotations

import json
import os
import time
from datetime import date
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache" / "sec"
UA = os.environ.get("SEC_USER_AGENT", "optichain-research contact@example.com")

REVENUE_TAGS = ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues"]
CAPEX_TAGS = ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"]


def _get(url: str, cache_name: str) -> dict | None:
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / cache_name
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    for attempt in range(3):
        r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
        if r.status_code == 404:
            return None
        if r.status_code == 200:
            f.write_text(r.text, encoding="utf-8")
            return r.json()
        time.sleep(2 ** attempt)
    raise RuntimeError(f"SEC {url} -> {r.status_code}")


def ticker_map() -> dict[str, dict]:
    d = _get("https://www.sec.gov/files/company_tickers.json", "company_tickers.json")
    return {v["ticker"].upper(): {"cik": str(v["cik_str"]).zfill(10), "title": v["title"]} for v in d.values()}


def concept_facts(cik: str, tag: str, unit: str = "USD") -> list[dict]:
    url = f"https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/us-gaap/{tag}.json"
    d = _get(url, f"{cik}_{tag}.json")
    if not d:
        return []
    out = {}
    for u in d.get("units", {}).get(unit, []):
        if "start" not in u:
            continue
        key = (u["start"], u["end"])
        if key not in out or u["filed"] < out[key]["filed"]:
            out[key] = {"start": u["start"], "end": u["end"], "val": u["val"], "form": u["form"],
                        "fp": u.get("fp"), "fy": u.get("fy"), "filed": u["filed"], "accn": u["accn"],
                        "tag": tag, "url": url}
    return sorted(out.values(), key=lambda x: (x["end"], x["start"]))


def quarterly(facts: list[dict]) -> dict[str, dict]:
    """只保留约 3 个月的区间（单季度），键为真实期末日。"""
    q = {}
    for f in facts:
        days = (date.fromisoformat(f["end"]) - date.fromisoformat(f["start"])).days
        if 80 <= days <= 100:
            q[f["end"]] = f
    return q


def first_available(cik: str, tags: list[str]) -> tuple[str | None, list[dict]]:
    """不同公司用不同标签；选最近披露最新的那个标签。"""
    best, best_facts = None, []
    for t in tags:
        facts = concept_facts(cik, t)
        if facts and (not best_facts or facts[-1]["end"] > best_facts[-1]["end"]):
            best, best_facts = t, facts
    return best, best_facts
