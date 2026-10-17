# OptiChain · AI 算力光互联产业研究台

> 从产业环节走到原始证据：区分“有收入 / 有订单 / 在研发 / 仅表态”，每个数字都能回查到接口调用记录。
> **不构成投资建议。数据非实时**：行情和估值截至快照的数据截止日，财务截至最新报告期，页面顶部常驻提示。

- 线上地址：**https://optichain.pages.dev**（Cloudflare Pages；问答接口为 Pages Function `/api/ask`，无需登录）
- 文档：[数据探测](docs/data_probe.md) · [方案](docs/proposal.md) · [风险清单](docs/risks.md) · [需求核对](docs/requirements_check.md) · [测试说明](docs/test_plan.md) · [测试结果](docs/test_results.md) · [线上问答检查](docs/live_qa_results.md) · [AI 使用与验证记录](docs/ai_log.md) · [数据契约](docs/snapshot_schema.md)

## 目标用户与要解决的问题
卖方 / 买方研究员和产业投资者，在研究“AI 算力光互联”（光模块 / CPO）时常遇到三个问题：
1. **这家公司到底在产业链哪个环节？** 行业分类往往不准（例如中际旭创在申万口径下是“通信网络设备及器件”），需要看分产品收入。
2. **概念股里谁真有业务？** CPO 概念有 225 只成分股，概念解析里混着“年报有收入”和“互动易里说在关注”。
3. **外部变化会传到谁身上？** 云厂商 Capex、美国管制 / 关税、光芯片紧缺、铜价、汇率，分别影响哪些环节、哪些公司的哪些指标；上一轮发生同类变化之后，这些公司的财务实际怎么走的。

## 核心设计
| 模块 | 做法 |
|---|---|
| 产业链口径 | 自建分层：上中下游 → 环节 → 子环节 → 公司（iFinD 没有产业链接口）。按“收入来自什么产品”归属，不按行业代码。归属证据分 4 档：1 分产品收入 > 2 公告 / IR > 3 研报·资讯 > 4 官网（本版未采集）。一家公司可以挂多个环节，按收入占比标主次 |
| 概念可信度 A–E | A 有收入（分产品收入，给金额和占比）/ B 有订单·产能（正式公告、募投）/ C 研发·送样（年报研发项目、调研纪要）/ D 仅表态（只在互动平台或媒体转述里出现，非正式来源上限 D）/ E 无证据或否认（记录检索范围和时间）。**等级由规则函数裁定**；LLM 只给引文、建议等级和理由，两者不一致时标“待人工复核”；有冲突时保留双方证据并降一级展示 |
| 中外可比 | 4 个可比组（光模块、光芯片、光器件、电芯片）。为何可比：产品线、客户、技术节点、环节位置（都带证据）。不可比：财年、会计准则、币种、业务结构 / 代工、并表范围、上市地估值。口径调整：海外财年标签映射成真实区间，统一 TTM、统一 USD（标注汇率月份），每一步都能展开 |
| 传导路径 | 7 个外部变量 → 环节 → 公司 → 指标。每条边有方向、机制、时滞、已校验的支持 / 反驳引文、规则计算的置信度；“事件对照”列出 Capex 加速、政策事件之后各公司营收和毛利率的实际变化（历史对照，不做预测） |
| 持续更新 | `python -m pipeline.run_all`：ingest → evidence → extract → grade → metrics → comparables → transmission → drafts → checks → snapshot。每次生成带版本号和时间戳的快照，并和上一版做 diff（等级变动、数字变化、新证据），在“更新与校验”页展示 |
| 可追溯 | 每个数字都是 Value（value / formula / inputs），inputs 指回 iFinD 调用记录（call_id、列名、行）或 SEC accn。页面上点数字可以逐层展开 |

## AI 的角色（代码里有对应的边界）
| AI 做 | 实现 | 约束 |
|---|---|---|
| 证据抽取（公告、资讯、概念解析、10-K） | `pipeline/extract.py`、`pipeline/sec_text.py`（DeepSeek） | 引文必须逐字出现在原文里，否则丢弃并写入 `extract_rejects.jsonl` |
| 建议等级与理由 | 同上（prompt v2） | 最终等级由 `pipeline/grade.py` 的规则裁定；不一致时待复核 |
| 业务描述归纳、可比性差异初稿、传导机制解释 | `pipeline/drafts.py` | 只依据给定事实；初稿里的数字必须出现在事实中，依据编号必须存在，不能有投资建议措辞，否则整份丢弃；页面标“AI 初稿” |
| 传导边的支持 / 反驳证据 | `pipeline/transmission.py` | 逐字校验，并且必须提到该变量本身的关键词 |
| 问答 | `web/functions/api/ask.js` | 只在快照内检索；投资建议 / 全文 / 实时行情问题在调用 LLM 之前就拦截；回答里含数字的句子必须带引用，且数字要出现在被引用的文档里；检索不到就说不知道 |

**AI 不做**：任何数字计算（TTM、占比、折算、相关系数都由代码计算）、最终等级裁定、投资判断。

