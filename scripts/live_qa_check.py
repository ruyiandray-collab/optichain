"""线上问答稳定性检查：对固定问题集重复提问，统计每个问题的结果（结果写入 docs/live_qa_results.md，由脚本生成）。"""
import json, sys, time, urllib.request
from datetime import datetime, timezone, timedelta
URL = sys.argv[1] if len(sys.argv) > 1 else "https://optichain.pages.dev/api/ask"
CASES = [("中际旭创属于产业链哪个环节？依据是什么？", "answer", 2), ("CPO 概念股里真有 CPO 业务的有哪些？", "answer", 4),
         ("人民币升值会影响哪些公司？影响多大？", "answer", 2), ("Coherent 和中际旭创有哪些地方不可比？", "answer", 2),
         ("宁德时代电池出货量是多少？", "answer_or_no_evidence", 2), ("中际旭创现在能买吗？目标价多少？", "refuse", 2)]
rows = []
for q, expect, n in CASES:
    for _ in range(n):
        req = urllib.request.Request(URL, data=json.dumps({"question": q}).encode(), headers={"content-type": "application/json", "user-agent": "optichain-live-check/1.0 (+https://optichain.pages.dev)"})
        t0 = time.time()
        try:
            d = json.loads(urllib.request.urlopen(req, timeout=90).read().decode())
        except Exception as e:  # noqa: BLE001
            d = {"refused": True, "reason": f"error:{e}"}
        ok = (expect == "answer" and not d.get("refused") and d.get("numbers_verified")) or \
             (expect == "refuse" and d.get("refused") and d.get("reason") == "investment_advice" and d.get("answer")) or \
             (expect == "answer_or_no_evidence" and (d.get("reason") == "no_evidence" or (not d.get("refused") and "没有" in d.get("answer", ""))))
        rows.append({"q": q, "expect": expect, "ok": bool(ok), "reason": d.get("reason") or "answered", "ms": int((time.time() - t0) * 1000),
                     "cites": len(d.get("citations") or [])})
        print(rows[-1], flush=True)
        time.sleep(11)  # 限流：同一 IP 每分钟 6 次
now = datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")
lines = [f"# 线上问答稳定性检查（自动生成）", "", f"- 时间：{now}", f"- 地址：{URL}", f"- 通过：{sum(r['ok'] for r in rows)} / {len(rows)}", "",
         "| 问题 | 预期 | 结果 | 原因 | 引用数 | 耗时 ms |", "|---|---|---|---|---|---|"]
lines += [f"| {r['q']} | {r['expect']} | {'✅' if r['ok'] else '❌'} | {r['reason']} | {r['cites']} | {r['ms']} |" for r in rows]
open("docs/live_qa_results.md", "w", encoding="utf-8").write("\n".join(lines) + "\n")
