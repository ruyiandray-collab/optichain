# OptiChain · AI 算力光互联产业研究台

Vite + React + TypeScript 静态前端，无 UI 框架、无图表依赖。仅读取 `public/data/*.json`，不修改管道快照，不生成业务指标。所有工作文件均位于 `web/`。

## 开发、构建和测试

需要 Node.js 22.12+（本次验证使用 Node.js 24）。在仓库根目录执行：

```sh
cd web
npm install
npm run dev
```

默认开发地址为 `http://127.0.0.1:5173/`。默认使用 Vite/Rollup 监听构建，再提供本地预览；源码修改会重新构建，刷新浏览器查看。此方式避开当前 Windows 沙箱中 esbuild 依赖预打包访问上级目录时的 `Access is denied`。在普通开发环境中可使用 `npm run dev:hmr` 获得 Vite HMR。

```sh
npm run build
npx vitest run
npm run preview
```

产物输出到 `web/dist/`；Cloudflare Pages 的构建目录设为 `web`，命令为 `npm run build`，输出目录为 `dist`。页面使用 hash 路由，不需要服务端路由回退。开发和生产构建都只生成 `web/` 内的文件。

PowerShell 若禁止执行 `npm.ps1`，使用 `npm.cmd` / `npx.cmd`，例如 `npm.cmd run build`。本轮构建通过；前端 24 项 Vitest 测试通过（`src/app.test.ts` 16 项、`src/data.test.ts` 8 项）。`functions-tests/qa.test.mjs` 使用 Node 内置测试框架，单独执行 `node --test functions-tests/qa.test.mjs`，9 项通过。

当前沙箱中普通 `npx vitest run` 加载外部进程新增的 `vitest.config.ts` 时，esbuild 被拒绝读取工作区上级目录。使用以下等价加载方式可运行完整前端套件；本轮未修改该配置或后端测试：

```sh
npx vitest run --configLoader native
```

测试覆盖真实快照所有页面及全部公司数字溯源、全部事件窗口及默认最近规则事件、变化值正负号与百分点、未披露财报、新闻事件出口前后值、空 AI 初稿和缺失 v2 字段、AI 事实角标与来源入口、子环节和候选角色、LLM 分歧与检索时间、逐月汇率与净利率口径、初稿拒绝原因、问答未引用数字诊断。测试为 React 服务端渲染和数据契约检查；本轮没有新增浏览器端自动点击测试。

## 页面清单

| 路由 | 内容 |
| --- | --- |
| `#/` | 上中下游产业链、主/次/候选归属、子环节和依据、归属证据档位、收入占比/上限、海外公司、CPO 证据分布、原始校验数 |
| `#/company/:code` | AI 初稿及可点击事实角标、公司资料、子环节和证据档位、双概念规则等级及 LLM 建议/理由/待复核、检索时间及调用、收入命中明细、季度与 TTM、估值、证据/调用/计算链抽屉 |
| `#/concepts` | 境内研究公司双概念矩阵，等级/待复核/角色筛选，全量 CPO 可搜索名单 |
| `#/compare` | 产品/客户/技术节点依据、调用记录及 column、并表范围等不可比维度、逐行汇率口径/月份/输入、净利率说明、AI 可比性初稿、指标表与调整步骤 |
| `#/transmission` | 动态变量选择（含铜价）、分层 SVG 路径、所选边机制/方向/时滞/置信度/支持引文/AI 初稿、事件对照、敞口表、双轴历史图 |
| `#/changes` | 元数据、变更、校验、iFinD/SEC 差异及处理口径、采集缺失、AI 初稿总数及被拒原因 |
| `#/method` | 口径来源/调整/理由、四档归属证据优先级、A–E 含义与示例表、来源上限/冲突降级/人工复核规则、AI 边界、来源与局限 |
| `#/ask` | 快照问答输入、引用抽屉、拒答、未引用数字的句子/数字诊断表、接口不可用状态 |

