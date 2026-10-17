import { afterEach, describe, expect, it, vi } from 'vitest';
import { canonicalReference, childRef, clearDataCache, formatValue, loadData, resolvePointer, safeUrl } from './data';
afterEach(()=>{vi.unstubAllGlobals();clearDataCache();});
describe('快照数字显示',()=>{
  it('只做币种单位和比例显示换算',()=>{
    expect(formatValue(9318455131.82237,'USD')).toBe('$9.32B');
    expect(formatValue(65228720000,'CNY')).toBe('652.29 亿元');
    expect(formatValue(0.453503610066,'ratio')).toBe('45.35%');
    expect(formatValue(34.131,'%')).toBe('34.13%');
    expect(formatValue(12.5419,'x')).toBe('12.54×');
  });
  it('保留零、负数、小数原值，缺失值不伪装成零',()=>{
    expect(formatValue(0,'ratio')).toBe('0.00%');
    expect(formatValue(-200000000,'CNY')).toBe('-2.00 亿元');
    expect(formatValue(0.00012)).toBe('0.00012');
    for(const value of [null,undefined,NaN,Infinity,'10'])expect(formatValue(value)).toBe('—');
  });
  it('JSON Pointer 正确处理中文、斜线、波浪号和数组',()=>{
    const path=childRef('companies.json#/0/valuation','a/b~毛利');
    expect(path).toBe('companies.json#/0/valuation/a~1b~0毛利');
    expect(resolvePointer([{valuation:{'a/b~毛利':{value:10}}}],'/0/valuation/a~1b~0毛利/value')).toBe(10);
    expect(resolvePointer({x:1},'')).toEqual({x:1});
    expect(resolvePointer({},'/missing')).toBeUndefined();
  });
  it('将实际快照中的公司选择器与历史路径规范成 JSON Pointer',()=>{
    const companies=[{code:'300308.SZ',metrics:{quarters:[{fiscal:'2023Q1'}]}}];
    expect(canonicalReference('companies.json#code=300308.SZ/attribution',companies)).toBe('companies.json#/0/attribution');
    expect(canonicalReference('companies[300308.SZ].metrics.quarters[2023Q1].revenue',companies)).toBe('companies.json#/0/metrics/quarters/0/revenue');
    expect(canonicalReference('companies[unknown].metrics.quarters[2023Q1].revenue',companies)).toBeUndefined();
    expect(canonicalReference('companies.json#/0/name')).toBe('companies.json#/0/name');
  });
});
describe('按需读取与缓存',()=>{
  it('同一文件并发与后续请求复用，不提前加载其他文件',async()=>{
    const fetcher=vi.fn().mockResolvedValue({ok:true,json:async()=>[{code:'test'}]});vi.stubGlobal('fetch',fetcher);
    const a=loadData('companies'),b=loadData('companies');expect(a).toBe(b);
    expect(await a).toEqual([{code:'test'}]);await loadData('companies');
    expect(fetcher).toHaveBeenCalledTimes(1);expect(fetcher.mock.calls[0][0]).toBe('/data/companies.json');
  });
  it('HTTP 失败后可重试',async()=>{
    const fetcher=vi.fn().mockResolvedValueOnce({ok:false,status:503}).mockResolvedValueOnce({ok:true,json:async()=>[]});vi.stubGlobal('fetch',fetcher);
    await expect(loadData('comparables')).rejects.toThrow('快照读取失败');expect(await loadData('comparables')).toEqual([]);expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it('无效 JSON 不污染缓存',async()=>{
    const fetcher=vi.fn().mockResolvedValueOnce({ok:true,json:async()=>{throw Error('invalid JSON');}}).mockResolvedValueOnce({ok:true,json:async()=>({})});vi.stubGlobal('fetch',fetcher);
    await expect(loadData('meta')).rejects.toThrow('invalid JSON');expect(await loadData('meta')).toEqual({});
  });
  it('仅允许 HTTP(S) 原文链接',()=>{
    expect(safeUrl('javascript:alert(1)')).toBeUndefined();expect(safeUrl('https://www.cninfo.com.cn')).toBe('https://www.cninfo.com.cn/');
  });
});
