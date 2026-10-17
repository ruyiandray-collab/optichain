# 快照数据契约（web/public/data/*.json）

前端只读这些静态文件。**前端不计算任何新数字**，只做格式化（单位换算显示，如 元→亿元、USD→十亿美元、ratio→%）。
每个展示的数字元素都要带 `data-ref="<文件名>#<JSON 路径>"`，例如 `data-ref="companies.json#/12/metrics/ttm/revenue/value"`，
数字溯源测试会抓取页面上所有带 data-ref 的元素，回查快照并比对。

## Value（所有数字的通用结构）
```json
{"value": 9318455131.8, "unit": "CNY|USD|ratio|x|%", "period_start": "2025-07-01", "period_end": "2026-06-30",
 "fiscal": "2026Q2", "label": "20260630", "formula": "Σ 最近 4 个单季度revenue", "inputs": [ ... ]}
```
`inputs` 里的元素要么是原始数据引用（含 `call_id`、`column`、`field`、`label`、`value`），要么是中间结果。`formula` 是给人看的计算说明。
**计算步骤展开**：点击数字 → 显示 formula + inputs；inputs 里有 `call_id` 的，可以再点开 `calls.json[call_id]`（工具、参数、调用时间、耗时）。

## meta.json
`version`、`generated_at`、`data_cutoff`（行情 / 估值截止日）、`financial_latest_period`、`realtime:false`、`disclaimer`、`previous_version`、`sources[]`、`counts`。
页面顶部常驻：`非实时 · 数据截止 {data_cutoff} · 财务截至 {financial_latest_period} · 快照 {version}`，以及免责声明。

## taxonomy.json
`layers[]: {id, name, segments[]: {id, name, desc, product_keywords[], generic_items[]}}`；`item_overrides[]`；`grade_rules`（等级、阶段词、否认词、概念定义）。用于“方法”页展示口径。

## companies.json（数组）
```
{code, name, market: "cn"|"global", role: "core"|"concept_sample"|"demand",
 profile: {公司中文名称, 所属申万行业, 主营业务, 可比公司},               # 仅 A 股
 primary_segment: "module"|..., 
 attribution: {label, revenue, revenue_source, items[]: {name, revenue, rank, segment, kind: specific|override|generic|none, keyword, share, source},
               memberships[]: {segment, role: 主|次|minor|候选, share|null, share_upper?, basis, evidence[]: evidence_id, items[]}},
 grades: {optical_interconnect|cpo: {grade: A-E, basis, needs_review, conflict?, a_detail?, best?, trace[]: {evidence_id, quote, stage, rule_stage, llm_stage, formal, stale, grade, date, source_name, review?}, search_scope?[] }},
 metrics: {code, currency, fye, quarters[]: {end, start, fiscal, label, revenue: Value, gross_profit?: Value, gross_margin?: Value, net_profit?: Value},
           ttm: {window, quarters[], revenue, gross_profit, net_profit, gross_margin: Value}, ttm_usd: {revenue, gross_profit, net_profit: Value, fx?},
           valuation: {字段名: Value}, market_cap_usd: Value, ps_ttm: Value},
 sec_cik?}
```
- 等级 A–E 的含义见 taxonomy.grade_rules；`needs_review=true` 的要显示“待复核”标记；`conflict` 要同时展示否认引文。
- `grade == "E"` 且 `basis == "无证据"` 时要展示 `search_scope`（检索了哪些 query、时间窗），**不能留空**。

## evidence.json（对象，键为 evidence_id）
`{evidence_id, entity, source_kind: notice|news|ifind_concept, origin_kind: periodic|ir|announcement|clarification|hdy|hdy_media|media|unspecified, source_name, date, text, url, url_kind: 原文|检索页|null, call_id, query, retrieved_at}`
证据抽屉必须显示：来源类型（中文）、来源名称、日期、原文片段（高亮 trace 里的 quote）、链接（url_kind=检索页时标注“巨潮检索页”）、接口调用记录（calls.json）。

## calls.json（对象，键为 call_id）
`{call_id, source, server, tool, params, called_at, ms, status, attempts, raw_sha256}`

## cpo_universe.json（数组，225 只 CPO 概念成分股）
`{entity, name, grade, basis, in_universe, single_source, needs_review, trace?[]}`。用于展示“概念股里真有业务的有多少”的分布，以及可筛选的列表。

## comparables.json（数组，可比组）
```
{id, name, segment, cn: [code], global: [code],
 comparable[]: {dim: 产品线|客户|技术节点|环节位置, text, refs[]: {kind: field|evidence|metric, ref}},
 non_comparable[]: {dim: 财年|会计准则|币种|业务结构|商业模式|上市地估值, text, values: {code: string}},
 table[]: {code, name, market, fye, accounting, currency, ttm_window: [start, end], revenue_ttm_usd: Value, gross_margin_ttm: Value,
           net_margin_ttm: Value, pe_ttm: Value, ps_ttm: Value, market_cap_usd: Value, focus_share: Value|null, focus_share_note},
 adjustments[]: {step, text}}
```