顶部快照时间和版本常驻，可展开原始声明；页脚保留声明。深浅主题保存在本地。移动端使用 16px 边距、导航换行、表格转为逐行卡片，避免横向滚动。动画仅淡入，非匀速、时长轻微随机，并尊重 `prefers-reduced-motion`。抽屉支持 Escape、返回、焦点限制和背景 inert。

## data-ref 规范

- 每个业务数字元素直接绑定其标量来源，格式为 `文件名.json#/RFC6901/Pointer`。
- Value 对象指向 `/value`；原始数值直接指向字段，不能虚构额外 `/value`。
- 数组始终使用原始索引。筛选和排序先保留索引，再渲染。关键词中 `/`、`~` 分别转义为 `~1`、`~0`。
- 金额显示仅执行 `CNY → 亿元`、`USD → $B`；`ratio → %`；`%` 不重复乘百，`x → ×`。金额和比例保留两位小数。缺失值显示 `—`，零与负数保留。
- 事件对照的营收/毛利率直接使用 `revenue_ref` / `gross_margin_ref`；变化值直接引用 `transmission.json` 的预计算字段，以一位小数显示 `+x.x%` / `±x.x pp`，不在前端计算变化。默认选择最近的 `data_rule` 事件，可按变量筛选并切换新闻事件。未披露季度保持“未披露”，不补零、不预测。
- AI 初稿正文与事实文本分别指向 `draft` / `facts` 原字段；点击正文角标展开事实，事实中的 evidence_id / call_id 可打开对应抽屉。初稿为 null 或缺失时隐藏；缺少 LLM 建议时不推算等级。事件窗口、汇率或子环节缺失时显示空状态或省略相应模块。
- 日期、原始标签、代码、引文中的数字也绑定对应字符串字段。源字段缺失时不附加不存在的引用。
- 所有指标按钮打开原始 formula / inputs；输入中的 call_id 可打开工具、参数、时间、耗时和状态。原始标量没有 formula 时明确说明，尽可能展示同层来源，不补写推导。
- SVG 只计算绘图坐标、日期对齐和图遍历。轴上数值使用真实观测点；不生成增速、占比、相关性、汇总或预测。双轴分别缩放，图中明确提示。
- CPO 等级分布用每家公司一格的色带网格，按等级排列，直接引用各项 grade；不在浏览器统计等级数量。成分总数使用 meta.counts 中已有数值，校验概况直接使用 checks.summary。
- 单测 fixture 仅存在于 `src/__fixtures__/`，只能由单测导入。生产入口不导入 fixture；构建不会把 fixture 合并到应用。

`src/app.test.ts` 读取真实快照，对所有页面和全部公司渲染结果解析 data-ref，并回查数值；不是生产数据副本。

## 真实快照与契约的差异及处理

以下前九项保留早期前端开发的契约记录。早期版本从 `v20261017-1451` 更新到 `v20261017-1508`；本轮外部管道继续刷新到 `v20261017-1550`。前端没有写入 `public/data/`。空快照、旧字段和 v2 字段均有测试。

1. companies 实际为 38 家，其中境内 26 家有概念等级；海外公司没有 grades、中文 profile 和 memberships，仅有配置环节归属。海外展示配置依据和缺失占比，不推断等级或份额。
2. attribution、a_detail 的金额和占比是裸数值，不是 Value；字段可能缺少 formula / inputs。按实际字段指针显示，保留同层来源。
3. 部分 trace 是精简的 deny 记录，缺 date、source_name、rule_stage、llm_stage、stale；来源名和日期可从 evidence_id 对应的 evidence 补读，其余缺失显示未提供，不推断。
4. 海外 TTM 可能缺 gross_profit / gross_margin。quarter 的 Value 内 fiscal / period 常为 null，而季度外层有真实 start/end/fiscal/label；页面使用外层区间。fye 为 `0630` 等原始字符串。
5. ~~PE/PB 的 Value.unit 被记为 CNY 或 USD~~ —— 已在管道源头修正（PE / PB / PS 的单位为 `x`），见 docs/ai_log.md M4。
6. meta 增加 content_hash；calls 增加 error；checks.summary 是校验名到 pass/warn/fail/skipped 的嵌套对象，没有总通过率。全部按真实结构显示。
7. evidence 新增 `source_kind=sec_filing`，显示“SEC 原始披露”。url_kind 可能缺失；未提供链接时明确提示，有检索页时显示“巨潮全文检索页”。
8. comparables.refs 除 JSON Pointer 外，存在 `companies.json#code=300308.SZ/attribution`；点击时按公司代码定位并规范化为实际数组 Pointer。
9. transmission 的季度日期是 `2023Q1`，月度日期是 `2023-01`。绘图按这些标签对应的日历位置对齐，仍显示原始标签。points.ref 实际可能是 Value、`{call_id,...}` 或 `{path:'companies[代码].metrics.quarters[财季].revenue'}`，不是统一字符串；三种引用均支持回查。附加的 fx_sensitivity、stats、边上的 derived/stance 等以快照为准，不在前端重算。

