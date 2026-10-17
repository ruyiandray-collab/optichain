# 测试运行结果（自动生成）

- 生成时间：2026-10-17T20:13:26+08:00
- 生成脚本：`python scripts/test_report.py`

| 套件 | 退出码 | 通过 | 失败 | 跳过 |
|---|---|---|---|---|
| 管道（pytest） | 0 | 128 | 0 | 0 |
| 前端（Vitest） | 0 | 24 | 0 | 0 |
| 问答后端（node --test） | 0 | 14 | 0 | 0 |

## 管道（pytest）

命令：`-m pytest -q -rs --tb=line`（目录 `optichain`）

```
........................................................................ [ 56%]
........................................................                 [100%]
128 passed in 7.62s
```

## 前端（Vitest）

命令：`npx.cmd vitest run`（目录 `web`）

```
RUN  v3.2.7 C:/Users/ruyia/Desktop/Claude/optichain/web

 ✓ src/data.test.ts (8 tests) 28ms
 ✓ src/app.test.ts (16 tests) 2339ms
   ✓ 所有页面引用指向真实快照，数字与源值匹配  672ms
   ✓ 全部公司真实财务、归属、概念轨迹可渲染且引用有效  1148ms

 Test Files  2 passed (2)
      Tests  24 passed (24)
   Start at  21:13:36
   Duration  3.56s (transform 317ms, setup 0ms, collect 501ms, tests 2.37s, environment 0ms, prepare 359ms)
```

## 问答后端（node --test）

命令：`node --test functions-tests/qa.test.mjs`（目录 `web`）

```
✔ 投资建议类问题被拦截 (1.8134ms)
✔ 要求全文被拦截 (1.1139ms)
✔ 实时行情问题被拦截 (0.1468ms)
✔ 正常研究问题不被拦截 (0.368ms)
✔ 检索命中公司卡片 (39.2076ms)
✔ 覆盖率：研究问题 ≥ 0.3，明显未覆盖 < 0.3（阈值校准用例） (47.6939ms)
✔ 答案中的数字必须出现在上下文里 (2.5626ms)
✔ 逐句数字引用：数字必须出现在本句所引文档里 (0.8501ms)
✔ 按 IP 限流：60 秒内第 7 次被拒，窗口过后恢复 (2.1012ms)
✔ 审查发现的绕过方式都被拦住（回归） (0.9076ms)
✔ 独立审查列出的合规绕过问法全部拦截（归一化后匹配） (0.4128ms)
✔ 独立审查列出的数字绕过全部拦截 (1.2926ms)
✔ 交叉核对（astra）给出的反例全部拦截 (1.1627ms)
✔ 实测误拦的三种正常回答现在通过（且反例仍被拦） (0.3276ms)
ℹ tests 14
ℹ suites 0
ℹ pass 14
ℹ fail 0
ℹ cancelled 0
ℹ skipped 0
ℹ todo 0
ℹ duration_ms 225.8532
```