## transmission.json
```
{variables[]: {id, name, kind: demand|policy|supply|fx, desc, series?: {name, unit, source, freq, points[]: {date, value, ref}}, evidence[]: evidence_id},
 nodes[]: {id, kind: variable|segment|company|metric, label, ref?},
 edges[]: {id, from, to, sign: "+"|"-", mechanism, lag, confidence: 高|中|低, confidence_rule, support[]: {evidence_id, quote}, data_check?: {...}},
 exposures[]: {code, name, metric, value: Value, note},
 history[]: {id, title, x: {name, unit, points[]}, y: {name, unit, points[]}, note}}
```
交互：选一个变量 → 高亮它出发的路径（变量 → 环节 → 公司 → 指标），每条边可以点开看机制、时滞、置信度和证据；`history` 用双轴折线对照（例如云厂商季度 Capex vs 中际旭创单季营收）。

## checks.json / diff.json
- checks：`summary`（各校验通过数）、`conflicts[]`（iFinD vs SEC 冲突，含双方数值和 resolution）、`ingest_missing[]`。
- diff：`{previous_version, changes[]: {type: grade|number|new_company|new_evidence, ...}}`，用于“本次更新变化”页。

## v2 新增字段（需求对齐，2026-10-17）

### taxonomy.json
- `source_note`：口径来源说明；`adjustments[]: {what, why}`：我们的调整和理由；`evidence_tiers: {1..4: 说明}`（归属证据优先级：1 分产品收入 > 2 公告 / IR > 3 研报 / 资讯 > 4 官网，未采集）。
- `layers[].segments[].sub_segments[]: {id, name, keywords[]}`：子环节（环节 → 子环节 → 公司）。
- `grade_rules`：A–E 定义见 `pipeline/grade.py` 文档字符串。**非正式来源（互动平台 / 媒体转述）上限 D**；冲突时降一级（A 也降）；LLM 建议等级 ≠ 规则等级时标待复核，以规则为准。

### companies.json
- `attribution.memberships[]` 新增：`evidence_tier`（1/2/3/null）、`evidence[]`（evidence_id）、`sub_segments[]: {id, name, tier, basis, evidence_id?}`、`role` 可能是 `候选`。
- `grades.*.trace[]` 新增：`llm_grade`（LLM 建议等级 A–E）、`llm_reason`、`rule_stage`、`review`（不一致说明，“以规则为准”）。
- `grades.*.search_scope[]` 新增：`called_at`、`call_id`（检索时间）。
- `ai_summary`：`{label: "AI 初稿", draft, facts[]: {id, text, ref?}, model, prompt_version, llm_call_id}` 或 null（初稿未通过校验时为 null）。初稿里的 `[F1]` 对应 facts。

### comparables.json
- 新维度：`comparable[].dim` 可能是 `客户`、`技术节点`；`refs[].kind` 可能是 `record`（`{ref: call_id, column}`）。
- `non_comparable[].dim` 新增 `并表范围`。
- `table[]` 新增：`fx_basis: {kind: converted|native, text, rate?, months?[], inputs?[]}`（汇率日期与口径）、`market_cap_fx`（市值折算说明）、`net_margin_note`。
- `ai_draft`：同 ai_summary 结构，或 null。

### transmission.json
- 新变量 `copper`（铜价，世界银行月度，USD/t）、新边 `e_copper_substrate`、新的 history `h_copper_pcb`。
- `edges[].ai_explanation`：同 ai_summary 结构，或 null。
- `event_windows[]`：
  - `kind: "data_rule"`（Capex 加速）：`{id, variable, title, rule, trigger: {quarter, qoq, value, prev_value, ref}, window: [t-1, t, t+1, t+2], companies[]: {code, name, rows[]: {quarter, available, end?, revenue?, revenue_ref?（JSON Pointer）, gross_margin?, gross_margin_ref?, revenue_vs_t_minus_1?, gross_margin_pp_vs_t_minus_1?}}, note}`
  - `kind: "news_event"`（政策事件）：`{id, variable, title, date, evidence_id, evidence_ids[], reports, financial_available, financial_note?, window?, companies?, export_before[]: {month, value}, export_after[], export_note}`
  - `revenue_vs_t_minus_1` 是比例（0.6 = +60%），`gross_margin_pp_vs_t_minus_1` 是百分点。都由管道计算，前端只做格式化。

### checks.json
- `ai_drafts: {total, rejected[]: {kind, key, errors[]}}`：没通过校验的 AI 初稿及原因。

### 问答 /api/ask
- 新的 reason：`answer_blocked_uncited_numbers`（有数字没带引用，或引用的文档里没有这个数字），返回里附 `uncited_numbers[]: {sentence, number, cited[]}`。