本轮 v2 核对发现（基于上述刷新版本，后台继续运行时可能变化）：

- `comparables.comparable.refs[kind=record]` 引用的 `d2874f6b3804`、`28422346fd4f`、`d6eedc40f0d5` 不在 `calls.json`。按钮保留 column 并尝试打开调用；缺记录时明确提示。
- 归属/AI 事实引用中的 `ev_13fafb412f`、`ev_b008bffea9`、`ev_a112a95d71` 不在 `evidence.json`；证据抽屉显示当前快照缺失提示。
- AI 事实的调用引用 `161a97b0c1a8`、`44c268d6e139`、`78b9891c9cff`、`53c36d3666e0`、`539106f9506d`、`db383f9511c5` 不在 `calls.json`。
- `603083.SH` 的 CPO 中有三条精简否认轨迹未提供 `llm_grade` / `llm_reason`；保留规则等级和原引文，不生成建议等级。
- 产品核对表把铜价写为“沪铜连续”，v2 契约及实际快照为“iFinD EDB（世界银行）”月度铜价。页面依据快照展示来源，未改口径或数据。

## 问答接口与假设

请求为同源 `POST /api/ask`，JSON body 为 `{ "question": "..." }`。要求返回 answer、citations 数组和 refused 布尔值；reason 可选。HTTP 错误、非 JSON、结构错误或超时显示“问答服务暂不可用”，可重新提交。evidence_id 引用打开证据抽屉，ref 引用打开原始字段。

问答后端由独立进程维护，本轮未修改 `functions/`。静态预览需要另行接入同源 API 才能成功提问。拒答文本或带 `numbers_verified=true` 的回答按返回展示；未通过校验的普通回答将数字替换为“〔见引用〕”。新增 `answer_blocked_uncited_numbers` 的中文原因，并列出 `uncited_numbers` 中的“句子 / 数字”。

`uncited_numbers` 属于 API 拦截诊断，其数字可能根本不在快照里，无法合法指向 `*.json#Pointer`。诊断表明确标为未经证据验证，用 `data-response-ref="/uncited_numbers/..."` 对应本次响应字段，不伪造快照 `data-ref`。这是 API 诊断与静态快照数字契约之间尚未定义的部分；其余新增快照数字均使用有效 `data-ref`。

图遍历按变量 id 与对应 variable 节点 id 相同处理（真实数据符合）。空数组不等于零业务；E 级无证据仅表示所列检索范围内未找到证据。快照原始声明逐字保留。

## 验证范围与环境限制

浏览器已验证深浅主题、手机宽度无整页横向溢出、财务值→输入→调用记录、公司选择器引用回查、问答服务错误。真实可比和传导数据已通过渲染与引用测试；fixture 覆盖空状态与未来结构。未进行线上部署，未实现后端服务，未提交或推送 Git。

本环境曾拒绝读取工作区上级目录、下载额外 DOM 测试依赖及访问 npm 审计端点。最终不依赖该额外包，使用 React 服务端渲染进行结构/溯源测试，并用浏览器验证交互。安装、生产构建、Vitest 和默认监听预览均已通过；完整在线依赖安全审计未完成。
