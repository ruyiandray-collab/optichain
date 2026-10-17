"""一键刷新：ingest → evidence → extract/grade → SEC 文本 → metrics → comparables → transmission → checks → snapshot。

用法：
  python -m pipeline.run_all            # 全量（iFinD 和 LLM 都走缓存，只有新请求才会真实调用）
  python -m pipeline.run_all --refresh  # 强制重新调用 iFinD（定时任务用）
  python -m pipeline.run_all --offline  # 不调用 iFinD，只用 staging 已有数据重建快照（SEC / LLM 优先读缓存，缓存缺失才联网）
每一步的耗时和结果写入 data/staging/run_log.jsonl。
"""
from __future__ import annotations

import argparse
import json
import time
import traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "data" / "staging" / "run_log.jsonl"
CST = timezone(timedelta(hours=8))


def main(refresh: bool = False, offline: bool = False) -> int:
    from pipeline import ingest, evidence, m2, sec_text, metrics, comparables, transmission, checks, build_snapshot, drafts
    steps = [] if offline else [
        ("ingest", lambda: ingest.run(refresh=refresh)),
        ("evidence", lambda: evidence.collect()),
        ("evidence_vars", lambda: evidence.collect_variables()),
        ("extract_grade", lambda: m2.run(use_llm=True)),
        ("sec_text", lambda: sec_text.run()),
    ]
    steps += [
        ("grade", lambda: m2.run(use_llm=False)),
        ("metrics", lambda: {"companies": len(metrics.build()["companies"])}),
        ("comparables", lambda: {"groups": len(comparables.build())}),
        ("transmission", lambda: transmission.build()["stats"]),
        ("drafts", lambda: drafts.run()),
        ("checks", lambda: checks.run()["summary"]),
        ("snapshot", lambda: build_snapshot.build()),
    ]
    run_id = datetime.now(CST).strftime("%Y%m%d-%H%M%S")
    LOG.parent.mkdir(parents=True, exist_ok=True)
    for name, fn in steps:
        t0 = time.time()
        rec = {"run_id": run_id, "step": name, "started_at": datetime.now(CST).isoformat(timespec="seconds")}
        try:
            out = fn()
            rec.update(status="ok", seconds=round(time.time() - t0, 1),
                       result=json.loads(json.dumps(out, ensure_ascii=False, default=str))
                       if not isinstance(out, dict) or len(json.dumps(out, default=str)) < 4000 else "（结果过长，略）")
        except Exception as e:  # 任何一步失败都停止，不发布半成品快照
            rec.update(status="failed", seconds=round(time.time() - t0, 1), error=f"{type(e).__name__}: {e}",
                       trace=traceback.format_exc()[-1500:])
            with LOG.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            print(f"[failed] {name}: {rec['error']}")
            return 1
        with LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"[ok] {name} {rec['seconds']}s", flush=True)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--offline", action="store_true")
    a = ap.parse_args()
    raise SystemExit(main(a.refresh, a.offline))
