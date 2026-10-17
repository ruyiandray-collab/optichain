import React from 'react';
import { formatValue, RecordData as D } from './data';

const plotDate=(date:string)=>{const q=/^(\d{4})Q([1-4])$/.exec(date);return q?Date.UTC(Number(q[1]),(Number(q[2])-1)*3,1):Date.parse(date);};
const pointOverlay=(p:D,at:string,unit:string)=>{
  if(typeof p.ref==='string')return {kind:'ref',ref:p.ref};
  if(p.ref?.path)return {kind:'ref',ref:p.ref.path};
  if(p.ref && 'value' in p.ref)return {kind:'value',data:p.ref,ref:`${at}/ref`};
  if(p.ref?.call_id)return {kind:'ref',ref:`${at}/ref`};
  return {kind:'value',data:p.value,ref:`${at}/value`,unit};
};

export function History({data,at,open}: {data:D;at:string;open:(x:any)=>void}) {
  const sides=['x','y'];
  const dated=sides.flatMap(side=>(data[side]?.points||[]).map((p:D)=>plotDate(p.date))).filter(Number.isFinite);
  if(!dated.length)return <section className="panel"><h3>{data.title}</h3><p>暂无可绘制的历史观测值。</p></section>;
  const first=Math.min(...dated),last=Math.max(...dated);
  // Only viewport coordinates are calculated. Axis labels always use original observations.
  const scaleX=(p:D)=>100+(plotDate(p.date)-first)/Math.max(1,last-first)*560;
  return <section className="panel"><h3 data-ref={`${at}/title`}>{data.title}</h3><div className="section-heading">{sides.map(side=><span key={side}><span data-ref={`${at}/${side}/name`}>{data[side]?.name}</span> · <span data-ref={`${at}/${side}/unit`}>{data[side]?.unit}</span></span>)}</div><svg viewBox="0 0 760 250" role="img" aria-label="双轴历史对照，按真实日期对齐；两侧纵轴独立缩放"><path d="M95 30V210H665V30" fill="none" stroke="var(--border)"/>{sides.map((side,si)=>{
    const points:D[]=data[side]?.points||[];
    const values=points.map(p=>p.value).filter(v=>typeof v==='number'&&Number.isFinite(v));
    if(!values.length)return null;
    const low=Math.min(...values),high=Math.max(...values),color=si?'var(--grade-c)':'var(--accent)';
    const scaleY=(p:D)=>200-(p.value-low)/Math.max(1,high-low)*155;
    const valid=points.map((p,i)=>({p,i})).filter(({p})=>typeof p.value==='number'&&Number.isFinite(plotDate(p.date)));
    return <g key={side} fill={color}><polyline points={valid.map(({p})=>`${scaleX(p)},${scaleY(p)}`).join(' ')} fill="none" stroke={color} strokeWidth="2"/>{valid.map(({p,i})=><g key={i} tabIndex={0} role="button" aria-label="查看历史观测原值" onClick={()=>open(pointOverlay(p,`${at}/${side}/points/${i}`,data[side].unit))} onKeyDown={e=>{if(e.key==='Enter')open(pointOverlay(p,`${at}/${side}/points/${i}`,data[side].unit));}}><circle cx={scaleX(p)} cy={scaleY(p)} r="10" fill="transparent"/><circle cx={scaleX(p)} cy={scaleY(p)} r="3"/><title data-ref={`${at}/${side}/points/${i}/date`}>{p.date}</title></g>)}{[high,low].map((v,j)=>{const i=points.findIndex(p=>p.value===v);return <text key={j} x={si?675:85} y={j?205:40} textAnchor={si?'start':'end'} fontSize="10" data-ref={`${at}/${side}/points/${i}/value`}>{formatValue(v,data[side].unit)}</text>;})}</g>;})}{['x','y'].map((side,si)=>{const points=data[side]?.points||[];if(!points.length)return null;return <text key={side} x={si?660:100} y="235" textAnchor={si?'end':'start'} fill="var(--muted)" fontSize="10" data-ref={`${at}/${side}/points/${si?points.length-1:0}/date`}>{points[si?points.length-1:0].date}</text>;})}</svg><p className="muted small">左右纵轴独立缩放，不由线条高低推断量级。点击观测点回查来源。</p><p data-ref={data.note?`${at}/note`:undefined}>{data.note}</p><details><summary>历史观测原值</summary>{sides.map(side=><div key={side}><h4 data-ref={`${at}/${side}/name`}>{data[side]?.name}</h4>{data[side]?.points?.map((p:D,i:number)=><div className="membership-detail" key={i}><span data-ref={`${at}/${side}/points/${i}/date`}>{p.date}</span><button className="number" data-ref={`${at}/${side}/points/${i}/value`} onClick={()=>open(pointOverlay(p,`${at}/${side}/points/${i}`,data[side].unit))}>{formatValue(p.value,data[side].unit)}</button></div>)}</div>)}</details></section>;
}
