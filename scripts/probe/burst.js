// 限频探测：同时发 N 个实时快照请求，观察是否报错
const path = require('path'); const fs = require('fs');
const SKILL = process.env.IFIND_SKILL_DIR || path.join(process.env.USERPROFILE, '.claude/skills/ifind-finance-data');
const { call } = require(path.join(SKILL, 'call-node.js'));
const N = parseInt(process.argv[2] || '6');
const syms = ['300308.SZ', '300502.SZ', '300394.SZ', '688498.SH', '003031.SZ', '002281.SZ', '000988.SZ', '300620.SZ'];
(async () => {
  // 先串行打一次，完成 initialize，排除握手干扰
  await call('stock', 'stock_highfreq_quotes', { symbols: syms[0], indicators: '最新价', data_mode: 'real_time' });
  const t0 = Date.now();
  const res = await Promise.allSettled(Array.from({ length: N }, (_, i) => call('stock', 'stock_highfreq_quotes', { symbols: syms[i % syms.length], indicators: '最新价', data_mode: 'real_time' }).then(r => ({ i, ms: Date.now() - t0, r }))));
  const out = res.map((x, i) => x.status === 'fulfilled' ? { i, ms: x.value.ms, ok: x.value.r.ok, head: JSON.stringify(x.value.r.data || x.value.r.error).slice(0, 160) } : { i, rejected: String(x.reason).slice(0, 160) });
  fs.writeFileSync(path.join(__dirname, `../../data/probe_raw/burst_${N}.json`), JSON.stringify(out, null, 2));
  out.forEach(o => console.log(JSON.stringify(o)));
})();
