"""M2 LLM 证据抽取（DeepSeek）。

AI 的职责仅限于：从一段原文里摘出“与该公司、该概念有关”的引文，并给出建议阶段和一句理由。
防幻觉：
- 引文必须逐字出现在原文中（去空白后做子串匹配），否则丢弃并写入 extract_rejects.jsonl
- 不让 LLM 输出任何数字或等级；最终等级由 grade.py 的规则函数裁定
- temperature=0，响应按 (模型, 提示词版本, evidence_id) 缓存，可复现
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

from pipeline.grade import CONCEPTS, mentions_concept

ROOT = Path(__file__).resolve().parent.parent
STAGING = ROOT / "data" / "staging"
CACHE = ROOT / "data" / "cache" / "llm"
API = "https://api.deepseek.com/chat/completions"
MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-flash")  # /models 实测：deepseek-flash, deepseek-v4-pro；对比试验见 ai_log（2026-10-17）
PROMPT_VERSION = "extract-v2"  # v2：增加建议等级 suggested_grade
MAX_TOKENS = int(os.environ.get("DEEPSEEK_MAX_TOKENS", "8000"))  # v4-pro 是推理模型，思考 token 计入 completion
STAGES = {"order", "rnd", "talk", "deny", "none"}

SYSTEM = """你是卖方研究所的证据摘录员。你只做摘录和建议，不做计算、不做投资建议；最终等级由规则程序裁定。
输入是一段关于某上市公司的原文（公告片段、资讯片段或数据库的概念解析），以及它的来源。
任务：找出原文中【直接描述该公司自身】与指定概念相关业务的句子，原样摘录，并给出建议阶段和建议等级。

严格要求：
1. quote 必须从原文逐字复制（可以截取句子的一部分，但不能改字、不能拼接不相邻的内容），长度 10–150 字。
2. 只摘录描述该公司自身业务的内容；行业综述、其他公司、券商观点、投资者提问本身都不算（about_company=false）。
3. stage 只能取：order（已量产/批量出货/订单/供货/放量/募投扩产）、rnd（研发/送样/小批量/验证中）、
   talk（关注/布局/可应用于/有望，没有实质进展）、deny（明确否认或尚未涉及）、none（与概念无关）。
4. suggested_grade 按下表给出（只依据这一段原文和它的来源类型）：
   A 有收入：财报分产品收入里有对应项（一般只有财报的分产品数据才能给 A）
   B 有订单/产能：正式公告（定期报告、公告、投资者关系记录）中的订单、客户认证、量产、扩产、定增/可转债募投
   C 有研发/送样：正式文件里的研发项目、送样、小批量
   D 仅表态：只在互动平台（互动易、e互动）或媒体转述中口头提及；或只有“关注/布局”
   E 无证据/否认：明确否认，或与概念无关
