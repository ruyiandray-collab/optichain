// 列出 iFinD 8 个服务当前密钥可用的工具（list_tools 不消耗额度）
const path = require('path');
const fs = require('fs');
const SKILL = process.env.IFIND_SKILL_DIR || path.join(process.env.USERPROFILE, '.claude/skills/ifind-finance-data');
const { listTools } = require(path.join(SKILL, 'call-node.js'));
const OUT = path.join(__dirname, '../../data/probe_raw');
const SERVERS = ['stock', 'fund', 'edb', 'news', 'bond', 'global_stock', 'index', 'future'];
(async () => {
  const summary = [];
  for (const s of SERVERS) {
    const t0 = Date.now();
    try {
      const r = await listTools(s);
      const ms = Date.now() - t0;
      fs.writeFileSync(path.join(OUT, `tools_${s}.json`), JSON.stringify(r, null, 2));
      const tools = (r.data && r.data.result && r.data.result.tools) || [];
      summary.push({ server: s, ok: r.ok, ms, tools: tools.map(t => ({ name: t.name, params: Object.keys((t.inputSchema && t.inputSchema.properties) || {}), required: (t.inputSchema && t.inputSchema.required) || [] })) });
    } catch (e) {
      summary.push({ server: s, ok: false, ms: Date.now() - t0, error: e.message });
    }
  }
  fs.writeFileSync(path.join(OUT, 'tools_summary.json'), JSON.stringify(summary, null, 2));
  for (const s of summary) console.log(s.server, s.ok, s.ms + 'ms', s.error || s.tools.map(t => `${t.name}(${t.params.join(',')}; req=${t.required.join(',')})`).join(' | '));
})();
