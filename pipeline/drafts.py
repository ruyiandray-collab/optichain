"""AI 初稿：业务描述归纳、可比性差异初稿、传导机制解释（产品要求第 5 节“AI 做”的三项）。

只依据给定的“事实”（字段值、已校验引文、规则结论）写，每份初稿都过确定性校验：
1. 初稿里的数字必须出现在输入事实里（不允许 LLM 新增或换算数字）
2. 引用的事实编号必须存在
3. 不能出现投资建议措辞（复用 config/compliance.yaml）
4. 长度上限
不通过的整份丢弃并记录原因。页面上标“AI 初稿”，并列出所依据的事实。
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

from pipeline.extract import api_key, _call_llm, MODEL, CACHE
from pipeline.attribution import SEG_BY_ID

ROOT = Path(__file__).resolve().parent.parent
STAGING = ROOT / "data" / "staging"
RULES = yaml.safe_load((ROOT / "config" / "compliance.yaml").read_text(encoding="utf-8"))
PROMPT_VERSION = "draft-v1"
SYSTEM = """你是卖方研究所的分析助理，负责写“初稿”，由研究员审核。
只能使用给定的【事实】，每句话后面用 [F编号] 标出依据。
严格要求：
1. 不得出现事实里没有的数字，不得自行计算、换算、估计；能不用数字就不用数字。
2. 不得给出买卖、目标价、仓位、涨跌判断。
3. 不确定的地方直接写“事实不足”。
4. 输出 JSON：{"draft":"...","used":["F1","F3"]}。"""
TASKS = {
    "company": "用不超过 120 字归纳这家公司在 AI 光互联产业链里做什么、证据强度如何。",
    "compare": "用不超过 160 字写这组中外公司的可比性初稿：先写为何可比，再写最关键的 2–3 个不可比之处。",
    "edge": "用不超过 120 字解释这条传导路径：外部变量如何影响该环节，证据支持到什么程度，数据对照是否支持。",
}
LIMITS = {"company": 160, "compare": 220, "edge": 160}


COUNT_AFTER = re.compile(r"\s*(条|家|项|名|位|次|句|类|组|步|档|种|方面|季度|个季度|个月|个环节|个变量|个公司)")
UNIT = re.compile(r"\s*(个百分点|pp|%|亿元|亿美元|万亿元|万亿|亿|百万美元|百万元|百万|万元|万美元|万|美元|元|倍|x|年|月|日|季度|Q)", re.I)
UNIT_CANON = {"个百分点": "pp"}
UNIT_FAMILY = {"亿元": "亿", "亿美元": "亿", "万亿元": "万亿", "百万美元": "百万", "百万元": "百万", "万元": "万", "万美元": "万"}
MODIFIER = re.compile(r"(超过|超|逾|近|约|接近|将近|不到|不足|高达|仅|达)\s*$")
QUANT = re.compile(r"(翻了?[一二两三四五六七八九十\d]+(番|倍)|翻番|翻倍|[一二两三四五六七八九十]+倍|百分之[零一二三四五六七八九十百点]+|"
                   r"[一二三四五六七八九十两]+成|[零一二三四五六七八九十百千两]+(亿|万|元|倍|个百分点))")
METRIC_GROUPS = [["营收", "营业收入", "收入"], ["毛利率"], ["净利率"], ["净利润", "归母净利"], ["毛利"], ["占比", "比例", "份额"],
                 ["市值"], ["市盈率", "PE"], ["市销率", "PS"], ["Capex", "资本开支", "资本性支出"], ["出口"], ["汇率", "中间价"],
                 ["研发费用"], ["同比"], ["环比"], ["敏感性"], ["境外"]]
FORMAT_CHARS = re.compile("[" + "".join(f"{chr(a)}-{chr(b)}" for a, b in
                                         [(0xad, 0xad), (0x200b, 0x200f), (0x2028, 0x202f), (0x2060, 0x206f), (0xfeff, 0xfeff)]) + "]")
_SENT = re.compile(r"[^。！!？?\n]+[。！!？?]?(?:\s*\[F\d+\])*")  # 按句号级切分（不按分号）：实测发现分号切分会误拦


def normalize(s: str) -> str:
    """全角转半角（NFKC）、清除零宽字符、去千分位（与问答校验一致，审查发现过这些绕过）。"""
    t = FORMAT_CHARS.sub("", unicodedata.normalize("NFKC", s or ""))
    return re.sub(r"(\d),(?=\d{3}\b)", r"\1", t).replace("−", "-")


def _metric(before: str) -> str | None:
    best, pos = None, -1
    for g in METRIC_GROUPS:
        for w in g:
            i = before.rfind(w)
            if i > pos:
                best, pos = g[0], i
    return best


def tokens(s: str) -> list[dict]:
    t = normalize(s)
    out = []
    for m in re.finditer(r"([+-])?(\d+(?:\.\d+)?)", t):
        if re.match(r"-[KQF](?![A-Za-z])", t[m.end():m.end() + 3]) or re.match(r"[A-Za-z]", t[m.end():m.end() + 1] or " ") and \
                re.match(r"[A-Za-z]", t[max(0, m.start() - 1):m.start()] or " "):
            continue  # 10-K / 20-F 等表单编号、字母夹着的代码不是数字事实（实测：“仅10-K”被误判）
        um = UNIT.match(t, m.end())
        u = um.group(1) if um else ""
        unit = UNIT_CANON.get(u, u.lower())
        before = re.split(r"[，,；;。：:、（(/]", t[max(0, m.start() - 14):m.start()])[-1]  # 只在同一分句内找修饰词 / 指标
        mm = MODIFIER.search(before)
        out.append({"raw": m.group(0), "value": float(m.group(2)), "sign": m.group(1) or "", "unit": unit,
                    "family": UNIT_FAMILY.get(u, unit), "modifier": mm.group(1) if mm else "", "metric": _metric(before),
                    "count": bool(re.fullmatch(r"\d", m.group(2)) and (COUNT_AFTER.match(t, m.end()) or before.endswith("第")))})
    return out


def numbers(s: str) -> list[str]:
    body = re.sub(r"\[F\d+\]", " ", s)
    return [f"{k['modifier']}{k['raw']}{k['unit']}" for k in tokens(body) if not k["count"]] + \
        [m.group(0) for m in QUANT.finditer(normalize(body))]


def number_set(text: str) -> set:
    out = set()
    for k in tokens(text):
        for u in {k["unit"], k["family"], "*"}:
            out.add((k["value"], u))
            if k["sign"]:
                out.add((k["sign"], k["value"], u))
            if k["modifier"]:
                out.add(("mod", k["modifier"], k["value"], u))
            if k["metric"]:
                out.add(("metric", k["metric"], k["value"], u))
    return out


def _token_ok(known: set, k: dict) -> bool:
    u = k["unit"] or "*"
    if (k["value"], u) not in known:
        return False
    for s, o in (("+", "-"), ("-", "+")):
        if k["sign"] == s and (s, k["value"], u) not in known and (o, k["value"], u) in known:
            return False
    if k["modifier"] and ("mod", k["modifier"], k["value"], u) not in known:
        return False
    if k["metric"] and ("metric", k["metric"], k["value"], u) not in known:
        return False
    return True


NO_INFO = re.compile(r"事实不足|没有相关|无法判断|不提供|不构成投资建议|未给出|未提供|没有给出|未披露")


def validate(kind: str, draft: str, used: list[str], facts: list[dict]) -> list[str]:
    """逐句校验（交叉核对后的版本）：
    - 每个实质性句子都必须自带 [F编号]（不含数字的断言也要有依据）
    - 数字按数值 + 单位（区分币种）+ 正负号 + 修饰词 + 所属指标，与该句引用的事实比对
    - 中文数字、倍数表达必须原样出现在该句引用的事实里
    - 不能有投资建议 / 提示注入措辞；长度上限"""
    errs = []
    by_id = {f["id"]: f for f in facts}
    cited_all = set(re.findall(r"\[(F\d+)\]", draft))
    if not cited_all:
        errs.append("没有在句子里标注依据 [F编号]")
    if cited_all - set(by_id):
        errs.append(f"引用了不存在的事实 {sorted(cited_all - set(by_id))}")
    for sent in _SENT.findall(normalize(draft)):
        ids = [i for i in re.findall(r"\[(F\d+)\]", sent) if i in by_id]
        body = re.sub(r"\[F\d+\]", " ", sent)
        plain = re.sub(r"[。；;！!？?\s]", "", body)
        if len(plain) >= 10 and not ids and not NO_INFO.search(body):
            errs.append(f"句子没有标注依据：{sent.strip()[:40]}")
            continue
        if not ids:
            continue
        src = " | ".join(by_id[i]["text"] for i in ids)
        known = number_set(src)
        for k in tokens(body):
            if not k["count"] and not _token_ok(known, k):
                errs.append(f"数字 {k['modifier']}{k['sign']}{k['raw'].lstrip('+-')}{k['unit']}"
                            f"{'（' + k['metric'] + '）' if k['metric'] else ''} 与该句引用的事实 {ids} 不符")
        for m in QUANT.finditer(normalize(body)):
            if m.group(0) not in normalize(src):
                errs.append(f"中文数字 / 倍数表达 {m.group(0)} 不在该句引用的事实 {ids} 中")
    nd = normalize(draft)
    for r in RULES["rules"]:
        if r["id"] in ("investment_advice", "prompt_injection") and any(
                re.search(p, nd, re.I) or re.search(p, re.sub(r"\s+", "", nd), re.I) for p in r["patterns"]):
            errs.append("含投资建议措辞")
    if len(re.sub(r"\[[^\]]*\]", "", draft)) > LIMITS[kind]:
        errs.append("超出长度上限")
    return errs


def _ask(messages: list[dict], api: str) -> tuple[dict, str]:
    cid = hashlib.sha1(f"{MODEL}|{PROMPT_VERSION}|{json.dumps(messages, ensure_ascii=False)}".encode()).hexdigest()[:12]
    cf = CACHE / f"draft_{cid}.json"
    resp = json.loads(cf.read_text(encoding="utf-8")) if cf.exists() else None
    if not resp or resp["choices"][0].get("finish_reason") != "stop":
        resp = _call_llm(messages, api)
        CACHE.mkdir(parents=True, exist_ok=True)
        cf.write_text(json.dumps(resp, ensure_ascii=False), encoding="utf-8")
    return resp, cid


def _parse(resp: dict) -> tuple[str, list]:
    try:
        out = json.loads(resp["choices"][0]["message"].get("content") or "{}")
    except json.JSONDecodeError:
        out = {}
    return str(out.get("draft", "")), out.get("used") or []


def draft_one(kind: str, key: str, subject: str, facts: list[dict], api: str) -> dict:
    """生成 → 确定性校验 → 不通过时把具体错误反馈给模型修改一次 → 再校验；仍不通过就丢弃并记录。"""
    facts = [{**f, "id": f"F{i + 1}"} for i, f in enumerate(facts)]
    user = f"任务：{TASKS[kind]}\n对象：{subject}\n【事实】\n" + "\n".join(f"[{f['id']}] {f['text']}" for f in facts)
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]
    resp, cid = _ask(messages, api)
    draft, used = _parse(resp)
    errs = validate(kind, draft, used, facts) if draft else ["空输出"]
    attempts = [{"llm_call_id": cid, "draft": draft, "errors": errs}]
    if errs:
        repair = messages + [{"role": "assistant", "content": resp["choices"][0]["message"].get("content") or ""},
                             {"role": "user", "content": "初稿没有通过校验：" + "；".join(errs) +
                              "。请修改：每句末尾都要有 [F编号]；只使用该句所引事实里原样出现的数字；不确定就写“事实不足”。仍按 JSON 输出。"}]
        resp, cid = _ask(repair, api)
        draft, used = _parse(resp)
        errs = validate(kind, draft, used, facts) if draft else ["空输出"]
        attempts.append({"llm_call_id": cid, "draft": draft, "errors": errs})
    return {"kind": kind, "key": key, "status": "ok" if not errs else "rejected", "errors": errs,
            "draft": draft if not errs else None, "rejected_draft": draft if errs else None, "attempts": attempts,
            "facts": [{"id": f["id"], "text": f["text"], "ref": f.get("ref")} for f in facts],
            "model": resp.get("model"), "prompt_version": PROMPT_VERSION, "llm_call_id": cid, "label": "AI 初稿"}


def _pct(v):
    return None if v is None else f"{v * 100:.1f}%"


def jobs() -> list[tuple]:
    def load(n):
        return json.loads((STAGING / n).read_text(encoding="utf-8"))
    links = load("concept_links.json")
    members = {m["entity"]: m for m in load("memberships.json")}
    recs = [json.loads(l) for l in (STAGING / "records.jsonl").read_text(encoding="utf-8").splitlines()]
    sec_q = load("sec_quotes.json")
    comps = load("comparables.json")
    trans = load("transmission.json")
    names = {"optical_interconnect": "光互联", "cpo": "CPO"}
    J = []
    import yaml as _y
    U = _y.safe_load((ROOT / "config" / "universe.yaml").read_text(encoding="utf-8"))
    for c in U["cn"]:
        facts = []
        prof = next((r for r in recs if r["dataset"] == "cn_profile" and r["entity"] == c["code"] and r["field"] == "主营业务"), None)
        if prof:
            facts.append({"text": f"主营业务（iFinD）：{prof['value']}", "ref": {"call_id": prof["source"]["call_id"]}})
        for ms in members.get(c["code"], {}).get("memberships", []):
            seg_name = SEG_BY_ID.get(ms["segment"], {}).get("name", ms["segment"])
            facts.append({"text": f"环节归属：{seg_name}（{ms['role']}，依据：{ms['basis']}"
                                  + (f"，收入占比 {_pct(ms['share'])}" if ms.get("share") is not None else "") + "）"})
        for l in links:
            if l["entity"] == c["code"]:
                facts.append({"text": f"{names[l['concept']]}可信度等级 {l['grade']}（规则裁定，依据：{l['basis']}）"})
                b = l.get("best")
                if b:
                    facts.append({"text": f"引文（{b.get('source_name', '')}，{b.get('date')}）：{b['quote']}", "ref": {"evidence_id": b["evidence_id"]}})
        J.append(("company", c["code"], c["name"], facts))
    for g in U["global"]:
        qs = [q for q in sec_q if q["entity"] == g["code"]][:5]
        if qs:
            J.append(("company", g["code"], g["name"], [{"text": f"10-K（{q['topic']}）：{q['quote']}", "ref": {"evidence_id": q["evidence_id"]}} for q in qs]))
    for grp in comps:
        facts = [{"text": f"为何可比·{x['dim']}：{x['text']}"} for x in grp["comparable"][:8]] + \
                [{"text": f"不可比·{x['dim']}：{x['text']}"} for x in grp["non_comparable"]]
        J.append(("compare", grp["id"], grp["name"], facts))
    for e in trans["edges"]:
        if e.get("derived"):
            continue
        facts = [{"text": f"机制：{e['mechanism']}（方向 {e['sign']}，时滞 {e['lag']}）"},
                 {"text": f"置信度 {e['confidence']}：{e['confidence_rule']}"}]
        facts += [{"text": f"{'支持' if s['stance'] == 'support' else '反驳'}证据（{s['source_name'][:30]}，{s.get('date')}）：{s['quote']}",
                   "ref": {"evidence_id": s["evidence_id"]}} for s in e["support"][:4]]
        dc = e.get("data_check")
        if dc and dc.get("results"):
            r0 = dc["results"][0]
            facts.append({"text": f"数据对照：{dc.get('y', '')}，同期同比相关系数 r={r0['r']:.2f}（n={r0['n']}）；{dc['method']}"
                          if r0["r"] is not None else f"数据对照：样本不足（n={r0['n']}）"})
        J.append(("edge", e["id"], e["mechanism"][:30], facts))
    # 防止内部标识（module / order 等）漏进中文初稿
    import re as _re
    for _, _, _, facts in J:
        for f in facts:
            f["text"] = _re.sub(r"(order|rnd|talk)", lambda m: {"order": "订单/量产", "rnd": "研发/送样", "talk": "仅表态"}[m.group(1)], f["text"])
    return J


def run(workers: int = 6) -> dict:
    api = api_key()
    if not api:
        raise RuntimeError("DEEPSEEK_API_KEY 未设置")
    J = jobs()
    with ThreadPoolExecutor(workers) as ex:
        res = list(ex.map(lambda j: draft_one(*j, api), J))
    out = {"company": {}, "compare": {}, "edge": {}}
    for r in res:
        out[r["kind"]][r["key"]] = r
    (STAGING / "drafts.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"total": len(res), "ok": sum(r["status"] == "ok" for r in res),
            "rejected": [(r["key"], r["errors"]) for r in res if r["status"] != "ok"]}


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
