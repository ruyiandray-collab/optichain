import { History } from './history';
import React, { createContext, useContext, useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { cachedData, canonicalReference, childRef, files, FileName, formatValue, loadData, resolvePointer, RecordData as D, safeUrl } from './data';
import './style.css';
type Overlay = {
    kind: 'evidence' | 'call' | 'value' | 'ref';
    id?: string;
    quote?: string;
    data?: any;
    ref?: string;
    unit?: string;
};
const Open = createContext<(overlay: Overlay) => void>(() => { });
const gradeNames: D = { A: '有收入', B: '订单量产', C: '研发送样', D: '仅表态', E: '否认或无证据' };
const conceptNames: D = { optical_interconnect: 'AI 光互联', cpo: '共封装光学 CPO' };
const REASONS: D = { answer_blocked_uncited_claims: '回答里有结论没有标注依据，已拦截', rate_limited: '提问太频繁，请稍后再试', prompt_injection: '请求要求绕过产品规则', answer_blocked_uncited_numbers: '回答里有数字没有对应引用（或引用的文档里没有这个数字），已拦截', investment_advice: '涉及投资建议，不在产品范围内', full_text: '不分发研报 / 公告全文', realtime: '数据非实时', no_evidence: '快照内没有相关证据', answer_blocked_numbers: '回答里有未在证据中出现的数字，已拦截', answer_blocked_advice: '回答含投资建议措辞，已拦截', llm_unavailable: '模型服务未配置', llm_error: '模型服务出错', empty: '问题为空', bad_request: '请求格式错误' };
const stages: D = { order: '订单 / 量产', rnd: '研发 / 送样', talk: '表态', deny: '否认', none: '无证据' };
REASONS.answer_blocked_uncited_numbers = '回答里有数字没有对应引用，已拦截';
const labels: D = { revenue: '营收', gross_profit: '毛利', gross_margin: '毛利率', net_profit: '净利润', net_margin: '净利率', window: '真实窗口', quarters: '财年季度', fx: '换汇口径', rate: '汇率', market_cap_usd: '总市值（USD）', ps_ttm: 'PS · TTM', pass: '通过', warn: '警告', fail: '失败', skipped: '跳过', cn_single_sum_eq_cumulative: '单季与累计一致', segment_产品_sum_eq_revenue: '产品分部收入一致', segment_地区_sum_eq_revenue: '地区分部收入一致', global_revenue_vs_sec: '海外营收交叉核验', capex_vs_sec: '资本开支交叉核验', ifind_abs: 'iFinD 原值', sec: 'SEC 原值', diff: '差额', resolution: '处理口径', source: '来源', called_at: '调用时间', ms: '耗时（毫秒）', status: '状态', attempts: '尝试次数', params: '参数', tool: '工具', column: '原始字段', label: '原始标签', start: '区间起点', end: '区间终点', fye: '会计年结日', formula: '计算说明', inputs: '输入明细', value: '原始值', call_id: '调用记录', search_scope: '检索范围', query: '检索式', confidence_rule: '置信规则' };
Object.assign(labels, {mechanism:'传导机制',sign:'方向',lag:'时滞',confidence:'置信度',data_check:'数据核验',entity:'实体',real_end:'真实截至日',months:'换汇月份',month:'月份',field:'原始字段',dataset:'数据集',table:'原始表索引',row:'原始行索引',data_source:'原始来源',freq:'频率',evidence_id:'证据记录',revenue_ttm_usd:'TTM 营收（USD）'});
Object.assign(labels,{gross_margin_ttm:'TTM 毛利率',net_margin_ttm:'TTM 净利率',pe_ttm:'PE · TTM（原始值）',focus_share:'聚焦业务占比',dim:'比较维度',text:'说明',values:'各公司取值',accounting:'会计准则',currency:'币种',ttm_window:'TTM 窗口',step:'调整步骤',fx_sensitivity:'静态汇率敏感性',server:'数据服务',error:'错误信息',raw_sha256:'原始响应校验摘要'});
function useData(name: FileName) {
    const [data, setData] = useState<any>(() => cachedData(name));
    const [error, setError] = useState('');
    const [retry, setRetry] = useState(0);
    useEffect(() => { let active = true; setData(undefined); setError(''); loadData(name).then(x => active && setData(x)).catch(e => active && setError(e.message)); return () => { active = false; }; }, [name, retry]);
    return { data, error, reload: () => setRetry(x => x + 1) };
}
function Pending({ state }: {
    state: ReturnType<typeof useData>;
}) { return <div className="empty" role="status">{state.error || '正在读取快照…'}{state.error && <button onClick={state.reload}>重新读取</button>}</div>; }
function Text({ value, at }: {
    value: any;
    at: string;
}) { return <span data-ref={value == null ? undefined : at}>{value === null || value === undefined ? '—' : String(value)}</span>; }
function Empty({ children = '该部分数据生成中 / 暂无数据' }: {
    children?: React.ReactNode;
}) { return <div className="empty">
<span className="empty-icon">◇</span>
<p>{children}</p>
<a href="#/changes">查看更新与校验 →</a>
</div>; }
function Badge({ grade, review, conflict, mr }: {
    grade?: string;
    review?: boolean;
    conflict?: any;
    mr?: any;
}) { return <span className="badges">
<span className={`badge grade-${grade || 'unknown'}`}>{grade ? `${grade} · ${gradeNames[grade] || grade}` : '未分级'}</span>{review && <span className="flag">{mr?.status === 'stale' ? '待复核 · 复核已过期' : '待复核'}</span>}{mr?.status === 'applied' && <span className="flag reviewed" title={`${mr.reviewer} ${mr.reviewed_at}`}>已人工复核</span>}{conflict && <span className="flag danger">证据冲突</span>}</span>; }
function Value({ data, at, unit }: {
    data: any;
    at: string;
    unit?: string;
}) {
    const open = useContext(Open);
    const structured = data && typeof data === 'object' && 'value' in data;
    const v = structured ? data.value : data;
    if (v === null || v === undefined)
        return <span className="muted">—</span>;
    return <button className="number" data-ref={structured ? `${at}/value` : at} onClick={() => open({ kind: 'value', data, ref: at, unit })} title="查看计算说明与输入">{formatValue(v, unit ?? data?.unit ?? '')}</button>;
}
Object.assign(labels,{method:'校验方法',results:'历史检验结果',lag_quarters:'领先季度',n:'样本数',r:'相关系数',same_direction_share:'同向比例（原始值）',mechanism_author:'机制审核',from:'起点',to:'终点'});
function CompanyKey({code}: {code:string}) {
  const state=useData('companies');const i=state.data?.findIndex((c:D)=>c.code===code);
  return i!=null&&i>=0?<Text value={code} at={`companies.json#/${i}/code`}/>:<span>公司取值</span>;
}
function Tree({ data, at }: {
    data: any;
    at: string;
}) {
    const open = useContext(Open);
    if (data === null || data === undefined)
        return <span className="muted">未提供</span>;
    if (typeof data === 'number')
        return <Value data={data} at={at}/>;
    if (typeof data !== 'object')
        return <Text value={data} at={at}/>;
    if ('value' in data && ('unit' in data || 'formula' in data) && typeof data.value !== 'object')
        return <div className="tree-value">
<Value data={data} at={at}/>{data.formula && <p data-ref={`${at}/formula`}>{data.formula}</p>}{data.inputs && <details>
<summary>输入明细</summary>
<Tree data={data.inputs} at={`${at}/inputs`}/>
</details>}</div>;
    return <div className="tree">{Object.entries(data).map(([k, v]) => <div className="tree-row" key={k}>
<span className="tree-key">{Array.isArray(data) ? '记录' : /\d/.test(k) ? <CompanyKey code={k}/> : labels[k] || k}</span>
<div>{(k === 'call_id' || k === 'evidence_id') && typeof v === 'string' ? <button className="link" onClick={() => open({ kind: k === 'call_id' ? 'call' : 'evidence', id: v })}>
<Text value={v} at={childRef(at, k)}/>
</button> : <Tree data={v} at={childRef(at, k)}/>}</div>
</div>)}</div>;
}
function TierBadge({ tier, at }: {tier?: number | null; at: string}) {
  const names: D = {1: '分产品收入', 2: '公告·IR', 3: '研报·资讯', 4: '官网（未采集）'};
  return <span className="badge tier">{tier != null ? <>第 <Text value={tier} at={at}/> 档 {names[tier] || '其他来源'}</> : '无证据'}</span>;
}
function MembershipEvidence({ membership: m, at }: {membership: D; at: string}) {
  const open = useContext(Open);
  return <div className="membership-evidence">
    <TierBadge tier={m.evidence_tier} at={`${at}/evidence_tier`}/>
    {m.evidence?.map((id: string, i: number) => <button className="link" key={id} onClick={() => open({kind:'evidence', id})}>归属依据<Text value={id} at={`${at}/evidence/${i}`}/></button>)}
    {m.sub_segments?.map((s: D, i: number) => <div className="sub-segment" key={s.id || i}>
      <strong><Text value={s.name} at={`${at}/sub_segments/${i}/name`}/></strong>
      <TierBadge tier={s.tier} at={`${at}/sub_segments/${i}/tier`}/>
      {s.basis && <p><Text value={s.basis} at={`${at}/sub_segments/${i}/basis`}/></p>}
      {s.evidence_id && <button className="link" onClick={() => open({kind:'evidence', id:s.evidence_id})}>查看子环节证据 →</button>}
    </div>)}
  </div>;
}
function AiDraft({ data, at }: {data?: D | null; at: string}) {
  const [active, setActive] = useState('');
  const open = useContext(Open);
  if (!data?.draft) return null;
  const facts: D[] = data.facts || [];
  return <section className="inset ai-draft" aria-label="AI 初稿">
    <h3>AI 初稿</h3>
    <p className="draft-text">{String(data.draft).split(/(\[F\d+\])/g).map((part, i) => {
      const id = /^\[(F\d+)\]$/.exec(part)?.[1];
      const index = facts.findIndex(f => f.id === id);
      return id && index >= 0 ? <sup key={i}><button className="link" aria-label={`查看依据 ${id}`} aria-expanded={active === id} onClick={() => setActive(id)}>[<Text value={id} at={`${at}/facts/${index}/id`}/>]</button></sup> : <Text key={i} value={part} at={`${at}/draft`}/>;
    })}</p>
    <p className="muted small">初稿中的数字均来自所列依据，等级由规则裁定</p>
    {facts.map((f, i) => <details key={f.id || i} open={active === f.id || undefined} onToggle={e => {if (!e.currentTarget.open && active === f.id) setActive('');}}>
      <summary><Text value={f.id} at={`${at}/facts/${i}/id`}/> · 依据事实</summary>
      <p><Text value={f.text} at={`${at}/facts/${i}/text`}/></p>
      {f.ref?.evidence_id && <button onClick={() => open({kind:'evidence', id:f.ref.evidence_id})}>打开证据 →</button>}
      {f.ref?.call_id && <button onClick={() => open({kind:'call', id:f.ref.call_id})}>打开调用记录 →</button>}
      {typeof f.ref === 'string' && <button onClick={() => open({kind:'ref', ref:f.ref})}>打开事实来源 →</button>}
    </details>)}
  </section>;
}
function Checks({ data }: {
    data: D;
}) { return <div className="check-grid">{Object.entries(data.summary || {}).map(([name, states]: [
    string,
    any
]) => <div className="check" key={name}>
<span>{labels[name] || name}</span>
<div>{Object.entries(states).map(([k, v]) => <span className={`check-${k}`} key={k}>{labels[k] || k} <Value data={v} at={childRef(childRef('checks.json#/summary', name), k)}/>
</span>)}</div>
</div>)}</div>; }
function UniverseDistribution({ data, meta }: {
    data: D[];
    meta?: D;
}) { return <section className="panel">
<div className="section-heading">
<h2>CPO 概念成分 · 证据分布</h2>{meta && <span>
<Text value={meta.counts?.['cpo_universe.json']} at="meta.json#/counts/cpo_universe.json"/> 只成分股</span>}</div>
<p className="muted">每格代表一家成分公司，颜色对应快照等级；悬停查看名称。</p>
<div className="distribution">{data.map((c, i) => ({ c, i })).sort((a, b) => a.c.grade.localeCompare(b.c.grade)).map(({ c, i }) => <a key={c.entity} className={`grade-${c.grade}`} href={`#/concepts?entity=${encodeURIComponent(c.entity)}`} title={`${c.name} · ${c.grade} ${gradeNames[c.grade]}`} aria-label={`${c.name}，${c.grade} ${gradeNames[c.grade]}`} data-ref={`cpo_universe.json#/${i}/grade`}>{c.grade}</a>)}</div>
<div className="legend">{Object.entries(gradeNames).map(([g, n]) => <Badge key={g} grade={g}/>)}</div>
<p className="muted small">颜色表示证据阶段，不表示企业质量排序。</p>
</section>; }
function Overview({ meta, taxonomy }: {
    meta: D;
    taxonomy: D;
}) {
    const companies = useData('companies'), universe = useData('cpo_universe'), checks = useData('checks');
    return <>
<div className="page-heading">
<div>
<p className="eyebrow">产业结构 / EVIDENCE FIRST</p>
<h1>从产业环节，走到原始证据。</h1>
<p>区分业务收入、研发进展与概念表态。每个数字均可回查。</p>
</div>
<a className="outline" href="#/concepts">查看概念可信度 ↗</a>
</div>
<div className="overview-stats">{universe.data ? <UniverseDistribution data={universe.data} meta={meta}/> : <Pending state={universe}/>}<section className="panel">
<div className="section-heading">
<h2>数据校验</h2>
<a href="#/changes">查看明细 ↗</a>
</div>{checks.data ? <Checks data={checks.data}/> : <Pending state={checks}/>}</section>
</div>{!companies.data ? <Pending state={companies}/> : <div className="chain">{taxonomy.layers.map((layer: D, li: number) => <section className="layer" key={layer.id}>
<h2>
<span className="layer-dot"/>{layer.name}<span className="muted small">{layer.id.toUpperCase()}</span>
</h2>{layer.segments.map((segment: D, si: number) => <div className="segment" key={segment.id}>
<h3>{segment.name}</h3>
<p className="segment-desc" data-ref={`taxonomy.json#/layers/${li}/segments/${si}/desc`}>{segment.desc}</p>
<div className="company-stack">{companies.data.flatMap((c: D, i: number) => { const memberships = c.attribution?.memberships || []; const matches = memberships.map((m: D, j: number) => ({ m, j })).filter(({ m }: {
        m: D;
    }) => m.segment === segment.id); if (c.market === 'global' && c.primary_segment === segment.id)
        matches.push({ m: { role: '海外', basis: c.attribution?.basis }, j: -1 }); return matches.map(({ m, j }: {
        m: D;
        j: number;
    }) => <article className="company-card" key={`${c.code}-${j}`}>
<a className="company-title" href={`#/company/${c.code}`}>
<strong>{c.name}</strong>
<Text value={c.code} at={`companies.json#/${i}/code`}/>
<span>↗</span>
</a>
<div className="membership">
<span className={`role ${m.role === '候选' ? 'candidate' : ''}`}>{m.role === 'minor' ? '次' : m.role}</span>{m.share != null ? <Value data={m.share} unit="ratio" at={`companies.json#/${i}/attribution/memberships/${j}/share`}/> : m.share_upper != null ? <span>占比上限 <Value data={m.share_upper} unit="ratio" at={`companies.json#/${i}/attribution/memberships/${j}/share_upper`}/>
</span> : <span className="muted">占比未提供</span>}</div>
<p className="small muted"><Text value={m.basis} at={j >= 0 ? `companies.json#/${i}/attribution/memberships/${j}/basis` : `companies.json#/${i}/attribution/basis`}/></p>{j >= 0 && <MembershipEvidence membership={m} at={`companies.json#/${i}/attribution/memberships/${j}`}/>}{c.grades && <div className="card-grades">
<span>光互联 <Badge grade={c.grades.optical_interconnect?.grade} review={c.grades.optical_interconnect?.needs_review} conflict={c.grades.optical_interconnect?.conflict} mr={c.grades.optical_interconnect?.manual_review}/>
</span>
<span>CPO <Badge grade={c.grades.cpo?.grade} review={c.grades.cpo?.needs_review} conflict={c.grades.cpo?.conflict} mr={c.grades.cpo?.manual_review}/>
</span>
</div>}</article>); })}</div>
</div>)}</section>)}</div>}</>;
}
function Trace({ trace, at, reviewed }: {
    trace: D;
    at: string;
    reviewed?: boolean;
}) { const open = useContext(Open); const evidenceState=useData('evidence'); const original=evidenceState.data?.[trace.evidence_id]; const fallbackAt=childRef('evidence.json#',trace.evidence_id||''); return <button className="trace" onClick={() => open({ kind: 'evidence', id: trace.evidence_id, quote: trace.quote })}>
<span className="trace-meta">
<Text value={trace.date??original?.date} at={trace.date!=null?`${at}/date`:`${fallbackAt}/date`}/>
<span>{trace.formal ? '正式来源' : '非正式来源'} · {trace.stale ? '陈旧' : '未标陈旧'}</span>
</span>
<strong>
<Text value={trace.source_name??original?.source_name} at={trace.source_name!=null?`${at}/source_name`:`${fallbackAt}/source_name`}/>
</strong>
<span>阶段 {stages[trace.stage] || trace.stage || '未提供'} · 规则 {stages[trace.rule_stage] || trace.rule_stage || '未提供'} · LLM {stages[trace.llm_stage] || trace.llm_stage || '未提供'}</span>
<span>规则等级 <Text value={trace.grade} at={`${at}/grade`}/>{trace.llm_grade && <> · LLM 建议等级 <Text value={trace.llm_grade} at={`${at}/llm_grade`}/></>}</span>
{trace.llm_reason && <span>LLM 理由：<Text value={trace.llm_reason} at={`${at}/llm_reason`}/></span>}
{(trace.review || (trace.llm_grade && trace.grade && trace.llm_grade !== trace.grade)) && (reviewed ? <span className="flag reviewed">已人工复核 · 见上方复核结论</span> : <span className="flag">待复核 · 待人工复核，以规则为准</span>)}
<blockquote>
<Text value={trace.quote} at={`${at}/quote`}/>
</blockquote>{trace.review && <span className="flag" data-ref={`${at}/review`}>{trace.review}</span>}<span className="link">查看原文与调用记录 →</span>
</button>; }
function GradeCard({ grade, name, at }: {
    grade: D;
    name: string;
    at: string;
}) { const mr = grade.manual_review; const done = mr?.status === 'applied'; return <section className="panel">
<div className="section-heading">
<h2>{name}</h2>
<Badge grade={grade.grade} review={grade.needs_review} conflict={grade.conflict} mr={grade.manual_review}/>
</div>
<p>{grade.basis}</p>{mr && <div className={mr.status === 'applied' ? 'inset' : 'notice'}>
<h3>{mr.status === 'applied' ? '人工复核结论' : '人工复核已过期'}</h3>
<p>复核人 <Text value={mr.reviewer} at={`${at}/manual_review/reviewer`}/> · <Text value={mr.reviewed_at} at={`${at}/manual_review/reviewed_at`}/> · 规则等级 <Text value={mr.rule_grade} at={`${at}/manual_review/rule_grade`}/> → 复核等级 <Text value={mr.final_grade} at={`${at}/manual_review/final_grade`}/></p>
<p><Text value={mr.reason} at={`${at}/manual_review/reason`}/></p>{mr.status === 'stale' && <p>过期原因：<Text value={mr.stale_reason} at={`${at}/manual_review/stale_reason`}/>；展示规则等级，等待重新复核。</p>}</div>}{grade.a_detail && <div className="inset">
<h3>收入命中依据</h3>
<div className="metric-grid">
<div>金额<Value data={grade.a_detail.amount} unit="CNY" at={`${at}/a_detail/amount`}/>
</div>
<div>占比<Value data={grade.a_detail.share} unit="ratio" at={`${at}/a_detail/share`}/>
</div>
<div>报告期<Text value={grade.a_detail.label} at={`${at}/a_detail/label`}/>
</div>
</div>
<details>
<summary>命中项目及来源</summary>
<Tree data={grade.a_detail.items} at={`${at}/a_detail/items`}/>
</details>
</div>}{grade.best && <>
<h3>最强证据</h3>
<Trace trace={grade.best} at={`${at}/best`} reviewed={done}/>
</>}{grade.conflict && <div className="conflict">
<h3>证据冲突 · 否认引文</h3>
<Tree data={grade.conflict} at={`${at}/conflict`}/>{grade.trace?.map((t:D,i:number)=>t.stage==='deny'?<Trace key={i} trace={t} at={`${at}/trace/${i}`}/>:null)}</div>}{grade.grade === 'E' && (grade.basis === '无证据' || grade.search_scope?.length) && <div className="inset">
<h3>已执行检索与时间窗</h3>{grade.search_scope?.length ? <Tree data={grade.search_scope} at={`${at}/search_scope`}/> : <p>快照未提供检索范围，待补充；无证据不代表不存在业务。</p>}</div>}<details>
<summary>全部证据轨迹</summary>{grade.trace?.length ? grade.trace.map((t: D, i: number) => <Trace key={i} trace={t} at={`${at}/trace/${i}`} reviewed={done}/>) : <p className="muted">暂无证据轨迹</p>}</details>
</section>; }
function Sparkline({ quarters, at }: {
    quarters: D[];
    at: string;
}) { const open = useContext(Open); const valid = quarters.map((q, i) => ({ q, i })).filter(({ q }) => typeof q.revenue?.value === 'number'); if (!valid.length)
    return null; const values = valid.map(({ q }) => q.revenue.value), min = Math.min(...values), max = Math.max(...values); const point = (v: number, i: number) => [30 + i * 700 / Math.max(1, valid.length - 1), 130 - (v - min) / Math.max(1, max - min) * 100]; return <figure className="spark">
<figcaption>单季度营收 · 点击数据点回查</figcaption>
<svg viewBox="0 0 760 160" role="img" aria-label="营收趋势图，具体值见下方季度表">
<path d={values.map((v, i) => `${i ? 'L' : 'M'}${point(v, i).join(',')}`).join(' ')} fill="none" stroke="var(--accent)" strokeWidth="2"/>{valid.map(({ q, i }, j) => { const [x, y] = point(q.revenue.value, j); return <g key={i} role="button" tabIndex={0} aria-label={`${q.fiscal} 营收 ${formatValue(q.revenue.value, q.revenue.unit)}`} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ')
    open({ kind: 'value', data: q.revenue, ref: `${at}/${i}/revenue` }); }} onClick={() => open({ kind: 'value', data: q.revenue, ref: `${at}/${i}/revenue` })}>
<circle cx={x} cy={y} r="9" fill="transparent"/>
<circle cx={x} cy={y} r="3" fill="var(--accent)"/>
<title data-ref={`${at}/${i}/fiscal`}>{q.fiscal}</title>
</g>; })}</svg>
</figure>; }
function Company({ code, taxonomy }: {
    code: string;
    taxonomy: D;
}) { const state = useData('companies'); if (!state.data)
    return <Pending state={state}/>; const i = state.data.findIndex((c: D) => c.code === code), c = state.data[i]; if (!c)
    return <Empty>快照中没有这家公司</Empty>; const at = `companies.json#/${i}`, m = c.metrics || {}; const segment = (id: string) => taxonomy.layers.flatMap((l: D) => l.segments).find((s: D) => s.id === id)?.name || id; return <>
<a href="#/" className="back">← 产业链总览</a>
<div className="page-heading">
<div>
<p className="eyebrow">公司研究 / {c.market === 'global' ? '海外' : '境内'}</p>
<h1>{c.name} <small>
<Text value={c.code} at={`${at}/code`}/>
</small>
</h1>
<p>{segment(c.primary_segment)} · {c.role === 'concept_sample' ? '概念观察样本' : c.role === 'demand' ? '需求方' : '核心研究'}</p>
</div>
</div>
<AiDraft data={c.ai_summary} at={`${at}/ai_summary`}/>
<section className="panel">
<h2>基本资料与环节归属</h2>{c.profile ? <Tree data={c.profile} at={`${at}/profile`}/> : <p className="muted">海外公司暂无中文 profile；环节归属依据配置。</p>}{c.attribution?.memberships?.map((membership: D, j: number) => <div className="membership-detail" key={j}>
<strong>{segment(membership.segment)} · <span className={`role ${membership.role === '候选' ? 'candidate' : ''}`}>{membership.role === 'minor' ? '次' : membership.role}</span></strong>
<span>{membership.share != null ? <>占比 <Value data={membership.share} unit="ratio" at={`${at}/attribution/memberships/${j}/share`}/>
</> : membership.share_upper != null ? <>占比上限 <Value data={membership.share_upper} unit="ratio" at={`${at}/attribution/memberships/${j}/share_upper`}/>
</> : '占比未提供'}</span>
<span><Text value={membership.basis} at={`${at}/attribution/memberships/${j}/basis`}/></span>
<MembershipEvidence membership={membership} at={`${at}/attribution/memberships/${j}`}/>
</div>)}{c.attribution?.basis && <p>{c.attribution.basis}</p>}{c.attribution?.items?.length > 0 && <>
<p className="muted">分产品报告期 <Text value={c.attribution.label} at={`${at}/attribution/label`}/>
</p>
<div className="table-wrap">
<table>
<thead>
<tr>
<th>项目</th>
<th>收入</th>
<th>占比</th>
<th>匹配方式 / 关键词</th>
<th>环节</th>
</tr>
</thead>
<tbody>{c.attribution.items.map((item: D, j: number) => <tr key={j}>
<td data-ref={`${at}/attribution/items/${j}/name`}>{item.name}</td>
<td data-label="收入">
<Value data={item.revenue} unit="CNY" at={`${at}/attribution/items/${j}/revenue`}/>
</td>
<td data-label="占比">
<Value data={item.share} unit="ratio" at={`${at}/attribution/items/${j}/share`}/>
</td>
<td>{item.kind} · {item.keyword || '—'}</td>
<td>{segment(item.segment) || '未匹配'}</td>
</tr>)}</tbody>
</table>
</div>
</>}</section>
<div className="two-col">{Object.entries(c.grades || {}).map(([key, g]) => <GradeCard key={key} grade={g as D} name={conceptNames[key] || key} at={`${at}/grades/${key}`}/>)}</div>
<section className="panel">
<div className="section-heading">
<h2>财务 · 真实区间</h2>
<span>记账币种 <Text value={m.currency} at={`${at}/metrics/currency`}/>
</span>
</div>{c.market === 'global' && <p className="notice">会计年结日 <Text value={m.fye} at={`${at}/metrics/fye`}/> · iFinD 财年标签已映射为真实区间，请以起止日期为准。</p>}<Sparkline quarters={m.quarters || []} at={`${at}/metrics/quarters`}/>
<div className="table-wrap">
<table>
<thead>
<tr>
<th>财年 / 原始标签</th>
<th>真实起止区间</th>
<th>营收</th>
<th>毛利率</th>
<th>净利润</th>
</tr>
</thead>
<tbody>{m.quarters?.map((q: D, j: number) => <tr key={j}>
<td data-label="财年 / 原始标签">
<Text value={q.fiscal} at={`${at}/metrics/quarters/${j}/fiscal`}/>
<small>
<Text value={q.label} at={`${at}/metrics/quarters/${j}/label`}/>
</small>
</td>
<td data-label="真实区间">
<Text value={q.start} at={`${at}/metrics/quarters/${j}/start`}/> ~ <Text value={q.end} at={`${at}/metrics/quarters/${j}/end`}/>
</td>{['revenue', 'gross_margin', 'net_profit'].map(k => <td key={k} data-label={labels[k] || k}>
<Value data={q[k]} at={`${at}/metrics/quarters/${j}/${k}`}/>
</td>)}</tr>)}</tbody>
</table>
</div>
</section>
<section className="panel">
<h2>TTM 与估值 · 点击数字查看计算链</h2>{['ttm', 'ttm_usd', 'valuation'].map(k => <div className="financial-block" key={k}>
<h3>{k === 'ttm' ? 'TTM · 原币' : k === 'ttm_usd' ? 'TTM · 美元口径' : '估值 · 原始字段'}</h3>{k === 'valuation' && <p className="notice">PE / PB 的快照 unit 被记录为货币；下方保留原始数值，不作货币换算，口径待复核。</p>}<div className="metric-grid">{Object.entries(m[k] || {}).map(([field, value]: [
    string,
    any
]) => <div key={field}>
<span>{labels[field] || field}</span>{value && typeof value === 'object' && 'value' in value ? <Value data={value} unit={/市盈率|市净率|PE\(|PB\(/i.test(field) ? '' : undefined} at={childRef(`${at}/metrics/${k}`, field)}/> : <details>
<summary>{field === 'fx' ? '查看换汇口径' : '查看区间与来源'}</summary>
<Tree data={value} at={childRef(`${at}/metrics/${k}`, field)}/>
</details>}</div>)}</div>
</div>)}<div className="metric-grid">{['market_cap_usd', 'ps_ttm'].map(k => <div key={k}>
<span>{labels[k]}</span>
<Value data={m[k]} at={`${at}/metrics/${k}`}/>
</div>)}</div>
</section>
</>; }
function Concepts({ meta }: {
    meta: D;
}) {
    const companies = useData('companies'), universe = useData('cpo_universe');
    const initial = new URLSearchParams(location.hash.split('?')[1]).get('entity') || '';
    const [query, setQuery] = useState(initial), [grade, setGrade] = useState(''), [review, setReview] = useState(false), [role, setRole] = useState('');
    const open = useContext(Open);
    return <>
<PageTitle title="概念可信度" text="有概念，不等于有业务。等级由快照规则裁定，疑点单独标记。"/>
<div className="filters">
<label>搜索公司<input value={query} onChange={e => setQuery(e.target.value)} placeholder="名称 / 代码"/>
</label>
<label>等级<select value={grade} onChange={e => setGrade(e.target.value)}>
<option value="">全部等级</option>{Object.entries(gradeNames).map(([g, n]) => <option key={g} value={g}>{g} · {n}</option>)}</select>
</label>
<label>角色<select value={role} onChange={e => setRole(e.target.value)}>
<option value="">全部角色</option>
<option value="core">核心研究</option>
<option value="concept_sample">概念观察样本</option>
</select>
</label>
<label className="checkbox">
<input type="checkbox" checked={review} onChange={e => setReview(e.target.checked)}/>仅待复核</label>
</div>
<section className="panel">
<h2>研究公司 · 双概念矩阵</h2>{companies.data ? <div className="table-wrap">
<table>
<thead>
<tr>
<th>公司 / 角色</th>{Object.values(conceptNames).map(n => <th key={n}>{n}</th>)}</tr>
</thead>
<tbody>{companies.data.map((c: D, i: number) => ({ c, i })).filter(({ c }: {
        c: D;
    }) => c.grades && (!role || c.role === role) && (!query || `${c.name}${c.code}`.toLowerCase().includes(query.toLowerCase())) && Object.values(c.grades).some((g: any) => (!grade || g.grade === grade) && (!review || g.needs_review))).map(({ c, i }: {
        c: D;
        i: number;
    }) => <tr key={c.code}>
<td>
<a href={`#/company/${c.code}`}>{c.name}</a>
<small>
<Text value={c.code} at={`companies.json#/${i}/code`}/> · {c.role === 'core' ? '核心研究' : '概念观察'}</small>
</td>{Object.keys(conceptNames).map(k => <td key={k} data-label={labels[k] || k}>
<a href={`#/company/${c.code}`}>
<Badge grade={c.grades[k]?.grade} review={c.grades[k]?.needs_review} conflict={c.grades[k]?.conflict} mr={c.grades[k]?.manual_review}/>
</a>
<p className="small muted">{c.grades[k]?.basis}</p>
</td>)}</tr>)}</tbody>
</table>
</div> : <Pending state={companies}/>}</section>{universe.data ? <>
<UniverseDistribution data={universe.data} meta={meta}/>
<section className="panel">
<h2>CPO 全量成分 · 可搜索证据名单</h2>
<div className="universe-list">{universe.data.map((c: D, i: number) => ({ c, i })).filter(({ c }: {
        c: D;
    }) => (!query || `${c.name}${c.entity}`.toLowerCase().includes(query.toLowerCase())) && (!grade || c.grade === grade) && (!review || c.needs_review)).map(({ c, i }: {
        c: D;
        i: number;
    }) => <article key={c.entity}>
<strong>{c.name}</strong>
<Text value={c.entity} at={`cpo_universe.json#/${i}/entity`}/>
<Badge grade={c.grade} review={c.needs_review} mr={c.manual_review}/>
<p>{c.basis}</p>{c.single_source && <span className="flag">单一来源：iFinD 概念解析</span>}{companies.data?.some((x: D) => x.code === c.entity) && <a href={`#/company/${c.entity}`}>公司研究 →</a>}{c.trace?.map((t: D, j: number) => <button key={j} onClick={() => open({ kind: 'evidence', id: t.evidence_id, quote: t.quote })}>查看证据</button>)}</article>)}</div>
</section>
</> : <Pending state={universe}/>}</>;
}
function PageTitle({ title, text }: {
    title: string;
    text: string;
}) { return <div className="page-heading">
<div>
<p className="eyebrow">OPTICHAIN / RESEARCH DESK</p>
<h1>{title}</h1>
<p>{text}</p>
</div>
</div>; }
function Compare() { const state = useData('comparables'), open = useContext(Open); return <>
<PageTitle title="中外可比" text="先核对业务与会计口径，再阅读快照中的可比指标。"/>{!state.data ? <Pending state={state}/> : !state.data.length ? <Empty /> : state.data.map((g: D, i: number) => <section className="panel" key={g.id}>
<h2>{g.name}</h2>{g.note&&<p className="notice" data-ref={`comparables.json#/${i}/note`}>{g.note}</p>}<div className="two-col">
<div>
<h3>为何可比</h3>{g.comparable?.map((x: D, j: number) => <div key={j}>
<strong data-ref={`comparables.json#/${i}/comparable/${j}/dim`}>{x.dim}</strong>
<p data-ref={`comparables.json#/${i}/comparable/${j}/text`}>{x.text}</p>{x.refs?.map((r: D, k: number) => <button key={k} className="link" onClick={() => open(r.kind === 'evidence' ? { kind: 'evidence', id: r.ref } : r.kind === 'record' ? { kind: 'call', id: r.ref } : { kind: 'ref', ref: r.ref })}>查看依据{r.column && <> · <Text value={r.column} at={`comparables.json#/${i}/comparable/${j}/refs/${k}/column`}/></>}</button>)}</div>)}</div>
<div>
<h3>不可直接比较</h3>
<Tree data={g.non_comparable} at={`comparables.json#/${i}/non_comparable`}/>
</div>
</div>
<h3>对比指标</h3>
<div className="table-wrap">
<table>
<thead>
<tr>
<th>公司</th>
<th>会计与窗口</th>
<th>营收 USD</th>
<th>毛利率</th>
<th>净利率</th>
<th>PE</th>
<th>PS</th>
<th>市值 USD</th>
<th>聚焦业务占比</th>
<th>汇率口径</th>
</tr>
</thead>
<tbody>{g.table?.map((r: D, j: number) => <tr key={r.code}>
<td>
<Text value={r.name} at={`comparables.json#/${i}/table/${j}/name`}/>
<small>
<Text value={r.code} at={`comparables.json#/${i}/table/${j}/code`}/>
</small>
</td>
<td className="acct-cell">
<Tree data={{ fye: r.fye, accounting: r.accounting, currency: r.currency }} at={`comparables.json#/${i}/table/${j}`}/>
<div className="tree"><div className="tree-row">
<span className="tree-key">{labels.ttm_window}</span>
<div>{Array.isArray(r.ttm_window) && r.ttm_window.length === 2 ? <span className="ttm-window"><Text value={r.ttm_window[0]} at={`comparables.json#/${i}/table/${j}/ttm_window/0`}/> ~ <Text value={r.ttm_window[1]} at={`comparables.json#/${i}/table/${j}/ttm_window/1`}/></span> : <Tree data={r.ttm_window} at={`comparables.json#/${i}/table/${j}/ttm_window`}/>}</div>
</div></div>
</td>{['revenue_ttm_usd', 'gross_margin_ttm', 'net_margin_ttm', 'pe_ttm', 'ps_ttm', 'market_cap_usd', 'focus_share'].map(k => <td key={k} data-label={labels[k] || k}>
<Value data={r[k]} unit={k==='pe_ttm'?'':undefined} at={`comparables.json#/${i}/table/${j}/${k}`}/>{k === 'net_margin_ttm' && r.net_margin_note && <p><Text value={r.net_margin_note} at={`comparables.json#/${i}/table/${j}/net_margin_note`}/></p>}{k === 'focus_share' && <p data-ref={r.focus_share_note?`comparables.json#/${i}/table/${j}/focus_share_note`:undefined}>{r.focus_share_note}</p>}</td>)}<td className="fx-cell" data-label="汇率口径">{r.fx_basis ? <details><summary><Text value={r.fx_basis.text} at={`comparables.json#/${i}/table/${j}/fx_basis/text`}/></summary><Tree data={Object.fromEntries(Object.entries(r.fx_basis).filter(([k]) => k !== 'text'))} at={`comparables.json#/${i}/table/${j}/fx_basis`}/></details> : <span className="muted">未提供</span>}</td></tr>)}</tbody>
</table>
</div>
<h3>口径调整步骤</h3>
<Tree data={g.adjustments} at={`comparables.json#/${i}/adjustments`}/>
<AiDraft data={g.ai_draft} at={`comparables.json#/${i}/ai_draft`}/>
</section>)}</>; }
function HistoryChart({data,at}: {data:D;at:string}) {const open=useContext(Open);return <History data={data} at={at} open={open}/>;}
function ExposureTable({rows}: {rows:D[]}) {return rows.length?<div className="table-wrap">
<table>
<thead>
<tr>
<th>公司</th>
<th>指标</th>
<th>取值</th>
<th>静态汇率敏感性 / 说明</th>
</tr>
</thead>
<tbody>{rows.map((r,i)=>
<tr key={r.code}>
<td>
<a href={`#/company/${r.code}`}>{r.name}</a>
<small>
<Text value={r.code} at={`transmission.json#/exposures/${i}/code`}/>
</small>
</td>
<td>
<Text value={r.metric} at={`transmission.json#/exposures/${i}/metric`}/>
</td>
<td data-label="取值">
<Value data={r.value} at={`transmission.json#/exposures/${i}/value`}/>
</td>
<td data-label="静态汇率敏感性 / 说明">{r.fx_sensitivity&&<Value data={r.fx_sensitivity} at={`transmission.json#/exposures/${i}/fx_sensitivity`}/>}<p>
<Text value={r.note} at={`transmission.json#/exposures/${i}/note`}/>
</p>
</td>
</tr>)}</tbody>
</table>
</div>:<p className="muted">暂无敞口数据</p>;}
function EventDetail({ event: e, at }: {event: D; at: string}) {
  const open = useContext(Open), companies = useData('companies');
  const window: string[] = e.window || e.companies?.[0]?.rows?.map((r: D) => r.quarter) || [];
  const offsets = ['t−1', 't', 't+1', 't+2'];
  return <div className="event-detail">
    <h3><Text value={e.title} at={`${at}/title`}/></h3>
    {e.rule && <p>触发规则：<Text value={e.rule} at={`${at}/rule`}/></p>}
    {e.trigger && <div className="inset">Capex · <Text value={e.trigger.quarter} at={`${at}/trigger/quarter`}/> · 环比 <Value data={e.trigger.qoq} unit="signed_ratio" at={`${at}/trigger/qoq`}/> · 当季 <Value data={e.trigger.value} unit={e.trigger.ref?.unit} at={`${at}/trigger/value`}/> · 前季 <Value data={e.trigger.prev_value} unit={e.trigger.ref?.unit} at={`${at}/trigger/prev_value`}/>
      {e.trigger.ref && <details><summary>触发值依据与输入</summary>{typeof e.trigger.ref === 'string' ? <button onClick={() => open({kind:'ref', ref:e.trigger.ref})}>查看依据</button> : <Tree data={e.trigger.ref} at={`${at}/trigger/ref`}/>}</details>}
    </div>}
    {e.kind === 'news_event' && <>
      <p>事件日期 <Text value={e.date} at={`${at}/date`}/> · 报道数 <Value data={e.reports} at={`${at}/reports`}/></p>
      <div className="citations">{Array.from(new Set<string>([e.evidence_id, ...(e.evidence_ids || [])].filter(Boolean))).map(id => <button key={id} onClick={() => open({kind:'evidence', id})}>查看事件证据 →</button>)}</div>
      {e.financial_available === false && <p className="notice">{e.financial_note ? <Text value={e.financial_note} at={`${at}/financial_note`}/> : '暂无事后财务数据'}</p>}
    </>}
    {e.financial_available !== false && e.companies?.length > 0 && <div className="table-wrap"><table className="event-table">
      <caption>事件前后实际财务指标 · 变化以 t−1 为基准</caption>
      <thead><tr><th>公司</th>{window.map((q, i) => <th key={q}>{offsets[i]}<br/><Text value={q} at={e.window ? `${at}/window/${i}` : `${at}/companies/0/rows/${i}/quarter`}/></th>)}</tr></thead>
      <tbody>{e.companies.map((c: D, ci: number) => <tr key={c.code}>
        <td><a href={`#/company/${c.code}`}><Text value={c.name} at={`${at}/companies/${ci}/name`}/></a></td>
        {window.map((q, wi) => {
          const ri = c.rows?.findIndex((r: D) => r.quarter === q), r = c.rows?.[ri];
          const rowAt = `${at}/companies/${ci}/rows/${ri}`;
          const currency = companies.data?.find((x: D) => x.code === c.code)?.metrics?.currency;
          return <td key={q} data-label={offsets[wi]}>{!r || r.available === false ? '未披露' : <>
            <p><Text value={r.quarter} at={`${rowAt}/quarter`}/></p>
            <p>营收 {r.revenue_ref ? <Value data={r.revenue} unit={currency} at={r.revenue_ref}/> : '未提供引用'}</p>
            <p>毛利率 {r.gross_margin_ref ? <Value data={r.gross_margin} unit="ratio" at={r.gross_margin_ref}/> : '未披露'}</p>
            {r.revenue_vs_t_minus_1 != null && <p>营收变化 <Value data={r.revenue_vs_t_minus_1} unit="signed_ratio" at={`${rowAt}/revenue_vs_t_minus_1`}/></p>}
            {r.gross_margin_pp_vs_t_minus_1 != null && <p>毛利率变化 <Value data={r.gross_margin_pp_vs_t_minus_1} unit="signed_pp" at={`${rowAt}/gross_margin_pp_vs_t_minus_1`}/></p>}
          </>}</td>;
        })}
      </tr>)}</tbody>
    </table></div>}
    {e.kind === 'news_event' && <>
      <div className="two-col">{['export_before', 'export_after'].map(key => e[key]?.length > 0 && <div key={key}><h3>{key === 'export_before' ? '事件前出口' : '事件后出口'}</h3><div className="table-wrap"><table><thead><tr><th>月份</th><th>出口额（原始值）</th></tr></thead><tbody>{e[key].map((r: D, i: number) => <tr key={r.month}><td><Text value={r.month} at={`${at}/${key}/${i}/month`}/></td><td data-label="出口额（原始值）"><Value data={r.value} at={`${at}/${key}/${i}/value`}/></td></tr>)}</tbody></table></div></div>)}</div>
      {e.export_note && <p className="muted"><Text value={e.export_note} at={`${at}/export_note`}/></p>}
    </>}
    {e.note && <p><Text value={e.note} at={`${at}/note`}/></p>}
    <p className="muted">这只是历史对照，不构成因果推断或预测。</p>
  </div>;
}
function EventWindows({ data }: {data: D}) {
  const [variable, setVariable] = useState(''), [id, setId] = useState('');
  const events: {e: D; i: number}[] = (data.event_windows || []).map((e: D, i: number) => ({e, i})).filter(({e}: {e: D}) => !variable || e.variable === variable);
  const recent = events.filter(({e}) => e.kind === 'data_rule').sort((a, b) => String(b.e.trigger?.quarter || '').localeCompare(String(a.e.trigger?.quarter || '')))[0];
  const chosen = events.find(({e}) => e.id === id) || recent || events[0];
  return <section className="panel"><h2>事件对照</h2>
    <div className="filters"><label>按变量筛选<select value={variable} onChange={e => {setVariable(e.target.value); setId('');}}><option value="">全部变量</option>{data.variables?.map((v: D, i: number) => <option key={v.id} value={v.id} data-ref={`transmission.json#/variables/${i}/name`}>{v.name}</option>)}</select></label>
      <label>选择事件<select value={chosen?.e.id || ''} disabled={!events.length} onChange={e => setId(e.target.value)}>{events.map(({e, i}) => <option key={e.id} value={e.id} data-ref={`transmission.json#/event_windows/${i}/title`}>{e.title}</option>)}</select></label>
    </div>
    {chosen ? <EventDetail event={chosen.e} at={`transmission.json#/event_windows/${chosen.i}`}/> : <p className="muted">当前变量暂无事件窗口。</p>}
  </section>;
}
function Transmission() { const state = useData('transmission'), [selected, setSelected] = useState(''), [edge, setEdge] = useState<D>(), open = useContext(Open); if (!state.data)
    return <Pending state={state}/>; const d = state.data, kinds = ['variable', 'segment', 'company', 'metric']; const nodes: D[] = d.nodes || [], edges: D[] = d.edges || []; const positions = new Map<string, number[]>(); kinds.forEach((kind, col) => nodes.filter(n => n.kind === kind).forEach((n, i) => positions.set(n.id, [40 + col * 235, 55 + i * 80]))); const reachable = new Set<string>([selected]); for (let i = 0; i < nodes.length; i++)
    edges.forEach(e => { if (reachable.has(e.from))
        reachable.add(e.to); }); const ei = edges.indexOf(edge!); return <>
<PageTitle title="传导路径" text="需求如何经过环节传到公司指标；机制、时滞与证据分开呈现。"/>{!d.variables?.length && !edges.length ? <Empty /> : <>
<div className="filters">
<label>观察变量<select value={selected} onChange={e => { setSelected(e.target.value); setEdge(undefined); }}>
<option value="">全部路径</option>{d.variables?.map((v: D) => <option key={v.id} value={v.id}>{v.name}</option>)}</select>
</label>
</div>{d.variables?.map((v: D, i: number) => v.id === selected && <section className="panel" key={v.id}>
<h2>{v.name}</h2>
<p data-ref={`transmission.json#/variables/${i}/desc`}>{v.desc}</p>{v.evidence?.map((id: string) => <button key={id} onClick={() => open({ kind: 'evidence', id })}>变量依据</button>)}{v.series && <details>
<summary>变量序列与来源</summary>
<Tree data={v.series} at={`transmission.json#/variables/${i}/series`}/>
</details>}</section>)}<section className="panel">
<div className="graph-scroll">
<svg viewBox={`0 0 970 ${Math.max(260, ...Array.from(positions.values()).map(p => p[1] + 65))}`} role="img" aria-label="变量至指标的分层路径图">{edges.map((e: D, i: number) => { const a = positions.get(e.from), b = positions.get(e.to); if (!a || !b)
    return null; return <g key={e.id} tabIndex={0} role="button" aria-label={e.mechanism} className={!selected || reachable.has(e.from) ? 'edge active' : 'edge'} onClick={() => setEdge(e)} onKeyDown={k => { if (k.key === 'Enter')
    setEdge(e); }}>
<path d={`M${a[0] + 150},${a[1] + 15} C${a[0] + 200},${a[1] + 15} ${b[0] - 40},${b[1] + 15} ${b[0]},${b[1] + 15}`} fill="none"/>
<title>{e.mechanism}</title>
</g>; })}{nodes.map(n => { const p = positions.get(n.id); if (!p)
    return null; return <g key={n.id} className={selected && !reachable.has(n.id) ? 'dim' : ''}>
<rect x={p[0]} y={p[1]} width="150" height="38" rx="4"/>
<text x={p[0] + 10} y={p[1] + 24} data-ref={`transmission.json#/nodes/${nodes.indexOf(n)}/label`}>{n.label}</text>
</g>; })}</svg>
</div>
<details>
<summary>路径连线列表</summary>{edges.map((e,i)=>
<button className="trace" key={e.id} onClick={()=>setEdge(e)}>
<Text value={e.mechanism} at={`transmission.json#/edges/${i}/mechanism`}/>
</button>)}</details>
<p className="muted">路径从左至右：变量 → 环节 → 公司 → 指标；点击连线查看依据。</p>
</section>{edge && <section className="panel">
<h2>传导机制</h2>
<Tree data={Object.fromEntries(Object.entries(edge).filter(([k]) => !['support', 'ai_explanation'].includes(k)))} at={`transmission.json#/edges/${ei}`}/>
<AiDraft data={edge.ai_explanation} at={`transmission.json#/edges/${ei}/ai_explanation`}/>
<h3>支持与反驳引文</h3>{edge.support?.map((s: D, i: number) => <button className="trace" key={i} onClick={() => open({ kind: 'evidence', id: s.evidence_id, quote: s.quote })}>
<span className="flag">{s.stance==='contradict'?'反驳引文':'支持引文'}</span>{s.source_name&&<Text value={s.source_name} at={`transmission.json#/edges/${ei}/support/${i}/source_name`}/>}<Text value={s.quote} at={`transmission.json#/edges/${ei}/support/${i}/quote`}/>
<span className="link">查看原文 →</span>
</button>)}</section>}<section className="panel">
<h2>敞口观察</h2>
<ExposureTable rows={d.exposures || []}/>
</section><EventWindows data={d}/>{d.history?.map((h: D, i: number) => <HistoryChart key={h.id} data={h} at={`transmission.json#/history/${i}`}/>)}</>}</>; }
function Changes({ meta }: {
    meta: D;
}) { const checks = useData('checks'), diff = useData('diff'); return <>
<PageTitle title="更新与校验" text="追踪快照变更、跨源差异与缺失数据。"/>
<section className="panel">
<h2>当前快照</h2>
<Tree data={meta} at="meta.json#"/>
</section>
<section className="panel">
<h2>本次变化</h2>{!diff.data ? <Pending state={diff}/> : diff.data.changes?.length ? <Tree data={diff.data} at="diff.json#"/> : <p className="muted">暂无变更记录；上一快照版本未提供时，不推断变化。</p>}</section>{checks.data ? <>
<section className="panel">
<h2>校验概况</h2>
<Checks data={checks.data}/>
</section>
{checks.data.ai_drafts && <section className="panel">
<h2>AI 初稿校验</h2>
<p>初稿总数 <Value data={checks.data.ai_drafts.total} at="checks.json#/ai_drafts/total"/></p>
{checks.data.ai_drafts.rejected?.length ? <><h3>被拒的初稿及原因</h3><Tree data={checks.data.ai_drafts.rejected} at="checks.json#/ai_drafts/rejected"/></> : <p className="muted">未列出被拒初稿。</p>}
</section>}
<section className="panel">
<h2>跨源冲突 · iFinD vs SEC</h2>{checks.data.conflicts?.length ? <div className="table-wrap">
<table>
<thead>
<tr>
<th>实体 / 标签 / 区间</th>
<th>iFinD 值</th>
<th>SEC 值</th>
<th>差额</th>
<th>来源与处理</th>
</tr>
</thead>
<tbody>{checks.data.conflicts.map((c: D, i: number) => <tr key={i}>
<td>
<Tree data={{ entity: c.entity, label: c.label, real_end: c.real_end }} at={`checks.json#/conflicts/${i}`}/>
</td>{['ifind_abs', 'sec', 'diff'].map(k => <td key={k} data-label={labels[k] || k}>
<Value data={c[k]} at={`checks.json#/conflicts/${i}/${k}`}/>
</td>)}<td>
<p>{c.resolution}</p>
<details>
<summary>双方来源</summary>
<Tree data={c.sources} at={`checks.json#/conflicts/${i}/sources`}/>
</details>
</td>
</tr>)}</tbody>
</table>
</div> : <p>暂无跨源冲突</p>}</section>
<section className="panel">
<h2>采集缺失</h2>{checks.data.ingest_missing?.length ? <Tree data={checks.data.ingest_missing} at="checks.json#/ingest_missing"/> : <p className="muted">快照未列出采集缺失项。</p>}</section>
</> : <Pending state={checks}/>}</>; }
function Method({ taxonomy, meta }: {
    taxonomy: D;
    meta: D;
}) { return <>
<PageTitle title="方法与边界" text="让结论、证据和计算各自可追溯。"/>
<div className="two-col">
<section className="panel">
<h2>产业链口径</h2>{taxonomy.source_note && <p><Text value={taxonomy.source_note} at="taxonomy.json#/source_note"/></p>}
{taxonomy.adjustments?.length > 0 && <><h3>我们的调整与理由</h3>{taxonomy.adjustments.map((a: D, i: number) => <div className="inset" key={i}><strong><Text value={a.what} at={`taxonomy.json#/adjustments/${i}/what`}/></strong><p><Text value={a.why} at={`taxonomy.json#/adjustments/${i}/why`}/></p></div>)}</>}
{taxonomy.evidence_tiers && <><h3>归属证据优先级（由高到低）</h3>{Object.entries(taxonomy.evidence_tiers).map(([tier, text]) => <p key={tier}>第 {['一','二','三','四'][Number(tier)-1] || tier} 档：<Text value={text} at={childRef('taxonomy.json#/evidence_tiers', tier)}/></p>)}</>}
{taxonomy.layers.map((l: D, li:number) => <div key={l.id}>
<h3>{l.name}</h3>{l.segments.map((s: D, si:number) => <p key={s.id}>
<strong>{s.name}</strong> · <Text value={s.desc} at={`taxonomy.json#/layers/${li}/segments/${si}/desc`}/>
</p>)}</div>)}<details>
<summary>产品关键词、泛化词与人工覆盖规则</summary>
<Tree data={taxonomy} at="taxonomy.json#"/>
</details>
</section>
<section className="panel">
<h2>分级规则</h2>
<div className="table-wrap"><table className="grade-rules"><thead><tr><th>等级</th><th>含义</th><th>判定依据（示例）</th></tr></thead><tbody>{[
['A', '有收入', '财报可拆分出相关收入；年报 / 半年报分产品收入有对应项，给出金额与占比。'],
['B', '有订单 / 产能', '公告了订单、客户认证、量产、扩产；依据为公告、定增 / 可转债募投项目。'],
['C', '有研发 / 送样', '研发、送样、小批量；依据为年报研发项目、调研纪要。'],
['D', '仅表态', '只在互动平台 / 调研中口头提及；依据为互动易 / e互动问答。'],
['E', '无证据 / 否认', '没检索到证据或公司明确否认；记录检索范围与时间。']
].map(([g, meaning, basis]) => <tr key={g}><td><Badge grade={g}/></td><td data-label="含义">{meaning}</td><td data-label="判定依据（示例）">{basis}</td></tr>)}</tbody></table></div>
<ul className="method-rules"><li>非正式来源上限 D：互动平台、媒体转述均适用。</li><li>证据冲突时保留双方证据、降一级展示（A 也降），并标待复核。</li><li>LLM 给出建议等级和理由，最终等级由规则函数裁定；两者不一致时标“待人工复核”。</li><li>人工复核结论写在 config/manual_review.yaml（复核人、日期、理由），页面同时展示规则等级；规则等级或待复核证据变化后复核自动作废。</li></ul>
<p className="muted">等级表示证据阶段，不表示企业质量。</p>
<Tree data={taxonomy.grade_rules} at="taxonomy.json#/grade_rules"/>
</section>
<section className="panel">
<h2>AI 的角色</h2>
<h3>AI 做</h3>
<p>证据抽取、业务描述归纳、传导机制解释、可比性差异初稿；给出建议等级与理由。生成内容标“AI 初稿”，初稿里的数字必须出现在依据事实中，否则丢弃。</p>
<h3>AI 不做</h3>
<p>任何数字计算、最终等级裁定、投资判断。</p>
<h3>防幻觉</h3>
<p>引文逐字校验；建议等级与规则不一致时待人工复核，以规则为准。数字由数据管道生成，浏览器仅格式化与展示。</p>
</section>
<section className="panel">
<h2>数据来源与局限</h2>
<Tree data={meta.sources} at="meta.json#/sources"/>
<p>数据非实时；不同会计年结日、币种、分部披露方式会影响可比性。概念解析与媒体报道不能替代正式披露；未检索到证据不代表业务不存在。</p>
<p>海外分部收入缺失时采用配置环节，不推断业务占比。历史对照只展示观测值，不作因果判定。</p>
<p>本产品为产业研究工具，不构成任何投资建议。数据可能存在遗漏与口径差异，请回查原始披露。</p>
<details>
<summary>快照原始声明</summary>
<Text value={meta.disclaimer} at="meta.json#/disclaimer"/>
</details>
</section>
</div>
</>; }
function UncitedNumbers({ response }: {response: D}) {
  if (!response.reason?.startsWith('answer_blocked_') || !response.uncited_numbers?.length) return null;
  return <div className="notice"><h3>被拦截的未引用数字（接口诊断，未经证据验证）</h3><div className="table-wrap"><table><thead><tr><th>句子</th><th>数字</th></tr></thead><tbody>{response.uncited_numbers.map((u: D, i: number) => <tr key={i}><td data-response-ref={`/uncited_numbers/${i}/sentence`}>{u.sentence}</td><td data-label="数字" data-response-ref={`/uncited_numbers/${i}/number`}>{u.number}</td></tr>)}</tbody></table></div></div>;
}
function Ask() { const [question, setQuestion] = useState(''), [response, setResponse] = useState<D>(), [busy, setBusy] = useState(false), [error, setError] = useState(''); const open = useContext(Open); const abort = useRef<AbortController | null>(null); useEffect(() => () => abort.current?.abort(), []); async function submit(e: React.FormEvent) { e.preventDefault(); if (!question.trim() || busy)
    return; setBusy(true); setError(''); setResponse(undefined); abort.current = new AbortController(); const timeout = setTimeout(() => abort.current?.abort(), 20000); try {
    const r = await fetch('/api/ask', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question: question.trim() }), signal: abort.current.signal });
    if (!r.ok)
        throw Error();
    const d = await r.json();
    if (typeof d.answer !== 'string' || !Array.isArray(d.citations) || typeof d.refused !== 'boolean')
        throw Error();
    setResponse(d);
}
catch {
    setError('问答服务暂不可用');
}
finally {
    clearTimeout(timeout);
    setBusy(false);
} } return <>
<PageTitle title="快照问答" text="只基于快照内证据作答，不提供投资建议。"/>
<section className="panel ask">
<form onSubmit={submit}>
<label htmlFor="question">研究问题</label>
<textarea id="question" value={question} onChange={e => setQuestion(e.target.value)} placeholder="例如：如何核验一家公司的 CPO 业务阶段？" required/>
<button className="primary" disabled={busy || !question.trim()}>{busy ? '正在查阅快照…' : '基于快照提问 →'}</button>
</form>{error && <p role="alert" className="notice">{error}</p>}{response && <div aria-live="polite">
<h2>{response.refused ? '无法回答此问题' : '快照回答'}</h2>
{/* 拒答文本来自固定规则（compliance.yaml），可原样显示；LLM 回答只有通过服务端数字校验（numbers_verified）才显示数字 */}
<p className="answer">{response.refused || response.numbers_verified ? response.answer : response.answer.replace(/\d+(?:[.,]\d+)*/g, '〔见引用〕')}</p>{response.reason && <p className="muted">原因：{REASONS[response.reason] || response.reason}</p>}<UncitedNumbers response={response}/><div className="citations">{response.citations.map((c: D, i: number) => <button key={i} onClick={() => open(c.evidence_id ? { kind: 'evidence', id: c.evidence_id } : { kind: 'ref', ref: c.ref })}>{c.title ? `依据：${c.title}` : '查看引用依据'}</button>)}</div>
<p className="muted">{response.numbers_verified ? '回答中的数字已由服务端校验：每个数字都出现在所引用的快照证据里。' : '以上为服务返回文本；数字请通过引用回查快照。'}</p>
</div>}</section>
</>; }
const sourceNames: D = { notice: '公司披露', news: '新闻报道', ifind_concept: 'iFinD 概念解析', periodic: '定期报告', ir: '投资者关系活动', announcement: '公司公告', clarification: '澄清公告', hdy: '互动平台', hdy_media: '互动平台媒体转述', media: '媒体报道', unspecified: '未明确来源' };
Object.assign(sourceNames,{sec_filing:'SEC 原始披露'});
function Highlight({ text, quote, at }: {
    text: string;
    quote?: string;
    at: string;
}) { const i = quote ? text.indexOf(quote) : -1; return <div className="evidence-text" data-ref={at}>{i < 0 ? text : <>{text.slice(0, i)}<mark>{quote}</mark>{text.slice(i + quote!.length)}</>}</div>; }
function CallView({ id }: {
    id: string;
}) { const state = useData('calls'); if (!state.data)
    return <Pending state={state}/>; const d = state.data[id]; return d ? <>
<h3>接口调用记录</h3>
<Tree data={d} at={childRef('calls.json#', id)}/>
</> : <p>快照未提供此调用记录。</p>; }
function EvidenceView({ id, quote }: {
    id: string;
    quote?: string;
}) { const state = useData('evidence'); if (!state.data)
    return <Pending state={state}/>; const e = state.data[id], at = childRef('evidence.json#', id); if (!e)
    return <p>该证据不在当前快照中。</p>; const url = safeUrl(e.url); return <>
<p className="eyebrow">{sourceNames[e.source_kind] || '其他来源'} · {sourceNames[e.origin_kind] || '未明确来源'}</p>
<h2 data-ref={`${at}/source_name`}>{e.source_name}</h2>
<p>
<Text value={e.date} at={`${at}/date`}/>
</p>{quote && !e.text?.includes(quote) && <p className="notice">引文未在此片段逐字匹配，请回查来源。</p>}<Highlight text={e.text || '原文片段未提供'} quote={quote} at={`${at}/text`}/>{url ? <a className="outline" href={url} target="_blank" rel="noopener noreferrer">{e.url_kind === '检索页' ? '巨潮全文检索页' : '打开来源原文'} ↗</a> : <p className="muted">快照未提供可访问原文链接。</p>}{e.call_id ? <CallView id={e.call_id}/> : <p>未提供关联调用记录。</p>}</>; }
function RefView({ reference }: {
    reference: string;
}) { const companyState=useData('companies'); const normalized=canonicalReference(reference,companyState.data||[])||reference; const match = /^([a-z_]+)\.json#(.*)$/.exec(normalized); const state = useData((match && (['meta', 'companies', 'evidence', 'calls', 'cpo_universe', 'comparables', 'transmission', 'checks', 'diff', 'taxonomy'].includes(match[1])) ? match[1] : 'meta') as FileName); if (!match)
    return <p>无法识别此快照引用。</p>; if (!state.data)
    return <Pending state={state}/>; let value = state.data; for (const k of match[2].split('/').slice(1))
    value = value?.[k.replace(/~1/g, '/').replace(/~0/g, '~')]; return <>
<p className="muted">{reference}</p>{value === undefined ? <p>引用目标不存在。</p> : <Tree data={value} at={normalized}/>}</>; }
function ScalarSources({reference}: {reference:string}) {
  const [file,pointer]=reference.split('#');
  const name=file.replace('.json','') as FileName;
  if(!files.includes(name))return <p>快照未提供输入明细。</p>;
  const parentPointer=pointer.slice(0,pointer.lastIndexOf('/'));
  const parent=resolvePointer(cachedData(name),parentPointer);
  if(!parent || typeof parent!=='object')return <p>快照未提供输入明细。</p>;
  const entries=Object.entries(parent).filter(([key])=>['source','revenue_source','call_id','column','field','label','dataset','formula','inputs','evidence','basis'].includes(key));
  return entries.length?<Tree data={Object.fromEntries(entries)} at={`${file}#${parentPointer}`}/>:<p>快照未提供输入明细。</p>;
}
function OverlayView({ overlay, close, back, hasBack }: {
    overlay: Overlay;
    close: () => void;
    back: () => void;
    hasBack: boolean;
}) { const panel = useRef<HTMLElement>(null); useEffect(() => { const previous = document.activeElement as HTMLElement; const overflow = document.body.style.overflow; document.body.style.overflow = 'hidden'; panel.current?.focus(); const key = (e: KeyboardEvent) => { if (e.key === 'Escape')
    close(); if (e.key === 'Tab') {
    const items = panel.current?.querySelectorAll<HTMLElement>('button,a[href],input,select,textarea,summary,[tabindex="0"]');
    if (!items?.length)
        return;
    const first = items[0], last = items[items.length - 1];
    if (e.shiftKey && (document.activeElement === first || document.activeElement === panel.current)) {
        e.preventDefault();
        last.focus();
    }
    else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
    }
} }; document.addEventListener('keydown', key); return () => { document.body.style.overflow = overflow; document.removeEventListener('keydown', key); previous?.focus(); }; }, [close]); return <div className="overlay" onClick={close} style={{animationDuration:`${Math.round(220 + Math.random()*80)}ms`}}>
<aside className="drawer" ref={panel} tabIndex={-1} role="dialog" aria-modal="true" aria-label="证据与数据溯源" onClick={e => e.stopPropagation()}>
<div className="drawer-header">{hasBack ? <button onClick={back}>← 返回</button> : <strong>证据与数据溯源</strong>}<button onClick={close} aria-label="关闭抽屉">关闭 ×</button>
</div>{overlay.kind === 'evidence' ? <EvidenceView id={overlay.id!} quote={overlay.quote}/> : overlay.kind === 'call' ? <CallView id={overlay.id!}/> : overlay.kind === 'ref' ? <RefView reference={overlay.ref || ''}/> : <>
<h2>数字计算链</h2>
<p className="ref-code">{overlay.ref}</p>
<Value data={overlay.data} unit={overlay.unit} at={overlay.ref!}/>
<h3>计算说明</h3>{overlay.data?.formula ? <Text value={overlay.data?.formula} at={`${overlay.ref}/formula`}/> : <p>快照直接披露值；未提供 formula，不推断计算。</p>}<h3>输入明细</h3>{overlay.data?.inputs ? <Tree data={overlay.data?.inputs} at={`${overlay.ref}/inputs`}/> : <ScalarSources reference={overlay.ref!}/>}</>}</aside>
</div>; }
const nav = [['/', '产业链总览'], ['/concepts', '概念可信度'], ['/compare', '中外可比'], ['/transmission', '传导路径'], ['/changes', '更新与校验'], ['/method', '方法'], ['/ask', '问答']];
function App() { const meta = useData('meta'), taxonomy = useData('taxonomy'), [route, setRoute] = useState(location.hash.slice(1) || '/'), [overlays, setOverlays] = useState<Overlay[]>([]), [theme, setTheme] = useState(() => { try {
    return localStorage.getItem('optichain-theme') || 'dark';
}
catch {
    return 'dark';
} }); useEffect(() => { const update = () => { setRoute(location.hash.slice(1) || '/'); setOverlays([]); window.scrollTo(0, 0); }; window.addEventListener('hashchange', update); return () => window.removeEventListener('hashchange', update); }, []); useEffect(() => { document.documentElement.dataset.theme = theme; try {
    localStorage.setItem('optichain-theme', theme);
}
catch { } }, [theme]); const path = route.split('?')[0]; const open = (x: Overlay) => setOverlays(prev => [...prev, x]); const close = React.useCallback(() => setOverlays([]), []); return <Open.Provider value={open}>
<div className="app">
<header inert={overlays.length>0}>
<div className="brand-row">
<a className="brand" href="#/">
<span className="brand-mark">◈</span>
<span>OptiChain <small>AI 算力光互联产业研究台</small>
</span>
</a>
<button className="theme" onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}>{theme === 'dark' ? '浅色 ☼' : '深色 ◐'}</button>
</div>
<div className="snapshot-bar">{meta.data ? <details>
<summary>非实时 · 数据截止 <Text value={meta.data.data_cutoff} at="meta.json#/data_cutoff"/> · 财务截至 <Text value={meta.data.financial_latest_period} at="meta.json#/financial_latest_period"/> · 快照 <Text value={meta.data.version} at="meta.json#/version"/> <span className="muted">⌄ 声明</span>
</summary>
<p data-ref="meta.json#/disclaimer">{meta.data.disclaimer}</p>
</details> : <span>正在读取快照元信息…</span>}</div>
<nav aria-label="主导航">{nav.map(([url, label]) => <a href={`#${url}`} key={url} className={(url === '/' ? (path === '/' || path.startsWith('/company/')) : path === url) ? 'active' : ''} aria-current={path === url ? 'page' : undefined}>{label}</a>)}</nav>
</header>
<main inert={overlays.length>0} key={path} style={{animationDuration:`${Math.round(320 + Math.random()*140)}ms`}}>{!meta.data ? <Pending state={meta}/> : !taxonomy.data ? <Pending state={taxonomy}/> : path === '/' ? <Overview meta={meta.data} taxonomy={taxonomy.data}/> : path.startsWith('/company/') ? <Company code={decodeURIComponent(path.slice(9))} taxonomy={taxonomy.data}/> : path === '/concepts' ? <Concepts key={route} meta={meta.data}/> : path === '/compare' ? <Compare /> : path === '/transmission' ? <Transmission /> : path === '/changes' ? <Changes meta={meta.data}/> : path === '/method' ? <Method taxonomy={taxonomy.data} meta={meta.data}/> : path === '/ask' ? <Ask /> : <Empty>页面不存在，请返回产业链总览。</Empty>}</main>
<footer inert={overlays.length>0}>
<span>OptiChain · 以证据理解产业</span>
<p>本产品为产业研究工具，不构成任何投资建议。数据非实时，请核对快照日期、统计口径与原始披露。</p>{meta.data && <details>
<summary>快照免责声明</summary>
<Text value={meta.data.disclaimer} at="meta.json#/disclaimer"/>
</details>}</footer>{overlays.length > 0 && <OverlayView overlay={overlays[overlays.length - 1]} close={close} hasBack={overlays.length > 1} back={() => setOverlays(prev => prev.slice(0, -1))}/>}</div>
</Open.Provider>; }
if (typeof document !== 'undefined' && document.getElementById('root'))
    createRoot(document.getElementById('root')!).render(<React.StrictMode>
<App />
</React.StrictMode>);
export { AiDraft, EventDetail, EventWindows, UncitedNumbers, App, Overview, Company, Concepts, Compare, Transmission, Changes, Method, Ask, EvidenceView, OverlayView };
