export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
// Snapshot fields vary by company and provider; render absent fields explicitly.
export type RecordData = { [key: string]: any };
export const files = ['meta','companies','evidence','calls','cpo_universe','comparables','transmission','checks','diff','taxonomy'] as const;
export type FileName = typeof files[number];
const cache = new Map<FileName, Promise<any>>();
const resolved = new Map<FileName, any>();
export function cachedData(name: FileName): any { return resolved.get(name); }
export function loadData(name: FileName): Promise<any> {
  if (!cache.has(name)) {
    const request = fetch(`${import.meta.env.BASE_URL}data/${name}.json`).then(r => {
      if (!r.ok) throw new Error(`快照读取失败：${name} (${r.status})`);
      return r.json();
    }).then(data => { resolved.set(name, data); return data; }).catch(e => { cache.delete(name); throw e; });
    cache.set(name, request);
  }
  return cache.get(name)!;
}
export function clearDataCache() { cache.clear(); resolved.clear(); }
export const escapePointer = (key: string | number) => String(key).replace(/~/g, '~0').replace(/\//g, '~1');
export const childRef = (base: string, key: string | number) => `${base}/${escapePointer(key)}`;
export function resolvePointer(document: any, pointer: string): any {
  if (!pointer) return document;
  if (!pointer.startsWith('/')) throw new Error('无效 JSON Pointer');
  return pointer.slice(1).split('/').reduce((v, k) => v?.[k.replace(/~1/g, '/').replace(/~0/g, '~')], document);
}
export function canonicalReference(reference: string, companies: RecordData[] = []): string | undefined {
  const codeRef=/^companies\.json#code=([^/]+)(.*)$/.exec(reference);
  if(codeRef){const i=companies.findIndex(c=>c.code===codeRef[1]);return i<0?undefined:`companies.json#/${i}${codeRef[2]}`;}
  const path=/^companies\[([^\]]+)\]\.metrics\.quarters\[([^\]]+)\]\.(\w+)$/.exec(reference);
  if(path){const i=companies.findIndex(c=>c.code===path[1]),j=companies[i]?.metrics?.quarters?.findIndex((q:RecordData)=>q.fiscal===path[2]);return i<0||j==null||j<0?undefined:`companies.json#/${i}/metrics/quarters/${j}/${path[3]}`;}
  return /^[a-z_]+\.json#(?:\/.*)?$/.test(reference)?reference:undefined;
}
export function formatValue(value: unknown, unit = ''): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—';
  const number = (n: number) => n.toLocaleString('en-US', { maximumFractionDigits: 2, minimumFractionDigits: 2 });
  if (unit === 'signed_ratio' || unit === 'signed_pp') {
    const n = unit === 'signed_ratio' ? value * 100 : value;
    return `${n >= 0 ? '+' : ''}${n.toFixed(1)}${unit === 'signed_ratio' ? '%' : ' pp'}`;
  }
  if (unit === 'CNY') return `${number(value / 1e8)} 亿元`;
  if (unit === 'USD') return `$${number(value / 1e9)}B`;
  if (unit === 'ratio') return `${number(value * 100)}%`;
  if (unit === '%') return `${number(value)}%`;
  if (unit === 'x') return `${number(value)}×`;
  return String(value);
}
export function safeUrl(url: unknown): string | undefined {
  if (typeof url !== 'string') return;
  try { const u = new URL(url); return ['https:', 'http:'].includes(u.protocol) ? u.href : undefined; } catch { return; }
}
