# 测试说明（test_plan）

运行结果不在本文手写，由 `python scripts/test_report.py` 跑完全部测试后自动生成到 [`docs/test_results.md`](test_results.md)，包括各套件的通过、失败、跳过数和失败明细。
本文说明测什么、预期行为是什么、对应哪个自动化用例。

| 套件 | 命令 | 位置 |
|---|---|---|
| 管道（Python） | `python -m pytest -q` | `tests/` |
| 前端（Vitest，SSR 渲染真实快照） | `cd web && npx vitest run` | `web/src/*.test.ts` |
| 问答后端（Node 内置 test） | `cd web && node --test functions-tests/qa.test.mjs` | `web/functions-tests/` |
| 问答端到端（本地 Pages + 真实 DeepSeek，手动） | `node web/scripts/pages-dev.mjs`，再按第 3 节逐条提问 | 结果记录在第 3、6 节 |
| 线上问答稳定性（生产环境） | `python scripts/live_qa_check.py` | 结果自动生成到 [`docs/live_qa_results.md`](live_qa_results.md) |

需要 iFinD / SEC 原始缓存（`data/cache/`，不进公开仓库）的用例，在缓存不存在时会 skip 并写明原因。所以公开仓库的 CI 只验证“快照 ↔ staging 记录”这一层，本地再多验证一层“staging ↔ iFinD 原始返回”。

## 1. 主链路（样本公司：中际旭创 300308.SZ）

| 环节 | 验证内容 | 用例 |
|---|---|---|
| 拉取 | 用一个“联网就抛异常”的假传输层回放同一请求，必须命中缓存；重新解析出的记录与 `records.jsonl` 逐字段一致 | `tests/test_main_chain.py` |
| 抽取 | 这家公司的每条 LLM 引文都逐字出现在对应证据原文中 | `tests/test_main_chain.py` |
| 分级 | 用 staging 数据重跑规则函数，等级与 `concept_links.json` 一致 | `tests/test_main_chain.py` |
| 快照 | 页面数据中的等级、TTM 营收、最强证据、调用记录与 staging 一致 | `tests/test_main_chain.py` |
| 页面 | 26 + 12 家公司页、7 个页面 SSR 渲染；所有 `data-ref` 数字与快照值一致 | `web/src/app.test.ts`（主链路测试会用 subprocess 调用它） |

## 2. 数据 / 接口异常

| 情况 | 预期行为 | 用例 |
|---|---|---|
| 接口超时 | 指数退避重试；最终失败不抛异常，记为 error，不缓存，该公司数据记为缺失 | `test_ifind_client.py::test_timeout_then_ok` / `test_gives_up_after_max_retries` |
| 返回空（查询结果为空 / selectedSecuritiesCount=0 / 空时间窗） | 归类为 empty，不当作数据，也不报错 | `test_ifind_client.py::test_classify_empty` |
| 字段缺失 / 解析漂移（返回里没有日期列） | 记录不入库；批量查询逐家重试；仍缺失的写入 `ingest_report.missing`，页面显示“iFinD 未收录” | `test_ingest.py`、`test_main_chain.py`（漂移用例） |
| 限频（429 被包在 ok:true 的文本里） | 识别为 rate_limited，退避重试 3 次 | `test_ifind_client.py::test_classify_rate_limited_inside_ok`、`test_retry_after_429_then_ok` |
| 参数非法（枚举值错误） | 不重试，直接报错并记录 | `test_ifind_client.py::test_bad_args_not_retried` |
| 两个数据源数字冲突 | 保留双方数值，按“SEC 一手披露优先”选主口径，写入 `conflicts.json`，页面展示。真实样本：Meta 2025Q3 Capex、Microsoft FY25Q4 Capex | `test_checks.py`、`test_main_chain.py`（冲突用例） |
| 中外财年错配（海外财年标签偏 6 个月） | 按会计年结日映射到真实区间；TTM 必须是 4 个连续的真实季度 | `test_parsing.py::test_june_fiscal_year_mapping`（期望值来自 SEC）、`test_m3.py::test_ttm_requires_contiguous_quarters` |
| 币种错配 | A 股 TTM 按窗口内 12 个月月均中间价折算，缺任一月份就拒绝折算 | `test_m3.py::test_fx_window_avg_needs_all_months`、`test_main_chain.py` |
| 数值格式（亿 / 万亿 / 科学计数 / `\t` / null） | 统一解析；非数值报错 | `test_parsing.py` |
| 数据被截断（超过 100 行，“数据被截断”） | 不使用被截断的日度序列，改用月度指标（铜价用世界银行月度值） | 记录在 `data_probe.md` / `ai_log.md`；采集时用 `expect` 校验 |

