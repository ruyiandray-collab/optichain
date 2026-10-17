// 逐条执行 probes.json 中的真实调用，记录延迟/状态/返回大小，原始返回落盘 data/probe_raw/<id>.json
// 用法: node scripts/probe/run_probes.js scripts/probe/probes_1.json
const path = require('path');
const fs = require('fs');
const SKILL = process.env.IFIND_SKILL_DIR || path.join(process.env.USERPROFILE, '.claude/skills/ifind-finance-data');
const { call } = require(path.join(SKILL, 'call-node.js'));
const OUT = path.join(__dirname, '../../data/probe_raw');
const GAP_MS = 700; // 免费档 2 req/s，串行 + 间隔

function extractText(r) {
  const content = r && r.data && r.data.result && r.data.result.content;
  if (Array.isArray(content)) return content.map(c => c.text || '').join('\n');
  return JSON.stringify(r);
}

(async () => {
  const file = process.argv[2];
  const probes = JSON.parse(fs.readFileSync(file, 'utf-8'));
  const log = [];
  for (const p of probes) {
    const t0 = Date.now();
    let rec = { id: p.id, server: p.server, tool: p.tool, params: p.params, purpose: p.purpose, called_at: new Date().toISOString() };
    try {
      const r = await call(p.server, p.tool, p.params);
      rec.ms = Date.now() - t0;
      rec.ok = r.ok;
      const text = extractText(r);
      rec.bytes = Buffer.byteLength(text);
      rec.is_error = !!(r.data && r.data.result && r.data.result.isError);
      rec.preview = text.slice(0, 300);
      fs.writeFileSync(path.join(OUT, `${p.id}.json`), JSON.stringify({ request: p, called_at: rec.called_at, ms: rec.ms, response: r }, null, 2));
    } catch (e) {
      rec.ms = Date.now() - t0; rec.ok = false; rec.exception = e.message;
    }
    log.push(rec);
    console.log(`${rec.id}\t${rec.ok}\t${rec.ms}ms\t${rec.bytes || 0}B\t${rec.exception || (rec.preview || '').replace(/\s+/g, ' ').slice(0, 120)}`);
    await new Promise(r => setTimeout(r, GAP_MS));
  }
  const logFile = path.join(OUT, `log_${path.basename(file)}`);
  fs.writeFileSync(logFile, JSON.stringify(log, null, 2));
})();