5. reason 不超过 30 字，说明给这个等级的依据。
6. 不要输出任何原文里没有的数字、客户名或推测。
7. 输出 JSON：{"claims":[{"concept":"cpo|optical_interconnect","quote":"...","about_company":true,"stage":"...","suggested_grade":"A|B|C|D|E","reason":"..."}]}。
   没有相关内容时输出 {"claims":[]}。"""


SOURCE_KIND_TEXT = {"periodic": "定期报告", "ir": "投资者关系记录", "announcement": "公司公告", "clarification": "澄清公告",
                    "hdy": "互动平台问答（经 iFinD 概念解析转述）", "hdy_media": "媒体转述的互动平台问答", "media": "媒体报道",
                    "unspecified": "数据库概念解析（来源未注明）"}


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def verify_quote(quote: str, text: str) -> bool:
    q = _norm(quote)
    return len(q) >= 6 and q in _norm(text)


def build_user_prompt(ev: dict, company: str) -> str:
    concepts = "；".join(f"{k}={v['name']}（关键词：{'、'.join(v['keywords'][:8])}）" for k, v in CONCEPTS.items())
    return (f"公司：{company}\n概念：{concepts}\n来源：{ev['source_name']}（{ev.get('date') or '日期未知'}）\n"
            f"原文：\n{ev['text']}")


def _call_llm(messages: list[dict], key: str) -> dict:
    for attempt in range(4):
        try:
            r = requests.post(API, headers={"Authorization": f"Bearer {key}"}, timeout=90, json={
                "model": MODEL, "messages": messages, "temperature": 0,
                "response_format": {"type": "json_object"}, "max_tokens": MAX_TOKENS})
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 500, 502, 503):
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"DeepSeek {r.status_code}: {r.text[:200]}")
        except requests.Timeout:
            time.sleep(2 ** attempt)
    raise RuntimeError("DeepSeek: retries exhausted")


def extract_one(ev: dict, company: str, key: str) -> tuple[list[dict], list[dict], dict]:
    user = build_user_prompt(ev, company)
    # 缓存键包含完整提示词：改提示词或公司名都会触发重新抽取
    cid = hashlib.sha1(f"{MODEL}|{PROMPT_VERSION}|{SYSTEM}|{user}".encode()).hexdigest()[:12]
    f = CACHE / f"{cid}.json"
    resp = json.loads(f.read_text(encoding="utf-8")) if f.exists() else None
    if not resp or resp["choices"][0].get("finish_reason") != "stop":  # 截断的响应不复用
        resp = _call_llm([{"role": "system", "content": SYSTEM},
                          {"role": "user", "content": user}], key)
        CACHE.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(resp, ensure_ascii=False), encoding="utf-8")
    content = resp["choices"][0]["message"].get("content") or ""
    if resp["choices"][0].get("finish_reason") != "stop":
        return [], [{"evidence_id": ev["evidence_id"], "reason": f"LLM 输出被截断（finish_reason={resp['choices'][0].get('finish_reason')}）"}],             {"llm_call_id": cid, "model": resp.get("model", MODEL), "prompt_version": PROMPT_VERSION, "usage": resp.get("usage", {})}
    meta = {"llm_call_id": cid, "model": resp.get("model", MODEL), "prompt_version": PROMPT_VERSION,
            "usage": resp.get("usage", {})}
    try:
        claims = json.loads(content).get("claims", [])
    except (json.JSONDecodeError, AttributeError):
        return [], [{"evidence_id": ev["evidence_id"], "reason": "LLM 输出不是合法 JSON", "raw": content[:300], **meta}], meta
    ok, rejects = [], []
    for c in claims if isinstance(claims, list) else []:
        quote = str(c.get("quote", ""))
        base = {"evidence_id": ev["evidence_id"], "concept": c.get("concept"), "quote": quote,
                "llm_stage": c.get("stage"), "llm_grade": c.get("suggested_grade") if c.get("suggested_grade") in ("A", "B", "C", "D", "E") else None,
                "llm_reason": c.get("reason"), "extractor": "llm", **meta}
        if c.get("concept") not in CONCEPTS:
            rejects.append({**base, "reason": "concept 不在枚举内"})
        elif c.get("stage") not in STAGES:
            rejects.append({**base, "reason": "stage 不在枚举内"})
        elif not verify_quote(quote, ev["text"]):
            rejects.append({**base, "reason": "引文不在原文中（疑似改写或幻觉）"})
        elif not c.get("about_company", False):
            rejects.append({**base, "reason": "LLM 判断与该公司自身业务无关"})
        elif c.get("stage") == "none":
            rejects.append({**base, "reason": "LLM 判断与概念无关"})
        else:
            ok.append({**base, "verified": True})
    return ok, rejects, meta


def api_key() -> str | None:
    """先读进程环境；Windows 上会话启动后才设置的用户级变量，从注册表 HKCU/Environment 读取。不打印、不落盘。"""
    key = os.environ.get("DEEPSEEK_API_KEY")
    if key:
        return key
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            return winreg.QueryValueEx(k, "DEEPSEEK_API_KEY")[0]
    except (ImportError, OSError):
        return None


def run(evidences: list[dict], names: dict[str, str], workers: int = 8) -> dict:
    key = api_key()
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY 未设置")
    # 预筛：原文没有任何概念关键词的证据不送 LLM（省钱，也避免 LLM 硬凑）
    todo = [e for e in evidences if any(mentions_concept(e["text"], c) for c in CONCEPTS)]
    claims, rejects, usage = [], [], {"prompt_tokens": 0, "completion_tokens": 0}
    with ThreadPoolExecutor(workers) as ex:
        for ok, rej, meta in ex.map(lambda e: extract_one(e, names.get(e["entity"], e["entity"]), key), todo):
            claims += ok
            rejects += rej
            for k in usage:
                usage[k] += meta.get("usage", {}).get(k, 0)
    STAGING.mkdir(parents=True, exist_ok=True)
    for name, rows in (("claims.jsonl", claims), ("extract_rejects.jsonl", rejects)):
        with (STAGING / name).open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return {"sent": len(todo), "skipped_no_keyword": len(evidences) - len(todo), "claims": len(claims),
            "rejects": len(rejects), "usage": usage,
            "reject_reasons": {r: sum(1 for x in rejects if x["reason"] == r) for r in {x["reason"] for x in rejects}}}