## 数据使用
| 来源 | 用途 | 说明 |
|---|---|---|
| iFinD MCP（同花顺，HTTP） | A 股财务、分产品 / 分地区收入、估值、概念成分与概念解析、公告和资讯片段、港美股财务、宏观指标（汇率、海关出口、铜价） | 自然语言取数会漂移，所以用固定问句模板 + 列名校验；429 被包在成功返回里，要单独识别；超过 100 行会被截断 |
| SEC EDGAR XBRL / 10-K | 海外营收、毛利、Capex 的一手数据（与 iFinD 交叉核对），以及 10-K Item 1 业务描述 | iFinD 和 SEC 冲突时以 SEC 为准，冲突记录保留并展示 |
| DeepSeek（deepseek-flash） | 引文抽取、建议等级、AI 初稿、问答 | key 只走环境变量 / 注册表，不进仓库 |
| 扶摇 | **未接入**（本次会话中没有可用的接口），见“未做事项” | — |

公告和研报只展示片段和引用链接（公告没有 URL，用巨潮全文检索页代替），不分发全文。iFinD 原始返回（`data/cache/`、`data/probe_raw/`）不进仓库。仓库里有：调用元数据（`api_calls.jsonl`）、由 iFinD 返回解析出的规范化记录（`records.jsonl`，字段值 + call_id，用于数字溯源测试）、证据片段（`evidence_raw.jsonl`，每段 ≤ 约 500 字）和快照。**公开仓库前要确认 iFinD 数据的授权范围**；如果不允许，可以把 `data/staging/` 移出版本控制，只保留快照。

## 架构与部署
```
本机（iFinD 密钥只在本机）
  python -m pipeline.run_all  →  web/public/data/*.json（带版本号的快照）+ data/snapshots/<版本>/ 存档
  cd web && npm run build      →  web/dist
  npx wrangler pages deploy    →  Cloudflare Pages（静态页面 + functions/api/ask.js）
```
iFinD 接口能否从海外 CI 机器访问没有验证，所以**持续更新采用“本机定时任务跑管道 → 生成快照 → 推送部署”**，没有放在 GitHub Actions 上。线上问答需要在 Pages 项目里设置 `DEEPSEEK_API_KEY` 这个 secret。

## 运行
```bash
pip install -r requirements.txt
python -m pytest -q                      # 管道测试
python -m pipeline.run_all --offline     # 不调用 iFinD，用 staging 数据重建快照
python -m pipeline.run_all               # 全量（需要 iFinD 密钥：IFIND_CONFIG 指向 mcp_config.json）
cd web && npm install && npm run build && npx vitest run
node web/scripts/pages-dev.mjs           # 本地预览（静态页面 + 问答接口）
python scripts/test_report.py            # 跑全部测试，生成 docs/test_results.md
```

## 目录
```
config/     universe.yaml（公司全集）· segments.yaml（产业链口径、子环节、词表）· comparables.yaml · transmission.yaml · compliance.yaml
pipeline/   ifind.py · tables.py · fiscal.py · ingest.py · sec.py · checks.py · evidence.py · extract.py · grade.py · attribution.py
            m2.py · sec_text.py · metrics.py · comparables.py · transmission.py · drafts.py · qa_index.py · build_snapshot.py · run_all.py
web/        src/（React 前端）· functions/（问答接口）· functions-tests/ · public/data/（快照）
tests/      管道单测、主链路端到端、数字溯源
docs/       见顶部链接
```

## 已知边界
- **数据覆盖**：26 家 A 股 + 12 家海外公司全量分析；CPO 概念 225 只成分股只用了 iFinD 概念解析一个来源（标“单一来源”，不可能给 A）。
- **研报**：iFinD 没有研报库，只有资讯转载（大摩、高盛等的报道），作为第 3 档证据，权重低于公告。
- **互动易原文**：拿不到原文，只有 iFinD 概念解析和媒体的转述，来源会如实标注。
- **光芯片价格**：指标库里没有，只能用上游公司毛利率、公告文本作代理变量。
- **海外分部收入**：iFinD 不提供，10-K 也没有按光模块 / 光芯片口径拆分，所以海外公司只有整体口径，并在可比表里列为不可比项。
- **传导的数据对照**：样本只有 10 个季度左右，只做历史对照，不做因果推断。例如云厂商 Capex 同比增速和中际旭创营收同比增速，逐季相关系数接近 0，页面如实展示。
- **问答**：覆盖率阈值是经验值；快照资讯里顺带提到的公司会进入 LLM，靠拒答和数字校验兜底（见测试说明）。
- **分级复核**：标“待复核”的条目，需要研究员人工确认。

## 未做事项
- 扶摇数据源接入（会话中没有可用接口），以及用它做 A 股的第二来源交叉核对。
- 公司官网证据（第 4 档）。
- 线上问答的限流目前是单实例内存计数（同一 IP 每分钟 6 次），实例之间不共享；更严格的限流需要 KV / Durable Objects。
- GitHub Actions 定时更新（iFinD 能否在海外 CI 访问没有验证）。
- 待复核条目的人工复核记录（工作流已预留：`needs_review` + `review_items`）。
