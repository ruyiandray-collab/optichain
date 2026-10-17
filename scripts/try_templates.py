"""模板试验：小批量真实调用，验证问句模板能否稳定返回预期列。"""
import sys, json
sys.path.insert(0, '.')
from pipeline.ifind import IfindClient
from pipeline.tables import parse_tables
c = IfindClient()
D = "2025-03-31、2025-06-30、2025-09-30、2025-12-31、2026-03-31、2026-06-30"
trials = [
 ("stock", "get_stock_financials", {"query": f"中际旭创、新易盛、天孚通信在{D}的单季度营业收入、单季度销售毛利率、单季度归属于母公司所有者的净利润"}),
 ("stock", "get_stock_financials", {"query": "中际旭创、新易盛2025-12-31主营构成(按地区)项目名称、项目收入"}),
 ("global_stock", "global_stock_profile", {"query": "Coherent、Lumentum、Fabrinet、AAOI、博通、Marvell的会计年结日、交易币种"}),
 ("global_stock", "global_stock_financial", {"query": "英伟达在20250331、20250630、20250930、20251231、20260331、20260630的营业收入"}),
]
for s, t, p in trials:
    r = c.call(s, t, p)
    print('====', r.status, r.ms, 'ms', r.error)
    ans = r.data.get('answer') if isinstance(r.data, dict) else r.data
    for tb in parse_tables(ans or ''):
        print('  cols:', tb.names()[:12])
        for row in tb.rows[:20]: print('   ', list(row.values())[:8])