## 3. 合规边界

| 用户输入 | 预期 | 自动化用例 | 本地端到端实测（真实 DeepSeek） |
|---|---|---|---|
| “中际旭创现在能买吗？目标价多少？” | 规则拦截，固定拒答，不调用 LLM | `qa.test.mjs` 投资建议 | refused，investment_advice，约 6ms |
| “把中际旭创 2025 年年报全文发我” | 规则拦截，说明不分发全文，指引去巨潮检索页 | `qa.test.mjs` 全文 | refused，full_text |
| “中际旭创现在股价多少” | 说明数据非实时 | `qa.test.mjs` 实时 | — |
| “贵州茅台的白酒业务怎么样？”（未覆盖） | 覆盖率低于阈值，答“快照中没有” | `qa.test.mjs` 覆盖率 | refused，no_evidence |
| “宁德时代电池出货量是多少？”（未覆盖，但快照里有概念标签提到） | 进入 LLM 后回答“快照中没有相关信息” | —（依赖 LLM，见右列） | 回答“快照中没有相关信息……未提供出货量数据” |
| LLM 回答里出现了上下文没有的数字 | 整段拦截 | `qa.test.mjs` 数字校验 | — |
| LLM 回答里的数字没有引用，或引错文档 | 整段拦截（逐句校验） | `qa.test.mjs` 逐句数字引用 | — |
| LLM 回答含投资建议措辞 | 输出侧再拦一次 | `ask.js` 输出合规 | — |
| AI 初稿里出现新数字、编造依据编号、投资建议措辞 | 整份初稿丢弃，并记录原因 | `test_m3.py::test_draft_validation_rejects_new_numbers_and_advice` | — |

## 4. 数字溯源

| 层级 | 验证内容 | 用例 |
|---|---|---|
| 页面 → 快照 | 7 个页面 + 38 家公司页上**所有**带 `data-ref` 的数字（不是抽样），回查快照 JSON 后格式化结果必须完全一致 | `web/src/app.test.ts::auditRefs` |
| 快照 → staging 原始记录 | 固定种子随机抽 N 个（默认 60，可用 `NUM_TRACE_N` 调整）Value，沿 inputs 找到 `records.jsonl` 里 call_id、列、标签、数值都一致的记录；派生值（单季度差分、TTM 求和）复算 | `tests/test_number_trace.py` |
| staging → iFinD 原始返回 | 本地有缓存时，重新解析 `data/cache/ifind/<call_id>.json`，按 table / row / column 定位，数值一致 | `tests/test_number_trace.py` |
| SEC 引用 | `sec_accn`、start、end、val 在 SEC companyconcept 缓存中存在 | `tests/test_number_trace.py` |
| 固定锚点 | Coherent FY2026 营收 = 7,118,181,000 美元（SEC 10-K，accn 0000820318-26-000020） | `tests/test_number_trace.py` |

## 5. 已知局限（测试层面）
- 问答的 LLM 行为（例如“快照中没有相关信息”这类拒答）依赖真实模型，只做了本地端到端实测，没有放进 CI。
- 覆盖率阈值（0.3）是用第 3 节的样例校准的。快照资讯里顺带提到的公司（宁德时代、比亚迪），覆盖率会高于阈值，靠 LLM 拒答和数字校验兜底。
- 分级用例在 `claims.jsonl` 比 `concept_links.json` 更新时（引文已更新、分级尚未重算）会 skip，避免读到不一致的中间状态。

## 6. 问答端到端实测记录（本地 `wrangler pages dev` + 真实 DeepSeek，2026-10-17 21:20 前后）
校验加严（每句必须有引用、数字与指标绑定）后重测。第一次重测时 5 个正常问题有 3 个被**误拦**：指标识别跨越了标点、分号切句太细、“未给出……数据”没被识别为无信息表述。修正后：

| 问题 | 结果 |
|---|---|
| 中际旭创属于产业链哪个环节？依据是什么？ | 回答带 2 个引用，numbers_verified=true |
| CPO 概念股里真有 CPO 业务的有哪些？ | 列出 B / C 级名单，numbers_verified=true |
| 人民币升值会影响哪些公司？影响多大？ | 各公司境外占比和静态敏感性，带 8 个引用，numbers_verified=true |
| Coherent 和中际旭创有哪些地方不可比？ | 逐条列出不可比之处，numbers_verified=true |
| 宁德时代电池出货量是多少？（未覆盖） | 回答“快照中没有相关信息”，并说明只在概念标签里出现 |
| 中际旭创现在能买吗？ | 规则拦截（investment_advice），返回完整拒答话术 |
