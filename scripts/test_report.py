"""跑全部测试套件，把真实结果写进 docs/test_results.md（数字由程序生成，不手填）。"""
from __future__ import annotations

import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
NPX = "npx.cmd" if sys.platform == "win32" else "npx"
SUITES = [
    ("管道（pytest）", [sys.executable, "-m", "pytest", "-q", "-rs", "--tb=line"], ROOT),
    ("前端（Vitest）", [NPX, "vitest", "run"], WEB),
    ("问答后端（node --test）", ["node", "--test", "functions-tests/qa.test.mjs"], WEB),
]


def run(cmd, cwd):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       shell=(sys.platform == "win32" and cmd[0].endswith(".cmd")), timeout=900)
    return p.returncode, re.sub(r"\x1b\[[0-9;]*m", "", p.stdout + p.stderr)


def summarize(name, out):
    if "pytest" in name:
        m = re.findall(r"(\d+) (passed|failed|skipped|error)", out.splitlines()[-1] if out.strip() else "")
        skips = [l for l in out.splitlines() if l.startswith("SKIPPED")]
        return {k: int(v) for v, k in m}, skips
    if "Vitest" in name:
        m = re.search(r"Tests\s+(?:(\d+) failed \| )?(\d+) passed", out)
        return ({"passed": int(m.group(2)), "failed": int(m.group(1) or 0)} if m else {}), []
    p = re.search(r"ℹ pass (\d+)", out)
    f = re.search(r"ℹ fail (\d+)", out)
    return {"passed": int(p.group(1)) if p else 0, "failed": int(f.group(1)) if f else 0}, []


def main():
    now = datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")
    lines = [f"# 测试运行结果（自动生成）", "", f"- 生成时间：{now}", f"- 生成脚本：`python scripts/test_report.py`", "",
             "| 套件 | 退出码 | 通过 | 失败 | 跳过 |", "|---|---|---|---|---|"]
    details = []
    for name, cmd, cwd in SUITES:
        code, out = run(cmd, cwd)
        s, skips = summarize(name, out)
        lines.append(f"| {name} | {code} | {s.get('passed', 0)} | {s.get('failed', 0) + s.get('error', 0)} | {s.get('skipped', 0)} |")
        tail = "\n".join(out.strip().splitlines()[-25:])
        details += [f"## {name}", "", f"命令：`{' '.join(cmd[1:] if cmd[0] == sys.executable else cmd)}`（目录 `{cwd.name}`）", ""]
        if skips:
            details += ["跳过原因：", ""] + [f"- {s}" for s in skips] + [""]
        details += ["```", tail, "```", ""]
    (ROOT / "docs" / "test_results.md").write_text("\n".join(lines + [""] + details), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
