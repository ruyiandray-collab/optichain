import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { beforeAll, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { clearDataCache, files, formatValue, loadData, resolvePointer } from './data';
import { comparables, transmission, emptyComparables, emptyTransmission } from './__fixtures__/future';
import { App, Company, EvidenceView, OverlayView, EventDetail, EventWindows, AiDraft, UncitedNumbers } from './main';
const snapshots: Record<string, any> = {};
const decode = (s:string)=>s.replace(/&quot;/g,'"').replace(/&#x27;/g,"'").replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&amp;/g,'&');
for(const name of files)snapshots[`${name}.json`]=JSON.parse(readFileSync(new URL(`../public/data/${name}.json`,import.meta.url),'utf8'));
function auditRefs(html:string){
  let count=0;
  for(const match of html.matchAll(/<([a-z]+)[^>]*data-ref="([^"]+)"[^>]*>([\s\S]*?)<\/\1>/g)){
    const ref=decode(match[2]),[file,pointer]=ref.split('#'),value=resolvePointer(snapshots[file],pointer);count++;
    expect(value,`不存在的引用 ${ref}`).not.toBeUndefined();
    if(typeof value==='number')expect(['','CNY','USD','ratio','%','x','signed_ratio','signed_pp'].map(unit=>formatValue(value,unit)),`数字不匹配 ${ref}`).toContain(decode(match[3].replace(/<[^>]+>/g,'')).trim());
  }
  expect(count).toBeGreaterThan(0);
}
beforeAll(async()=>{
  vi.stubGlobal('fetch',vi.fn(async(input:string)=>({ok:true,json:async()=>snapshots[input.split('/').pop()!]})));
  vi.stubGlobal('location',{hash:'#/'});
  await Promise.all(files.map(loadData));
});
it('所有页面引用指向真实快照，数字与源值匹配',()=>{
  for(const path of ['/','/concepts','/compare','/transmission','/changes','/method','/ask']){
    location.hash=`#${path}`;const html=renderToStaticMarkup(React.createElement(App));expect(html).not.toContain('正在读取快照…');auditRefs(html);
  }
});
it('全部公司真实财务、归属、概念轨迹可渲染且引用有效',()=>{
  for(const c of snapshots['companies.json']){
    const html=renderToStaticMarkup(React.createElement(Company,{code:c.code,taxonomy:snapshots['taxonomy.json']}));auditRefs(html);
  }
});
it('证据抽屉高亮逐字引文并展示调用记录',()=>{
  const best=snapshots['companies.json'][0].grades.cpo.best;
  const html=renderToStaticMarkup(React.createElement(EvidenceView,{id:best.evidence_id,quote:best.quote}));
  expect(html).toContain('<mark>');expect(html).toContain('接口调用记录');auditRefs(html);
});
it('财务计算链与输入原始调用均保留快照路径',()=>{
  const data=snapshots['companies.json'][0].metrics.quarters[0].revenue;
  const html=renderToStaticMarkup(React.createElement(OverlayView,{overlay:{kind:'value',data,ref:'companies.json#/0/metrics/quarters/0/revenue'},close:()=>{},back:()=>{},hasBack:false}));
  expect(html).toContain(data.inputs[0].call_id);expect(html).toContain(data.formula);auditRefs(html);
});
it('空快照不生成替代业务数字',async()=>{
  const originals=[snapshots['comparables.json'],snapshots['transmission.json']];
  try{snapshots['comparables.json']=emptyComparables;snapshots['transmission.json']=emptyTransmission;clearDataCache();await Promise.all(files.map(loadData));
    for(const path of ['/compare','/transmission']){location.hash=`#${path}`;const html=renderToStaticMarkup(React.createElement(App));expect(html).toContain('该部分数据生成中 / 暂无数据');}
  }finally{[snapshots['comparables.json'],snapshots['transmission.json']]=originals;clearDataCache();await Promise.all(files.map(loadData));}
});
it('管道填充后的可比表与传导双轴图也遵守引用契约',async()=>{
  const originals=[snapshots['comparables.json'],snapshots['transmission.json']];
  try {
    snapshots['comparables.json']=comparables;snapshots['transmission.json']=transmission;clearDataCache();await Promise.all(files.map(loadData));
    for(const path of ['/compare','/transmission']){location.hash=`#${path}`;const html=renderToStaticMarkup(React.createElement(App));expect(html).not.toContain('该部分数据生成中');auditRefs(html);}
  } finally {
    [snapshots['comparables.json'],snapshots['transmission.json']]=originals;clearDataCache();await Promise.all(files.map(loadData));
  }
});

async function withSnapshots(overrides: Record<string, any>, run: () => void) {
  const originals = Object.fromEntries(Object.keys(overrides).map(k => [k, snapshots[k]]));
  try {
    Object.assign(snapshots, overrides); clearDataCache(); await Promise.all(files.map(loadData)); run();
  } finally {
    Object.assign(snapshots, originals); clearDataCache(); await Promise.all(files.map(loadData));
  }
}
it('方法页包含 A–E 的含义、判据、非正式来源上限 D 及 AI 边界', () => {
  location.hash = '#/method';
  const html = renderToStaticMarkup(React.createElement(App));
  for (const grade of ['A','B','C','D','E']) expect(html).toContain(`badge grade-${grade}`);
  for (const text of ['判定依据（示例）','非正式来源上限 D','定增 / 可转债募投项目','A 也降','待人工复核','初稿里的数字必须出现在依据事实中']) expect(html).toContain(text);
  auditRefs(html);
});
it('真实事件窗口全部通过数字溯源，并默认选择最近的规则事件', () => {
  const data = snapshots['transmission.json'];
  for (const [i, event] of (data.event_windows || []).entries()) {
    const html = renderToStaticMarkup(React.createElement(EventDetail, {event, at:`transmission.json#/event_windows/${i}`}));
    auditRefs(html);
    for (const button of html.matchAll(/<button\b[^>]*class="number"[^>]*>/g)) expect(button[0]).toContain('data-ref=');
    expect(html).toContain('不构成因果推断或预测');
  }
  const latest = (data.event_windows || []).filter((e:any) => e.kind === 'data_rule').sort((a:any,b:any) => b.trigger.quarter.localeCompare(a.trigger.quarter))[0];
  if (latest) {
    const html = renderToStaticMarkup(React.createElement(EventWindows, {data}));
    const option = Array.from(html.matchAll(/<option\b([^>]*)>/g)).find(m => m[1].includes(`value="${latest.id}"`));
    expect(option?.[1]).toContain('selected=""');
    expect(html).toContain('事件前后实际财务指标'); auditRefs(html);
  }
});
it('事件对照保持原始 Pointer，格式化预计算变化，缺财报显示未披露', async () => {
  const company = snapshots['companies.json'][0], q = company.metrics.quarters[0];
  const event = {id:'test-event', kind:'data_rule', variable:'capex', title:'测试 Capex 事件', rule:'来自管道的规则',
    trigger:{quarter:'2025Q4', qoq:0.2, value:123000000, prev_value:100000000},
    window:['2025Q3','2025Q4','2026Q1','2026Q2'], companies:[{code:company.code, name:company.name, rows:[
      {quarter:'2025Q3', available:true, revenue:q.revenue.value, revenue_ref:'companies.json#/0/metrics/quarters/0/revenue/value', gross_margin:0.25, gross_margin_ref:'transmission.json#/event_windows/0/companies/0/rows/0/gross_margin'},
      {quarter:'2025Q4', available:true, revenue:q.revenue.value, revenue_ref:'companies.json#/0/metrics/quarters/0/revenue/value', gross_margin:0.3, gross_margin_ref:'transmission.json#/event_windows/0/companies/0/rows/1/gross_margin', revenue_vs_t_minus_1:0.123, gross_margin_pp_vs_t_minus_1:-1.25},
      {quarter:'2026Q1', available:false}, {quarter:'2026Q2', available:false}
    ]}], note:'管道历史窗口'};
  await withSnapshots({'transmission.json':{event_windows:[event]}}, () => {
    const html = renderToStaticMarkup(React.createElement(EventDetail,{event, at:'transmission.json#/event_windows/0'}));
    for (const text of ['+12.3%', '-1.3 pp', '未披露', 'companies.json#/0/metrics/quarters/0/revenue/value', '/rows/1/revenue_vs_t_minus_1', '/rows/1/gross_margin_pp_vs_t_minus_1']) expect(html).toContain(text);
    auditRefs(html);
  });
});
it('公司页 AI 初稿为 null 或 v2 字段缺失时正常渲染', async () => {
  const companies = structuredClone(snapshots['companies.json']);
  companies[0].ai_summary = null;
  for (const g of Object.values(companies[0].grades || {}) as any[]) for (const t of g.trace || []) {delete t.llm_grade; delete t.llm_reason;}
  await withSnapshots({'companies.json': companies}, () => {
    const html = renderToStaticMarkup(React.createElement(Company,{code:companies[0].code, taxonomy:snapshots['taxonomy.json']}));
    expect(html).toContain('基本资料与环节归属'); expect(html).not.toContain('aria-label="AI 初稿"'); auditRefs(html);
  });
});
it('AI 初稿使用可点击事实角标、事实文本与证据入口，空初稿隐藏', async () => {
  const draft = {draft:'相关收入为 100 元。[F1]', facts:[{id:'F1', text:'依据披露收入 100 元', ref:{evidence_id:Object.keys(snapshots['evidence.json'])[0]}}]};
  await withSnapshots({'companies.json':[{ai_summary:draft}]}, () => {
    const html = renderToStaticMarkup(React.createElement(AiDraft,{data:draft, at:'companies.json#/0/ai_summary'}));
    for (const text of ['AI 初稿','<sup>','查看依据 F1','依据披露收入 100 元','打开证据','初稿中的数字均来自所列依据，等级由规则裁定']) expect(html).toContain(text);
    auditRefs(html);
    expect(renderToStaticMarkup(React.createElement(AiDraft,{data:null, at:'companies.json#/0/ai_summary'}))).toBe('');
  });
});
it('公司 v2 归属、子环节、LLM 分歧与检索调用时间均保留来源', async () => {
  const companies = structuredClone(snapshots['companies.json']); const c = companies[0];
  const evidence = Object.keys(snapshots['evidence.json'])[0], call = Object.keys(snapshots['calls.json'])[0];
  c.attribution.memberships = [{segment:c.primary_segment, role:'候选', basis:'正式证据待核实', evidence_tier:2, evidence:[evidence], sub_segments:[{id:'test-sub',name:'测试子环节',tier:2,basis:'产品引文',evidence_id:evidence}]}];
  c.grades = {cpo:{grade:'E', basis:'无证据', search_scope:[{query:'测试检索',called_at:'2026-10-17T01:02:03Z',call_id:call}],trace:[{evidence_id:evidence,grade:'D',llm_grade:'B',llm_reason:'测试建议理由',review:'建议与规则不一致，以规则为准'}]}};
  await withSnapshots({'companies.json':companies}, () => {
    const html = renderToStaticMarkup(React.createElement(Company,{code:c.code,taxonomy:snapshots['taxonomy.json']}));
    for (const text of ['candidate','测试子环节','公告·IR','规则等级','LLM 建议等级','测试建议理由','待复核','建议与规则不一致','2026-10-17T01:02:03Z',call]) expect(html).toContain(text);
    auditRefs(html);
  });
});
it('问答拦截明细标为接口诊断并列出句子与数字', () => {
  const response = {reason:'answer_blocked_uncited_numbers',uncited_numbers:[{sentence:'未经引用的营收 999 元',number:'999'}]};
  const html = renderToStaticMarkup(React.createElement(UncitedNumbers,{response}));
  for (const text of ['句子','数字','未经引用的营收 999 元','/uncited_numbers/0/number']) expect(html).toContain(text);
  expect(renderToStaticMarkup(React.createElement(UncitedNumbers,{response:{}}))).toBe('');
});
it('新闻事件没有事后财报时说明原因，出口前后值均可溯源', async () => {
  const event = {id:'news-test',kind:'news_event',title:'测试新闻事件',date:'2026-10-17',reports:2,evidence_ids:[Object.keys(snapshots['evidence.json'])[0]],financial_available:false,financial_note:'事件晚于财报，暂无事后财务数据',export_before:[{month:'2026-08',value:100}],export_after:[{month:'2026-09',value:90}],export_note:'出口样本仅作对照'};
  await withSnapshots({'transmission.json':{event_windows:[event]}}, () => {
    const html = renderToStaticMarkup(React.createElement(EventDetail,{event,at:'transmission.json#/event_windows/0'}));
    for (const text of ['报道数','查看事件证据','暂无事后财务数据','事件前出口','事件后出口','出口样本仅作对照','/export_before/0/value','/export_after/0/value']) expect(html).toContain(text);
    expect(html).not.toContain('event-table'); auditRefs(html);
  });
});
it('可比页展示客户、技术节点、并表范围、逐月汇率、净利率说明和 AI 初稿', async () => {
  const call = Object.keys(snapshots['calls.json'])[0];
  const group = {id:'v2-test',name:'测试可比组',comparable:[{dim:'客户',text:'客户依据',refs:[{kind:'record',ref:call,column:'客户字段'}]},{dim:'技术节点',text:'已核实技术'}],non_comparable:[{dim:'并表范围',text:'归母与合并口径差异'}],table:[{code:'TEST',name:'测试公司',net_margin_ttm:{value:0.1,unit:'ratio'},net_margin_note:'归母口径说明',fx_basis:{text:'月均汇率',months:['2026-01'],inputs:[{month:'2026-01',value:7.1,call_id:call}]}}],ai_draft:{draft:'可比性需核对。[F1]',facts:[{id:'F1',text:'并表范围不同'}]}};
  await withSnapshots({'comparables.json':[group]}, () => {
    location.hash='#/compare'; const html=renderToStaticMarkup(React.createElement(App));
    for (const text of ['客户字段','技术节点','并表范围','汇率口径','归母口径说明','AI 初稿','comparables.json#/0/table/0/fx_basis/inputs/0/value']) expect(html).toContain(text);
    auditRefs(html);
  });
});
it('更新页显示初稿总数和拒绝原因，不在前端统计', async () => {
  const checks = {...snapshots['checks.json'],ai_drafts:{total:3,rejected:[{kind:'company',key:'TEST',errors:['数字 888 不在事实中']}]}};
  await withSnapshots({'checks.json':checks}, () => {
    location.hash='#/changes'; const html=renderToStaticMarkup(React.createElement(App));
    expect(html).toContain('checks.json#/ai_drafts/total');expect(html).toContain('数字 888 不在事实中');auditRefs(html);
  });
});
